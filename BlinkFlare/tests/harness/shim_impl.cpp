// Runtime pieces of the Blink shim: math functions and the param registry.
#include <cmath>
#include <cstdlib>
#include <string>
#include <vector>

#include "blink_shim.h"
#include "kernel_api.h"

namespace blink {

float sin(float x) { return std::sin(x); }
float cos(float x) { return std::cos(x); }
float tan(float x) { return std::tan(x); }
float atan2(float y, float x) { return std::atan2(y, x); }
float sqrt(float x) { return std::sqrt(x); }
float exp(float x) { return std::exp(x); }
float pow(float x, float y) { return std::pow(x, y); }
float floor(float x) { return std::floor(x); }
float abs(float x) { return std::fabs(x); }
int abs(int x) { return std::abs(x); }

int2& currentPos() {
  thread_local int2 pos;
  return pos;
}

struct Entry {
  std::string name;
  void* ptr;
  ParamType type;
};

static std::vector<Entry>& registry() {
  static std::vector<Entry> entries;
  return entries;
}

void registerParam(const char* name, void* ptr, ParamType type) {
  registry().push_back(Entry{name, ptr, type});
}

}  // namespace blink

static int components(blink::ParamType t) {
  switch (t) {
    case blink::kFloat2: return 2;
    case blink::kFloat3: return 3;
    case blink::kFloat4: return 4;
    default: return 1;
  }
}

int bf_param_count() { return static_cast<int>(blink::registry().size()); }
const char* bf_param_name(int i) { return blink::registry()[static_cast<size_t>(i)].name.c_str(); }
int bf_param_components(int i) { return components(blink::registry()[static_cast<size_t>(i)].type); }

bool bf_set_param(const char* name, const double* v, int n) {
  for (auto& e : blink::registry()) {
    if (e.name != name) continue;
    if (n != components(e.type)) return false;
    float* f = static_cast<float*>(e.ptr);
    switch (e.type) {
      case blink::kInt: *static_cast<int*>(e.ptr) = static_cast<int>(std::lround(v[0])); break;
      case blink::kBool: *static_cast<bool*>(e.ptr) = v[0] != 0.0; break;
      default:
        for (int i = 0; i < n; i++) f[i] = static_cast<float>(v[i]);
    }
    return true;
  }
  return false;
}
