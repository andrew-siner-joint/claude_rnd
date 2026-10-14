// OpenCL C source for the GPU path (gpu.cpp), mirroring denoise.cpp step for
// step: motion search, the filtering passes and the final division. Kept as
// raw string pieces (MSVC limits a single literal to 16 KB).
#pragma once

namespace sd {
namespace detail {

static const char* const kKernelSource[] = {
R"CLC(
#pragma OPENCL FP_CONTRACT OFF
#define BS 8
#define NC 64
#define SUB 4
#define MAXS 7

typedef struct {
    int w, h;              // level size
    int nx, ny;            // block grid size
    int slots, ref;        // frames in the stack, reference slot
    int lumaAdaptive;
    float lumaLo, lumaHi;
    float matchVar, matchDcSigma, tolerance;
    int coarsest;          // no coarser level to predict from
    int cnx, cny, cstep;   // coarser grid
} LevelInfo;

inline int floorDiv(int a, int b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }

// NoiseProfile::lumaFactor
inline float lumaFactor(__constant const float* curve, float lo, float hi, float luma)
{
    const float span = hi - lo;
    if (!(span > 0.0f)) return curve[4];
    float p = (luma - lo) / span * 8.0f - 0.5f;
    if (p <= 0.0f) return curve[0];
    if (p >= 7.0f) return curve[7];
    const int i = (int)p;
    const float f = p - (float)i;
    return curve[i] * (1.0f - f) + curve[i + 1] * f;
}

// Catmull-Rom weights for a quarter-pixel fraction.
inline void subWeights(int f, float* w)
{
    const float t = (float)f / SUB, t2 = t * t, t3 = t2 * t;
    w[0] = 0.5f * (-t3 + 2.0f * t2 - t);
    w[1] = 0.5f * (3.0f * t3 - 5.0f * t2 + 2.0f);
    w[2] = 0.5f * (-3.0f * t3 + 4.0f * t2 + t);
    w[3] = 0.5f * (t3 - t2);
}

inline float subGain(int f)
{
    float w[4];
    subWeights(f, w);
    float g = 0.0f;
    for (int i = 0; i < 4; ++i) g += w[i] * w[i];
    return g;
}

inline int blockInside(int w, int h, int x, int y, int dx, int dy)
{
    const int ix = x + floorDiv(dx, SUB), iy = y + floorDiv(dy, SUB);
    const int fx = dx - floorDiv(dx, SUB) * SUB, fy = dy - floorDiv(dy, SUB) * SUB;
    const int mx = fx ? 1 : 0, my = fy ? 1 : 0;
    return ix - mx >= 0 && iy - my >= 0 && ix + BS + 2 * mx <= w && iy + BS + 2 * my <= h;
}

// One pixel (i, j) of the block at (x, y) displaced by (dx, dy) quarter
// pixels, computed exactly as loadBlockSub does.
inline float samplePixel(__global const float* p, int w, int x, int y, int dx, int dy, int i, int j)
{
    const int ix = x + floorDiv(dx, SUB), iy = y + floorDiv(dy, SUB);
    const int fx = dx - floorDiv(dx, SUB) * SUB, fy = dy - floorDiv(dy, SUB) * SUB;
    if (!fx && !fy) return p[(iy + j) * w + ix + i];
    float wx[4], wy[4];
    subWeights(fx, wx);
    subWeights(fy, wy);
    if (!fy) {
        __global const float* r = p + (iy + j) * w + ix + i;
        return wx[0] * r[-1] + wx[1] * r[0] + wx[2] * r[1] + wx[3] * r[2];
    }
    float hrow[4];
    for (int k = 0; k < 4; ++k) {
        __global const float* r = p + (iy + j - 1 + k) * w + ix + i;
        hrow[k] = fx ? wx[0] * r[-1] + wx[1] * r[0] + wx[2] * r[1] + wx[3] * r[2] : r[0];
    }
    return wy[0] * hrow[0] + wy[1] * hrow[1] + wy[2] * hrow[2] + wy[3] * hrow[3];
}

inline void loadSub(__global const float* p, int w, int x, int y, int dx, int dy, float* out)
{
    for (int j = 0; j < BS; ++j)
        for (int i = 0; i < BS; ++i) out[j * BS + i] = samplePixel(p, w, x, y, dx, dy, i, j);
}

inline float ssdBail(const float* a, const float* b, float bail)
{
    float s = 0.0f;
    for (int j = 0; j < BS; ++j) {
        for (int i = 0; i < BS; ++i) {
            const float d = a[j * BS + i] - b[j * BS + i];
            s += d * d;
        }
        if (s > bail) return s;
    }
    return s;
}

inline int nearestIndex(int n, int step, int pos)
{
    int i = (pos + step / 2) / step;
    return max(0, min(i, n - 1));
}
)CLC",
R"CLC(
// One work item per (block, slot). motion: int4 (dx, dy, ok, 0) per block
// and slot. planes: [slot][channel][h][w]; only luma is used here.
__kernel void motion(LevelInfo L, __global const float* planes, __global const int* xs, __global const int* ys,
                     __global const int4* coarser, __global const int* cxs, __global const int* cys,
                     __constant const float* curve, __global int4* motion)
{
    const int gid = get_global_id(0);
    const int nb = L.nx * L.ny;
    if (gid >= nb * L.slots) return;
    const int b = gid / L.slots, s = gid % L.slots;
    const int bx = b % L.nx, by = b / L.nx;
    const int x = xs[bx], y = ys[by];
    const int plane = L.w * L.h;
    if (s == L.ref) {
        motion[gid] = (int4)(0, 0, 1, 0);
        return;
    }
    __global const float* Y0 = planes + (L.ref * 3) * plane;
    __global const float* Y1 = planes + (s * 3) * plane;
    float refBlock[NC], cand[NC];
    float mean = 0.0f;
    for (int j = 0; j < BS; ++j)
        for (int i = 0; i < BS; ++i) mean += Y0[(y + j) * L.w + x + i];
    mean *= 1.0f / NC;
    for (int j = 0; j < BS; ++j)
        for (int i = 0; i < BS; ++i) refBlock[j * BS + i] = Y0[(y + j) * L.w + x + i];
    const float lf = L.lumaAdaptive ? lumaFactor(curve, L.lumaLo, L.lumaHi, mean) : 1.0f;
    const float expected = 2.0f * L.matchVar * lf * lf * NC;
    const float acceptSsd = expected * (1.0f + 0.75f * L.tolerance) + 1e-12f;
    const float acceptDc = 3.0f * 1.41421356f * L.matchDcSigma * lf * (1.0f + L.tolerance) + 1e-12f;
    float gain[SUB];
    for (int f = 0; f < SUB; ++f) gain[f] = subGain(f);

    float best = 3.0e38f;
    int bdx = 0, bdy = 0;
#define TRYVEC(DX, DY)                                                                        \
    {                                                                                         \
        const int tdx = (DX), tdy = (DY);                                                     \
        if (blockInside(L.w, L.h, x, y, tdx, tdy)) {                                          \
            loadSub(Y1, L.w, x, y, tdx, tdy, cand);                                           \
            const float pen = (1.0f - gain[tdx & (SUB - 1)] * gain[tdy & (SUB - 1)]) * expected * 0.5f; \
            const float c = ssdBail(refBlock, cand, best - pen) + pen;                         \
            if (c < best) {                                                                   \
                best = c;                                                                     \
                bdx = tdx;                                                                    \
                bdy = tdy;                                                                    \
            }                                                                                 \
        }                                                                                     \
    }
    TRYVEC(0, 0);
    if (!L.coarsest) {
        const int cbx = nearestIndex(L.cnx, L.cstep, x / 2);
        const int cby = nearestIndex(L.cny, L.cstep, y / 2);
        const int4 cm = coarser[(cby * L.cnx + cbx) * L.slots + s];
        TRYVEC(cm.x * 2, cm.y * 2);
    } else {
        for (int dy = -4; dy <= 4; ++dy)
            for (int dx = -4; dx <= 4; ++dx) TRYVEC(dx * SUB, dy * SUB);
    }
    for (int stepQ = SUB; stepQ >= 1; stepQ /= 2) {
        const int iters = stepQ == SUB ? 8 : 1;
        for (int it = 0; it < iters; ++it) {
            const int cx = bdx, cy = bdy;
            for (int dy = -1; dy <= 1; ++dy)
                for (int dx = -1; dx <= 1; ++dx)
                    if (dx || dy) TRYVEC(cx + dx * stepQ, cy + dy * stepQ);
            if (bdx == cx && bdy == cy) break;
        }
    }
#undef TRYVEC
    int ok = best <= acceptSsd;
    if (ok) {
        loadSub(Y1, L.w, x, y, bdx, bdy, cand);
        float dc = 0.0f;
        for (int i = 0; i < NC; ++i) dc += refBlock[i] - cand[i];
        ok = fabs(dc) / BS <= acceptDc;
    }
    motion[gid] = (int4)(bdx, bdy, ok, 0);
}
)CLC",
R"CLC(
// One output of dctColumns (internal.h): column i, coefficient k of C * m,
// where m is read through get(row, col).
#define DCT_COL(get, k, i, C)                                                                       \
    ((k) & 1 ? C[(k) * BS + 0] * (get(0, i) - get(7, i)) + C[(k) * BS + 1] * (get(1, i) - get(6, i)) + \
                   C[(k) * BS + 2] * (get(2, i) - get(5, i)) + C[(k) * BS + 3] * (get(3, i) - get(4, i)) \
             : C[(k) * BS + 0] * (get(0, i) + get(7, i)) + C[(k) * BS + 1] * (get(1, i) + get(6, i)) + \
                   C[(k) * BS + 2] * (get(2, i) + get(5, i)) + C[(k) * BS + 3] * (get(3, i) + get(4, i)))

// One output of idctColumns: row r, column i of C^T * m.
inline float idctCol(__local const float* m, int transposed, __constant const float* C, int r, int i)
{
    const int n = r < 4 ? r : 7 - r;
#define G(row) (transposed ? m[i * BS + (row)] : m[(row) * BS + i])
    const float even = C[0 * BS + n] * G(0) + C[2 * BS + n] * G(2) + C[4 * BS + n] * G(4) + C[6 * BS + n] * G(6);
    const float odd = C[1 * BS + n] * G(1) + C[3 * BS + n] * G(3) + C[5 * BS + n] * G(5) + C[7 * BS + n] * G(7);
#undef G
    return r < 4 ? even + odd : even - odd;
}

// 2D DCT of tile[64] in local memory, as dct8x8 does it: columns, transpose,
// columns, transpose. One value per work item (q).
inline void dctLocal(__local float* tile, __local float* tmp, __constant const float* C, int q)
{
    const int k = q / BS, i = q % BS;
#define GT(r, c) tile[(r) * BS + (c)]
    const float a = DCT_COL(GT, k, i, C);
#undef GT
    tmp[k * BS + i] = a;
    barrier(CLK_LOCAL_MEM_FENCE);
    // columns of tmp^T; the result is written transposed back into tile
#define GTT(r, c) tmp[(c) * BS + (r)]
    const float b = DCT_COL(GTT, k, i, C);
#undef GTT
    tile[i * BS + k] = b;
    barrier(CLK_LOCAL_MEM_FENCE);
}

inline void idctLocal(__local float* tile, __local float* tmp, __constant const float* C, int q)
{
    const int r = q / BS, i = q % BS;
    tmp[r * BS + i] = idctCol(tile, 0, C, r, i);
    barrier(CLK_LOCAL_MEM_FENCE);
    tile[i * BS + r] = idctCol(tmp, 1, C, r, i);
    barrier(CLK_LOCAL_MEM_FENCE);
}

// One work group of 64 per (block, channel); the blocks of one launch never
// overlap, so accumulation needs no atomics. mode: 0 hard threshold,
// 1 Wiener with the stack's own thresholded estimate, 2 Wiener with pilot.
// T: temporal DCT matrices, T[n][k][i] at ((n * MAXS) + k) * MAXS + i.
__kernel void filterPass(LevelInfo L, int mode, __global const float* planes, __global const float* pilot,
                         __global const int* xs, __global const int* ys, __global const int4* motion,
                         __global const int* order, int orderStart, __constant const float* C,
                         __constant const float* T, __constant const float* kaiser, __constant const float* sigma,
                         __constant const float* sbar, __constant const float* curve, __global float* num,
                         __global float* den)
{
    const int q = get_local_id(0);
    const int c = get_global_id(1);
    const int b = order[orderStart + get_group_id(0)];
    const int bx = b % L.nx, by = b / L.nx;
    const int x = xs[bx], y = ys[by];
    const int plane = L.w * L.h;
    const int qi = q % BS, qj = q / BS;

    __local float tiles[MAXS][NC];
    __local float tmp[NC];
    __local float red[NC];
    __local int ired[NC];

    // stack in time order
    int n = 0, r0 = 0;
    int sdx[MAXS], sdy[MAXS], sf[MAXS];
    for (int s = 0; s < L.slots; ++s) {
        const int4 m = motion[b * L.slots + s];
        if (!m.z) continue;
        if (s == L.ref) r0 = n;
        sdx[n] = m.x;
        sdy[n] = m.y;
        sf[n] = s;
        ++n;
    }

    // luma mean of the reference block, summed in row order like the CPU
    __global const float* Y0 = planes + (L.ref * 3) * plane;
    red[q] = Y0[(y + qj) * L.w + x + qi];
    barrier(CLK_LOCAL_MEM_FENCE);
    float mean = 0.0f;
    for (int i = 0; i < NC; ++i) mean += red[i];
    mean *= 1.0f / NC;
    barrier(CLK_LOCAL_MEM_FENCE);
    const float lf = L.lumaAdaptive ? lumaFactor(curve + c * 8, L.lumaLo, L.lumaHi, mean) : 1.0f;

    for (int i = 0; i < n; ++i) {
        __global const float* p = planes + (sf[i] * 3 + c) * plane;
        tiles[i][q] = samplePixel(p, L.w, x, y, sdx[i], sdy[i], qi, qj);
    }
    barrier(CLK_LOCAL_MEM_FENCE);
    for (int i = 0; i < n; ++i) dctLocal(tiles[i], tmp, C, q);
)CLC",
R"CLC(
    __constant const float* Tn = T + n * MAXS * MAXS;
    float b2[MAXS], c3[MAXS], est[MAXS];
    for (int i = 0; i < n; ++i) b2[i] = tiles[i][q];
    if (n == 1) {
        c3[0] = b2[0];
    } else {
        for (int k = 0; k < n; ++k) {
            float o = 0.0f;
            for (int i = 0; i < n; ++i) o += Tn[k * MAXS + i] * b2[i];
            c3[k] = o;
        }
    }
    const float sig = sigma[c * NC + q] * lf;
    float weight;
    if (mode == 0) {
        int nnz = 0;
        for (int k = 0; k < n; ++k) {
            const int keep = fabs(c3[k]) > 2.7f * sig;
            est[k] = keep ? c3[k] : 0.0f;
            nnz += keep;
        }
        ired[q] = nnz;
        barrier(CLK_LOCAL_MEM_FENCE);
        int total = 0;
        for (int i = 0; i < NC; ++i) total += ired[i];
        weight = 1.0f / (lf * lf * (float)max(total, 1));
    } else {
        float oracle[MAXS];
        for (int k = 0; k < n; ++k) oracle[k] = fabs(c3[k]) > 2.7f * sig ? c3[k] : 0.0f;
        if (mode == 2) {
            // pilot block's DCT into tile 0 (the stack's DCTs are in b2 now)
            barrier(CLK_LOCAL_MEM_FENCE);
            tiles[0][q] = pilot[c * plane + (y + qj) * L.w + x + qi];
            barrier(CLK_LOCAL_MEM_FENCE);
            dctLocal(tiles[0], tmp, C, q);
            const float p2 = tiles[0][q];
            if (n == 1) {
                oracle[0] = p2;
            } else {
                float slices[MAXS];
                for (int i = 0; i < n; ++i) {
                    float s = 0.0f;
                    for (int k = 0; k < n; ++k) s += Tn[k * MAXS + i] * oracle[k];
                    slices[i] = s;
                }
                slices[r0] = p2;
                for (int k = 0; k < n; ++k) {
                    float s = 0.0f;
                    for (int i = 0; i < n; ++i) s += Tn[k * MAXS + i] * slices[i];
                    oracle[k] = s;
                }
            }
        }
        float wsum = 0.0f;
        const float s2 = sig * sig;
        for (int k = 0; k < n; ++k) {
            const float o2 = oracle[k] * oracle[k];
            const float W = s2 > 0.0f ? o2 / (o2 + s2) : 1.0f;
            est[k] = W * c3[k];
            wsum += W * W * s2;
        }
        red[q] = wsum;
        barrier(CLK_LOCAL_MEM_FENCE);
        float total = 0.0f;
        for (int i = 0; i < NC; ++i) total += red[i];
        weight = 1.0f / fmax(total, 1e-12f);
        weight *= sbar[c] / NC + 1e-20f;
    }
    float ref2d;
    if (n == 1) {
        ref2d = est[0];
    } else {
        float s = 0.0f;
        for (int k = 0; k < n; ++k) s += Tn[k * MAXS + r0] * est[k];
        ref2d = s;
    }
    barrier(CLK_LOCAL_MEM_FENCE);
    tiles[0][q] = ref2d;
    barrier(CLK_LOCAL_MEM_FENCE);
    idctLocal(tiles[0], tmp, C, q);
    const float wk = weight * kaiser[q];
    const int idx = c * plane + (y + qj) * L.w + x + qi;
    num[idx] += wk * tiles[0][q];
    den[idx] += wk;
}

__kernel void resolve(int count, __global const float* num, __global const float* den, __global float* out)
{
    const int i = get_global_id(0);
    if (i >= count) return;
    const float d = den[i];
    out[i] = d > 0.0f ? num[i] / d : 0.0f;
}
)CLC",
};

}  // namespace detail
}  // namespace sd
