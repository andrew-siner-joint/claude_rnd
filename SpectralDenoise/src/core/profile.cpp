// Noise profiling: per-coefficient noise of 8x8 DCT blocks, per pyramid level
// and channel, plus a luma-dependent multiplier curve.
//
// Coefficients are measured on differences between a block and its neighbour
// 8 pixels away. A smooth gradient gives both blocks the same AC
// coefficients, so it cancels, while independent noise doubles in variance.
// The robust estimate sqrt(median(d^2) / 0.4549 / 2) then ignores the odd
// block with detail in it.
//
// Coarse levels are hard to measure without a large flat area: image
// structure dwarfs the little noise left after downsampling. So the noise at
// coarse levels is also predicted from the finest level (synthesise noise
// with the measured spectrum, downsample it, measure it), and estimates that
// can't be trusted are capped at a margin above that prediction.
// Overestimating noise destroys detail; underestimating only leaves noise.
#include "internal.h"

#include <complex>
#include <cstring>
#include <random>

namespace sd {

using namespace detail;

namespace {

constexpr float kChi2Median = 0.45493642f;  // median of chi-square with 1 dof
constexpr float kPredictionMargin = 3.0f;
// In auto mode, low frequencies (u + v <= 3) may be at most this much above
// the u + v = 4..5 ring: image structure dominates them almost everywhere.
constexpr float kLowFreqMargin = 1.5f;

std::vector<Frame> buildPyramid(const Frame& f, int scales)
{
    std::vector<Frame> pyr(static_cast<size_t>(scales));
    pyr[0] = f;
    for (int s = 1; s < scales; ++s)
        for (int c = 0; c < 3; ++c) downsample(pyr[size_t(s) - 1].c[c], pyr[size_t(s)].c[c]);
    return pyr;
}

float medianOf(std::vector<float>& v)
{
    if (v.empty()) return 0.0f;
    const size_t m = v.size() / 2;
    std::nth_element(v.begin(), v.begin() + static_cast<std::ptrdiff_t>(m), v.end());
    return v[m];
}

void blockDct(const Plane& p, int x, int y, float* out)
{
    float in[kCoefs];
    for (int j = 0; j < kBlock; ++j) std::memcpy(in + j * kBlock, p.row(y + j) + x, sizeof(float) * kBlock);
    dct8x8(in, out);
}

inline int freqSum(int k) { return (k / kBlock) + (k % kBlock); }

// Pixel sigma over the coefficients with u + v >= 4: the part of the
// spectrum that is reliably measurable everywhere, used for relative
// (luma curve) measurements.
float midHighSigma(const float sigma[kCoefs])
{
    double s = 0.0;
    int n = 0;
    for (int k = 0; k < kCoefs; ++k)
        if (freqSum(k) >= 4) {
            s += double(sigma[k]) * sigma[k];
            ++n;
        }
    return static_cast<float>(std::sqrt(s / n));
}

// Cap the coefficients with u + v <= 3 at a margin above the mean of the
// u + v = 4..5 ring.
void clampLowFrequencies(float sigma[kCoefs], float margin)
{
    double ring = 0.0;
    int n = 0;
    for (int k = 0; k < kCoefs; ++k)
        if (freqSum(k) == 4 || freqSum(k) == 5) {
            ring += sigma[k];
            ++n;
        }
    const float cap = margin * static_cast<float>(ring / n);
    for (int k = 0; k < kCoefs; ++k)
        if (freqSum(k) <= 3) sigma[k] = std::min(sigma[k], cap);
}

// Samples of squared block differences per channel and coefficient.
struct DiffSamples {
    std::vector<float> v[3][kCoefs];
    size_t count() const { return v[0][kCoefs - 1].size(); }
};

void addPair(const Frame& f, int xa, int ya, int xb, int yb, DiffSamples& out, const bool* useCoef = nullptr)
{
    float a[kCoefs], b[kCoefs];
    for (int c = 0; c < 3; ++c) {
        blockDct(f.c[c], xa, ya, a);
        blockDct(f.c[c], xb, yb, b);
        for (int k = 1; k < kCoefs; ++k) {
            if (useCoef && !useCoef[k]) continue;
            const float d = a[k] - b[k];
            out.v[c][k].push_back(d * d);
        }
    }
}

// sigma per coefficient from difference samples; DC extrapolated from the
// lowest AC coefficients.
void sigmaFromSamples(DiffSamples& s, float sigma[3][kCoefs])
{
    for (int c = 0; c < 3; ++c) {
        for (int k = 1; k < kCoefs; ++k) sigma[c][k] = std::sqrt(medianOf(s.v[c][k]) / kChi2Median * 0.5f);
        sigma[c][0] = 0.5f * (sigma[c][1] + sigma[c][kBlock]);
    }
}

float pixelSigmaOf(const float sigma[kCoefs])
{
    double s = 0.0;
    for (int k = 0; k < kCoefs; ++k) s += double(sigma[k]) * sigma[k];
    return static_cast<float>(std::sqrt(s / kCoefs));
}

void lumaRange(const Frame& f, float& lo, float& hi)
{
    std::vector<float> v;
    const Plane& y = f.c[0];
    for (int j = 0; j < y.h; j += 3)
        for (int i = 0; i < y.w; i += 3) v.push_back(y.at(i, j));
    if (v.empty()) {
        lo = 0.0f;
        hi = 1.0f;
        return;
    }
    std::sort(v.begin(), v.end());
    lo = v[static_cast<size_t>(0.01 * static_cast<double>(v.size() - 1))];
    hi = v[static_cast<size_t>(0.99 * static_cast<double>(v.size() - 1))];
    if (hi - lo < 1e-3f) {
        const float m = 0.5f * (lo + hi);
        lo = m - 0.5f;
        hi = m + 0.5f;
    }
}

int lumaBin(float luma, float lo, float hi)
{
    const int b = static_cast<int>((luma - lo) / (hi - lo) * kLumaBins);
    return std::max(0, std::min(kLumaBins - 1, b));
}

// --- prediction of coarse levels -------------------------------------------

using cpx = std::complex<double>;

void fft1(cpx* a, int n, bool inverse)
{
    for (int i = 1, j = 0; i < n; ++i) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(a[i], a[j]);
    }
    const double pi = 3.14159265358979323846;
    for (int len = 2; len <= n; len <<= 1) {
        const double ang = 2.0 * pi / len * (inverse ? 1.0 : -1.0);
        const cpx wl(std::cos(ang), std::sin(ang));
        for (int i = 0; i < n; i += len) {
            cpx w(1.0, 0.0);
            for (int j = 0; j < len / 2; ++j) {
                const cpx u = a[i + j], v = a[i + j + len / 2] * w;
                a[i + j] = u + v;
                a[i + j + len / 2] = u - v;
                w *= wl;
            }
        }
    }
}

void fft2(std::vector<cpx>& a, int n, bool inverse)
{
    std::vector<cpx> col(static_cast<size_t>(n));
    for (int y = 0; y < n; ++y) fft1(&a[size_t(y) * n], n, inverse);
    for (int x = 0; x < n; ++x) {
        for (int y = 0; y < n; ++y) col[size_t(y)] = a[size_t(y) * n + x];
        fft1(col.data(), n, inverse);
        for (int y = 0; y < n; ++y) a[size_t(y) * n + x] = col[size_t(y)];
    }
}

// Median-based sigma of every coefficient over all blocks of a pure-noise
// plane.
void measureNoisePlane(const Plane& p, float sigma[kCoefs])
{
    std::vector<float> v[kCoefs];
    float c[kCoefs];
    const int step = p.w >= 256 ? 4 : (p.w >= 128 ? 2 : 1);
    for (int y = 0; y + kBlock <= p.h; y += step)
        for (int x = 0; x + kBlock <= p.w; x += step) {
            blockDct(p, x, y, c);
            for (int k = 0; k < kCoefs; ++k) v[k].push_back(c[k] * c[k]);
        }
    for (int k = 0; k < kCoefs; ++k) sigma[k] = std::sqrt(medianOf(v[k]) / kChi2Median);
}

// Synthesise stationary Gaussian noise whose 8x8 DCT spectrum matches
// sigma0, optionally box-downsample it by `factor` (what a host does for a
// proxy or reduced render scale), push it through the pyramid, and measure
// each level.
void predictLevels(const float sigma0[kCoefs], int scales, float out[kMaxScales][kCoefs], int factor = 1)
{
    const int n = 512 * std::min(factor, 2);
    std::vector<double> table(kCoefs);
    for (int k = 0; k < kCoefs; ++k) table[size_t(k)] = double(sigma0[k]) * sigma0[k];

    std::mt19937 rng(12345);
    std::normal_distribution<double> gauss(0.0, 1.0);
    std::vector<cpx> white(size_t(n) * n);
    for (auto& v : white) v = cpx(gauss(rng), 0.0);
    fft2(white, n, false);

    Plane noise;
    for (int iter = 0; iter < 2; ++iter) {
        // Filter: sqrt of the table, read at each frequency (DCT index u
        // sits at u/16 cycles per pixel).
        std::vector<cpx> spec = white;
        for (int ky = 0; ky < n; ++ky) {
            const double fy = std::min(ky, n - ky) * 16.0 / n;
            const double vy = std::min(fy, 7.0);
            const int y0 = std::min(static_cast<int>(vy), 6);
            const double ty = vy - y0;
            for (int kx = 0; kx < n; ++kx) {
                const double fx = std::min(kx, n - kx) * 16.0 / n;
                const double vx = std::min(fx, 7.0);
                const int x0 = std::min(static_cast<int>(vx), 6);
                const double tx = vx - x0;
                const double p = (1 - ty) * ((1 - tx) * table[size_t(y0 * 8 + x0)] + tx * table[size_t(y0 * 8 + x0 + 1)]) +
                                 ty * ((1 - tx) * table[size_t((y0 + 1) * 8 + x0)] + tx * table[size_t((y0 + 1) * 8 + x0 + 1)]);
                spec[size_t(ky) * n + kx] *= std::sqrt(std::max(p, 0.0));
            }
        }
        fft2(spec, n, true);
        noise.resize(n, n);
        for (size_t i = 0; i < noise.d.size(); ++i) noise.d[i] = static_cast<float>(spec[i].real() / (double(n) * n));
        if (iter == 1) break;
        // Correct the table so level 0 matches the measured spectrum.
        float meas[kCoefs];
        measureNoisePlane(noise, meas);
        for (int k = 0; k < kCoefs; ++k) {
            const double m = double(meas[k]) * meas[k];
            if (m > 0.0) table[size_t(k)] *= double(sigma0[k]) * sigma0[k] / m;
        }
    }
    Plane cur;
    if (factor > 1) {
        const int m = n / factor;
        cur.resize(m, m);
        const float norm = 1.0f / float(factor * factor);
        for (int y = 0; y < m; ++y)
            for (int x = 0; x < m; ++x) {
                float acc = 0.0f;
                for (int j = 0; j < factor; ++j)
                    for (int i = 0; i < factor; ++i) acc += noise.at(x * factor + i, y * factor + j);
                cur.at(x, y) = acc * norm;
            }
    } else {
        cur = noise;
    }
    for (int s = 0; s < scales; ++s) {
        if (cur.w < 2 * kBlock) {
            // too small to measure: keep the previous level's estimate
            for (int k = 0; k < kCoefs; ++k) out[s][k] = s > 0 ? out[s - 1][k] : sigma0[k];
            continue;
        }
        measureNoisePlane(cur, out[s]);
        if (s + 1 < scales) {
            Plane next;
            downsample(cur, next);
            cur = std::move(next);
        }
    }
}

// --- automatic estimate ----------------------------------------------------

// Estimate at one level from the flattest blocks of the frame. Low-frequency
// coefficients are measured on blocks chosen for having little
// high-frequency energy and vice versa, so blocks are never chosen for being
// quiet in the very coefficients being measured (which would bias the
// estimate low). With measureBins, the noise of each luma bin is measured
// too (mid and high frequencies only).
struct AutoResult {
    float sigma[3][kCoefs] = {};
    float binSigma[3][kLumaBins] = {};
    int binCount[kLumaBins] = {};
    bool ok = false;
};

constexpr int kMinBinPairs = 40;

AutoResult autoLevel(const Frame& f, float lumaLo, float lumaHi, bool measureBins, int threads)
{
    AutoResult res;
    const int w = f.w(), h = f.h();
    if (w < 2 * kBlock || h < kBlock) return res;
    const long area = long(w) * h;
    const int step = area > 4000000 ? 8 : (area > 600000 ? 6 : 4);
    std::vector<int> xs, ys;
    for (int x = 0; x + 2 * kBlock <= w; x += step) xs.push_back(x);
    for (int y = 0; y + kBlock <= h; y += step) ys.push_back(y);
    const size_t n = xs.size() * ys.size();
    if (n < 16) return res;

    // Per pair (block at x and x+8): energies, luma.
    std::vector<float> elf(n), ehf(n), luma(n);
    parallelFor(static_cast<int>(ys.size()), threadCount(threads), [&](int j) {
        float a[kCoefs], b[kCoefs];
        for (size_t i = 0; i < xs.size(); ++i) {
            const size_t idx = size_t(j) * xs.size() + i;
            float lf = 0.0f, hf = 0.0f;
            for (int c = 0; c < 3; ++c) {
                blockDct(f.c[c], xs[i], ys[size_t(j)], a);
                blockDct(f.c[c], xs[i] + kBlock, ys[size_t(j)], b);
                if (c == 0) luma[idx] = 0.5f * (a[0] + b[0]) / kBlock;
                for (int k = 1; k < kCoefs; ++k) {
                    const int fs = freqSum(k);
                    const float e = a[k] * a[k] + b[k] * b[k];
                    if (fs <= 3)
                        lf += e;
                    else if (fs >= 9)
                        hf += e;
                }
            }
            elf[idx] = lf;
            ehf[idx] = hf;
        }
    });

    // Pairs that are exactly flat (clipped or synthetic) carry no noise.
    float medTot = 0.0f;
    {
        std::vector<float> tot(n);
        for (size_t i = 0; i < n; ++i) tot[i] = elf[i] + ehf[i];
        medTot = medianOf(tot);
    }
    auto live = [&](size_t i) { return elf[i] + ehf[i] > 1e-6f * medTot; };

    auto lowest = [&](const std::vector<float>& e, const std::vector<size_t>& pool, double frac, size_t minCount) {
        std::vector<size_t> idx = pool;
        const size_t keep =
            std::min(idx.size(), std::max(minCount, static_cast<size_t>(frac * static_cast<double>(idx.size()))));
        if (keep == 0) return idx;
        std::nth_element(idx.begin(), idx.begin() + static_cast<std::ptrdiff_t>(keep - 1), idx.end(),
                         [&](size_t p, size_t q) { return e[p] < e[q]; });
        idx.resize(keep);
        return idx;
    };

    bool lowSet[kCoefs], highSet[kCoefs];
    for (int k = 0; k < kCoefs; ++k) {
        lowSet[k] = k > 0 && freqSum(k) <= 6;
        highSet[k] = freqSum(k) >= 7;
    }

    auto measure = [&](const std::vector<size_t>& pool, float sigma[3][kCoefs]) {
        DiffSamples s;
        for (size_t p : lowest(ehf, pool, 0.1, 48))
            addPair(f, xs[p % xs.size()], ys[p / xs.size()], xs[p % xs.size()] + kBlock, ys[p / xs.size()], s, lowSet);
        for (size_t p : lowest(elf, pool, 0.1, 48))
            addPair(f, xs[p % xs.size()], ys[p / xs.size()], xs[p % xs.size()] + kBlock, ys[p / xs.size()], s,
                    highSet);
        sigmaFromSamples(s, sigma);
    };

    std::vector<size_t> all;
    all.reserve(n);
    for (size_t i = 0; i < n; ++i)
        if (live(i)) all.push_back(i);
    if (all.size() < 16) return res;
    measure(all, res.sigma);
    res.ok = true;

    if (measureBins) {
        std::vector<size_t> pools[kLumaBins];
        for (size_t i : all) pools[lumaBin(luma[i], lumaLo, lumaHi)].push_back(i);
        for (int b = 0; b < kLumaBins; ++b) {
            res.binCount[b] = static_cast<int>(pools[b].size());
            if (pools[b].size() < kMinBinPairs) continue;
            float sg[3][kCoefs];
            measure(pools[b], sg);
            for (int c = 0; c < 3; ++c) res.binSigma[c][b] = midHighSigma(sg[c]);
        }
    }
    return res;
}

// Turn per-bin sigmas into a relative curve, filling bins without data from
// their neighbours.
void buildCurve(const AutoResult& a, NoiseProfile& p)
{
    for (int c = 0; c < 3; ++c) {
        const float ref = midHighSigma(a.sigma[c]);
        float v[kLumaBins];
        bool have[kLumaBins];
        int nHave = 0;
        for (int b = 0; b < kLumaBins; ++b) {
            have[b] = a.binCount[b] >= kMinBinPairs && a.binSigma[c][b] > 0.0f && ref > 0.0f;
            v[b] = have[b] ? a.binSigma[c][b] / ref : 1.0f;
            nHave += have[b];
        }
        if (nHave == 0) {
            for (int b = 0; b < kLumaBins; ++b) p.lumaCurve[c][b] = 1.0f;
            continue;
        }
        for (int b = 0; b < kLumaBins; ++b) {
            if (have[b]) continue;
            int l = b - 1, r = b + 1;
            while (l >= 0 && !have[l]) --l;
            while (r < kLumaBins && !have[r]) ++r;
            if (l >= 0 && r < kLumaBins)
                v[b] = v[l] + (v[r] - v[l]) * float(b - l) / float(r - l);
            else
                v[b] = l >= 0 ? v[l] : v[r];
        }
        for (int b = 0; b < kLumaBins; ++b) p.lumaCurve[c][b] = std::max(0.05f, std::min(20.0f, v[b]));
    }
}

void capByPrediction(NoiseProfile& p, int fromScale, const float pred[3][kMaxScales][kCoefs])
{
    for (int s = fromScale; s < p.scales; ++s)
        for (int c = 0; c < 3; ++c)
            for (int k = 0; k < kCoefs; ++k)
                p.sigma[s][c][k] = std::min(p.sigma[s][c][k], kPredictionMargin * pred[c][s][k]);
}

}  // namespace

NoiseProfile analyseAuto(const Frame& frame, Space space, int threads)
{
    NoiseProfile p;
    const int scales = scalesForSize(frame.w(), frame.h());
    if (frame.w() < 2 * kBlock || frame.h() < kBlock) return p;
    auto pyr = buildPyramid(frame, scales);
    lumaRange(frame, p.lumaLo, p.lumaHi);
    p.space = space;
    p.scales = scales;
    p.imageW = frame.w();
    p.imageH = frame.h();
    for (int s = 0; s < scales; ++s) {
        AutoResult a = autoLevel(pyr[size_t(s)], p.lumaLo, p.lumaHi, s == 0, threads);
        if (!a.ok) {
            if (s == 0) return NoiseProfile();
            // too small to measure: the prediction below fills it in
            for (int c = 0; c < 3; ++c)
                for (int k = 0; k < kCoefs; ++k) p.sigma[s][c][k] = 1e30f;
            continue;
        }
        std::memcpy(p.sigma[s], a.sigma, sizeof a.sigma);
        if (s == 0) buildCurve(a, p);
    }
    for (int s = 0; s < scales; ++s)
        for (int c = 0; c < 3; ++c) clampLowFrequencies(p.sigma[s][c], kLowFreqMargin);
    float pred[3][kMaxScales][kCoefs];
    parallelFor(3, threadCount(threads), [&](int c) { predictLevels(p.sigma[0][c], scales, pred[c]); });
    capByPrediction(p, 1, pred);
    p.valid = true;
    return p;
}

NoiseProfile profileAtScale(const NoiseProfile& p, int shift, int threads)
{
    if (!p.valid || shift <= 0) return p;
    shift = std::min(shift, 3);
    const int factor = 1 << shift;
    NoiseProfile q = p;
    float pred[3][kMaxScales][kCoefs];
    parallelFor(3, threadCount(threads), [&](int c) { predictLevels(p.sigma[0][c], p.scales, pred[c], factor); });
    // The prediction covers what the downscale keeps of the fine noise; the
    // measured coarser levels cover low-frequency noise (blotches) it can't
    // know about. Take the larger of the two.
    for (int s = 0; s < q.scales; ++s)
        for (int c = 0; c < 3; ++c)
            for (int k = 0; k < kCoefs; ++k) {
                const float measured = s + shift < p.scales ? p.sigma[s + shift][c][k] : 0.0f;
                q.sigma[s][c][k] = std::max(pred[c][s][k], measured);
            }
    return q;
}

NoiseProfile analyseRegion(const Frame& frame, const RectI& regionIn, Space space, int threads, std::string* error)
{
    NoiseProfile p;
    RectI r = regionIn;
    if (r.x1 > r.x2) std::swap(r.x1, r.x2);
    if (r.y1 > r.y2) std::swap(r.y1, r.y2);
    r.x1 = std::max(r.x1, 0);
    r.y1 = std::max(r.y1, 0);
    r.x2 = std::min(r.x2, frame.w());
    r.y2 = std::min(r.y2, frame.h());
    if (r.x2 - r.x1 < 3 * kBlock || r.y2 - r.y1 < 3 * kBlock) {
        if (error) *error = "The analysis region must cover at least 24 x 24 pixels of the image.";
        return p;
    }

    // Whole-frame estimate: luma curve, and the levels the region is too
    // small for.
    NoiseProfile autoP = analyseAuto(frame, space, threads);
    if (!autoP.valid) {
        if (error) *error = "The image is too small to analyse.";
        return p;
    }
    auto pyr = buildPyramid(frame, autoP.scales);

    p = autoP;
    p.fromRegion = 1;
    p.region = r;

    int lastGood = -1;
    for (int s = 0; s < autoP.scales; ++s) {
        // Region at this level, inset by a pixel past level 0 so the pyramid
        // filter doesn't pull in what's outside it.
        const int inset = s > 0 ? 1 : 0;
        const int x1 = ((r.x1 + (1 << s) - 1) >> s) + inset, y1 = ((r.y1 + (1 << s) - 1) >> s) + inset;
        const int x2 = (r.x2 >> s) - inset, y2 = (r.y2 >> s) - inset;
        DiffSamples smp;
        const Frame& f = pyr[size_t(s)];
        const int area = std::max(0, x2 - x1) * std::max(0, y2 - y1);
        const int step = area > 250000 ? 4 : (area > 40000 ? 2 : 1);
        for (int y = y1; y + kBlock <= y2; y += step)
            for (int x = x1; x + kBlock <= x2; x += step) {
                if (x + 2 * kBlock <= x2) addPair(f, x, y, x + kBlock, y, smp);
                if (y + 2 * kBlock <= y2) addPair(f, x, y, x, y + kBlock, smp);
            }
        if (smp.count() < 24) break;
        sigmaFromSamples(smp, p.sigma[s]);
        lastGood = s;
    }
    if (lastGood < 0) {
        if (error) *error = "The analysis region is too small.";
        return NoiseProfile();
    }
    // Levels the region couldn't measure: the whole-frame estimate, scaled
    // to agree with the region at the coarsest level it did measure, and
    // capped by the prediction from the region's own finest level.
    if (lastGood + 1 < autoP.scales) {
        for (int s = lastGood + 1; s < autoP.scales; ++s)
            for (int c = 0; c < 3; ++c) {
                const float a = pixelSigmaOf(autoP.sigma[lastGood][c]);
                const float ratio = a > 0.0f ? pixelSigmaOf(p.sigma[lastGood][c]) / a : 1.0f;
                for (int k = 0; k < kCoefs; ++k) p.sigma[s][c][k] = autoP.sigma[s][c][k] * ratio;
            }
        float pred[3][kMaxScales][kCoefs];
        parallelFor(3, threadCount(threads), [&](int c) { predictLevels(p.sigma[0][c], autoP.scales, pred[c]); });
        capByPrediction(p, lastGood + 1, pred);
    }
    // The region defines the noise at its own brightness: normalise the curve
    // there.
    double sumY = 0.0;
    for (int y = r.y1; y < r.y2; ++y)
        for (int x = r.x1; x < r.x2; ++x) sumY += frame.c[0].at(x, y);
    const float meanY = static_cast<float>(sumY / (double(r.x2 - r.x1) * (r.y2 - r.y1)));
    for (int c = 0; c < 3; ++c) {
        const float at = p.lumaFactor(c, meanY);
        if (at > 0.0f)
            for (int b = 0; b < kLumaBins; ++b) p.lumaCurve[c][b] /= at;
    }
    p.valid = true;
    return p;
}

}  // namespace sd
