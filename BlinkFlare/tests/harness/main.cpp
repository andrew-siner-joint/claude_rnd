// Harness driver: renders BlinkFlare.blink on the CPU.
//
//   blinkflare_render --list-params
//   blinkflare_render W H params.txt out.raw [--canvas f] [--occlusion f] [--dirt f]
//
// params.txt: one "name v0 [v1 ...]" per line. Images are raw float32 RGBA,
// W*H*4 floats, row 0 at the bottom.
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "kernel_api.h"

static bool loadRaw(const std::string& path, std::vector<float>& buf) {
  std::ifstream f(path, std::ios::binary);
  if (!f) return false;
  f.read(reinterpret_cast<char*>(buf.data()), static_cast<std::streamsize>(buf.size() * sizeof(float)));
  return static_cast<size_t>(f.gcount()) == buf.size() * sizeof(float);
}

int main(int argc, char** argv) {
  if (argc >= 2 && std::strcmp(argv[1], "--list-params") == 0) {
    std::vector<float> dummy(4, 0.0f);
    BfImage im{dummy.data(), 1, 1};
    bf_create(im, im, im, im);
    for (int i = 0; i < bf_param_count(); i++) {
      std::printf("%s %d\n", bf_param_name(i), bf_param_components(i));
    }
    return 0;
  }
  if (argc < 5) {
    std::fprintf(stderr, "usage: %s W H params.txt out.raw [--canvas f] [--occlusion f] [--dirt f]\n", argv[0]);
    return 2;
  }
  int w = std::atoi(argv[1]);
  int h = std::atoi(argv[2]);
  size_t n = static_cast<size_t>(w) * static_cast<size_t>(h) * 4;
  std::vector<float> canvas(n, 0.0f), occlusion(n, 0.0f), dirt(n, 0.0f), out(n, 0.0f);

  for (int i = 5; i + 1 < argc; i += 2) {
    std::string flag = argv[i];
    std::vector<float>* target = flag == "--canvas" ? &canvas
                               : flag == "--occlusion" ? &occlusion
                               : flag == "--dirt" ? &dirt : nullptr;
    if (!target || !loadRaw(argv[i + 1], *target)) {
      std::fprintf(stderr, "bad input %s %s\n", argv[i], argv[i + 1]);
      return 2;
    }
  }

  bf_create(BfImage{canvas.data(), w, h}, BfImage{occlusion.data(), w, h},
            BfImage{dirt.data(), w, h}, BfImage{out.data(), w, h});

  std::ifstream pf(argv[3]);
  std::string line;
  while (std::getline(pf, line)) {
    std::istringstream ss(line);
    std::string name;
    if (!(ss >> name) || name[0] == '#') continue;
    std::vector<double> vals;
    double v;
    while (ss >> v) vals.push_back(v);
    if (!bf_set_param(name.c_str(), vals.data(), static_cast<int>(vals.size()))) {
      std::fprintf(stderr, "unknown param or wrong arity: %s\n", name.c_str());
      return 3;
    }
  }

  auto t0 = std::chrono::steady_clock::now();
  bf_init();
  unsigned threads = std::thread::hardware_concurrency();
  if (threads == 0) threads = 4;
  std::vector<std::thread> pool;
  for (unsigned t = 0; t < threads; t++) {
    int y0 = static_cast<int>(static_cast<long>(h) * t / threads);
    int y1 = static_cast<int>(static_cast<long>(h) * (t + 1) / threads);
    pool.emplace_back([y0, y1] { bf_process_rows(y0, y1); });
  }
  for (auto& th : pool) th.join();
  auto t1 = std::chrono::steady_clock::now();
  double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
  std::fprintf(stderr, "rendered %dx%d in %.1f ms on %u threads\n", w, h, ms, threads);

  std::ofstream of(argv[4], std::ios::binary);
  of.write(reinterpret_cast<const char*>(out.data()), static_cast<std::streamsize>(n * sizeof(float)));
  return 0;
}
