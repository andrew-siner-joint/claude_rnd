// SpectralDenoise: OFX image effect around the core in src/core.
//
// Parameters, clips and the region overlay live here; all image maths is in
// the core. The noise profile is stored in a hidden string parameter, so it
// is saved with the script and renders the same everywhere.
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#endif

#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#ifdef __APPLE__
#include <OpenGL/gl.h>
#else
#include <GL/gl.h>
#endif
#ifdef __linux__
#include <dlfcn.h>
#endif

#include "ofxsImageEffect.h"
#include "ofxsInteract.h"

#include "../core/denoise.h"

#define kPluginName "SpectralDenoise"
#define kPluginGrouping "JointPost"
#define kPluginIdentifier "tv.jointpost.SpectralDenoise"
#define kPluginVersionMajor 1
#define kPluginVersionMinor 0
#define kPluginDescription                                                                                            \
    "Profile-based noise reduction.\n\n"                                                                              \
    "Draw the analysis box over an area with no detail (sky, a wall, a grey card), press Analyse Noise, and the "     \
    "noise in that area is measured: its strength at every spatial frequency, for luma and chroma, at four scales, " \
    "plus how it changes with brightness. Only that noise is then removed, so detail that doesn't look like it is "  \
    "kept. Temporal Radius adds neighbouring frames, matched for motion, for much stronger reduction."

#define kClipMask "Mask"

// Profile
#define kParamProfileMode "profileMode"
#define kParamRegionBL "analysisBottomLeft"
#define kParamRegionTR "analysisTopRight"
#define kParamAnalyse "analyseNoise"
#define kParamStatus "profileStatus"
#define kParamProfileData "profileData"
#define kParamColourspace "inputColourspace"
#define kParamLumaAdaptive "brightnessAdaptive"
// Reduction
#define kParamLevel "noiseLevel"
#define kParamLumaLevel "lumaLevel"
#define kParamChromaLevel "chromaLevel"
#define kParamLumaAmount "lumaReduction"
#define kParamChromaAmount "chromaReduction"
#define kParamFine "fineLevel"
#define kParamMedium "mediumLevel"
#define kParamCoarse "coarseLevel"
#define kParamVeryCoarse "veryCoarseLevel"
#define kParamSharpen "sharpen"
// Temporal
#define kParamTemporalRadius "temporalRadius"
#define kParamMotionTolerance "motionTolerance"
// Output
#define kParamQuality "quality"
#define kParamOutput "outputMode"
#define kParamMix "mix"
#define kParamMaskInvert "maskInvert"

enum ProfileMode { eProfileStored = 0, eProfileRegionLive = 1, eProfileAuto = 2 };
enum OutputMode { eOutputDenoised = 0, eOutputNoise = 1 };

namespace {

double clamp01(double v) { return v < 0.0 ? 0.0 : (v > 1.0 ? 1.0 : v); }

// Copy RGB(A) float pixels of an OFX image inside `win` into a tight buffer
// (comps floats per pixel, rows bottom to top as in OFX). Pixels outside the
// image are zero.
void copyImage(const OFX::Image& img, const OfxRectI& win, int comps, std::vector<float>& out)
{
    const int w = win.x2 - win.x1, h = win.y2 - win.y1;
    out.assign(size_t(w) * size_t(h) * size_t(comps), 0.0f);
    const OfxRectI& b = img.getBounds();
    const int ic = img.getPixelComponentCount();
    for (int y = std::max(win.y1, b.y1); y < std::min(win.y2, b.y2); ++y) {
        const int x0 = std::max(win.x1, b.x1), x1 = std::min(win.x2, b.x2);
        if (x0 >= x1) continue;
        const float* s = static_cast<const float*>(img.getPixelAddress(x0, y));
        if (!s) continue;
        float* d = out.data() + (size_t(y - win.y1) * size_t(w) + size_t(x0 - win.x1)) * size_t(comps);
        for (int x = x0; x < x1; ++x) {
            if (ic >= 3) {
                d[0] = s[0];
                d[1] = s[1];
                d[2] = s[2];
                if (comps == 4) d[3] = ic == 4 ? s[3] : 1.0f;
            } else {  // single channel: grey
                d[0] = d[1] = d[2] = s[0];
                if (comps == 4) d[3] = s[0];
            }
            s += ic;
            d += comps;
        }
    }
}

bool imageCovers(const OFX::Image& img, const OfxRectI& win)
{
    const OfxRectI& b = img.getBounds();
    return b.x1 <= win.x1 && b.y1 <= win.y1 && b.x2 >= win.x2 && b.y2 >= win.y2;
}

}  // namespace

class SpectralDenoisePlugin : public OFX::ImageEffect {
public:
    explicit SpectralDenoisePlugin(OfxImageEffectHandle handle);

    void render(const OFX::RenderArguments& args) override;
    bool isIdentity(const OFX::IsIdentityArguments& args, OFX::Clip*& identityClip, double& identityTime) override;
    void getFramesNeeded(const OFX::FramesNeededArguments& args, OFX::FramesNeededSetter& frames) override;
    void getRegionsOfInterest(const OFX::RegionsOfInterestArguments& args, OFX::RegionOfInterestSetter& rois) override;
    void changedParam(const OFX::InstanceChangedArgs& args, const std::string& paramName) override;

private:
    sd::Settings settingsAt(double time) const;
    // Region parameters in pixel coordinates of an image window at a scale.
    sd::RectI regionPixels(double time, const OfxPointD& renderScale, const OfxRectI& win) const;
    // The analysed profile, converted for a render scale of 1 / 2^shift.
    bool storedProfile(sd::NoiseProfile& out, int shift = 0);
    void analyse(double time, const OfxPointD& renderScale);
    void updateStatus();
    int temporalRadiusAt(double time) const;

    OFX::Clip* dst_ = nullptr;
    OFX::Clip* src_ = nullptr;
    OFX::Clip* mask_ = nullptr;

    OFX::ChoiceParam* profileMode_;
    OFX::Double2DParam* regionBL_;
    OFX::Double2DParam* regionTR_;
    OFX::StringParam* status_;
    OFX::StringParam* profileData_;
    OFX::ChoiceParam* colourspace_;
    OFX::BooleanParam* lumaAdaptive_;
    OFX::DoubleParam* level_;
    OFX::DoubleParam* lumaLevel_;
    OFX::DoubleParam* chromaLevel_;
    OFX::DoubleParam* lumaAmount_;
    OFX::DoubleParam* chromaAmount_;
    OFX::DoubleParam* scaleLevel_[4];
    OFX::DoubleParam* sharpen_;
    OFX::IntParam* temporalRadius_;
    OFX::DoubleParam* motionTolerance_;
    OFX::ChoiceParam* quality_;
    OFX::ChoiceParam* output_;
    OFX::DoubleParam* mix_;
    OFX::BooleanParam* maskInvert_ = nullptr;

    std::mutex cacheMutex_;
    std::string cachedText_;
    sd::NoiseProfile cachedProfile_[4];  // per render-scale shift
    bool cachedShift_[4] = {false, false, false, false};
};

SpectralDenoisePlugin::SpectralDenoisePlugin(OfxImageEffectHandle handle) : ImageEffect(handle)
{
    dst_ = fetchClip(kOfxImageEffectOutputClipName);
    src_ = fetchClip(kOfxImageEffectSimpleSourceClipName);
    if (getContext() == OFX::eContextGeneral || getContext() == OFX::eContextPaint) {
        mask_ = fetchClip(kClipMask);
        maskInvert_ = fetchBooleanParam(kParamMaskInvert);
    }
    profileMode_ = fetchChoiceParam(kParamProfileMode);
    regionBL_ = fetchDouble2DParam(kParamRegionBL);
    regionTR_ = fetchDouble2DParam(kParamRegionTR);
    status_ = fetchStringParam(kParamStatus);
    profileData_ = fetchStringParam(kParamProfileData);
    colourspace_ = fetchChoiceParam(kParamColourspace);
    lumaAdaptive_ = fetchBooleanParam(kParamLumaAdaptive);
    level_ = fetchDoubleParam(kParamLevel);
    lumaLevel_ = fetchDoubleParam(kParamLumaLevel);
    chromaLevel_ = fetchDoubleParam(kParamChromaLevel);
    lumaAmount_ = fetchDoubleParam(kParamLumaAmount);
    chromaAmount_ = fetchDoubleParam(kParamChromaAmount);
    scaleLevel_[0] = fetchDoubleParam(kParamFine);
    scaleLevel_[1] = fetchDoubleParam(kParamMedium);
    scaleLevel_[2] = fetchDoubleParam(kParamCoarse);
    scaleLevel_[3] = fetchDoubleParam(kParamVeryCoarse);
    sharpen_ = fetchDoubleParam(kParamSharpen);
    temporalRadius_ = fetchIntParam(kParamTemporalRadius);
    motionTolerance_ = fetchDoubleParam(kParamMotionTolerance);
    quality_ = fetchChoiceParam(kParamQuality);
    output_ = fetchChoiceParam(kParamOutput);
    mix_ = fetchDoubleParam(kParamMix);
}

int SpectralDenoisePlugin::temporalRadiusAt(double time) const
{
    int r = 0;
    temporalRadius_->getValueAtTime(time, r);
    return std::max(0, std::min(r, sd::kMaxTemporalRadius));
}

sd::Settings SpectralDenoisePlugin::settingsAt(double t) const
{
    sd::Settings s;
    int cs = 0, q = 1;
    colourspace_->getValueAtTime(t, cs);
    s.space = cs == 1 ? sd::Space::Video : sd::Space::SceneLinear;
    s.level = float(level_->getValueAtTime(t));
    s.lumaLevel = float(lumaLevel_->getValueAtTime(t));
    s.chromaLevel = float(chromaLevel_->getValueAtTime(t));
    s.lumaAmount = float(clamp01(lumaAmount_->getValueAtTime(t)));
    s.chromaAmount = float(clamp01(chromaAmount_->getValueAtTime(t)));
    for (int i = 0; i < 4; ++i) s.scaleLevel[i] = float(std::max(0.0, scaleLevel_[i]->getValueAtTime(t)));
    s.sharpen = float(sharpen_->getValueAtTime(t));
    s.temporalRadius = temporalRadiusAt(t);
    s.motionTolerance = float(std::max(0.0, motionTolerance_->getValueAtTime(t)));
    quality_->getValueAtTime(t, q);
    s.quality = q;
    bool la = true;
    lumaAdaptive_->getValueAtTime(t, la);
    s.lumaAdaptive = la;
    return s;
}

sd::RectI SpectralDenoisePlugin::regionPixels(double time, const OfxPointD& rs, const OfxRectI& win) const
{
    double x1, y1, x2, y2;
    regionBL_->getValueAtTime(time, x1, y1);
    regionTR_->getValueAtTime(time, x2, y2);
    const double par = src_->getPixelAspectRatio() > 0.0 ? src_->getPixelAspectRatio() : 1.0;
    sd::RectI r;
    r.x1 = int(std::floor(std::min(x1, x2) * rs.x / par)) - win.x1;
    r.x2 = int(std::ceil(std::max(x1, x2) * rs.x / par)) - win.x1;
    r.y1 = int(std::floor(std::min(y1, y2) * rs.y)) - win.y1;
    r.y2 = int(std::ceil(std::max(y1, y2) * rs.y)) - win.y1;
    return r;
}

bool SpectralDenoisePlugin::storedProfile(sd::NoiseProfile& out, int shift)
{
    shift = std::max(0, std::min(shift, 3));
    std::string text;
    profileData_->getValue(text);
    std::lock_guard<std::mutex> lock(cacheMutex_);
    if (text != cachedText_) {
        cachedText_ = text;
        cachedProfile_[0].parse(text);
        for (int i = 1; i < 4; ++i) cachedShift_[i] = false;
    }
    if (shift > 0 && !cachedShift_[shift]) {
        cachedProfile_[shift] = sd::profileAtScale(cachedProfile_[0], shift);
        cachedShift_[shift] = true;
    }
    out = cachedProfile_[shift];
    return out.valid;
}

void SpectralDenoisePlugin::updateStatus()
{
    int mode = 0;
    profileMode_->getValue(mode);
    std::string s;
    if (mode == eProfileAuto) {
        s = "Auto: the noise is measured on every frame.";
    } else if (mode == eProfileRegionLive) {
        s = "Region: the box is measured on every frame.";
    } else {
        sd::NoiseProfile p;
        if (!storedProfile(p)) {
            s = "No profile yet (using Auto). Put the box on a flat area and press Analyse Noise.";
        } else {
            int cs = 0;
            colourspace_->getValue(cs);
            s = p.summary();
            if ((cs == 1) != (p.space == sd::Space::Video))
                s += "  [Measured with the other Input Colourspace: using Auto until you re-analyse.]";
        }
    }
    status_->setValue(s);
}

void SpectralDenoisePlugin::analyse(double time, const OfxPointD& renderScale)
{
    if (!src_ || !src_->isConnected()) {
        sendMessage(OFX::Message::eMessageError, "", "Connect a source before analysing.");
        return;
    }
    const OfxRangeD range = src_->getFrameRange();
    if (time < range.min || time > range.max) {
        sendMessage(OFX::Message::eMessageError, "", "There is no source frame at this time: move to a frame of the clip.");
        return;
    }
    std::unique_ptr<OFX::Image> img;
    try {
        img.reset(src_->fetchImage(time));
    } catch (...) {
        img.reset();
    }
    if (!img) {
        sendMessage(OFX::Message::eMessageError, "",
                    "Could not read the source image here. Set Profile to 'Region (every frame)' instead: it "
                    "measures the box while rendering.");
        return;
    }
    if (img->getPixelDepth() != OFX::eBitDepthFloat) {
        sendMessage(OFX::Message::eMessageError, "", "Unsupported bit depth.");
        return;
    }
    const OfxRectI win = img->getBounds();
    const int comps = 4;
    std::vector<float> buf;
    copyImage(*img, win, comps, buf);
    int cs = 0;
    colourspace_->getValueAtTime(time, cs);
    const sd::Space space = cs == 1 ? sd::Space::Video : sd::Space::SceneLinear;
    sd::ImageView v;
    v.data = buf.data();
    v.w = win.x2 - win.x1;
    v.h = win.y2 - win.y1;
    v.comps = comps;
    v.rowStride = std::ptrdiff_t(v.w) * comps;
    sd::Frame f;
    sd::toWorking(v, space, f);
    // Profiles are always stored at full resolution: undo the render scale.
    std::string err;
    sd::NoiseProfile p;
    const sd::RectI rect = regionPixels(time, renderScale, win);
    if (renderScale.x < 0.99 || renderScale.y < 0.99) {
        sendMessage(OFX::Message::eMessageWarning, "",
                    "The viewer is at a reduced resolution (proxy or downscale). Analyse at full resolution for an "
                    "accurate profile.");
    }
    p = sd::analyseRegion(f, rect, space, 0, &err);
    if (!p.valid) {
        sendMessage(OFX::Message::eMessageError, "", err.empty() ? "Analysis failed." : err);
        return;
    }
    p.frame = time;
    beginEditBlock("Analyse Noise");
    profileData_->setValue(p.serialize());
    int mode = 0;
    profileMode_->getValue(mode);
    if (mode != eProfileStored) profileMode_->setValue(int(eProfileStored));
    updateStatus();
    endEditBlock();
}

void SpectralDenoisePlugin::changedParam(const OFX::InstanceChangedArgs& args, const std::string& name)
{
    if (name == kParamAnalyse) {
        analyse(args.time, args.renderScale);
    } else if (name == kParamProfileMode || name == kParamColourspace || name == kParamProfileData) {
        updateStatus();
    }
}

bool SpectralDenoisePlugin::isIdentity(const OFX::IsIdentityArguments& args, OFX::Clip*& identityClip,
                                       double& identityTime)
{
    int out = 0;
    output_->getValueAtTime(args.time, out);
    if (out == eOutputNoise) return false;
    const double mix = mix_->getValueAtTime(args.time);
    const double la = lumaAmount_->getValueAtTime(args.time), ca = chromaAmount_->getValueAtTime(args.time);
    const double sh = sharpen_->getValueAtTime(args.time);
    if (mix <= 0.0 || (la <= 0.0 && ca <= 0.0 && sh == 0.0)) {
        identityClip = src_;
        identityTime = args.time;
        return true;
    }
    return false;
}

void SpectralDenoisePlugin::getFramesNeeded(const OFX::FramesNeededArguments& args, OFX::FramesNeededSetter& frames)
{
    const int r = temporalRadiusAt(args.time);
    OfxRangeD range;
    range.min = args.time - r;
    range.max = args.time + r;
    frames.setFramesNeeded(*src_, range);
}

void SpectralDenoisePlugin::getRegionsOfInterest(const OFX::RegionsOfInterestArguments& args,
                                                 OFX::RegionOfInterestSetter& rois)
{
    // Blocks, motion search and the pyramid look far around each pixel and
    // the auto profile reads the whole frame: ask for all of the source.
    rois.setRegionOfInterest(*src_, src_->getRegionOfDefinition(args.time));
    if (mask_ && mask_->isConnected()) rois.setRegionOfInterest(*mask_, args.regionOfInterest);
}

void SpectralDenoisePlugin::render(const OFX::RenderArguments& args)
{
    std::unique_ptr<OFX::Image> dst(dst_->fetchImage(args.time));
    if (!dst) OFX::throwSuiteStatusException(kOfxStatFailed);
    if (dst->getPixelDepth() != OFX::eBitDepthFloat) OFX::throwSuiteStatusException(kOfxStatErrImageFormat);
    const int dstComps = dst->getPixelComponentCount();
    const OfxRectI win = args.renderWindow;
    const int w = win.x2 - win.x1, h = win.y2 - win.y1;
    if (w <= 0 || h <= 0) return;

    std::unique_ptr<OFX::Image> src(src_ && src_->isConnected() ? src_->fetchImage(args.time) : nullptr);
    if (src && src->getPixelDepth() != OFX::eBitDepthFloat) OFX::throwSuiteStatusException(kOfxStatErrImageFormat);

    auto writeOut = [&](const std::vector<float>& rgba) {
        for (int y = 0; y < h; ++y) {
            float* d = static_cast<float*>(dst->getPixelAddress(win.x1, win.y1 + y));
            if (!d) continue;
            const float* s = rgba.data() + size_t(y) * size_t(w) * 4;
            for (int x = 0; x < w; ++x) {
                if (dstComps == 1) {
                    d[0] = s[3];
                } else {
                    d[0] = s[0];
                    d[1] = s[1];
                    d[2] = s[2];
                    if (dstComps == 4) d[3] = s[3];
                }
                d += dstComps;
                s += 4;
            }
        }
    };

    std::vector<float> in;
    if (!src) {
        in.assign(size_t(w) * size_t(h) * 4, 0.0f);
        writeOut(in);
        return;
    }
    copyImage(*src, win, 4, in);

    const sd::Settings st = settingsAt(args.time);

    // Neighbouring frames, inside the clip's range and covering the window.
    const int radius = st.temporalRadius;
    std::vector<std::unique_ptr<OFX::Image>> nbImgs;
    std::vector<std::vector<float>> nbBufs(size_t(2 * radius + 1));
    std::vector<sd::Frame> frames(size_t(2 * radius + 1));
    std::vector<const sd::Frame*> framePtrs(size_t(2 * radius + 1), nullptr);
    sd::ImageView view;
    view.w = w;
    view.h = h;
    view.comps = 4;
    view.rowStride = std::ptrdiff_t(w) * 4;
    view.data = in.data();
    sd::toWorking(view, st.space, frames[size_t(radius)]);
    framePtrs[size_t(radius)] = &frames[size_t(radius)];
    if (radius > 0) {
        const OfxRangeD range = src_->getFrameRange();
        for (int k = -radius; k <= radius; ++k) {
            if (k == 0) continue;
            const double t = args.time + k;
            if (t < range.min || t > range.max) continue;
            std::unique_ptr<OFX::Image> img(src_->fetchImage(t));
            if (!img || img->getPixelDepth() != OFX::eBitDepthFloat || !imageCovers(*img, win)) continue;
            auto& buf = nbBufs[size_t(k + radius)];
            copyImage(*img, win, 4, buf);
            sd::ImageView v = view;
            v.data = buf.data();
            sd::toWorking(v, st.space, frames[size_t(k + radius)]);
            framePtrs[size_t(k + radius)] = &frames[size_t(k + radius)];
            if (abort()) return;
        }
    }

    // Profile.
    int mode = 0;
    profileMode_->getValueAtTime(args.time, mode);
    sd::NoiseProfile profile;
    const double rs = std::min(args.renderScale.x, args.renderScale.y);
    // A profile measured at full resolution, used at a reduced render scale
    // (proxy, downscaled viewer): converted for that scale.
    const int scaleShift = rs > 0.0 && rs < 0.99 ? std::max(0, std::min(3, int(std::lround(std::log2(1.0 / rs))))) : 0;
    if (mode == eProfileStored && storedProfile(profile, scaleShift) && profile.space == st.space) {
    } else if (mode == eProfileRegionLive) {
        std::string err;
        profile = sd::analyseRegion(frames[size_t(radius)], regionPixels(args.time, args.renderScale, win), st.space,
                                    0, &err);
        if (!profile.valid) profile = sd::analyseAuto(frames[size_t(radius)], st.space, 0);
    } else {
        profile = sd::analyseAuto(frames[size_t(radius)], st.space, 0);
    }
    if (abort()) return;

    sd::Frame result;
    if (!sd::denoise(framePtrs, radius, profile, st, result, [this]() { return abort(); })) return;

    std::vector<float> out(in.size());
    std::memcpy(out.data(), in.data(), in.size() * sizeof(float));
    sd::fromWorking(result, st.space, out.data(), std::ptrdiff_t(w) * 4, 4);

    // Mix and mask.
    const float mix = float(clamp01(mix_->getValueAtTime(args.time)));
    std::unique_ptr<OFX::Image> mask;
    bool invert = false;
    if (mask_ && mask_->isConnected()) {
        mask.reset(mask_->fetchImage(args.time));
        if (maskInvert_) maskInvert_->getValueAtTime(args.time, invert);
    }
    if (mix < 1.0f || mask || invert) {
        const int mc = mask ? mask->getPixelComponentCount() : 0;
        const OfxRectI mb = mask ? mask->getBounds() : OfxRectI{0, 0, 0, 0};
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                float m = 1.0f;
                if (mask) {
                    const int px = win.x1 + x, py = win.y1 + y;
                    m = 0.0f;
                    if (px >= mb.x1 && px < mb.x2 && py >= mb.y1 && py < mb.y2) {
                        const float* mp = static_cast<const float*>(mask->getPixelAddress(px, py));
                        if (mp) m = mp[mc - 1];
                    }
                }
                if (invert) m = 1.0f - m;
                m = std::max(0.0f, std::min(1.0f, m)) * mix;
                float* o = out.data() + (size_t(y) * size_t(w) + size_t(x)) * 4;
                const float* i = in.data() + (size_t(y) * size_t(w) + size_t(x)) * 4;
                for (int c = 0; c < 3; ++c) o[c] = i[c] + m * (o[c] - i[c]);
            }
    }

    int outMode = 0;
    output_->getValueAtTime(args.time, outMode);
    if (outMode == eOutputNoise) {
        for (size_t p = 0; p < out.size(); p += 4)
            for (int c = 0; c < 3; ++c) out[p + size_t(c)] = in[p + size_t(c)] - out[p + size_t(c)];
    }
    writeOut(out);
}

// ---------------------------------------------------------------------------
// Overlay: the analysis box, with draggable corners and body.

// The few OpenGL calls the overlay makes. On Linux they are looked up in the
// host process at draw time rather than linked, so the plugin also loads on
// render nodes without libGL.
struct GlFns {
    void (*color3f)(GLfloat, GLfloat, GLfloat) = nullptr;
    void (*lineWidth)(GLfloat) = nullptr;
    void (*begin)(GLenum) = nullptr;
    void (*end)() = nullptr;
    void (*vertex2d)(GLdouble, GLdouble) = nullptr;
    bool ok = false;
    GlFns()
    {
#ifdef __linux__
        color3f = reinterpret_cast<decltype(color3f)>(dlsym(RTLD_DEFAULT, "glColor3f"));
        lineWidth = reinterpret_cast<decltype(lineWidth)>(dlsym(RTLD_DEFAULT, "glLineWidth"));
        begin = reinterpret_cast<decltype(begin)>(dlsym(RTLD_DEFAULT, "glBegin"));
        end = reinterpret_cast<decltype(end)>(dlsym(RTLD_DEFAULT, "glEnd"));
        vertex2d = reinterpret_cast<decltype(vertex2d)>(dlsym(RTLD_DEFAULT, "glVertex2d"));
#else
        color3f = &glColor3f;
        lineWidth = &glLineWidth;
        begin = &glBegin;
        end = &glEnd;
        vertex2d = &glVertex2d;
#endif
        ok = color3f && lineWidth && begin && end && vertex2d;
    }
};

const GlFns& gl()
{
    static const GlFns f;
    return f;
}

class RegionInteract : public OFX::OverlayInteract {
public:
    RegionInteract(OfxInteractHandle handle, OFX::ImageEffect* effect) : OFX::OverlayInteract(handle), effect_(effect)
    {
        mode_ = effect->fetchChoiceParam(kParamProfileMode);
        bl_ = effect->fetchDouble2DParam(kParamRegionBL);
        tr_ = effect->fetchDouble2DParam(kParamRegionTR);
        addParamToSlaveTo(mode_);
        addParamToSlaveTo(bl_);
        addParamToSlaveTo(tr_);
    }

    bool draw(const OFX::DrawArgs& args) override
    {
        if (!visible(args.time) || !gl().ok) return false;
        const GlFns& g = gl();
        double x1, y1, x2, y2;
        bl_->getValueAtTime(args.time, x1, y1);
        tr_->getValueAtTime(args.time, x2, y2);
        const double hx = kHandle * args.pixelScale.x, hy = kHandle * args.pixelScale.y;
        for (int pass = 0; pass < 2; ++pass) {
            // shadow first, offset by a pixel, then the box
            const double ox = pass == 0 ? args.pixelScale.x : 0.0, oy = pass == 0 ? -args.pixelScale.y : 0.0;
            if (pass == 0)
                g.color3f(0.0f, 0.0f, 0.0f);
            else if (drag_ != eNone)
                g.color3f(1.0f, 0.9f, 0.3f);
            else
                g.color3f(0.9f, 0.7f, 0.1f);
            g.lineWidth(1.5f);
            g.begin(GL_LINE_LOOP);
            g.vertex2d(x1 + ox, y1 + oy);
            g.vertex2d(x2 + ox, y1 + oy);
            g.vertex2d(x2 + ox, y2 + oy);
            g.vertex2d(x1 + ox, y2 + oy);
            g.end();
            const double cx[4] = {x1, x2, x2, x1}, cy[4] = {y1, y1, y2, y2};
            for (int i = 0; i < 4; ++i) {
                g.begin(GL_LINE_LOOP);
                g.vertex2d(cx[i] - hx + ox, cy[i] - hy + oy);
                g.vertex2d(cx[i] + hx + ox, cy[i] - hy + oy);
                g.vertex2d(cx[i] + hx + ox, cy[i] + hy + oy);
                g.vertex2d(cx[i] - hx + ox, cy[i] + hy + oy);
                g.end();
            }
        }
        return true;
    }

    bool penDown(const OFX::PenArgs& args) override
    {
        if (!visible(args.time)) return false;
        double x1, y1, x2, y2;
        bl_->getValueAtTime(args.time, x1, y1);
        tr_->getValueAtTime(args.time, x2, y2);
        const double px = args.penPosition.x, py = args.penPosition.y;
        const double tx = 2.0 * kHandle * args.pixelScale.x, ty = 2.0 * kHandle * args.pixelScale.y;
        auto near = [&](double ax, double ay) { return std::fabs(px - ax) <= tx && std::fabs(py - ay) <= ty; };
        drag_ = eNone;
        if (near(x1, y1))
            drag_ = eBL;
        else if (near(x2, y1))
            drag_ = eBR;
        else if (near(x2, y2))
            drag_ = eTR;
        else if (near(x1, y2))
            drag_ = eTL;
        else if (px > std::min(x1, x2) && px < std::max(x1, x2) && py > std::min(y1, y2) && py < std::max(y1, y2))
            drag_ = eBody;
        if (drag_ == eNone) return false;
        lastX_ = px;
        lastY_ = py;
        effect_->beginEditBlock("Move analysis box");
        requestRedraw();
        return true;
    }

    bool penMotion(const OFX::PenArgs& args) override
    {
        if (drag_ == eNone) return false;
        double x1, y1, x2, y2;
        bl_->getValueAtTime(args.time, x1, y1);
        tr_->getValueAtTime(args.time, x2, y2);
        const double px = args.penPosition.x, py = args.penPosition.y;
        switch (drag_) {
        case eBL: x1 = px; y1 = py; break;
        case eBR: x2 = px; y1 = py; break;
        case eTR: x2 = px; y2 = py; break;
        case eTL: x1 = px; y2 = py; break;
        case eBody:
            x1 += px - lastX_;
            x2 += px - lastX_;
            y1 += py - lastY_;
            y2 += py - lastY_;
            break;
        default: break;
        }
        lastX_ = px;
        lastY_ = py;
        bl_->setValue(x1, y1);
        tr_->setValue(x2, y2);
        requestRedraw();
        return true;
    }

    bool penUp(const OFX::PenArgs& args) override
    {
        if (drag_ == eNone) return false;
        penMotion(args);
        drag_ = eNone;
        // keep bottom-left below/left of top-right
        double x1, y1, x2, y2;
        bl_->getValueAtTime(args.time, x1, y1);
        tr_->getValueAtTime(args.time, x2, y2);
        if (x1 > x2 || y1 > y2) {
            bl_->setValue(std::min(x1, x2), std::min(y1, y2));
            tr_->setValue(std::max(x1, x2), std::max(y1, y2));
        }
        effect_->endEditBlock();
        requestRedraw();
        return true;
    }

private:
    enum Drag { eNone, eBL, eBR, eTR, eTL, eBody };
    static constexpr double kHandle = 4.0;  // half size of a corner handle, screen pixels

    bool visible(double time) const
    {
        int mode = 0;
        mode_->getValueAtTime(time, mode);
        return mode != eProfileAuto;
    }

    OFX::ImageEffect* effect_;
    OFX::ChoiceParam* mode_;
    OFX::Double2DParam* bl_;
    OFX::Double2DParam* tr_;
    Drag drag_ = eNone;
    double lastX_ = 0.0, lastY_ = 0.0;
};

class RegionOverlayDescriptor : public OFX::DefaultEffectOverlayDescriptor<RegionOverlayDescriptor, RegionInteract> {
};

// ---------------------------------------------------------------------------
// Factory

mDeclarePluginFactory(SpectralDenoiseFactory, {}, {});

void SpectralDenoiseFactory::describe(OFX::ImageEffectDescriptor& desc)
{
    desc.setLabels(kPluginName, kPluginName, kPluginName);
    desc.setPluginGrouping(kPluginGrouping);
    desc.setPluginDescription(kPluginDescription);
    desc.addSupportedContext(OFX::eContextFilter);
    desc.addSupportedContext(OFX::eContextGeneral);
    desc.addSupportedBitDepth(OFX::eBitDepthFloat);
    desc.setSingleInstance(false);
    desc.setHostFrameThreading(false);
    desc.setSupportsMultiResolution(true);
    desc.setSupportsTiles(false);
    desc.setTemporalClipAccess(true);
    desc.setRenderTwiceAlways(false);
    desc.setSupportsMultipleClipPARs(false);
    desc.setRenderThreadSafety(OFX::eRenderFullySafe);
    desc.setOverlayInteractDescriptor(new RegionOverlayDescriptor);
}

namespace {

OFX::DoubleParamDescriptor* defineDouble(OFX::ImageEffectDescriptor& desc, const char* name, const char* label,
                                         const char* hint, double def, double dmin, double dmax,
                                         OFX::GroupParamDescriptor* group, OFX::PageParamDescriptor* page)
{
    OFX::DoubleParamDescriptor* p = desc.defineDoubleParam(name);
    p->setLabels(label, label, label);
    p->setHint(hint);
    p->setDefault(def);
    p->setRange(0.0, 1000.0);
    p->setDisplayRange(dmin, dmax);
    p->setIncrement(0.01);
    p->setDigits(2);
    if (group) p->setParent(*group);
    if (page) page->addChild(*p);
    return p;
}

}  // namespace

void SpectralDenoiseFactory::describeInContext(OFX::ImageEffectDescriptor& desc, OFX::ContextEnum context)
{
    OFX::ClipDescriptor* srcClip = desc.defineClip(kOfxImageEffectSimpleSourceClipName);
    srcClip->addSupportedComponent(OFX::ePixelComponentRGBA);
    srcClip->addSupportedComponent(OFX::ePixelComponentRGB);
    srcClip->setTemporalClipAccess(true);
    srcClip->setSupportsTiles(false);
    srcClip->setIsMask(false);

    OFX::ClipDescriptor* dstClip = desc.defineClip(kOfxImageEffectOutputClipName);
    dstClip->addSupportedComponent(OFX::ePixelComponentRGBA);
    dstClip->addSupportedComponent(OFX::ePixelComponentRGB);
    dstClip->setSupportsTiles(false);

    if (context == OFX::eContextGeneral || context == OFX::eContextPaint) {
        OFX::ClipDescriptor* maskClip = desc.defineClip(kClipMask);
        maskClip->addSupportedComponent(OFX::ePixelComponentAlpha);
        maskClip->setTemporalClipAccess(false);
        maskClip->setOptional(true);
        maskClip->setSupportsTiles(false);
        maskClip->setIsMask(true);
    }

    OFX::PageParamDescriptor* page = desc.definePageParam("Controls");

    // --- Noise profile ---
    OFX::GroupParamDescriptor* gProfile = desc.defineGroupParam("profileGroup");
    gProfile->setLabels("Noise Profile", "Noise Profile", "Noise Profile");
    gProfile->setOpen(true);
    page->addChild(*gProfile);
    {
        OFX::ChoiceParamDescriptor* p = desc.defineChoiceParam(kParamProfileMode);
        p->setLabels("Profile", "Profile", "Profile");
        p->setHint("Analysed: the profile measured by Analyse Noise, saved with the script (recommended).\n"
                   "Region (every frame): measure the box on every frame while rendering.\n"
                   "Auto (every frame): measure the flattest parts of every frame.");
        p->appendOption("Analysed");
        p->appendOption("Region (every frame)");
        p->appendOption("Auto (every frame)");
        p->setDefault(eProfileStored);
        p->setAnimates(false);
        p->setParent(*gProfile);
        page->addChild(*p);
    }
    {
        OFX::Double2DParamDescriptor* p = desc.defineDouble2DParam(kParamRegionBL);
        p->setLabels("Box Bottom Left", "Box Bottom Left", "Box Bottom Left");
        p->setHint("Bottom-left corner of the analysis box. Drag the box in the viewer.");
        p->setDoubleType(OFX::eDoubleTypeXYAbsolute);
        p->setDefaultCoordinateSystem(OFX::eCoordinatesCanonical);
        p->setDefault(200.0, 200.0);
        p->setAnimates(true);
        p->setParent(*gProfile);
        page->addChild(*p);
    }
    {
        OFX::Double2DParamDescriptor* p = desc.defineDouble2DParam(kParamRegionTR);
        p->setLabels("Box Top Right", "Box Top Right", "Box Top Right");
        p->setHint("Top-right corner of the analysis box. Drag the box in the viewer.");
        p->setDoubleType(OFX::eDoubleTypeXYAbsolute);
        p->setDefaultCoordinateSystem(OFX::eCoordinatesCanonical);
        p->setDefault(456.0, 456.0);
        p->setAnimates(true);
        p->setParent(*gProfile);
        page->addChild(*p);
    }
    {
        OFX::PushButtonParamDescriptor* p = desc.definePushButtonParam(kParamAnalyse);
        p->setLabels("Analyse Noise", "Analyse Noise", "Analyse Noise");
        p->setHint("Measure the noise inside the box on the current frame. The box should hold no detail: sky, "
                   "a wall, a grey card, out-of-focus background. Bigger is better (256 x 256 or more).");
        p->setParent(*gProfile);
        page->addChild(*p);
    }
    {
        OFX::StringParamDescriptor* p = desc.defineStringParam(kParamStatus);
        p->setLabels("Status", "Status", "Status");
        p->setStringType(OFX::eStringTypeLabel);
        p->setDefault("No profile yet (using Auto). Put the box on a flat area and press Analyse Noise.");
        p->setAnimates(false);
        p->setEvaluateOnChange(false);
        p->setParent(*gProfile);
        page->addChild(*p);
    }
    {
        OFX::StringParamDescriptor* p = desc.defineStringParam(kParamProfileData);
        p->setLabels("Profile Data", "Profile Data", "Profile Data");
        p->setStringType(OFX::eStringTypeSingleLine);
        p->setIsSecret(true);
        p->setAnimates(false);
        p->setDefault("");
        p->setParent(*gProfile);
    }
    {
        OFX::ChoiceParamDescriptor* p = desc.defineChoiceParam(kParamColourspace);
        p->setLabels("Input Colourspace", "Input Colourspace", "Input Colourspace");
        p->setHint("Scene Linear: linear-light footage (Nuke's default working space). A log-like curve is applied "
                   "internally, so shadows and highlights are treated evenly.\n"
                   "Log / Video: log-encoded or display-referred footage, processed as is.\n"
                   "Re-analyse after changing this.");
        p->appendOption("Scene Linear");
        p->appendOption("Log / Video");
        p->setDefault(0);
        p->setAnimates(false);
        p->setParent(*gProfile);
        page->addChild(*p);
    }
    {
        OFX::BooleanParamDescriptor* p = desc.defineBooleanParam(kParamLumaAdaptive);
        p->setLabels("Brightness Adaptive", "Brightness Adaptive", "Brightness Adaptive");
        p->setHint("Follow how the noise changes with brightness (measured over the whole frame when you analyse). "
                   "Off: the box's noise is used everywhere.");
        p->setDefault(true);
        p->setAnimates(false);
        p->setParent(*gProfile);
        page->addChild(*p);
    }

    // --- Reduction ---
    OFX::GroupParamDescriptor* gRed = desc.defineGroupParam("reductionGroup");
    gRed->setLabels("Reduction", "Reduction", "Reduction");
    gRed->setOpen(true);
    page->addChild(*gRed);
    defineDouble(desc, kParamLevel, "Noise Level",
                 "Scales the measured noise. Above 1 removes more (and starts to remove fine detail), below 1 keeps "
                 "more.",
                 1.0, 0.0, 3.0, gRed, page);
    defineDouble(desc, kParamLumaLevel, "Luma Level", "Noise level for brightness noise.", 1.0, 0.0, 3.0, gRed, page);
    defineDouble(desc, kParamChromaLevel, "Chroma Level", "Noise level for colour noise.", 1.0, 0.0, 3.0, gRed, page);
    defineDouble(desc, kParamLumaAmount, "Luma Reduction",
                 "How much of the brightness noise to remove (1 = all of it, 0.7 keeps some grain).", 1.0, 0.0, 1.0,
                 gRed, page);
    defineDouble(desc, kParamChromaAmount, "Chroma Reduction", "How much of the colour noise to remove.", 1.0, 0.0,
                 1.0, gRed, page);
    defineDouble(desc, kParamFine, "Fine", "Noise level for the finest noise (pixel-sized grain).", 1.0, 0.0, 3.0,
                 gRed, page);
    defineDouble(desc, kParamMedium, "Medium", "Noise level for noise a few pixels across.", 1.0, 0.0, 3.0, gRed,
                 page);
    defineDouble(desc, kParamCoarse, "Coarse", "Noise level for blotchy noise (around 10 pixels).", 1.0, 0.0, 3.0,
                 gRed, page);
    defineDouble(desc, kParamVeryCoarse, "Very Coarse", "Noise level for large, low-frequency blotches.", 1.0, 0.0,
                 3.0, gRed, page);
    defineDouble(desc, kParamSharpen, "Sharpen", "Sharpen the denoised luma a little.", 0.0, 0.0, 1.0, gRed, page);

    // --- Temporal ---
    OFX::GroupParamDescriptor* gTemp = desc.defineGroupParam("temporalGroup");
    gTemp->setLabels("Temporal", "Temporal", "Temporal");
    gTemp->setOpen(true);
    page->addChild(*gTemp);
    {
        OFX::IntParamDescriptor* p = desc.defineIntParam(kParamTemporalRadius);
        p->setLabels("Temporal Radius", "Temporal Radius", "Temporal Radius");
        p->setHint("Frames used on each side of the current one. 0 = spatial only. Each block is matched in the "
                   "neighbouring frames; where motion can't be matched, fewer frames are used.");
        p->setDefault(1);
        p->setRange(0, sd::kMaxTemporalRadius);
        p->setDisplayRange(0, sd::kMaxTemporalRadius);
        p->setAnimates(false);
        p->setParent(*gTemp);
        page->addChild(*p);
    }
    defineDouble(desc, kParamMotionTolerance, "Motion Tolerance",
                 "How different a matched block in another frame may be. Lower if you see ghosting on moving "
                 "objects, raise for more reduction on static shots.",
                 1.0, 0.0, 4.0, gTemp, page);

    // --- Output ---
    OFX::GroupParamDescriptor* gOut = desc.defineGroupParam("outputGroup");
    gOut->setLabels("Output", "Output", "Output");
    gOut->setOpen(true);
    page->addChild(*gOut);
    {
        OFX::ChoiceParamDescriptor* p = desc.defineChoiceParam(kParamQuality);
        p->setLabels("Quality", "Quality", "Quality");
        p->setHint("Fast: one pass, sparse blocks. Normal: two passes. Best: two passes, denser blocks.");
        p->appendOption("Fast");
        p->appendOption("Normal");
        p->appendOption("Best");
        p->setDefault(1);
        p->setAnimates(false);
        p->setParent(*gOut);
        page->addChild(*p);
    }
    {
        OFX::ChoiceParamDescriptor* p = desc.defineChoiceParam(kParamOutput);
        p->setLabels("Output", "Output", "Output");
        p->setHint("Denoised: the result. Removed Noise: what was taken out (source minus result), to check no "
                   "detail is being removed.");
        p->appendOption("Denoised");
        p->appendOption("Removed Noise");
        p->setDefault(eOutputDenoised);
        p->setAnimates(false);
        p->setParent(*gOut);
        page->addChild(*p);
    }
    {
        OFX::DoubleParamDescriptor* p = defineDouble(desc, kParamMix, "Mix", "Blend with the source.", 1.0, 0.0, 1.0,
                                                     gOut, page);
        p->setRange(0.0, 1.0);
    }
    if (context == OFX::eContextGeneral || context == OFX::eContextPaint) {
        OFX::BooleanParamDescriptor* p = desc.defineBooleanParam(kParamMaskInvert);
        p->setLabels("Invert Mask", "Invert Mask", "Invert Mask");
        p->setDefault(false);
        p->setAnimates(false);
        p->setParent(*gOut);
        page->addChild(*p);
    }
}

OFX::ImageEffect* SpectralDenoiseFactory::createInstance(OfxImageEffectHandle handle, OFX::ContextEnum)
{
    return new SpectralDenoisePlugin(handle);
}

namespace OFX {
namespace Plugin {
void getPluginIDs(OFX::PluginFactoryArray& ids)
{
    static SpectralDenoiseFactory p(kPluginIdentifier, kPluginVersionMajor, kPluginVersionMinor);
    ids.push_back(&p);
}
}  // namespace Plugin
}  // namespace OFX
