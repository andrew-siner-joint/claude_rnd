// Minimal stand-in for the BlinkScript language, used to compile and run
// BlinkFlare.blink outside Nuke for syntax checks and preview renders.
//
// Deliberately strict: only the subset of Blink the kernel is allowed to use
// is provided, and this header includes no system headers, so a call to a
// function Blink doesn't have (or a stray double literal, via -Werror flags)
// fails to compile here instead of in Nuke.
#pragma once

namespace blink {

// ------------------------------------------------------------------- vectors

struct int2 {
  int x, y;
  int2() : x(0), y(0) {}
  int2(int a, int b) : x(a), y(b) {}
};

struct float2 {
  float x, y;
  float2() : x(0.0f), y(0.0f) {}
  float2(float a, float b) : x(a), y(b) {}
};

struct float3 {
  float x, y, z;
  float3() : x(0.0f), y(0.0f), z(0.0f) {}
  float3(float a, float b, float c) : x(a), y(b), z(c) {}
};

struct float4 {
  float x, y, z, w;
  float4() : x(0.0f), y(0.0f), z(0.0f), w(0.0f) {}
  float4(float a, float b, float c, float d) : x(a), y(b), z(c), w(d) {}
};

#define BLINK_VEC_OPS2(T)                                                              \
  inline T operator+(T a, T b) { return T(a.x + b.x, a.y + b.y); }                     \
  inline T operator-(T a, T b) { return T(a.x - b.x, a.y - b.y); }                     \
  inline T operator*(T a, T b) { return T(a.x * b.x, a.y * b.y); }                     \
  inline T operator/(T a, T b) { return T(a.x / b.x, a.y / b.y); }                     \
  inline T operator*(T a, float s) { return T(a.x * s, a.y * s); }                     \
  inline T operator/(T a, float s) { return T(a.x / s, a.y / s); }                     \
  inline T& operator+=(T& a, T b) { a = a + b; return a; }                             \
  inline T& operator-=(T& a, T b) { a = a - b; return a; }                             \
  inline T& operator*=(T& a, float s) { a = a * s; return a; }

#define BLINK_VEC_OPS3(T)                                                              \
  inline T operator+(T a, T b) { return T(a.x + b.x, a.y + b.y, a.z + b.z); }          \
  inline T operator-(T a, T b) { return T(a.x - b.x, a.y - b.y, a.z - b.z); }          \
  inline T operator*(T a, T b) { return T(a.x * b.x, a.y * b.y, a.z * b.z); }          \
  inline T operator/(T a, T b) { return T(a.x / b.x, a.y / b.y, a.z / b.z); }          \
  inline T operator*(T a, float s) { return T(a.x * s, a.y * s, a.z * s); }            \
  inline T operator/(T a, float s) { return T(a.x / s, a.y / s, a.z / s); }            \
  inline T& operator+=(T& a, T b) { a = a + b; return a; }                             \
  inline T& operator-=(T& a, T b) { a = a - b; return a; }                             \
  inline T& operator*=(T& a, float s) { a = a * s; return a; }

#define BLINK_VEC_OPS4(T)                                                              \
  inline T operator+(T a, T b) { return T(a.x + b.x, a.y + b.y, a.z + b.z, a.w + b.w); } \
  inline T operator-(T a, T b) { return T(a.x - b.x, a.y - b.y, a.z - b.z, a.w - b.w); } \
  inline T operator*(T a, T b) { return T(a.x * b.x, a.y * b.y, a.z * b.z, a.w * b.w); } \
  inline T operator*(T a, float s) { return T(a.x * s, a.y * s, a.z * s, a.w * s); }   \
  inline T& operator+=(T& a, T b) { a = a + b; return a; }

BLINK_VEC_OPS2(float2)
BLINK_VEC_OPS3(float3)
BLINK_VEC_OPS4(float4)

// ----------------------------------------------------------------- functions

float sin(float);
float cos(float);
float tan(float);
float atan2(float, float);
float sqrt(float);
float exp(float);
float pow(float, float);
float floor(float);
float abs(float);
int abs(int);

inline float min(float a, float b) { return a < b ? a : b; }
inline float max(float a, float b) { return a > b ? a : b; }
inline int min(int a, int b) { return a < b ? a : b; }
inline int max(int a, int b) { return a > b ? a : b; }
inline float clamp(float x, float lo, float hi) { return min(max(x, lo), hi); }
inline int clamp(int x, int lo, int hi) { return min(max(x, lo), hi); }

inline float dot(float2 a, float2 b) { return a.x * b.x + a.y * b.y; }
inline float dot(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline float length(float2 a) { return sqrt(dot(a, a)); }
inline float length(float3 a) { return sqrt(dot(a, a)); }
inline float2 normalize(float2 a) { return a / length(a); }
inline float3 normalize(float3 a) { return a / length(a); }

// -------------------------------------------------------------------- images

enum { eRead = 1, eWrite, eReadWrite };
enum { eAccessPoint = 10, eAccessRandom, eAccessRanged1D, eAccessRanged2D };
enum { eEdgeNone = 20, eEdgeClamped, eEdgeConstant };
enum { ePixelWise = 30, eComponentWise };

// Current pixel for point-access images (set by the harness per pixel).
int2& currentPos();

struct ImageData {
  float4* pixels = nullptr;
  int width = 0;
  int height = 0;
};

template <int Access, int Mode = eAccessPoint, int Edge = eEdgeNone>
struct Image {
  ImageData data;

  float4 fetch(int x, int y) const {
    if (x < 0 || y < 0 || x >= data.width || y >= data.height) {
      if (Edge == eEdgeConstant || data.pixels == nullptr) {
        return float4();
      }
      x = clamp(x, 0, data.width - 1);
      y = clamp(y, 0, data.height - 1);
    }
    return data.pixels[y * data.width + x];
  }

  // Point access: value at the pixel being processed.
  float4 operator()() const {
    static_assert(Mode == eAccessPoint, "point access requires eAccessPoint");
    static_assert(Access != eWrite, "cannot read a write-only image");
    return fetch(currentPos().x, currentPos().y);
  }

  // Random access.
  float4 operator()(int x, int y) const {
    static_assert(Mode == eAccessRandom, "random access requires eAccessRandom");
    return fetch(x, y);
  }
};

template <>
struct Image<eWrite, eAccessPoint, eEdgeNone> {
  ImageData data;
  float4& operator()() {
    return data.pixels[currentPos().y * data.width + currentPos().x];
  }
};

template <int A, int M, int E>
float4 bilinear(const Image<A, M, E>& img, float x, float y) {
  static_assert(M == eAccessRandom, "bilinear requires eAccessRandom");
  float fx = floor(x);
  float fy = floor(y);
  int ix = static_cast<int>(fx);
  int iy = static_cast<int>(fy);
  float tx = x - fx;
  float ty = y - fy;
  float4 a = img.fetch(ix, iy) * (1.0f - tx) + img.fetch(ix + 1, iy) * tx;
  float4 b = img.fetch(ix, iy + 1) * (1.0f - tx) + img.fetch(ix + 1, iy + 1) * tx;
  return a * (1.0f - ty) + b * ty;
}

// -------------------------------------------------------------------- params

enum ParamType { kFloat, kFloat2, kFloat3, kFloat4, kInt, kBool };

void registerParam(const char* name, void* ptr, ParamType type);

template <class T> struct ParamTypeOf;
template <> struct ParamTypeOf<float>  { static const ParamType value = kFloat; };
template <> struct ParamTypeOf<float2> { static const ParamType value = kFloat2; };
template <> struct ParamTypeOf<float3> { static const ParamType value = kFloat3; };
template <> struct ParamTypeOf<float4> { static const ParamType value = kFloat4; };
template <> struct ParamTypeOf<int>    { static const ParamType value = kInt; };
template <> struct ParamTypeOf<bool>   { static const ParamType value = kBool; };

template <int Granularity>
struct ImageComputationKernel {
  template <class T>
  void defineParam(T& param, const char* name, T defaultValue) {
    param = defaultValue;
    registerParam(name, &param, ParamTypeOf<T>::value);
  }
};

}  // namespace blink

#define kernel struct
#define param public
#define local public
