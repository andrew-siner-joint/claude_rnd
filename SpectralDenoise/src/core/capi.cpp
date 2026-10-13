// C entry points to the core, for the Python tests (ctypes). Not used by the
// OFX plugin.
#include "denoise.h"

#include <cstring>

namespace {
sd::ImageView view(const float* rgb, int w, int h, int comps)
{
    sd::ImageView v;
    v.data = rgb;
    v.w = w;
    v.h = h;
    v.comps = comps;
    v.rowStride = std::ptrdiff_t(w) * comps;
    return v;
}

int copyOut(const std::string& s, char* out, int outLen)
{
    if (!out || outLen <= 0) return static_cast<int>(s.size()) + 1;
    const size_t n = std::min(s.size(), size_t(outLen - 1));
    std::memcpy(out, s.data(), n);
    out[n] = 0;
    return static_cast<int>(s.size()) + 1;
}
}  // namespace

extern "C" {

struct SdSettingsC {
    int space;
    float level, lumaLevel, chromaLevel;
    float scaleLevel[4];
    float lumaAmount, chromaAmount, sharpen;
    int temporalRadius;
    float motionTolerance;
    int quality;
    int lumaAdaptive;
    int threads;
};

// mode 0 = region (rect = x1 y1 x2 y2), 1 = auto. Returns the length needed
// (including NUL) or 0 on failure, with the error in out.
int sd_analyse(const float* rgb, int w, int h, int comps, int space, int mode, const int* rect, char* out, int outLen)
{
    sd::Frame f;
    sd::toWorking(view(rgb, w, h, comps), static_cast<sd::Space>(space), f);
    sd::NoiseProfile p;
    std::string err;
    if (mode == 0) {
        sd::RectI r;
        r.x1 = rect[0];
        r.y1 = rect[1];
        r.x2 = rect[2];
        r.y2 = rect[3];
        p = sd::analyseRegion(f, r, static_cast<sd::Space>(space), 0, &err);
    } else {
        p = sd::analyseAuto(f, static_cast<sd::Space>(space), 0);
    }
    if (!p.valid) {
        copyOut(err, out, outLen);
        return 0;
    }
    return copyOut(p.serialize(), out, outLen);
}

int sd_summary(const char* profile, char* out, int outLen)
{
    sd::NoiseProfile p;
    p.parse(profile ? profile : "");
    return copyOut(p.summary(), out, outLen);
}

// frames: nframes pointers (nullptr allowed except ref) of w*h*comps floats.
// out: w*h*comps floats; alpha (if comps == 4) is copied from the reference.
int sd_denoise(const float** frames, int nframes, int ref, int w, int h, int comps, const char* profile,
               const SdSettingsC* sc, int scaleShift, float* out)
{
    sd::NoiseProfile p;
    if (!p.parse(profile ? profile : "")) return 0;
    sd::Settings s;
    s.space = static_cast<sd::Space>(sc->space);
    s.level = sc->level;
    s.lumaLevel = sc->lumaLevel;
    s.chromaLevel = sc->chromaLevel;
    for (int i = 0; i < 4; ++i) s.scaleLevel[i] = sc->scaleLevel[i];
    s.lumaAmount = sc->lumaAmount;
    s.chromaAmount = sc->chromaAmount;
    s.sharpen = sc->sharpen;
    s.temporalRadius = sc->temporalRadius;
    s.motionTolerance = sc->motionTolerance;
    s.quality = sc->quality;
    s.lumaAdaptive = sc->lumaAdaptive != 0;
    s.threads = sc->threads;

    std::vector<sd::Frame> wf(static_cast<size_t>(nframes));
    std::vector<const sd::Frame*> ptrs(static_cast<size_t>(nframes), nullptr);
    for (int i = 0; i < nframes; ++i) {
        if (!frames[i]) continue;
        sd::toWorking(view(frames[i], w, h, comps), s.space, wf[size_t(i)]);
        ptrs[size_t(i)] = &wf[size_t(i)];
    }
    sd::Frame result;
    if (scaleShift > 0) p = sd::profileAtScale(p, scaleShift, s.threads);
    if (!sd::denoise(ptrs, ref, p, s, result)) return 0;
    std::memcpy(out, frames[ref], sizeof(float) * size_t(w) * size_t(h) * size_t(comps));
    sd::fromWorking(result, s.space, out, std::ptrdiff_t(w) * comps, comps);
    return 1;
}
}
