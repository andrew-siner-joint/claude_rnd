// Tables, pyramid, colour conversion and profile (de)serialisation.
#include "internal.h"

#include <cstdio>
#include <locale>
#include <sstream>

namespace sd {
namespace detail {

namespace {
double besselI0(double x)
{
    double sum = 1.0, term = 1.0;
    for (int k = 1; k < 50; ++k) {
        term *= (x / (2.0 * k)) * (x / (2.0 * k));
        sum += term;
    }
    return sum;
}
}  // namespace

DctTables::DctTables()
{
    const double pi = 3.14159265358979323846;
    for (int k = 0; k < kBlock; ++k) {
        const double a = k == 0 ? std::sqrt(1.0 / kBlock) : std::sqrt(2.0 / kBlock);
        for (int n = 0; n < kBlock; ++n)
            c8[k][n] = static_cast<float>(a * std::cos(pi * (2 * n + 1) * k / (2.0 * kBlock)));
    }
    for (auto& a : t)
        for (auto& b : a)
            for (auto& c : b) c = 0.0f;
    for (int n = 1; n <= 2 * kMaxTemporalRadius + 1; ++n)
        for (int k = 0; k < n; ++k) {
            const double a = k == 0 ? std::sqrt(1.0 / n) : std::sqrt(2.0 / n);
            for (int i = 0; i < n; ++i)
                t[n][k][i] = static_cast<float>(a * std::cos(pi * (2 * i + 1) * k / (2.0 * n)));
        }
    const double beta = 2.0;
    double w1[kBlock];
    for (int i = 0; i < kBlock; ++i) {
        const double r = 2.0 * i / (kBlock - 1) - 1.0;
        w1[i] = besselI0(beta * std::sqrt(std::max(0.0, 1.0 - r * r))) / besselI0(beta);
    }
    for (int y = 0; y < kBlock; ++y)
        for (int x = 0; x < kBlock; ++x) kaiser[y * kBlock + x] = static_cast<float>(w1[y] * w1[x]);
}

const DctTables& tables()
{
    static const DctTables t;
    return t;
}

void blur5(const Plane& src, Plane& dst)
{
    const int w = src.w, h = src.h;
    Plane tmp;
    tmp.resize(w, h);
    for (int y = 0; y < h; ++y) {
        const float* s = src.row(y);
        float* o = tmp.row(y);
        for (int x = 0; x < w; ++x) {
            o[x] = (s[mirror(x - 2, w)] + s[mirror(x + 2, w)] + 4.0f * (s[mirror(x - 1, w)] + s[mirror(x + 1, w)]) +
                    6.0f * s[x]) *
                   (1.0f / 16.0f);
        }
    }
    dst.resize(w, h);
    for (int y = 0; y < h; ++y) {
        const float* a = tmp.row(mirror(y - 2, h));
        const float* b = tmp.row(mirror(y - 1, h));
        const float* c = tmp.row(y);
        const float* d = tmp.row(mirror(y + 1, h));
        const float* e = tmp.row(mirror(y + 2, h));
        float* o = dst.row(y);
        for (int x = 0; x < w; ++x) o[x] = (a[x] + e[x] + 4.0f * (b[x] + d[x]) + 6.0f * c[x]) * (1.0f / 16.0f);
    }
}

void downsample(const Plane& src, Plane& dst)
{
    const int w = src.w, h = src.h;
    const int dw = (w + 1) / 2, dh = (h + 1) / 2;
    // horizontal filter + decimate
    Plane tmp;
    tmp.resize(dw, h);
    for (int y = 0; y < h; ++y) {
        const float* s = src.row(y);
        float* o = tmp.row(y);
        for (int x = 0; x < dw; ++x) {
            const int c = 2 * x;
            o[x] = (s[mirror(c - 2, w)] + s[mirror(c + 2, w)] + 4.0f * (s[mirror(c - 1, w)] + s[mirror(c + 1, w)]) +
                    6.0f * s[c]) *
                   (1.0f / 16.0f);
        }
    }
    dst.resize(dw, dh);
    for (int y = 0; y < dh; ++y) {
        const int c = 2 * y;
        const float* a = tmp.row(mirror(c - 2, h));
        const float* b = tmp.row(mirror(c - 1, h));
        const float* m = tmp.row(c);
        const float* d = tmp.row(mirror(c + 1, h));
        const float* e = tmp.row(mirror(c + 2, h));
        float* o = dst.row(y);
        for (int x = 0; x < dw; ++x) o[x] = (a[x] + e[x] + 4.0f * (b[x] + d[x]) + 6.0f * m[x]) * (1.0f / 16.0f);
    }
}

void upsample(const Plane& src, int w, int h, Plane& dst)
{
    const int sw = src.w, sh = src.h;
    // horizontal expand: even x = (s[m-1] + 6 s[m] + s[m+1]) / 8, odd x = (s[m] + s[m+1]) / 2
    Plane tmp;
    tmp.resize(w, sh);
    for (int y = 0; y < sh; ++y) {
        const float* s = src.row(y);
        float* o = tmp.row(y);
        for (int x = 0; x < w; ++x) {
            const int m = x >> 1;
            if ((x & 1) == 0)
                o[x] = (s[mirror(m - 1, sw)] + 6.0f * s[mirror(m, sw)] + s[mirror(m + 1, sw)]) * 0.125f;
            else
                o[x] = (s[mirror(m, sw)] + s[mirror(m + 1, sw)]) * 0.5f;
        }
    }
    dst.resize(w, h);
    for (int y = 0; y < h; ++y) {
        const int m = y >> 1;
        float* o = dst.row(y);
        if ((y & 1) == 0) {
            const float* a = tmp.row(mirror(m - 1, sh));
            const float* b = tmp.row(mirror(m, sh));
            const float* c = tmp.row(mirror(m + 1, sh));
            for (int x = 0; x < w; ++x) o[x] = (a[x] + 6.0f * b[x] + c[x]) * 0.125f;
        } else {
            const float* a = tmp.row(mirror(m, sh));
            const float* b = tmp.row(mirror(m + 1, sh));
            for (int x = 0; x < w; ++x) o[x] = (a[x] + b[x]) * 0.5f;
        }
    }
}

int threadCount(int requested)
{
    if (requested > 0) return requested;
    const unsigned n = std::thread::hardware_concurrency();
    return n == 0 ? 4 : static_cast<int>(std::min(n, 64u));
}

std::vector<int> blockGrid(int n, int step)
{
    std::vector<int> g;
    const int last = n - kBlock;
    if (last < 0) return g;
    for (int p = 0; p < last; p += step) g.push_back(p);
    g.push_back(last);
    return g;
}

}  // namespace detail

using namespace detail;

// --- colour --------------------------------------------------------------

namespace {
constexpr float kLinKnee = 0.05f;
}

float toWorkingValue(float v, Space space)
{
    if (space == Space::Video) return v;
    return std::asinh(v / kLinKnee);
}

float fromWorkingValue(float v, Space space)
{
    if (space == Space::Video) return v;
    return std::sinh(v) * kLinKnee;
}

void toWorking(const ImageView& src, Space space, Frame& out)
{
    for (auto& p : out.c) p.resize(src.w, src.h);
    for (int y = 0; y < src.h; ++y) {
        const float* s = src.data + y * src.rowStride;
        float* py = out.c[0].row(y);
        float* pu = out.c[1].row(y);
        float* pv = out.c[2].row(y);
        for (int x = 0; x < src.w; ++x) {
            float r = s[0], g = s[1], b = s[2];
            if (!std::isfinite(r)) r = 0.0f;
            if (!std::isfinite(g)) g = 0.0f;
            if (!std::isfinite(b)) b = 0.0f;
            rgbToYuv(toWorkingValue(r, space), toWorkingValue(g, space), toWorkingValue(b, space), py[x], pu[x], pv[x]);
            s += src.comps;
        }
    }
}

void fromWorking(const Frame& src, Space space, float* dst, std::ptrdiff_t rowStride, int comps)
{
    for (int y = 0; y < src.h(); ++y) {
        float* o = dst + y * rowStride;
        const float* py = src.c[0].row(y);
        const float* pu = src.c[1].row(y);
        const float* pv = src.c[2].row(y);
        for (int x = 0; x < src.w(); ++x) {
            float r, g, b;
            yuvToRgb(py[x], pu[x], pv[x], r, g, b);
            o[0] = fromWorkingValue(r, space);
            o[1] = fromWorkingValue(g, space);
            o[2] = fromWorkingValue(b, space);
            o += comps;
        }
    }
}

int scalesForSize(int w, int h)
{
    int s = 1;
    int m = std::min(w, h);
    while (s < kMaxScales && (m + 1) / 2 >= 24) {
        m = (m + 1) / 2;
        ++s;
    }
    return s;
}

// --- profile ---------------------------------------------------------------

float NoiseProfile::pixelSigma(int scale, int ch) const
{
    scale = std::max(0, std::min(scale, scales - 1));
    double s = 0.0;
    for (int k = 0; k < kCoefs; ++k) s += double(sigma[scale][ch][k]) * sigma[scale][ch][k];
    return static_cast<float>(std::sqrt(s / kCoefs));
}

float NoiseProfile::lumaFactor(int ch, float luma) const
{
    const float span = lumaHi - lumaLo;
    if (!(span > 0.0f)) return lumaCurve[ch][kLumaBins / 2];
    // bin centres at lo + (b + 0.5) * span / bins
    float p = (luma - lumaLo) / span * kLumaBins - 0.5f;
    if (p <= 0.0f) return lumaCurve[ch][0];
    if (p >= kLumaBins - 1) return lumaCurve[ch][kLumaBins - 1];
    const int i = static_cast<int>(p);
    const float f = p - static_cast<float>(i);
    return lumaCurve[ch][i] * (1.0f - f) + lumaCurve[ch][i + 1] * f;
}

std::string NoiseProfile::serialize() const
{
    if (!valid) return std::string();
    std::ostringstream os;
    os.imbue(std::locale::classic());
    os.precision(9);  // floats round-trip exactly
    os << "SDP1 " << int(space) << ' ' << scales << ' ' << lumaLo << ' ' << lumaHi << ' ' << fromRegion << ' '
       << region.x1 << ' ' << region.y1 << ' ' << region.x2 << ' ' << region.y2 << ' ' << frame << ' ' << imageW << ' '
       << imageH;
    for (int c = 0; c < 3; ++c)
        for (int b = 0; b < kLumaBins; ++b) os << ' ' << lumaCurve[c][b];
    for (int s = 0; s < scales; ++s)
        for (int c = 0; c < 3; ++c)
            for (int k = 0; k < kCoefs; ++k) os << ' ' << sigma[s][c][k];
    return os.str();
}

bool NoiseProfile::parse(const std::string& text)
{
    *this = NoiseProfile();
    std::istringstream is(text);
    is.imbue(std::locale::classic());
    std::string tag;
    if (!(is >> tag) || tag != "SDP1") return false;
    int sp = 0;
    NoiseProfile p;
    if (!(is >> sp >> p.scales >> p.lumaLo >> p.lumaHi >> p.fromRegion >> p.region.x1 >> p.region.y1 >> p.region.x2 >>
          p.region.y2 >> p.frame >> p.imageW >> p.imageH))
        return false;
    if (sp < 0 || sp > 1 || p.scales < 1 || p.scales > kMaxScales) return false;
    p.space = static_cast<Space>(sp);
    for (int c = 0; c < 3; ++c)
        for (int b = 0; b < kLumaBins; ++b)
            if (!(is >> p.lumaCurve[c][b]) || !std::isfinite(p.lumaCurve[c][b]) || p.lumaCurve[c][b] < 0.0f)
                return false;
    for (int s = 0; s < p.scales; ++s)
        for (int c = 0; c < 3; ++c)
            for (int k = 0; k < kCoefs; ++k)
                if (!(is >> p.sigma[s][c][k]) || !std::isfinite(p.sigma[s][c][k]) || p.sigma[s][c][k] < 0.0f)
                    return false;
    p.valid = true;
    *this = p;
    return true;
}

std::string NoiseProfile::summary() const
{
    if (!valid) return "No profile";
    char buf[256];
    std::snprintf(buf, sizeof buf, "%s, frame %g: luma noise %.4f, chroma %.4f (%d levels)",
                  fromRegion ? "Region" : "Auto", frame, pixelSigma(0, 0),
                  0.5f * (pixelSigma(0, 1) + pixelSigma(0, 2)), scales);
    return buf;
}

}  // namespace sd
