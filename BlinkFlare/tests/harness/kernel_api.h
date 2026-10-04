// C-style bridge between the kernel translation unit (which must not see any
// system headers) and the harness driver.
#pragma once

struct BfImage {
  float* rgba;  // width * height * 4 floats, row 0 = bottom (Nuke y-up)
  int width;
  int height;
};

void bf_create(BfImage src, BfImage occlusion, BfImage dirt, BfImage elements, BfImage out);
void bf_init();
void bf_process_rows(int y0, int y1);

// Param registry (filled by defineParam during bf_create).
int bf_param_count();
const char* bf_param_name(int index);
int bf_param_components(int index);
bool bf_set_param(const char* name, const double* values, int count);
