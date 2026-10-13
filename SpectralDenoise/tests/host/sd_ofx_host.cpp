// A minimal, scriptable OFX host for testing the plugin without Nuke.
//
// Built on the OpenFX HostSupport library (the reference host-side
// implementation of the API), so the plugin goes through the real C API:
// property suites, parameter suites, clip image fetching, render actions.
//
// Commands are read from a script file, one per line:
//   load <pluginDir> <pluginId> <context>       context: filter | general
//   clip <name> <w> <h> <first> <last> <pattern>  float RGBA raw files, rows
//                                                 bottom-up; pattern has %d
//   create                                       createInstance + clip prefs
//   set <param> <v1> [v2 ...]                    user edit (instanceChanged)
//   time <t>                                     current time for set/press
//   press <param>                                push button at current time
//   render <t> <out.raw> [scale]                 renders the output clip
//   get <param>                                  prints VALUE <param> ...
//   frames <t>                                   prints frames needed
//   identity <t>                                 prints IDENTITY yes|no
//   describe                                     prints PARAM lines
// Output lines: MESSAGE, VALUE, PARAM, FRAMES, IDENTITY, RENDERED, ERROR.
#include <algorithm>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "ofxCore.h"
#include "ofxImageEffect.h"
#include "ofxPixels.h"

// (order matters: these headers are not self-contained)
#include "ofxhBinary.h"
#include "ofxhPropertySuite.h"
#include "ofxhClip.h"
#include "ofxhParam.h"
#include "ofxhMemory.h"
#include "ofxhImageEffect.h"
#include "ofxhPluginAPICache.h"
#include "ofxhPluginCache.h"
#include "ofxhHost.h"
#include "ofxhImageEffectAPI.h"

namespace TH {

using namespace OFX::Host;

struct ClipSource {
    int w = 0, h = 0;
    int first = 0, last = 0;
    std::string pattern;
};

class Effect;

// ---------------------------------------------------------------------------
// Images

class FloatImage : public ImageEffect::Image {
public:
    FloatImage(ImageEffect::ClipInstance& clip, int w, int h, int comps, double scale)
        : ImageEffect::Image(clip), comps_(comps), w_(w), h_(h), data_(size_t(w) * size_t(h) * size_t(comps), 0.0f)
    {
        setDoubleProperty(kOfxImageEffectPropRenderScale, scale, 0);
        setDoubleProperty(kOfxImageEffectPropRenderScale, scale, 1);
        setPointerProperty(kOfxImagePropData, data_.data());
        for (int i = 0; i < 4; ++i) {
            const int v = i == 2 ? w : (i == 3 ? h : 0);
            setIntProperty(kOfxImagePropBounds, v, i);
            setIntProperty(kOfxImagePropRegionOfDefinition, v, i);
        }
        setIntProperty(kOfxImagePropRowBytes, int(sizeof(float)) * w * comps);
    }
    float* data() { return data_.data(); }
    int comps() const { return comps_; }
    int width() const { return w_; }
    int height() const { return h_; }

private:
    int comps_, w_, h_;
    std::vector<float> data_;
};

bool readRaw(const std::string& path, int w, int h, std::vector<float>& rgba)
{
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    rgba.resize(size_t(w) * size_t(h) * 4);
    f.read(reinterpret_cast<char*>(rgba.data()), std::streamsize(rgba.size() * sizeof(float)));
    return bool(f);
}

// ---------------------------------------------------------------------------
// Clips

class Clip : public ImageEffect::ClipInstance {
public:
    Clip(Effect* effect, ImageEffect::ClipDescriptor* desc);
    ~Clip() override
    {
        if (output_) output_->releaseReference();
    }

    const std::string& getUnmappedBitDepth() const override
    {
        static const std::string v(kOfxBitDepthFloat);
        return v;
    }
    const std::string& getUnmappedComponents() const override
    {
        static const std::string rgba(kOfxImageComponentRGBA), a(kOfxImageComponentAlpha);
        return isMask() ? a : rgba;
    }
    const std::string& getPremult() const override
    {
        static const std::string v(kOfxImageUnPreMultiplied);
        return v;
    }
    double getAspectRatio() const override { return 1.0; }
    double getFrameRate() const override { return 24.0; }
    void getFrameRange(double& a, double& b) const override;
    const std::string& getFieldOrder() const override
    {
        static const std::string v(kOfxImageFieldNone);
        return v;
    }
    bool getConnected() const override;
    double getUnmappedFrameRate() const override { return 24.0; }
    void getUnmappedFrameRange(double& a, double& b) const override { getFrameRange(a, b); }
    bool getContinuousSamples() const override { return false; }
    ImageEffect::Image* getImage(OfxTime time, const OfxRectD* optionalBounds) override;
#ifdef OFX_SUPPORTS_OPENGLRENDER
    ImageEffect::Texture* loadTexture(OfxTime, const char*, const OfxRectD*) override { return nullptr; }
#endif
    OfxRectD getRegionOfDefinition(OfxTime time) const override;

    FloatImage* output() { return output_; }
    void prepareOutput(int w, int h, double scale)
    {
        if (output_) output_->releaseReference();
        output_ = new FloatImage(*this, w, h, componentCount(), scale);
    }
    int componentCount() const
    {
        const std::string& c = getComponents();
        return c == kOfxImageComponentAlpha ? 1 : (c == kOfxImageComponentRGB ? 3 : 4);
    }

    double renderScale = 1.0;
    std::vector<std::string> fetchLog;

private:
    Effect* effect_;
    std::string name_;
    FloatImage* output_ = nullptr;
};

// ---------------------------------------------------------------------------
// Parameters: one value store for every kind.

struct ParamStore {
    std::vector<double> nums;
    std::string str;
};

// HostSupport notifies the plugin (instanceChanged, reason pluginEdited)
// when the plugin sets a value through the suite.
template <class Base>
class ParamBase : public Base {
public:
    ParamBase(Param::SetInstance* set, const std::string&, Param::Descriptor& d) : Base(d, set) {}
};

class IntParam : public ParamBase<Param::IntegerInstance> {
public:
    IntParam(Param::SetInstance* e, const std::string& n, Param::Descriptor& d) : ParamBase(e, n, d)
    {
        v_ = d.getProperties().getIntProperty(kOfxParamPropDefault);
    }
    OfxStatus get(int& v) override { return v = v_, kOfxStatOK; }
    OfxStatus get(OfxTime, int& v) override { return v = v_, kOfxStatOK; }
    OfxStatus set(int v) override { return v_ = v, kOfxStatOK; }
    OfxStatus set(OfxTime t, int v) override { return v_ = v, kOfxStatOK; }
    int v_;
};

class ChoiceParam : public ParamBase<Param::ChoiceInstance> {
public:
    ChoiceParam(Param::SetInstance* e, const std::string& n, Param::Descriptor& d) : ParamBase(e, n, d)
    {
        v_ = d.getProperties().getIntProperty(kOfxParamPropDefault);
    }
    OfxStatus get(int& v) override { return v = v_, kOfxStatOK; }
    OfxStatus get(OfxTime, int& v) override { return v = v_, kOfxStatOK; }
    OfxStatus set(int v) override { return v_ = v, kOfxStatOK; }
    OfxStatus set(OfxTime t, int v) override { return v_ = v, kOfxStatOK; }
    int v_;
};

class BoolParam : public ParamBase<Param::BooleanInstance> {
public:
    BoolParam(Param::SetInstance* e, const std::string& n, Param::Descriptor& d) : ParamBase(e, n, d)
    {
        v_ = d.getProperties().getIntProperty(kOfxParamPropDefault) != 0;
    }
    OfxStatus get(bool& v) override { return v = v_, kOfxStatOK; }
    OfxStatus get(OfxTime, bool& v) override { return v = v_, kOfxStatOK; }
    OfxStatus set(bool v) override { return v_ = v, kOfxStatOK; }
    OfxStatus set(OfxTime t, bool v) override { return v_ = v, kOfxStatOK; }
    bool v_;
};

class DoubleParam : public ParamBase<Param::DoubleInstance> {
public:
    DoubleParam(Param::SetInstance* e, const std::string& n, Param::Descriptor& d) : ParamBase(e, n, d)
    {
        v_ = d.getProperties().getDoubleProperty(kOfxParamPropDefault);
    }
    OfxStatus get(double& v) override { return v = v_, kOfxStatOK; }
    OfxStatus get(OfxTime, double& v) override { return v = v_, kOfxStatOK; }
    OfxStatus set(double v) override { return v_ = v, kOfxStatOK; }
    OfxStatus set(OfxTime t, double v) override { return v_ = v, kOfxStatOK; }
    OfxStatus derive(OfxTime, double& v) override { return v = 0, kOfxStatOK; }
    OfxStatus integrate(OfxTime a, OfxTime b, double& v) override { return v = v_ * (b - a), kOfxStatOK; }
    double v_;
};

class Double2DParam : public ParamBase<Param::Double2DInstance> {
public:
    Double2DParam(Param::SetInstance* e, const std::string& n, Param::Descriptor& d) : ParamBase(e, n, d)
    {
        x_ = d.getProperties().getDoubleProperty(kOfxParamPropDefault, 0);
        y_ = d.getProperties().getDoubleProperty(kOfxParamPropDefault, 1);
    }
    OfxStatus get(double& x, double& y) override { return x = x_, y = y_, kOfxStatOK; }
    OfxStatus get(OfxTime, double& x, double& y) override { return x = x_, y = y_, kOfxStatOK; }
    OfxStatus set(double x, double y) override { return x_ = x, y_ = y, kOfxStatOK; }
    OfxStatus set(OfxTime t, double x, double y) override { return x_ = x, y_ = y, kOfxStatOK; }
    double x_, y_;
};

class StringParam : public ParamBase<Param::StringInstance> {
public:
    StringParam(Param::SetInstance* e, const std::string& n, Param::Descriptor& d) : ParamBase(e, n, d)
    {
        v_ = d.getProperties().getStringProperty(kOfxParamPropDefault);
    }
    OfxStatus get(std::string& v) override { return v = v_, kOfxStatOK; }
    OfxStatus get(OfxTime, std::string& v) override { return v = v_, kOfxStatOK; }
    OfxStatus set(const char* v) override { return v_ = v, kOfxStatOK; }
    OfxStatus set(OfxTime t, const char* v) override { return v_ = v, kOfxStatOK; }
    std::string v_;
};

class PushParam : public Param::PushbuttonInstance {
public:
    PushParam(Param::SetInstance* e, const std::string&, Param::Descriptor& d) : Param::PushbuttonInstance(d, e) {}
};

// ---------------------------------------------------------------------------
// Effect instance

class Effect : public ImageEffect::Instance {
public:
    Effect(ImageEffect::ImageEffectPlugin* plugin, ImageEffect::Descriptor& desc, const std::string& context)
        : ImageEffect::Instance(plugin, desc, context, false)
    {
    }

    ImageEffect::ClipInstance* newClipInstance(ImageEffect::Instance*, ImageEffect::ClipDescriptor* d, int) override
    {
        return new Clip(this, d);
    }
    const std::string& getDefaultOutputFielding() const override
    {
        static const std::string v(kOfxImageFieldNone);
        return v;
    }
    OfxStatus vmessage(const char* type, const char*, const char* format, va_list args) override
    {
        char buf[4096];
        vsnprintf(buf, sizeof buf, format, args);
        std::string s(buf);
        std::replace(s.begin(), s.end(), '\n', ' ');
        std::printf("MESSAGE %s %s\n", type, s.c_str());
        return kOfxStatOK;
    }
    OfxStatus setPersistentMessage(const char* type, const char* id, const char* format, va_list args) override
    {
        return vmessage(type, id, format, args);
    }
    OfxStatus clearPersistentMessage() override { return kOfxStatOK; }
    void getProjectSize(double& x, double& y) const override { x = projW, y = projH; }
    void getProjectOffset(double& x, double& y) const override { x = y = 0.0; }
    void getProjectExtent(double& x, double& y) const override { x = projW, y = projH; }
    double getProjectPixelAspectRatio() const override { return 1.0; }
    double getEffectDuration() const override { return 100.0; }
    double getFrameRate() const override { return 24.0; }
    double getFrameRecursive() const override { return currentTime; }
    void getRenderScaleRecursive(double& x, double& y) const override { x = y = renderScale; }
    Param::Instance* newParam(const std::string& name, Param::Descriptor& d) override
    {
        const std::string& t = d.getType();
        if (t == kOfxParamTypeInteger) return new IntParam(this, name, d);
        if (t == kOfxParamTypeDouble) return new DoubleParam(this, name, d);
        if (t == kOfxParamTypeBoolean) return new BoolParam(this, name, d);
        if (t == kOfxParamTypeChoice) return new ChoiceParam(this, name, d);
        if (t == kOfxParamTypeDouble2D) return new Double2DParam(this, name, d);
        if (t == kOfxParamTypeString) return new StringParam(this, name, d);
        if (t == kOfxParamTypePushButton) return new PushParam(this, name, d);
        if (t == kOfxParamTypeGroup) return new Param::GroupInstance(d, this);
        if (t == kOfxParamTypePage) return new Param::PageInstance(d, this);
        std::printf("ERROR unsupported param type %s for %s\n", t.c_str(), name.c_str());
        return nullptr;
    }
    OfxStatus editBegin(const std::string&) override { return kOfxStatOK; }
    OfxStatus editEnd() override { return kOfxStatOK; }
    void progressStart(const std::string&, const std::string&) override {}
    void progressEnd() override {}
    bool progressUpdate(double) override { return true; }
    double timeLineGetTime() override { return currentTime; }
    void timeLineGotoTime(double t) override { currentTime = t; }
    void timeLineGetBounds(double& a, double& b) override { a = 0, b = 100; }

    std::map<std::string, ClipSource> sources;
    double projW = 1920, projH = 1080;
    double currentTime = 0.0;
    double renderScale = 1.0;
    bool created = false;
};

Clip::Clip(Effect* effect, ImageEffect::ClipDescriptor* desc)
    : ImageEffect::ClipInstance(effect, *desc), effect_(effect), name_(desc->getName())
{
}

void Clip::getFrameRange(double& a, double& b) const
{
    auto it = effect_->sources.find(name_);
    if (it == effect_->sources.end()) {
        auto src = effect_->sources.find(kOfxImageEffectSimpleSourceClipName);
        if (src == effect_->sources.end()) {
            a = 0, b = 0;
            return;
        }
        it = src;
    }
    a = it->second.first;
    b = it->second.last;
}

bool Clip::getConnected() const
{
    return name_ == kOfxImageEffectOutputClipName || effect_->sources.count(name_) > 0;
}

OfxRectD Clip::getRegionOfDefinition(OfxTime) const
{
    OfxRectD r{0, 0, 0, 0};
    auto it = effect_->sources.find(name_ == kOfxImageEffectOutputClipName ? kOfxImageEffectSimpleSourceClipName : name_);
    if (it != effect_->sources.end()) {
        r.x2 = it->second.w;
        r.y2 = it->second.h;
    }
    return r;
}

ImageEffect::Image* Clip::getImage(OfxTime time, const OfxRectD*)
{
    if (name_ == kOfxImageEffectOutputClipName) {
        if (!output_) return nullptr;
        output_->addReference();
        return output_;
    }
    auto it = effect_->sources.find(name_);
    if (it == effect_->sources.end()) return nullptr;
    const ClipSource& s = it->second;
    // Outside the clip, hold the first / last frame, as Nuke does.
    const int t = std::max(s.first, std::min(s.last, int(std::lround(time))));
    char path[4096];
    std::snprintf(path, sizeof path, s.pattern.c_str(), t);
    std::vector<float> rgba;
    if (!readRaw(path, s.w, s.h, rgba)) {
        std::printf("ERROR cannot read %s\n", path);
        return nullptr;
    }
    // Reduced render scale: box-filter the full-resolution image.
    const double scale = effect_->renderScale;
    const int f = scale < 0.99 ? int(std::lround(1.0 / scale)) : 1;
    const int w = s.w / f, h = s.h / f;
    const int comps = componentCount();
    FloatImage* img = new FloatImage(*this, w, h, comps, scale);
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            float acc[4] = {0, 0, 0, 0};
            for (int j = 0; j < f; ++j)
                for (int i = 0; i < f; ++i)
                    for (int c = 0; c < 4; ++c)
                        acc[c] += rgba[(size_t(y * f + j) * size_t(s.w) + size_t(x * f + i)) * 4 + size_t(c)];
            float* d = img->data() + (size_t(y) * size_t(w) + size_t(x)) * size_t(comps);
            for (int c = 0; c < 4; ++c) acc[c] /= float(f * f);
            if (comps == 1)
                d[0] = acc[3];
            else
                for (int c = 0; c < comps; ++c) d[c] = acc[c];
        }
    char buf[64];
    std::snprintf(buf, sizeof buf, "%d", t);
    fetchLog.push_back(buf);
    return img;
}

// ---------------------------------------------------------------------------
// Host

class Host : public ImageEffect::Host {
public:
    Host()
    {
        _properties.setStringProperty(kOfxPropName, "SDTestHost");
        _properties.setStringProperty(kOfxPropLabel, "SpectralDenoise test host");
        _properties.setIntProperty(kOfxImageEffectHostPropIsBackground, 0);
        _properties.setIntProperty(kOfxImageEffectPropSupportsOverlays, 0);
        _properties.setIntProperty(kOfxImageEffectPropSupportsMultiResolution, 1);
        _properties.setIntProperty(kOfxImageEffectPropSupportsTiles, 1);
        _properties.setIntProperty(kOfxImageEffectPropTemporalClipAccess, 1);
        _properties.setStringProperty(kOfxImageEffectPropSupportedComponents, kOfxImageComponentRGBA, 0);
        _properties.setStringProperty(kOfxImageEffectPropSupportedComponents, kOfxImageComponentAlpha, 1);
        _properties.setStringProperty(kOfxImageEffectPropSupportedContexts, kOfxImageEffectContextFilter, 0);
        _properties.setStringProperty(kOfxImageEffectPropSupportedContexts, kOfxImageEffectContextGeneral, 1);
        _properties.setIntProperty(kOfxImageEffectPropSupportsMultipleClipDepths, 0);
        _properties.setIntProperty(kOfxImageEffectPropSupportsMultipleClipPARs, 0);
        _properties.setIntProperty(kOfxImageEffectPropSetableFrameRate, 0);
        _properties.setIntProperty(kOfxImageEffectPropSetableFielding, 0);
        _properties.setIntProperty(kOfxParamHostPropSupportsCustomInteract, 0);
        _properties.setIntProperty(kOfxParamHostPropSupportsStringAnimation, 0);
        _properties.setIntProperty(kOfxParamHostPropSupportsChoiceAnimation, 0);
        _properties.setIntProperty(kOfxParamHostPropSupportsBooleanAnimation, 0);
        _properties.setIntProperty(kOfxParamHostPropSupportsCustomAnimation, 0);
        _properties.setIntProperty(kOfxParamHostPropMaxParameters, -1);
        _properties.setIntProperty(kOfxParamHostPropMaxPages, 0);
        _properties.setIntProperty(kOfxParamHostPropPageRowColumnCount, 0, 0);
        _properties.setIntProperty(kOfxParamHostPropPageRowColumnCount, 0, 1);
    }
    ImageEffect::Instance* newInstance(void*, ImageEffect::ImageEffectPlugin* plugin, ImageEffect::Descriptor& desc,
                                       const std::string& context) override
    {
        return new Effect(plugin, desc, context);
    }
    ImageEffect::Descriptor* makeDescriptor(ImageEffect::ImageEffectPlugin* plugin) override
    {
        return new ImageEffect::Descriptor(plugin);
    }
    ImageEffect::Descriptor* makeDescriptor(const ImageEffect::Descriptor& root,
                                            ImageEffect::ImageEffectPlugin* plugin) override
    {
        return new ImageEffect::Descriptor(root, plugin);
    }
    ImageEffect::Descriptor* makeDescriptor(const std::string& bundlePath,
                                            ImageEffect::ImageEffectPlugin* plugin) override
    {
        return new ImageEffect::Descriptor(bundlePath, plugin);
    }
    OfxStatus vmessage(const char* type, const char*, const char* format, va_list args) override
    {
        char buf[4096];
        vsnprintf(buf, sizeof buf, format, args);
        std::printf("MESSAGE %s %s\n", type, buf);
        return kOfxStatOK;
    }
    OfxStatus setPersistentMessage(const char* type, const char* id, const char* format, va_list args) override
    {
        return vmessage(type, id, format, args);
    }
    OfxStatus clearPersistentMessage() override { return kOfxStatOK; }
#ifdef OFX_SUPPORTS_OPENGLRENDER
    OfxStatus flushOpenGLResources() const override { return kOfxStatFailed; }
#endif
};

}  // namespace TH

namespace {

std::vector<std::string> split(const std::string& line)
{
    std::istringstream is(line);
    std::vector<std::string> out;
    std::string w;
    while (is >> w) out.push_back(w);
    return out;
}

std::string rest(const std::string& line, int skip)
{
    std::istringstream is(line);
    std::string w;
    for (int i = 0; i < skip; ++i) is >> w;
    std::string r;
    std::getline(is, r);
    const size_t p = r.find_first_not_of(' ');
    return p == std::string::npos ? std::string() : r.substr(p);
}

void printParam(OFX::Host::Param::Instance* p, const std::string& name)
{
    using namespace TH;
    std::printf("VALUE %s", name.c_str());
    if (auto* i = dynamic_cast<IntParam*>(p)) std::printf(" %d", i->v_);
    else if (auto* c = dynamic_cast<ChoiceParam*>(p)) std::printf(" %d", c->v_);
    else if (auto* b = dynamic_cast<BoolParam*>(p)) std::printf(" %d", b->v_ ? 1 : 0);
    else if (auto* d = dynamic_cast<DoubleParam*>(p)) std::printf(" %.9g", d->v_);
    else if (auto* d2 = dynamic_cast<Double2DParam*>(p)) std::printf(" %.9g %.9g", d2->x_, d2->y_);
    else if (auto* s = dynamic_cast<StringParam*>(p)) std::printf(" %s", s->v_.c_str());
    std::printf("\n");
}

bool setParam(OFX::Host::Param::Instance* p, const std::vector<std::string>& a, const std::string& line)
{
    using namespace TH;
    if (auto* i = dynamic_cast<IntParam*>(p)) return i->v_ = std::stoi(a.at(2)), true;
    if (auto* c = dynamic_cast<ChoiceParam*>(p)) return c->v_ = std::stoi(a.at(2)), true;
    if (auto* b = dynamic_cast<BoolParam*>(p)) return b->v_ = std::stoi(a.at(2)) != 0, true;
    if (auto* d = dynamic_cast<DoubleParam*>(p)) return d->v_ = std::stod(a.at(2)), true;
    if (auto* d2 = dynamic_cast<Double2DParam*>(p)) return d2->x_ = std::stod(a.at(2)), d2->y_ = std::stod(a.at(3)), true;
    if (auto* s = dynamic_cast<StringParam*>(p)) return s->v_ = rest(line, 2), true;
    return false;
}

}  // namespace

int main(int argc, char** argv)
{
    if (argc < 2) {
        std::fprintf(stderr, "usage: %s script.txt\n", argv[0]);
        return 2;
    }
    std::setvbuf(stdout, nullptr, _IOLBF, 0);
    std::ifstream script(argv[1]);
    if (!script) {
        std::printf("ERROR cannot open script\n");
        return 2;
    }

    TH::Host host;
    OFX::Host::ImageEffect::PluginCache cache(host);
    cache.registerInCache(*OFX::Host::PluginCache::getPluginCache());
    std::unique_ptr<OFX::Host::ImageEffect::Instance> inst;
    TH::Effect* fx = nullptr;
    int failures = 0;

    std::string line;
    while (std::getline(script, line)) {
        const auto a = split(line);
        if (a.empty() || a[0][0] == '#') continue;
        try {
            if (a[0] == "load") {
                OFX::Host::PluginCache::getPluginCache()->addFileToPath(a.at(1));
                OFX::Host::PluginCache::getPluginCache()->scanPluginFiles();
                auto* plugin = cache.getPluginById(a.at(2));
                if (!plugin) {
                    std::printf("ERROR plugin %s not found\n", a.at(2).c_str());
                    return 1;
                }
                const std::string ctx = a.at(3) == "general" ? kOfxImageEffectContextGeneral : kOfxImageEffectContextFilter;
                inst.reset(plugin->createInstance(ctx, nullptr));
                fx = dynamic_cast<TH::Effect*>(inst.get());
                if (!fx) {
                    std::printf("ERROR createInstance failed\n");
                    return 1;
                }
                std::printf("LOADED %s %s\n", a[2].c_str(), ctx.c_str());
            } else if (a[0] == "clip") {
                TH::ClipSource s;
                s.w = std::stoi(a.at(2));
                s.h = std::stoi(a.at(3));
                s.first = std::stoi(a.at(4));
                s.last = std::stoi(a.at(5));
                s.pattern = a.at(6);
                fx->sources[a[1]] = s;
                if (a[1] == kOfxImageEffectSimpleSourceClipName) {
                    fx->projW = s.w;
                    fx->projH = s.h;
                }
            } else if (a[0] == "create") {
                OfxStatus st = fx->createInstanceAction();
                if (st != kOfxStatOK && st != kOfxStatReplyDefault) {
                    std::printf("ERROR createInstanceAction %d\n", st);
                    return 1;
                }
                fx->getClipPreferences();
                fx->created = true;
                std::printf("CREATED\n");
            } else if (a[0] == "time") {
                fx->currentTime = std::stod(a.at(1));
            } else if (a[0] == "describe") {
                for (auto& kv : fx->getParams()) {
                    auto& props = kv.second->getProperties();
                    std::string def;
                    const std::string& type = kv.second->getType();
                    if (type == kOfxParamTypeDouble || type == kOfxParamTypeDouble2D) {
                        def = std::to_string(props.getDoubleProperty(kOfxParamPropDefault));
                    } else if (type == kOfxParamTypeInteger || type == kOfxParamTypeChoice ||
                               type == kOfxParamTypeBoolean) {
                        def = std::to_string(props.getIntProperty(kOfxParamPropDefault));
                    }
                    std::printf("PARAM %s type=%s secret=%d parent=%s default=%s label=%s\n", kv.first.c_str(),
                                type.c_str(), props.getIntProperty(kOfxParamPropSecret),
                                props.getStringProperty(kOfxParamPropParent).c_str(), def.c_str(),
                                props.getStringProperty(kOfxPropLabel).c_str());
                }
            } else if (a[0] == "set") {
                auto* p = fx->getParam(a.at(1));
                if (!p) throw std::runtime_error("no param " + a[1]);
                if (!setParam(p, a, line)) throw std::runtime_error("cannot set " + a[1]);
                OfxPointD rs{fx->renderScale, fx->renderScale};
                fx->beginInstanceChangedAction(kOfxChangeUserEdited);
                fx->paramInstanceChangedAction(a[1], kOfxChangeUserEdited, fx->currentTime, rs);
                fx->endInstanceChangedAction(kOfxChangeUserEdited);
            } else if (a[0] == "press") {
                OfxPointD rs{fx->renderScale, fx->renderScale};
                fx->beginInstanceChangedAction(kOfxChangeUserEdited);
                OfxStatus st = fx->paramInstanceChangedAction(a.at(1), kOfxChangeUserEdited, fx->currentTime, rs);
                fx->endInstanceChangedAction(kOfxChangeUserEdited);
                std::printf("PRESSED %s %d\n", a[1].c_str(), st);
            } else if (a[0] == "get") {
                auto* p = fx->getParam(a.at(1));
                if (!p) throw std::runtime_error("no param " + a[1]);
                printParam(p, a[1]);
            } else if (a[0] == "frames") {
                OFX::Host::ImageEffect::RangeMap ranges;
                fx->getFrameNeededAction(std::stod(a.at(1)), ranges);
                for (auto& kv : ranges) {
                    std::printf("FRAMES %s", kv.first->getName().c_str());
                    for (auto& r : kv.second) std::printf(" %g:%g", r.min, r.max);
                    std::printf("\n");
                }
            } else if (a[0] == "identity") {
                double t = std::stod(a.at(1));
                std::string clip;
                const auto& s = fx->sources.at(kOfxImageEffectSimpleSourceClipName);
                OfxRectI win{0, 0, s.w, s.h};
                OfxPointD rs{1.0, 1.0};
                OfxStatus st = fx->isIdentityAction(t, kOfxImageFieldNone, win, rs, clip);
                std::printf("IDENTITY %s %s\n", st == kOfxStatOK ? "yes" : "no", clip.c_str());
            } else if (a[0] == "render") {
                const double t = std::stod(a.at(1));
                const double scale = a.size() > 3 ? std::stod(a[3]) : 1.0;
                fx->renderScale = scale;
                const auto& s = fx->sources.at(kOfxImageEffectSimpleSourceClipName);
                const int f = scale < 0.99 ? int(std::lround(1.0 / scale)) : 1;
                const int w = s.w / f, h = s.h / f;
                auto* out = dynamic_cast<TH::Clip*>(fx->getClip(kOfxImageEffectOutputClipName));
                out->prepareOutput(w, h, scale);
                auto* src = dynamic_cast<TH::Clip*>(fx->getClip(kOfxImageEffectSimpleSourceClipName));
                src->fetchLog.clear();
                OfxPointD rs{scale, scale};
                OfxRectI win{0, 0, w, h};
                fx->beginRenderAction(t, t, 1.0, false, rs, false, false);
                OfxStatus st = fx->renderAction(t, kOfxImageFieldNone, win, rs, false, false, false);
                fx->endRenderAction(t, t, 1.0, false, rs, false, false);
                fx->renderScale = 1.0;
                if (st != kOfxStatOK) {
                    std::printf("ERROR render status %d\n", st);
                    ++failures;
                    continue;
                }
                std::ofstream o(a.at(2), std::ios::binary);
                TH::FloatImage* img = out->output();
                o.write(reinterpret_cast<const char*>(img->data()),
                        std::streamsize(sizeof(float) * size_t(w) * size_t(h) * size_t(img->comps())));
                std::string fetched;
                for (auto& x : src->fetchLog) fetched += " " + x;
                std::printf("RENDERED %s %d %d %d fetched:%s\n", a[2].c_str(), w, h, img->comps(), fetched.c_str());
            } else {
                std::printf("ERROR unknown command %s\n", a[0].c_str());
                ++failures;
            }
        } catch (const std::exception& e) {
            std::printf("ERROR %s: %s\n", line.c_str(), e.what());
            ++failures;
        }
    }
    inst.reset();
    OFX::Host::PluginCache::clearPluginCache();
    return failures ? 1 : 0;
}
