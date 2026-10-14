// OpenCL runtime, loaded dynamically, and the host side of the GPU path.
#include "gpu.h"

#include "kernels_cl.h"

#define CL_TARGET_OPENCL_VERSION 120
#ifdef __APPLE__
#include <OpenCL/cl.h>
#else
#include <CL/cl.h>
#endif

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace sd {
namespace detail {

namespace {

// --- dynamic loading -----------------------------------------------------------

struct ClApi {
#define SD_CL_FN(name) decltype(&::name) name = nullptr;
    SD_CL_FN(clGetPlatformIDs)
    SD_CL_FN(clGetPlatformInfo)
    SD_CL_FN(clGetDeviceIDs)
    SD_CL_FN(clGetDeviceInfo)
    SD_CL_FN(clCreateContext)
    SD_CL_FN(clCreateCommandQueue)
    SD_CL_FN(clCreateProgramWithSource)
    SD_CL_FN(clBuildProgram)
    SD_CL_FN(clGetProgramBuildInfo)
    SD_CL_FN(clCreateKernel)
    SD_CL_FN(clGetKernelWorkGroupInfo)
    SD_CL_FN(clSetKernelArg)
    SD_CL_FN(clCreateBuffer)
    SD_CL_FN(clEnqueueWriteBuffer)
    SD_CL_FN(clEnqueueReadBuffer)
    SD_CL_FN(clEnqueueFillBuffer)
    SD_CL_FN(clEnqueueNDRangeKernel)
    SD_CL_FN(clFinish)
    SD_CL_FN(clReleaseMemObject)
    SD_CL_FN(clReleaseKernel)
    SD_CL_FN(clReleaseProgram)
    SD_CL_FN(clReleaseCommandQueue)
    SD_CL_FN(clReleaseContext)
#undef SD_CL_FN

    bool load(std::string& why)
    {
#if defined(_WIN32)
        HMODULE lib = LoadLibraryA("OpenCL.dll");
        auto sym = [&](const char* n) { return lib ? reinterpret_cast<void*>(GetProcAddress(lib, n)) : nullptr; };
#else
        void* lib = nullptr;
#if defined(__APPLE__)
        const char* names[] = {"/System/Library/Frameworks/OpenCL.framework/OpenCL"};
#else
        const char* names[] = {"libOpenCL.so.1", "libOpenCL.so"};
#endif
        for (const char* n : names)
            if (!lib) lib = dlopen(n, RTLD_NOW | RTLD_LOCAL);
        auto sym = [&](const char* n) { return lib ? dlsym(lib, n) : nullptr; };
#endif
        if (!lib) {
            why = "no OpenCL driver found";
            return false;
        }
        bool ok = true;
#define SD_CL_LOAD(name)                                            \
    name = reinterpret_cast<decltype(name)>(sym(#name));            \
    if (!name) {                                                    \
        ok = false;                                                 \
        why = "OpenCL driver lacks " #name;                         \
    }
        SD_CL_LOAD(clGetPlatformIDs)
        SD_CL_LOAD(clGetPlatformInfo)
        SD_CL_LOAD(clGetDeviceIDs)
        SD_CL_LOAD(clGetDeviceInfo)
        SD_CL_LOAD(clCreateContext)
        SD_CL_LOAD(clCreateCommandQueue)
        SD_CL_LOAD(clCreateProgramWithSource)
        SD_CL_LOAD(clBuildProgram)
        SD_CL_LOAD(clGetProgramBuildInfo)
        SD_CL_LOAD(clCreateKernel)
        SD_CL_LOAD(clGetKernelWorkGroupInfo)
        SD_CL_LOAD(clSetKernelArg)
        SD_CL_LOAD(clCreateBuffer)
        SD_CL_LOAD(clEnqueueWriteBuffer)
        SD_CL_LOAD(clEnqueueReadBuffer)
        SD_CL_LOAD(clEnqueueFillBuffer)
        SD_CL_LOAD(clEnqueueNDRangeKernel)
        SD_CL_LOAD(clFinish)
        SD_CL_LOAD(clReleaseMemObject)
        SD_CL_LOAD(clReleaseKernel)
        SD_CL_LOAD(clReleaseProgram)
        SD_CL_LOAD(clReleaseCommandQueue)
        SD_CL_LOAD(clReleaseContext)
#undef SD_CL_LOAD
        return ok;
    }
};

// --- runtime: one device, context, queue and program for the process ----------

struct Runtime {
    ClApi cl;
    bool ok = false;
    std::string device;  // name, or the reason there is none
    cl_context ctx = nullptr;
    cl_command_queue queue = nullptr;
    cl_program program = nullptr;
    cl_kernel kMotion = nullptr, kFilter = nullptr, kResolve = nullptr;
    std::mutex use;  // one level on the GPU at a time

    Runtime() { ok = init(); }

    bool init()
    {
        // SD_OPENCL=off disables the GPU; SD_OPENCL=any also accepts CPU
        // OpenCL devices (used by the tests).
        const char* env = std::getenv("SD_OPENCL");
        const std::string mode = env ? env : "";
        if (mode == "off") {
            device = "disabled by SD_OPENCL=off";
            return false;
        }
        if (!cl.load(device)) return false;
        cl_uint np = 0;
        if (cl.clGetPlatformIDs(0, nullptr, &np) != CL_SUCCESS || np == 0) {
            device = "no OpenCL platform";
            return false;
        }
        std::vector<cl_platform_id> plats(np);
        cl.clGetPlatformIDs(np, plats.data(), nullptr);
        const cl_device_type want = mode == "any" ? CL_DEVICE_TYPE_ALL : CL_DEVICE_TYPE_GPU;
        cl_device_id best = nullptr;
        cl_uint bestUnits = 0;
        for (cl_platform_id p : plats) {
            cl_uint nd = 0;
            if (cl.clGetDeviceIDs(p, want, 0, nullptr, &nd) != CL_SUCCESS || nd == 0) continue;
            std::vector<cl_device_id> devs(nd);
            cl.clGetDeviceIDs(p, want, nd, devs.data(), nullptr);
            for (cl_device_id d : devs) {
                cl_uint units = 0;
                size_t wg = 0;
                cl_bool avail = CL_FALSE;
                cl.clGetDeviceInfo(d, CL_DEVICE_MAX_COMPUTE_UNITS, sizeof units, &units, nullptr);
                cl.clGetDeviceInfo(d, CL_DEVICE_MAX_WORK_GROUP_SIZE, sizeof wg, &wg, nullptr);
                cl.clGetDeviceInfo(d, CL_DEVICE_AVAILABLE, sizeof avail, &avail, nullptr);
                if (!avail || wg < 64) continue;
                if (!best || units > bestUnits) {
                    best = d;
                    bestUnits = units;
                }
            }
        }
        if (!best) {
            device = "no OpenCL GPU";
            return false;
        }
        char name[256] = {0};
        cl.clGetDeviceInfo(best, CL_DEVICE_NAME, sizeof name - 1, name, nullptr);
        cl_int err = CL_SUCCESS;
        ctx = cl.clCreateContext(nullptr, 1, &best, nullptr, nullptr, &err);
        if (err != CL_SUCCESS) return fail("could not create an OpenCL context");
        queue = cl.clCreateCommandQueue(ctx, best, 0, &err);
        if (err != CL_SUCCESS) return fail("could not create an OpenCL queue");
        const cl_uint pieces = sizeof kKernelSource / sizeof kKernelSource[0];
        std::vector<const char*> src(kKernelSource, kKernelSource + pieces);
        program = cl.clCreateProgramWithSource(ctx, pieces, src.data(), nullptr, &err);
        if (err != CL_SUCCESS) return fail("could not create the OpenCL program");
        if (cl.clBuildProgram(program, 1, &best, "-cl-fp32-correctly-rounded-divide-sqrt", nullptr, nullptr) !=
                CL_SUCCESS &&
            cl.clBuildProgram(program, 1, &best, "", nullptr, nullptr) != CL_SUCCESS) {
            size_t len = 0;
            cl.clGetProgramBuildInfo(program, best, CL_PROGRAM_BUILD_LOG, 0, nullptr, &len);
            std::string log(len, '\0');
            cl.clGetProgramBuildInfo(program, best, CL_PROGRAM_BUILD_LOG, len, &log[0], nullptr);
            std::fprintf(stderr, "SpectralDenoise: OpenCL build failed on %s:\n%s\n", name, log.c_str());
            return fail("the GPU kernels did not compile");
        }
        kMotion = cl.clCreateKernel(program, "motion", &err);
        if (err == CL_SUCCESS) kFilter = cl.clCreateKernel(program, "filterPass", &err);
        if (err == CL_SUCCESS) kResolve = cl.clCreateKernel(program, "resolve", &err);
        if (err != CL_SUCCESS) return fail("could not create the GPU kernels");
        size_t kwg = 0;
        cl.clGetKernelWorkGroupInfo(kFilter, best, CL_KERNEL_WORK_GROUP_SIZE, sizeof kwg, &kwg, nullptr);
        if (kwg < 64) return fail("the GPU can't run the filter kernel");
        device = name;
        return true;
    }

    bool fail(const char* why)
    {
        device = why;
        return false;
    }
};

Runtime& runtime()
{
    static Runtime rt;
    return rt;
}

// Owns a cl_mem.
struct Buf {
    const ClApi* cl = nullptr;
    cl_mem m = nullptr;
    Buf() = default;
    Buf(const Buf&) = delete;
    Buf& operator=(const Buf&) = delete;
    ~Buf()
    {
        if (m) cl->clReleaseMemObject(m);
    }
    bool create(Runtime& rt, size_t bytes, cl_mem_flags flags = CL_MEM_READ_WRITE)
    {
        cl = &rt.cl;
        cl_int err = CL_SUCCESS;
        m = rt.cl.clCreateBuffer(rt.ctx, flags, std::max<size_t>(bytes, 16), nullptr, &err);
        return err == CL_SUCCESS;
    }
    bool write(Runtime& rt, const void* data, size_t bytes, size_t offset = 0)
    {
        return bytes == 0 ||
               rt.cl.clEnqueueWriteBuffer(rt.queue, m, CL_FALSE, offset, bytes, data, 0, nullptr, nullptr) ==
                   CL_SUCCESS;
    }
    bool read(Runtime& rt, void* data, size_t bytes, size_t offset = 0)
    {
        return rt.cl.clEnqueueReadBuffer(rt.queue, m, CL_TRUE, offset, bytes, data, 0, nullptr, nullptr) ==
               CL_SUCCESS;
    }
    bool zero(Runtime& rt, size_t bytes)
    {
        const float z = 0.0f;
        return rt.cl.clEnqueueFillBuffer(rt.queue, m, &z, sizeof z, 0, bytes, 0, nullptr, nullptr) == CL_SUCCESS;
    }
};

// Must match LevelInfo in the kernel source.
struct LevelInfo {
    cl_int w, h, nx, ny, slots, ref, lumaAdaptive;
    cl_float lumaLo, lumaHi, matchVar, matchDcSigma, tolerance;
    cl_int coarsest, cnx, cny, cstep;
};

template <class T>
bool setArg(Runtime& rt, cl_kernel k, cl_uint i, const T& v)
{
    return rt.cl.clSetKernelArg(k, i, sizeof(T), &v) == CL_SUCCESS;
}

void packMotion(const MotionField& mf, std::vector<cl_int>& out)
{
    out.resize(mf.m.size() * 4);
    for (size_t i = 0; i < mf.m.size(); ++i) {
        out[i * 4 + 0] = mf.m[i].dx;
        out[i * 4 + 1] = mf.m[i].dy;
        out[i * 4 + 2] = mf.m[i].ok ? 1 : 0;
        out[i * 4 + 3] = 0;
    }
}

}  // namespace

bool gpuAvailable(std::string* deviceOrReason)
{
    Runtime& rt = runtime();
    if (deviceOrReason) *deviceOrReason = rt.device;
    return rt.ok;
}

bool gpuLevel(const std::vector<const Frame*>& fr, int ref, const LevelParams& lp, const MotionField* coarser,
              std::vector<bool>& dropped, double minMatchedFraction, bool twoPass, MotionField& mf, Frame& out,
              const AbortFn& abort)
{
    Runtime& rt = runtime();
    if (!rt.ok) return false;
    std::lock_guard<std::mutex> lock(rt.use);

    const int nf = static_cast<int>(fr.size());
    const int w = fr[size_t(ref)]->w(), h = fr[size_t(ref)]->h();
    const size_t plane = size_t(w) * size_t(h);
    const int nx = static_cast<int>(mf.xs.size()), ny = static_cast<int>(mf.ys.size());
    const size_t nb = size_t(nx) * size_t(ny);

    LevelInfo L{};
    L.w = w;
    L.h = h;
    L.nx = nx;
    L.ny = ny;
    L.slots = nf;
    L.ref = ref;
    L.lumaAdaptive = lp.lumaAdaptive ? 1 : 0;
    L.lumaLo = lp.prof->lumaLo;
    L.lumaHi = lp.prof->lumaHi;
    L.matchVar = lp.matchVar;
    L.matchDcSigma = lp.matchDcSigma;
    L.tolerance = lp.tolerance;
    L.coarsest = coarser ? 0 : 1;
    L.cnx = coarser ? static_cast<int>(coarser->xs.size()) : 1;
    L.cny = coarser ? static_cast<int>(coarser->ys.size()) : 1;
    L.cstep = coarser ? coarser->step : 1;

    // constants
    const auto& tb = tables();
    std::vector<float> C(kCoefs), T(size_t(8) * kMaxSlots * kMaxSlots, 0.0f), kaiser(kCoefs), sigma(3 * kCoefs),
        sbar(3), curve(3 * kLumaBins);
    for (int k = 0; k < kBlock; ++k)
        for (int n = 0; n < kBlock; ++n) C[size_t(k * kBlock + n)] = tb.c8[k][n];
    for (int n = 1; n <= kMaxSlots; ++n)
        for (int k = 0; k < n; ++k)
            for (int i = 0; i < n; ++i) T[size_t((n * kMaxSlots + k) * kMaxSlots + i)] = tb.t[n][k][i];
    for (int q = 0; q < kCoefs; ++q) kaiser[size_t(q)] = tb.kaiser[q];
    for (int c = 0; c < 3; ++c) {
        float s = 0.0f;
        for (int q = 0; q < kCoefs; ++q) {
            sigma[size_t(c * kCoefs + q)] = lp.sigma[c][q];
            s += lp.sigma[c][q] * lp.sigma[c][q];
        }
        sbar[size_t(c)] = s;
        for (int b = 0; b < kLumaBins; ++b) curve[size_t(c * kLumaBins + b)] = lp.prof->lumaCurve[c][b];
    }

    Buf planes, xsB, ysB, motionB, coarserB, cxsB, cysB, orderB, cB, tB, kB, sB, sbB, cvB, num, den, pilot, res;
    const size_t planesBytes = sizeof(float) * plane * 3 * size_t(nf);
    if (!planes.create(rt, planesBytes, CL_MEM_READ_ONLY) || !xsB.create(rt, sizeof(int) * size_t(nx)) ||
        !ysB.create(rt, sizeof(int) * size_t(ny)) || !motionB.create(rt, sizeof(cl_int) * 4 * nb * size_t(nf)) ||
        !cB.create(rt, sizeof(float) * C.size()) || !tB.create(rt, sizeof(float) * T.size()) ||
        !kB.create(rt, sizeof(float) * kaiser.size()) || !sB.create(rt, sizeof(float) * sigma.size()) ||
        !sbB.create(rt, sizeof(float) * sbar.size()) || !cvB.create(rt, sizeof(float) * curve.size()) ||
        !num.create(rt, sizeof(float) * plane * 3) || !den.create(rt, sizeof(float) * plane * 3) ||
        !res.create(rt, sizeof(float) * plane * 3) || !pilot.create(rt, sizeof(float) * plane * 3))
        return false;
    for (int f = 0; f < nf; ++f)
        for (int c = 0; c < 3; ++c)
            if (!planes.write(rt, fr[size_t(f)]->c[c].d.data(), sizeof(float) * plane,
                              sizeof(float) * plane * size_t(f * 3 + c)))
                return false;
    if (!xsB.write(rt, mf.xs.data(), sizeof(int) * size_t(nx)) || !ysB.write(rt, mf.ys.data(), sizeof(int) * size_t(ny)) ||
        !cB.write(rt, C.data(), sizeof(float) * C.size()) || !tB.write(rt, T.data(), sizeof(float) * T.size()) ||
        !kB.write(rt, kaiser.data(), sizeof(float) * kaiser.size()) ||
        !sB.write(rt, sigma.data(), sizeof(float) * sigma.size()) ||
        !sbB.write(rt, sbar.data(), sizeof(float) * sbar.size()) ||
        !cvB.write(rt, curve.data(), sizeof(float) * curve.size()))
        return false;

    // --- motion
    mf.slots = nf;
    mf.m.assign(nb * size_t(nf), Motion());
    std::vector<cl_int> packed;
    if (nf > 1) {
        std::vector<cl_int> cpacked;
        if (coarser) packMotion(*coarser, cpacked);
        if (!coarserB.create(rt, sizeof(cl_int) * std::max<size_t>(cpacked.size(), 4)) ||
            !cxsB.create(rt, sizeof(int) * (coarser ? coarser->xs.size() : 1)) ||
            !cysB.create(rt, sizeof(int) * (coarser ? coarser->ys.size() : 1)))
            return false;
        if (coarser && (!coarserB.write(rt, cpacked.data(), sizeof(cl_int) * cpacked.size()) ||
                        !cxsB.write(rt, coarser->xs.data(), sizeof(int) * coarser->xs.size()) ||
                        !cysB.write(rt, coarser->ys.data(), sizeof(int) * coarser->ys.size())))
            return false;
        cl_kernel k = rt.kMotion;
        if (!setArg(rt, k, 0, L) || !setArg(rt, k, 1, planes.m) || !setArg(rt, k, 2, xsB.m) ||
            !setArg(rt, k, 3, ysB.m) || !setArg(rt, k, 4, coarserB.m) || !setArg(rt, k, 5, cxsB.m) ||
            !setArg(rt, k, 6, cysB.m) || !setArg(rt, k, 7, cvB.m) || !setArg(rt, k, 8, motionB.m))
            return false;
        const size_t total = nb * size_t(nf);
        const size_t global = (total + 63) / 64 * 64;
        if (rt.cl.clEnqueueNDRangeKernel(rt.queue, k, 1, nullptr, &global, nullptr, 0, nullptr, nullptr) !=
            CL_SUCCESS)
            return false;
        packed.resize(total * 4);
        if (!motionB.read(rt, packed.data(), sizeof(cl_int) * packed.size())) return false;
        for (size_t i = 0; i < total; ++i) {
            mf.m[i].dx = static_cast<int16_t>(packed[i * 4 + 0]);
            mf.m[i].dy = static_cast<int16_t>(packed[i * 4 + 1]);
            mf.m[i].ok = packed[i * 4 + 2] != 0;
        }
        // cut detection, as on the CPU
        for (int f = 0; f < nf; ++f) {
            if (f == ref) continue;
            if (!coarser) {
                size_t okCount = 0;
                for (size_t b = 0; b < nb; ++b) okCount += mf.m[b * size_t(nf) + size_t(f)].ok;
                dropped[size_t(f)] = double(okCount) < minMatchedFraction * double(nb);
            }
            if (dropped[size_t(f)])
                for (size_t b = 0; b < nb; ++b) mf.m[b * size_t(nf) + size_t(f)].ok = false;
        }
    } else {
        for (auto& m : mf.m) m.ok = true;
    }
    packMotion(mf, packed);
    if (!motionB.write(rt, packed.data(), sizeof(cl_int) * packed.size())) return false;
    if (abort && abort()) return false;

    // --- block order: classes of blocks that never overlap
    const int m = (kBlock + lp.step - 1) / lp.step + 1;
    std::vector<int> order;
    order.reserve(nb);
    std::vector<std::pair<int, int>> classes;  // start, count
    for (int cy = 0; cy < m; ++cy)
        for (int cx = 0; cx < m; ++cx) {
            const int start = static_cast<int>(order.size());
            for (int by = cy; by < ny; by += m)
                for (int bx = cx; bx < nx; bx += m) order.push_back(by * nx + bx);
            classes.emplace_back(start, static_cast<int>(order.size()) - start);
        }
    if (!orderB.create(rt, sizeof(int) * order.size()) || !orderB.write(rt, order.data(), sizeof(int) * order.size()))
        return false;

    auto pass = [&](int mode) -> bool {
        if (!num.zero(rt, sizeof(float) * plane * 3) || !den.zero(rt, sizeof(float) * plane * 3)) return false;
        cl_kernel k = rt.kFilter;
        const cl_int md = mode;
        if (!setArg(rt, k, 0, L) || !setArg(rt, k, 1, md) || !setArg(rt, k, 2, planes.m) ||
            !setArg(rt, k, 3, pilot.m) || !setArg(rt, k, 4, xsB.m) || !setArg(rt, k, 5, ysB.m) ||
            !setArg(rt, k, 6, motionB.m) || !setArg(rt, k, 7, orderB.m) || !setArg(rt, k, 9, cB.m) ||
            !setArg(rt, k, 10, tB.m) || !setArg(rt, k, 11, kB.m) || !setArg(rt, k, 12, sB.m) ||
            !setArg(rt, k, 13, sbB.m) || !setArg(rt, k, 14, cvB.m) || !setArg(rt, k, 15, num.m) ||
            !setArg(rt, k, 16, den.m))
            return false;
        for (auto& cls : classes) {
            if (cls.second == 0) continue;
            const cl_int start = cls.first;
            if (!setArg(rt, k, 8, start)) return false;
            const size_t global[2] = {size_t(cls.second) * 64, 3}, local[2] = {64, 1};
            if (rt.cl.clEnqueueNDRangeKernel(rt.queue, k, 2, nullptr, global, local, 0, nullptr, nullptr) !=
                CL_SUCCESS)
                return false;
        }
        return rt.cl.clFinish(rt.queue) == CL_SUCCESS;
    };
    auto resolveInto = [&](Buf& dst) -> bool {
        cl_kernel k = rt.kResolve;
        const cl_int count = static_cast<cl_int>(plane * 3);
        if (!setArg(rt, k, 0, count) || !setArg(rt, k, 1, num.m) || !setArg(rt, k, 2, den.m) ||
            !setArg(rt, k, 3, dst.m))
            return false;
        const size_t global = (plane * 3 + 63) / 64 * 64;
        return rt.cl.clEnqueueNDRangeKernel(rt.queue, k, 1, nullptr, &global, nullptr, 0, nullptr, nullptr) ==
               CL_SUCCESS;
    };

    if (twoPass) {
        if (!pass(0) || !resolveInto(pilot)) return false;
        if (abort && abort()) return false;
        if (!pass(2)) return false;
    } else {
        if (!pass(1)) return false;
    }
    if (!resolveInto(res)) return false;
    Frame result;
    for (int c = 0; c < 3; ++c) {
        result.c[c].resize(w, h);
        if (!res.read(rt, result.c[c].d.data(), sizeof(float) * plane, sizeof(float) * plane * size_t(c)))
            return false;
    }
    out = std::move(result);
    return true;
}

}  // namespace detail
}  // namespace sd
