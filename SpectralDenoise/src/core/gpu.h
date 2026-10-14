// GPU (OpenCL) version of one pyramid level of denoising. OpenCL is loaded
// at run time, so the plugin still loads where there is none; any failure
// makes the caller fall back to the CPU code.
#pragma once

#include "internal.h"

#include <string>

namespace sd {
namespace detail {

// Name of the OpenCL device that would be used, or empty with a reason.
bool gpuAvailable(std::string* deviceOrReason);

// Motion (if fr.size() > 1), cut detection and both filtering passes for one
// level, on the GPU. mf must have xs, ys and step set; on return it holds the
// motion (for the next, finer level). dropped is read and, at the coarsest
// level, written, like the CPU path. Returns false if the GPU can't be used
// or fails; out is then untouched.
bool gpuLevel(const std::vector<const Frame*>& fr, int ref, const LevelParams& lp, const MotionField* coarser,
              std::vector<bool>& dropped, double minMatchedFraction, bool twoPass, MotionField& mf, Frame& out,
              const AbortFn& abort);

}  // namespace detail
}  // namespace sd
