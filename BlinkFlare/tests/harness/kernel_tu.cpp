// Compiles the real BlinkFlare.blink source against the Blink shim.
#include "blink_shim.h"

namespace blink {
#include KERNEL_SOURCE
}

#include "kernel_api.h"

static blink::KERNEL_CLASS* g_kernel = nullptr;

static blink::ImageData wrap(BfImage im) {
  blink::ImageData d;
  d.pixels = reinterpret_cast<blink::float4*>(im.rgba);
  d.width = im.width;
  d.height = im.height;
  return d;
}

void bf_create(BfImage canvas, BfImage occlusion, BfImage dirt, BfImage out) {
  g_kernel = new blink::KERNEL_CLASS();
  g_kernel->canvas.data = wrap(canvas);
  g_kernel->occlusion.data = wrap(occlusion);
  g_kernel->dirt.data = wrap(dirt);
  g_kernel->dst.data = wrap(out);
  g_kernel->define();
}

void bf_init() { g_kernel->init(); }

void bf_process_rows(int y0, int y1) {
  int w = g_kernel->dst.data.width;
  for (int y = y0; y < y1; y++) {
    for (int x = 0; x < w; x++) {
      blink::currentPos() = blink::int2(x, y);
      g_kernel->process(blink::int2(x, y));
    }
  }
}
