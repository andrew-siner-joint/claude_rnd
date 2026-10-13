// SpectralDenoise core: noise profiling and denoising, independent of OFX.
//
// The image is moved into a working space (optional log-like curve, then an
// orthonormal opponent transform: Y, U, V) and split into a Gaussian pyramid.
// At every pyramid level, overlapping 8x8 blocks are denoised in the DCT
// domain against a measured noise profile: the noise standard deviation of
// each of the 64 DCT coefficients, per channel and per level. With a temporal
// radius, each block is matched in the neighbouring frames and the stack is
// filtered with a 3D (DCT x temporal DCT) transform. Levels are recombined
// Laplacian-style so each level contributes the frequencies it owns.
#pragma once

#include <atomic>
#include <cstddef>
#include <functional>
#include <string>
#include <vector>

namespace sd {

constexpr int kBlock = 8;
constexpr int kCoefs = kBlock * kBlock;
constexpr int kMaxScales = 4;
constexpr int kLumaBins = 8;
constexpr int kMaxTemporalRadius = 3;

// One float plane.
struct Plane {
    int w = 0, h = 0;
    std::vector<float> d;
    void resize(int width, int height, float v = 0.0f)
    {
        w = width;
        h = height;
        d.assign(static_cast<size_t>(width) * static_cast<size_t>(height), v);
    }
    float* row(int y) { return d.data() + static_cast<size_t>(y) * w; }
    const float* row(int y) const { return d.data() + static_cast<size_t>(y) * w; }
    float& at(int x, int y) { return d[static_cast<size_t>(y) * w + x]; }
    float at(int x, int y) const { return d[static_cast<size_t>(y) * w + x]; }
};

// Three planes in the working space: 0 = luma (Y), 1 and 2 = chroma (U, V).
struct Frame {
    Plane c[3];
    int w() const { return c[0].w; }
    int h() const { return c[0].h; }
};

// How the input values are encoded.
enum class Space : int {
    SceneLinear = 0,  // a log-like curve is applied first (noise in linear
                      // light grows with brightness)
    Video = 1,        // log or display-referred footage, processed as is
};

// A strided view of interleaved float pixels (RGB or RGBA).
struct ImageView {
    const float* data = nullptr;
    int w = 0, h = 0;
    std::ptrdiff_t rowStride = 0;  // in floats
    int comps = 4;                 // 3 or 4; RGB taken from the first three
};

// Rectangle in pixel coordinates of the frame, x1/y1 exclusive.
struct RectI {
    int x1 = 0, y1 = 0, x2 = 0, y2 = 0;
};

struct NoiseProfile {
    bool valid = false;
    Space space = Space::SceneLinear;  // working space it was measured in
    int scales = 0;                    // levels measured
    // Noise std of each DCT coefficient (row-major v*8+u), per level and
    // channel, in working-space units.
    float sigma[kMaxScales][3][kCoefs] = {};
    // Relative noise multiplier per luma bin and channel (1 = profile value).
    float lumaCurve[3][kLumaBins] = {};
    float lumaLo = 0.0f, lumaHi = 1.0f;  // working-space luma range of the bins
    // Where it came from, for the panel.
    int fromRegion = 0;
    RectI region;
    double frame = 0.0;
    int imageW = 0, imageH = 0;

    // Std of the noise per pixel (all frequencies) at a level.
    float pixelSigma(int scale, int ch) const;
    // Multiplier from the luma curve at a working-space luma value.
    float lumaFactor(int ch, float luma) const;

    std::string serialize() const;
    bool parse(const std::string& text);
    std::string summary() const;  // one human-readable line
};

struct Settings {
    Space space = Space::SceneLinear;
    float level = 1.0f;                    // global noise level multiplier
    float lumaLevel = 1.0f, chromaLevel = 1.0f;
    float scaleLevel[kMaxScales] = {1.0f, 1.0f, 1.0f, 1.0f};
    float lumaAmount = 1.0f, chromaAmount = 1.0f;  // 0 = keep all noise
    float sharpen = 0.0f;
    int temporalRadius = 0;
    float motionTolerance = 1.0f;
    int quality = 1;  // 0 fast, 1 normal, 2 best
    bool lumaAdaptive = true;
    int threads = 0;  // 0 = hardware concurrency
};

using AbortFn = std::function<bool()>;

// Colour conversion. toWorking reads RGB from the view.
void toWorking(const ImageView& src, Space space, Frame& out);
// Writes RGB into an interleaved buffer with the given components (3 or 4);
// alpha, if any, is left untouched.
void fromWorking(const Frame& src, Space space, float* dst, std::ptrdiff_t rowStride, int comps);
float toWorkingValue(float v, Space space);
float fromWorkingValue(float v, Space space);

// Number of pyramid levels used for an image of this size.
int scalesForSize(int w, int h);

// Measure the noise inside a region of a frame that contains no detail.
// The luma curve is measured over the whole frame. Returns an invalid profile
// (with a reason in *error) if the region is unusable.
NoiseProfile analyseRegion(const Frame& frame, const RectI& region, Space space, int threads,
                           std::string* error = nullptr);

// Measure the noise from the flattest parts of the whole frame.
NoiseProfile analyseAuto(const Frame& frame, Space space, int threads);

// The profile as seen at a reduced render scale (1 / 2^shift), e.g. a proxy
// or a downscaled viewer: the host's downscale averages part of the noise
// away, and what it keeps is predicted from the full-resolution profile.
NoiseProfile profileAtScale(const NoiseProfile& profile, int shift, int threads = 0);

// Denoise frames[ref] using its neighbours (frames.size() must be
// 2 * temporalRadius + 1 or fewer; missing neighbours may be nullptr).
// For a reduced render scale pass profileAtScale(). Returns false if aborted.
bool denoise(const std::vector<const Frame*>& frames, int ref, const NoiseProfile& profile,
             const Settings& settings, Frame& out, const AbortFn& abort = AbortFn());

}  // namespace sd
