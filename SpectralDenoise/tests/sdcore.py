"""ctypes wrapper around the core library (src/core) for tests and experiments.

The library is built by tests/build_core.sh (or CMake's sdcore target) into
tests/build/libsdcore.so.
"""
import ctypes
import os
import subprocess

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIB_PATH = os.environ.get("SDCORE_LIB", os.path.join(HERE, "build", "libsdcore.so"))


class SdSettings(ctypes.Structure):
    _fields_ = [
        ("space", ctypes.c_int),
        ("level", ctypes.c_float),
        ("lumaLevel", ctypes.c_float),
        ("chromaLevel", ctypes.c_float),
        ("scaleLevel", ctypes.c_float * 4),
        ("lumaAmount", ctypes.c_float),
        ("chromaAmount", ctypes.c_float),
        ("sharpen", ctypes.c_float),
        ("temporalRadius", ctypes.c_int),
        ("motionTolerance", ctypes.c_float),
        ("quality", ctypes.c_int),
        ("lumaAdaptive", ctypes.c_int),
        ("threads", ctypes.c_int),
        ("useGpu", ctypes.c_int),
    ]


LINEAR, VIDEO = 0, 1


def build():
    subprocess.check_call(["bash", os.path.join(HERE, "build_core.sh")])


_lib = None


def lib():
    global _lib
    if _lib is None:
        if not os.path.exists(LIB_PATH):
            build()
        _lib = ctypes.CDLL(LIB_PATH)
        _lib.sd_analyse.restype = ctypes.c_int
        _lib.sd_denoise.restype = ctypes.c_int
        _lib.sd_summary.restype = ctypes.c_int
    return _lib


def _as_img(a):
    a = np.ascontiguousarray(a, dtype=np.float32)
    assert a.ndim == 3 and a.shape[2] in (3, 4)
    return a


def analyse(img, region=None, space=LINEAR):
    """Profile string. region = (x1, y1, x2, y2) in array coords (x = column,
    y = row); None = automatic."""
    img = _as_img(img)
    h, w, c = img.shape
    rect = (ctypes.c_int * 4)(*(region or (0, 0, 0, 0)))
    buf = ctypes.create_string_buffer(1 << 20)
    n = lib().sd_analyse(img.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), w, h, c, space,
                         0 if region else 1, rect, buf, len(buf))
    if n == 0:
        raise ValueError(buf.value.decode())
    return buf.value.decode()


def gpu_info():
    """(available, device name or reason)."""
    buf = ctypes.create_string_buffer(512)
    ok = lib().sd_gpu_info(buf, len(buf))
    return bool(ok), buf.value.decode()


def summary(profile):
    buf = ctypes.create_string_buffer(512)
    lib().sd_summary(profile.encode(), buf, len(buf))
    return buf.value.decode()


def parse_profile(profile):
    """Profile string -> dict with numpy arrays (mirrors NoiseProfile::serialize)."""
    t = profile.split()
    assert t[0] == "SDP1"
    vals = t[1:]
    space, scales = int(vals[0]), int(vals[1])
    hdr = 12
    curve = np.array(vals[hdr:hdr + 24], dtype=np.float64).reshape(3, 8)
    sig = np.array(vals[hdr + 24:hdr + 24 + scales * 3 * 64], dtype=np.float64).reshape(scales, 3, 8, 8)
    return dict(space=space, scales=scales, luma_lo=float(vals[2]), luma_hi=float(vals[3]),
                from_region=int(vals[4]), curve=curve, sigma=sig)


def settings(**kw):
    s = SdSettings()
    s.space = kw.get("space", LINEAR)
    s.level = kw.get("level", 1.0)
    s.lumaLevel = kw.get("luma_level", 1.0)
    s.chromaLevel = kw.get("chroma_level", 1.0)
    sl = kw.get("scale_level", (1.0, 1.0, 1.0, 1.0))
    for i in range(4):
        s.scaleLevel[i] = sl[i]
    s.lumaAmount = kw.get("luma_amount", 1.0)
    s.chromaAmount = kw.get("chroma_amount", 1.0)
    s.sharpen = kw.get("sharpen", 0.0)
    s.temporalRadius = kw.get("temporal_radius", 0)
    s.motionTolerance = kw.get("motion_tolerance", 1.0)
    s.quality = kw.get("quality", 1)
    s.lumaAdaptive = int(kw.get("luma_adaptive", True))
    s.threads = kw.get("threads", 0)
    s.useGpu = int(kw.get("use_gpu", False))
    return s


def denoise(frames, ref, profile, scale_shift=0, **kw):
    """frames: list of HxWxC float arrays (None allowed except ref)."""
    frames = [None if f is None else _as_img(f) for f in frames]
    h, w, c = frames[ref].shape
    arr = (ctypes.POINTER(ctypes.c_float) * len(frames))()
    for i, f in enumerate(frames):
        arr[i] = f.ctypes.data_as(ctypes.POINTER(ctypes.c_float)) if f is not None else None
    out = np.empty_like(frames[ref])
    s = settings(**kw)
    ok = lib().sd_denoise(arr, len(frames), ref, w, h, c, profile.encode(), ctypes.byref(s), scale_shift,
                          out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
    if not ok:
        raise RuntimeError("denoise failed")
    return out


def format_profile(p, template):
    """Inverse of parse_profile, reusing the header of an existing profile string."""
    t = template.split()
    hdr = t[:13]
    hdr[2] = str(p["sigma"].shape[0])
    vals = list(p["curve"].reshape(-1)) + list(p["sigma"].reshape(-1))
    return " ".join(hdr + ["%.6g" % v for v in vals])
