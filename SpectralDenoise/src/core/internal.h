// Shared helpers for the core: DCT, pyramid, threads.
#pragma once

#include "denoise.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <thread>
#include <vector>

namespace sd {
namespace detail {

constexpr float kHardThreshold = 2.7f;
constexpr int kMaxSlots = 2 * kMaxTemporalRadius + 1;
// Motion vectors are in quarter pixels.
constexpr int kSub = 4;

struct Motion {
    int16_t dx = 0, dy = 0;
    bool ok = false;
};

// Motion of every block on a level's grid, for every frame slot.
struct MotionField {
    std::vector<int> xs, ys;
    int step = 1;
    int slots = 0;  // frames.size()
    std::vector<Motion> m;
    Motion& at(size_t bx, size_t by, int slot) { return m[(by * xs.size() + bx) * size_t(slots) + size_t(slot)]; }
    const Motion& at(size_t bx, size_t by, int slot) const
    {
        return m[(by * xs.size() + bx) * size_t(slots) + size_t(slot)];
    }
};

struct LevelParams {
    float sigma[3][kCoefs];  // noise std used for filtering (user multipliers in)
    float matchVar;          // luma pixel noise variance, for match rejection
    float matchDcSigma;      // luma DC coefficient noise std, for match rejection
    const NoiseProfile* prof;
    bool lumaAdaptive;
    int step;
    int quality;
    float tolerance;
};

// Orthonormal DCT-II matrices.
struct DctTables {
    float c8[kBlock][kBlock];  // c8[k][n]
    // Temporal DCT of length n (1..2R+1), t[n][k][i].
    float t[2 * kMaxTemporalRadius + 2][2 * kMaxTemporalRadius + 1][2 * kMaxTemporalRadius + 1];
    float kaiser[kCoefs];  // 2D Kaiser window (beta 2) for aggregation
    DctTables();
};
const DctTables& tables();

// 1D 8-point DCT of the 8 columns of a block at once (out = C * in), using
// the symmetry c[k][7-n] = (-1)^k c[k][n]: even outputs from sums, odd ones
// from differences, half the multiplies of a plain matrix product. Rows are
// processed as 8-wide vectors, which compilers vectorise.
inline void dctColumns(const float* in, float* out)
{
    const auto& T = tables().c8;
    float sum[4][kBlock], dif[4][kBlock];
    for (int n = 0; n < 4; ++n)
        for (int i = 0; i < kBlock; ++i) {
            sum[n][i] = in[n * kBlock + i] + in[(7 - n) * kBlock + i];
            dif[n][i] = in[n * kBlock + i] - in[(7 - n) * kBlock + i];
        }
    for (int k = 0; k < kBlock; ++k) {
        const float(*src)[kBlock] = (k & 1) ? dif : sum;
        float* o = out + k * kBlock;
        for (int i = 0; i < kBlock; ++i)
            o[i] = T[k][0] * src[0][i] + T[k][1] * src[1][i] + T[k][2] * src[2][i] + T[k][3] * src[3][i];
    }
}

// Inverse of dctColumns (out = C^T * in).
inline void idctColumns(const float* in, float* out)
{
    const auto& T = tables().c8;
    for (int n = 0; n < 4; ++n) {
        float even[kBlock], odd[kBlock];
        for (int i = 0; i < kBlock; ++i) {
            even[i] = T[0][n] * in[0 * kBlock + i] + T[2][n] * in[2 * kBlock + i] + T[4][n] * in[4 * kBlock + i] +
                      T[6][n] * in[6 * kBlock + i];
            odd[i] = T[1][n] * in[1 * kBlock + i] + T[3][n] * in[3 * kBlock + i] + T[5][n] * in[5 * kBlock + i] +
                     T[7][n] * in[7 * kBlock + i];
        }
        for (int i = 0; i < kBlock; ++i) {
            out[n * kBlock + i] = even[i] + odd[i];
            out[(7 - n) * kBlock + i] = even[i] - odd[i];
        }
    }
}

inline void transpose8(const float* in, float* out)
{
    for (int y = 0; y < kBlock; ++y)
        for (int x = 0; x < kBlock; ++x) out[x * kBlock + y] = in[y * kBlock + x];
}

// 2D DCT of an 8x8 block (row-major): out = C * in * C^T. in/out may not alias.
inline void dct8x8(const float* in, float* out)
{
    float a[kCoefs], b[kCoefs];
    dctColumns(in, a);  // C * in
    transpose8(a, b);   // (C * in)^T = in^T * C^T
    dctColumns(b, a);   // C * in^T * C^T = (C * in * C^T)^T
    transpose8(a, out);
}

inline void idct8x8(const float* in, float* out)
{
    float a[kCoefs], b[kCoefs];
    idctColumns(in, a);
    transpose8(a, b);
    idctColumns(b, a);
    transpose8(a, out);
}

inline int mirror(int i, int n)
{
    if (n == 1) return 0;
    while (i < 0 || i >= n) {
        if (i < 0) i = -i;
        if (i >= n) i = 2 * n - 2 - i;
    }
    return i;
}

// Gaussian pyramid step: [1 4 6 4 1]/16 blur, keep every second sample.
void downsample(const Plane& src, Plane& dst);
// Inverse of downsample (Laplacian pyramid "expand") to size w x h.
void upsample(const Plane& src, int w, int h, Plane& dst);
// Separable [1 4 6 4 1]/16 blur at full resolution.
void blur5(const Plane& src, Plane& dst);

int threadCount(int requested);

// Run fn(i) for i in [0, n) on worker threads.
template <class Fn>
void parallelFor(int n, int threads, Fn&& fn)
{
    threads = std::max(1, std::min(threads, n));
    if (threads <= 1) {
        for (int i = 0; i < n; ++i) fn(i);
        return;
    }
    std::atomic<int> next{0};
    auto worker = [&]() {
        for (;;) {
            const int i = next.fetch_add(1);
            if (i >= n) break;
            fn(i);
        }
    };
    std::vector<std::thread> pool;
    pool.reserve(static_cast<size_t>(threads - 1));
    for (int t = 0; t < threads - 1; ++t) pool.emplace_back(worker);
    worker();
    for (auto& th : pool) th.join();
}

// Block positions along one axis: 0, step, 2 step, ..., always ending at n - 8.
std::vector<int> blockGrid(int n, int step);

// Opponent transform (orthonormal).
inline void rgbToYuv(float r, float g, float b, float& y, float& u, float& v)
{
    constexpr float k3 = 0.57735026919f;  // 1/sqrt(3)
    constexpr float k2 = 0.70710678119f;  // 1/sqrt(2)
    constexpr float k6 = 0.40824829046f;  // 1/sqrt(6)
    y = (r + g + b) * k3;
    u = (r - b) * k2;
    v = (r - 2.0f * g + b) * k6;
}
inline void yuvToRgb(float y, float u, float v, float& r, float& g, float& b)
{
    constexpr float k3 = 0.57735026919f;
    constexpr float k2 = 0.70710678119f;
    constexpr float k6 = 0.40824829046f;
    r = y * k3 + u * k2 + v * k6;
    g = y * k3 - 2.0f * v * k6;
    b = y * k3 - u * k2 + v * k6;
}

}  // namespace detail
}  // namespace sd
