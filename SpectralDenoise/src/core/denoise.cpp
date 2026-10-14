// Multi-scale, optionally temporal, DCT-domain denoising against a profile.
//
// At each pyramid level (coarse to fine):
//   1. Motion: every reference block on the grid is matched in each
//      neighbouring frame (predictors from the coarser level and from the
//      blocks already matched, then a local search). Matches whose difference
//      is larger than the noise alone explains are rejected, so moving or
//      occluded areas fall back to fewer frames.
//   2. Pass 1: the stack of matched blocks is transformed (2D DCT per block,
//      DCT along time), coefficients under 2.7 sigma are zeroed, and the
//      reference block's estimate is accumulated with overlap weights into a
//      pilot image.
//   3. Pass 2: the same stacks are Wiener-filtered, using the pilot as the
//      estimate of the clean signal, and accumulated into the result.
// Levels are then recombined: each level keeps its own band (itself minus
// its blurred self) and takes everything coarser from the level below.
#include "internal.h"
#include "gpu.h"

#include <cstdint>
#include <cstring>

namespace sd {

using namespace detail;

namespace {

// A neighbouring frame with fewer matched blocks than this is not used.
constexpr double kMinMatchedFraction = 0.3;

inline void loadBlock(const Plane& p, int x, int y, float* out)
{
    for (int j = 0; j < kBlock; ++j) std::memcpy(out + j * kBlock, p.row(y + j) + x, sizeof(float) * kBlock);
}

inline int floorDiv(int a, int b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }

// Catmull-Rom weights for each quarter-pixel fraction (taps at -1, 0, 1, 2).
struct SubWeights {
    float w[kSub][4];
    float gain[kSub];  // sum of squared weights: noise variance kept
    SubWeights()
    {
        for (int f = 0; f < kSub; ++f) {
            const float t = float(f) / kSub, t2 = t * t, t3 = t2 * t;
            w[f][0] = 0.5f * (-t3 + 2.0f * t2 - t);
            w[f][1] = 0.5f * (3.0f * t3 - 5.0f * t2 + 2.0f);
            w[f][2] = 0.5f * (-3.0f * t3 + 4.0f * t2 + t);
            w[f][3] = 0.5f * (t3 - t2);
            gain[f] = 0.0f;
            for (int i = 0; i < 4; ++i) gain[f] += w[f][i] * w[f][i];
        }
    }
};
const SubWeights& subWeights()
{
    static const SubWeights s;
    return s;
}

// Is the block at (x, y) displaced by (dx, dy) quarter pixels inside the
// plane, including the interpolation taps?
inline bool blockInside(const Plane& p, int x, int y, int dx, int dy)
{
    const int ix = x + floorDiv(dx, kSub), iy = y + floorDiv(dy, kSub);
    const int fx = dx - floorDiv(dx, kSub) * kSub, fy = dy - floorDiv(dy, kSub) * kSub;
    const int mx = fx ? 1 : 0, my = fy ? 1 : 0;
    return ix - mx >= 0 && iy - my >= 0 && ix + kBlock + 2 * mx <= p.w && iy + kBlock + 2 * my <= p.h;
}

// Load the block at (x, y) displaced by (dx, dy) quarter pixels, with
// Catmull-Rom interpolation for fractional positions. The position must be
// inside (blockInside).
void loadBlockSub(const Plane& p, int x, int y, int dx, int dy, float* out)
{
    const int ix = x + floorDiv(dx, kSub), iy = y + floorDiv(dy, kSub);
    const int fx = dx - floorDiv(dx, kSub) * kSub, fy = dy - floorDiv(dy, kSub) * kSub;
    if (!fx && !fy) {
        loadBlock(p, ix, iy, out);
        return;
    }
    const float* wx = subWeights().w[fx];
    const float* wy = subWeights().w[fy];
    // horizontal pass into rows -1..8 (only row 0..7 when fy == 0)
    float tmp[(kBlock + 3) * kBlock];
    const int r0 = fy ? -1 : 0, r1 = fy ? kBlock + 2 : kBlock;
    for (int j = r0; j < r1; ++j) {
        const float* r = p.row(iy + j) + ix;
        float* o = tmp + (j - r0) * kBlock;
        if (fx)
            for (int i = 0; i < kBlock; ++i) o[i] = wx[0] * r[i - 1] + wx[1] * r[i] + wx[2] * r[i + 1] + wx[3] * r[i + 2];
        else
            std::memcpy(o, r, sizeof(float) * kBlock);
    }
    if (!fy) {
        std::memcpy(out, tmp, sizeof(float) * kCoefs);
        return;
    }
    for (int j = 0; j < kBlock; ++j) {
        const float* a = tmp + j * kBlock;  // row j - 1
        float* o = out + j * kBlock;
        for (int i = 0; i < kBlock; ++i)
            o[i] = wy[0] * a[i] + wy[1] * a[i + kBlock] + wy[2] * a[i + 2 * kBlock] + wy[3] * a[i + 3 * kBlock];
    }
}

float blockSsd(const float* a, const float* b, float bail)
{
    float s = 0.0f;
    for (int j = 0; j < kBlock; ++j) {
        for (int i = 0; i < kBlock; ++i) {
            const float d = a[j * kBlock + i] - b[j * kBlock + i];
            s += d * d;
        }
        if (s > bail) return s;
    }
    return s;
}

int nearestIndex(const std::vector<int>& grid, int step, int pos)
{
    int i = (pos + step / 2) / step;
    return std::max(0, std::min(i, static_cast<int>(grid.size()) - 1));
}

// ---------------------------------------------------------------------------
// Motion

void estimateMotion(const std::vector<const Frame*>& fr, int ref, const LevelParams& lp, const MotionField* coarser,
                    MotionField& mf, int threads)
{
    const Plane& Y0 = fr[size_t(ref)]->c[0];
    const int slots = static_cast<int>(fr.size());
    mf.slots = slots;
    mf.m.assign(mf.xs.size() * mf.ys.size() * size_t(slots), Motion());
    const int coarsestRadius = 4;

    parallelFor(static_cast<int>(mf.ys.size()), threads, [&](int byi) {
        const size_t by = size_t(byi);
        const int y = mf.ys[by];
        for (size_t bx = 0; bx < mf.xs.size(); ++bx) {
            const int x = mf.xs[bx];
            float mean = 0.0f;
            for (int j = 0; j < kBlock; ++j)
                for (int i = 0; i < kBlock; ++i) mean += Y0.at(x + i, y + j);
            mean *= 1.0f / kCoefs;
            const float lf = lp.lumaAdaptive ? lp.prof->lumaFactor(0, mean) : 1.0f;
            const float expected = 2.0f * lp.matchVar * lf * lf * kCoefs;  // SSD of two noisy copies
            const float acceptSsd = expected * (1.0f + 0.75f * lp.tolerance) + 1e-12f;
            // A brightness change shows up in the block mean long before the
            // SSD notices it (one coefficient out of 64): bound it separately.
            const float acceptDc = 3.0f * 1.41421356f * lp.matchDcSigma * lf * (1.0f + lp.tolerance) + 1e-12f;

            // noise variance lost by interpolation, per fraction
            float interpPenalty[kSub * kSub];
            for (int fy = 0; fy < kSub; ++fy)
                for (int fx = 0; fx < kSub; ++fx)
                    interpPenalty[fx + kSub * fy] =
                        (1.0f - subWeights().gain[fx] * subWeights().gain[fy]) * expected * 0.5f;
            float refBlock[kCoefs], cand[kCoefs];
            loadBlock(Y0, x, y, refBlock);
            for (int s = 0; s < slots; ++s) {
                Motion& out = mf.at(bx, by, s);
                if (s == ref) {
                    out.ok = true;
                    continue;
                }
                if (!fr[size_t(s)]) continue;
                const Plane& Y1 = fr[size_t(s)]->c[0];
                float best = 3.0e38f;
                int bdx = 0, bdy = 0;
                auto tryVec = [&](int dx, int dy) {
                    if (!blockInside(Y1, x, y, dx, dy)) return;
                    loadBlockSub(Y1, x, y, dx, dy, cand);
                    // Interpolation averages away part of the candidate's
                    // noise; without this penalty the search would prefer
                    // fractional (blurred) matches for that reason alone.
                    const float pen = interpPenalty[(dx & (kSub - 1)) + kSub * (dy & (kSub - 1))];
                    const float c = blockSsd(refBlock, cand, best - pen) + pen;
                    if (c < best) {
                        best = c;
                        bdx = dx;
                        bdy = dy;
                    }
                };
                tryVec(0, 0);
                if (coarser) {
                    const int cbx = nearestIndex(coarser->xs, coarser->step, x / 2);
                    const int cby = nearestIndex(coarser->ys, coarser->step, y / 2);
                    const Motion& cm = coarser->at(size_t(cbx), size_t(cby), s);
                    tryVec(cm.dx * 2, cm.dy * 2);
                } else {
                    for (int dy = -coarsestRadius; dy <= coarsestRadius; ++dy)
                        for (int dx = -coarsestRadius; dx <= coarsestRadius; ++dx) tryVec(dx * kSub, dy * kSub);
                }
                // local refinement: whole pixels, then half, then quarter
                for (int stepQ = kSub; stepQ >= 1; stepQ /= 2) {
                    for (int it = 0; it < (stepQ == kSub ? 8 : 1); ++it) {
                        const int cx = bdx, cy = bdy;
                        for (int dy = -1; dy <= 1; ++dy)
                            for (int dx = -1; dx <= 1; ++dx)
                                if (dx || dy) tryVec(cx + dx * stepQ, cy + dy * stepQ);
                        if (bdx == cx && bdy == cy) break;
                    }
                }
                out.dx = static_cast<int16_t>(bdx);
                out.dy = static_cast<int16_t>(bdy);
                bool ok = best <= acceptSsd;
                if (ok) {
                    loadBlockSub(Y1, x, y, bdx, bdy, cand);
                    float dc = 0.0f;
                    for (int i = 0; i < kCoefs; ++i) dc += refBlock[i] - cand[i];
                    ok = std::fabs(dc) / kBlock <= acceptDc;  // DC coefficient = sum / 8
                }
                out.ok = ok;
            }
        }
    });
}

// ---------------------------------------------------------------------------
// Filtering passes

enum class PassMode { HardThreshold, WienerSelf, WienerPilot };

struct Accum {
    Plane num[3], den[3];
    void init(int w, int h)
    {
        for (int c = 0; c < 3; ++c) {
            num[c].resize(w, h);
            den[c].resize(w, h);
        }
    }
};

// Filter one block stack for one channel. blocks2d: [n][64] 2D DCT
// coefficients in time order; r0: index of the reference. Writes the
// reference's filtered spatial block to outBlock and returns its weight.
float filterStack(float (*blocks2d)[kCoefs], int n, int r0, const float* sigma, float lf, PassMode mode,
                  const float* pilot2d, float* outBlock)
{
    const auto& T = tables().t[n];
    float c3[kMaxSlots][kCoefs];
    // forward temporal transform
    if (n == 1) {
        std::memcpy(c3[0], blocks2d[0], sizeof(float) * kCoefs);
    } else {
        for (int k = 0; k < n; ++k) {
            float* o = c3[k];
            for (int q = 0; q < kCoefs; ++q) o[q] = 0.0f;
            for (int i = 0; i < n; ++i) {
                const float t = T[k][i];
                const float* b = blocks2d[i];
                for (int q = 0; q < kCoefs; ++q) o[q] += t * b[q];
            }
        }
    }

    float sig[kCoefs];
    for (int q = 0; q < kCoefs; ++q) sig[q] = sigma[q] * lf;

    float est[kMaxSlots][kCoefs];
    float weight;
    if (mode == PassMode::HardThreshold) {
        int nnz = 0;
        for (int k = 0; k < n; ++k)
            for (int q = 0; q < kCoefs; ++q) {
                const float v = c3[k][q];
                const bool keep = std::fabs(v) > kHardThreshold * sig[q];
                est[k][q] = keep ? v : 0.0f;
                nnz += keep;
            }
        weight = 1.0f / (lf * lf * float(std::max(nnz, 1)));
    } else {
        // Oracle spectrum: hard-thresholded stack; for WienerPilot the
        // reference slice is replaced by the pilot block.
        float oracle[kMaxSlots][kCoefs];
        for (int k = 0; k < n; ++k)
            for (int q = 0; q < kCoefs; ++q) {
                const float v = c3[k][q];
                oracle[k][q] = std::fabs(v) > kHardThreshold * sig[q] ? v : 0.0f;
            }
        if (mode == PassMode::WienerPilot) {
            if (n == 1) {
                std::memcpy(oracle[0], pilot2d, sizeof(float) * kCoefs);
            } else {
                // back to slices, swap in the pilot, forward again
                float slices[kMaxSlots][kCoefs];
                for (int i = 0; i < n; ++i)
                    for (int q = 0; q < kCoefs; ++q) {
                        float s = 0.0f;
                        for (int k = 0; k < n; ++k) s += T[k][i] * oracle[k][q];
                        slices[i][q] = s;
                    }
                std::memcpy(slices[r0], pilot2d, sizeof(float) * kCoefs);
                for (int k = 0; k < n; ++k)
                    for (int q = 0; q < kCoefs; ++q) {
                        float s = 0.0f;
                        for (int i = 0; i < n; ++i) s += T[k][i] * slices[i][q];
                        oracle[k][q] = s;
                    }
            }
        }
        float wsum = 0.0f;
        for (int k = 0; k < n; ++k)
            for (int q = 0; q < kCoefs; ++q) {
                const float o2 = oracle[k][q] * oracle[k][q];
                const float s2 = sig[q] * sig[q];
                const float W = s2 > 0.0f ? o2 / (o2 + s2) : 1.0f;
                est[k][q] = W * c3[k][q];
                wsum += W * W * s2;
            }
        weight = 1.0f / std::max(wsum, 1e-12f);
        // normalise like the hard-threshold weights so levels are comparable
        float sbar = 0.0f;
        for (int q = 0; q < kCoefs; ++q) sbar += sigma[q] * sigma[q];
        weight *= sbar / kCoefs + 1e-20f;
    }

    // reference slice
    float ref2d[kCoefs];
    if (n == 1) {
        std::memcpy(ref2d, est[0], sizeof ref2d);
    } else {
        for (int q = 0; q < kCoefs; ++q) {
            float s = 0.0f;
            for (int k = 0; k < n; ++k) s += T[k][r0] * est[k][q];
            ref2d[q] = s;
        }
    }
    idct8x8(ref2d, outBlock);
    return weight;
}

void runPass(const std::vector<const Frame*>& fr, int ref, const LevelParams& lp, const MotionField& mf,
             PassMode mode, const Frame* pilot, Accum& acc, int threads, const AbortFn& abort,
             std::atomic<bool>& aborted)
{
    const Plane& Y0 = fr[size_t(ref)]->c[0];
    const int w = Y0.w, h = Y0.h;
    acc.init(w, h);
    const auto& kaiser = tables().kaiser;
    const int rowsPerBand = std::max(1, (2 * kBlock + lp.step - 1) / lp.step);
    const int nRows = static_cast<int>(mf.ys.size());
    const int nBands = (nRows + rowsPerBand - 1) / rowsPerBand;

    auto doBand = [&](int band) {
        if (aborted.load()) return;
        if (abort && abort()) {
            aborted.store(true);
            return;
        }
        float blocks[kMaxSlots][kCoefs];
        float tmp[kCoefs], pilot2d[kCoefs], outBlock[kCoefs];
        int sx[kMaxSlots], sy[kMaxSlots], sf[kMaxSlots];
        const int r1 = std::min(nRows, (band + 1) * rowsPerBand);
        for (int byi = band * rowsPerBand; byi < r1; ++byi) {
            const size_t by = size_t(byi);
            const int y = mf.ys[by];
            for (size_t bx = 0; bx < mf.xs.size(); ++bx) {
                const int x = mf.xs[bx];
                // stack in time order
                int n = 0, r0 = 0;
                for (int s = 0; s < mf.slots; ++s) {
                    const Motion& m = mf.at(bx, by, s);
                    if (!m.ok) continue;
                    if (s == ref) r0 = n;
                    sx[n] = m.dx;
                    sy[n] = m.dy;
                    sf[n] = s;
                    ++n;
                }
                float mean = 0.0f;
                for (int j = 0; j < kBlock; ++j)
                    for (int i = 0; i < kBlock; ++i) mean += Y0.at(x + i, y + j);
                mean *= 1.0f / kCoefs;
                for (int c = 0; c < 3; ++c) {
                    const float lf = lp.lumaAdaptive ? lp.prof->lumaFactor(c, mean) : 1.0f;
                    for (int i = 0; i < n; ++i) {
                        loadBlockSub(fr[size_t(sf[i])]->c[c], x, y, sx[i], sy[i], tmp);
                        dct8x8(tmp, blocks[i]);
                    }
                    if (mode == PassMode::WienerPilot) {
                        loadBlock(pilot->c[c], x, y, tmp);
                        dct8x8(tmp, pilot2d);
                    }
                    const float wgt = filterStack(blocks, n, r0, lp.sigma[c], lf, mode, pilot2d, outBlock);
                    for (int j = 0; j < kBlock; ++j) {
                        float* nr = acc.num[c].row(y + j) + x;
                        float* dr = acc.den[c].row(y + j) + x;
                        for (int i = 0; i < kBlock; ++i) {
                            const float wk = wgt * kaiser[j * kBlock + i];
                            nr[i] += wk * outBlock[j * kBlock + i];
                            dr[i] += wk;
                        }
                    }
                }
            }
        }
    };
    // Bands two apart never touch the same pixels: run even bands, then odd.
    for (int phase = 0; phase < 2; ++phase) {
        const int count = (nBands - phase + 1) / 2;
        parallelFor(count, threads, [&](int i) { doBand(2 * i + phase); });
    }
}

void resolve(const Accum& acc, Frame& out)
{
    for (int c = 0; c < 3; ++c) {
        out.c[c].resize(acc.num[c].w, acc.num[c].h);
        const size_t n = out.c[c].d.size();
        for (size_t i = 0; i < n; ++i) {
            const float d = acc.den[c].d[i];
            out.c[c].d[i] = d > 0.0f ? acc.num[c].d[i] / d : 0.0f;
        }
    }
}

}  // namespace

bool gpuInfo(std::string* deviceOrReason) { return gpuAvailable(deviceOrReason); }

bool denoise(const std::vector<const Frame*>& framesIn, int ref, const NoiseProfile& profile, const Settings& st,
             Frame& out, const AbortFn& abort)
{
    const Frame& src = *framesIn[size_t(ref)];
    const int w = src.w(), h = src.h();
    out = src;
    if (!profile.valid || w < kBlock || h < kBlock) return true;
    const int threads = threadCount(st.threads);
    std::atomic<bool> aborted{false};

    // Temporal neighbours that exist and match the reference size.
    std::vector<const Frame*> frames;
    int refIdx = 0;
    const int radius = std::max(0, std::min(st.temporalRadius, kMaxTemporalRadius));
    for (int i = 0; i < static_cast<int>(framesIn.size()); ++i) {
        if (std::abs(i - ref) > radius) continue;
        const Frame* f = framesIn[size_t(i)];
        if (i != ref && (!f || f->w() != w || f->h() != h)) continue;
        if (i == ref) refIdx = static_cast<int>(frames.size());
        frames.push_back(f);
    }
    const int nf = static_cast<int>(frames.size());

    const int levels = scalesForSize(w, h);
    // pyr[level][frame]
    std::vector<std::vector<Frame>> pyr(static_cast<size_t>(levels), std::vector<Frame>(static_cast<size_t>(nf)));
    parallelFor(nf * 3, threads, [&](int job) {
        const int f = job / 3, c = job % 3;
        const Plane* prev = &frames[size_t(f)]->c[c];
        for (int s = 1; s < levels; ++s) {
            downsample(*prev, pyr[size_t(s)][size_t(f)].c[c]);
            prev = &pyr[size_t(s)][size_t(f)].c[c];
        }
    });

    const int step = st.quality <= 0 ? 4 : (st.quality == 1 ? 3 : 2);
    const bool twoPass = st.quality >= 1;

    std::vector<Frame> denoised(static_cast<size_t>(levels));
    std::vector<bool> dropped(static_cast<size_t>(nf), false);
    bool useGpu = st.useGpu && gpuAvailable(nullptr);
    MotionField coarser;
    bool haveCoarser = false;
    for (int s = levels - 1; s >= 0; --s) {
        std::vector<const Frame*> fr(static_cast<size_t>(nf));
        for (int f = 0; f < nf; ++f) fr[size_t(f)] = s == 0 ? frames[size_t(f)] : &pyr[size_t(s)][size_t(f)];
        const Frame& r = *fr[size_t(refIdx)];
        if (r.w() < kBlock || r.h() < kBlock) {
            denoised[size_t(s)] = r;
            continue;
        }

        LevelParams lp;
        const int ps = std::max(0, std::min(s, profile.scales - 1));
        const float scaleMul = st.scaleLevel[std::min(s, kMaxScales - 1)];
        for (int c = 0; c < 3; ++c) {
            const float m = st.level * (c == 0 ? st.lumaLevel : st.chromaLevel) * scaleMul;
            for (int k = 0; k < kCoefs; ++k) lp.sigma[c][k] = std::max(0.0f, profile.sigma[ps][c][k] * m);
        }
        const float ps0 = profile.pixelSigma(ps, 0);
        lp.matchVar = ps0 * ps0;
        lp.matchDcSigma = profile.sigma[ps][0][0];
        lp.prof = &profile;
        lp.lumaAdaptive = st.lumaAdaptive;
        lp.step = step;
        lp.quality = st.quality;
        lp.tolerance = st.motionTolerance;

        MotionField mf;
        mf.xs = blockGrid(r.w(), step);
        mf.ys = blockGrid(r.h(), step);
        mf.step = step;
        if (useGpu) {
            if (gpuLevel(fr, refIdx, lp, haveCoarser ? &coarser : nullptr, dropped, kMinMatchedFraction, twoPass, mf,
                         denoised[size_t(s)], abort)) {
                coarser = std::move(mf);
                haveCoarser = true;
                continue;
            }
            if (abort && abort()) return false;
            useGpu = false;  // the CPU does this level and the rest
            mf = MotionField();
            mf.xs = blockGrid(r.w(), step);
            mf.ys = blockGrid(r.h(), step);
            mf.step = step;
        }
        if (nf > 1) {
            estimateMotion(fr, refIdx, lp, haveCoarser ? &coarser : nullptr, mf, threads);
            // Shot cuts and flashes: a neighbour that hardly matches anywhere
            // is dropped altogether. Decided at the coarsest level, where
            // matching is most reliable, and kept for the finer ones.
            const size_t nb = mf.xs.size() * mf.ys.size();
            for (int f = 0; f < nf; ++f) {
                if (f == refIdx) continue;
                if (!haveCoarser) {
                    size_t ok = 0;
                    for (size_t b = 0; b < nb; ++b) ok += mf.m[b * size_t(nf) + size_t(f)].ok;
                    dropped[size_t(f)] = double(ok) < kMinMatchedFraction * double(nb);
                }
                if (dropped[size_t(f)])
                    for (size_t b = 0; b < nb; ++b) mf.m[b * size_t(nf) + size_t(f)].ok = false;
            }
        } else {
            mf.slots = 1;
            mf.m.assign(mf.xs.size() * mf.ys.size(), Motion());
            for (auto& m : mf.m) m.ok = true;
        }

        Accum acc;
        if (twoPass) {
            Frame pilot;
            runPass(fr, refIdx, lp, mf, PassMode::HardThreshold, nullptr, acc, threads, abort, aborted);
            resolve(acc, pilot);
            runPass(fr, refIdx, lp, mf, PassMode::WienerPilot, &pilot, acc, threads, abort, aborted);
        } else {
            runPass(fr, refIdx, lp, mf, PassMode::WienerSelf, nullptr, acc, threads, abort, aborted);
        }
        if (aborted.load()) return false;
        resolve(acc, denoised[size_t(s)]);
        coarser = std::move(mf);
        haveCoarser = true;
    }

    // Recombine: each level keeps its own band, coarser bands come from below.
    Frame result = denoised[size_t(levels - 1)];
    for (int s = levels - 2; s >= 0; --s) {
        Frame& u = denoised[size_t(s)];
        parallelFor(3, threads, [&](int c) {
            Plane down, upU, upR;
            downsample(u.c[c], down);
            upsample(down, u.c[c].w, u.c[c].h, upU);
            upsample(result.c[c], u.c[c].w, u.c[c].h, upR);
            for (size_t i = 0; i < u.c[c].d.size(); ++i) u.c[c].d[i] += upR.d[i] - upU.d[i];
        });
        result = std::move(u);
    }

    // Amounts and sharpening.
    Plane blurY;
    if (st.sharpen != 0.0f) blur5(result.c[0], blurY);
    for (int c = 0; c < 3; ++c) {
        const float keep = 1.0f - (c == 0 ? st.lumaAmount : st.chromaAmount);
        const auto& in = src.c[c].d;
        auto& o = out.c[c].d;
        const auto& r = result.c[c].d;
        for (size_t i = 0; i < o.size(); ++i) {
            float v = r[i] + keep * (in[i] - r[i]);
            if (c == 0 && st.sharpen != 0.0f) v += st.sharpen * (r[i] - blurY.d[i]);
            o[i] = v;
        }
    }
    return true;
}

}  // namespace sd
