// Shared helpers for the core: DCT, pyramid, threads.
#pragma once

#include "denoise.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <thread>
#include <vector>

namespace sd {
namespace detail {

// Orthonormal DCT-II matrices.
struct DctTables {
    float c8[kBlock][kBlock];  // c8[k][n]
    // Temporal DCT of length n (1..2R+1), t[n][k][i].
    float t[2 * kMaxTemporalRadius + 2][2 * kMaxTemporalRadius + 1][2 * kMaxTemporalRadius + 1];
    float kaiser[kCoefs];  // 2D Kaiser window (beta 2) for aggregation
    DctTables();
};
const DctTables& tables();

// 2D DCT of an 8x8 block (row-major), in/out may not alias.
inline void dct8x8(const float* in, float* out)
{
    const auto& T = tables().c8;
    float tmp[kCoefs];
    // rows: tmp[y][k] = sum_x in[y][x] * C[k][x]
    for (int y = 0; y < kBlock; ++y) {
        const float* r = in + y * kBlock;
        for (int k = 0; k < kBlock; ++k) {
            float s = 0.0f;
            for (int x = 0; x < kBlock; ++x) s += r[x] * T[k][x];
            tmp[y * kBlock + k] = s;
        }
    }
    // columns: out[l][k] = sum_y C[l][y] * tmp[y][k]
    for (int l = 0; l < kBlock; ++l) {
        float* o = out + l * kBlock;
        for (int k = 0; k < kBlock; ++k) o[k] = 0.0f;
        for (int y = 0; y < kBlock; ++y) {
            const float c = T[l][y];
            const float* r = tmp + y * kBlock;
            for (int k = 0; k < kBlock; ++k) o[k] += c * r[k];
        }
    }
}

inline void idct8x8(const float* in, float* out)
{
    const auto& T = tables().c8;
    float tmp[kCoefs];
    // columns: tmp[y][k] = sum_l C[l][y] * in[l][k]
    for (int y = 0; y < kBlock; ++y) {
        float* o = tmp + y * kBlock;
        for (int k = 0; k < kBlock; ++k) o[k] = 0.0f;
        for (int l = 0; l < kBlock; ++l) {
            const float c = T[l][y];
            const float* r = in + l * kBlock;
            for (int k = 0; k < kBlock; ++k) o[k] += c * r[k];
        }
    }
    // rows: out[y][x] = sum_k tmp[y][k] * C[k][x]
    for (int y = 0; y < kBlock; ++y) {
        const float* r = tmp + y * kBlock;
        float* o = out + y * kBlock;
        for (int x = 0; x < kBlock; ++x) o[x] = 0.0f;
        for (int k = 0; k < kBlock; ++k) {
            const float v = r[k];
            const float* c = T[k];
            for (int x = 0; x < kBlock; ++x) o[x] += v * c[x];
        }
    }
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
