"""Drive the built plugin through the scriptable test host (tests/host).

Builds the plugin and host with CMake into tests/build/cmake if needed.
Images are passed as float RGBA raw files with OFX row order (bottom row
first); the helpers here flip to and from numpy's top-down order.
"""
import os
import subprocess

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.environ.get("SD_BUILD_DIR", os.path.join(HERE, "build", "cmake"))
HOST = os.path.join(BUILD, "tests", "host", "sd_ofx_host")
PLUGIN_ID = "tv.jointpost.SpectralDenoise"


def build():
    args = ["cmake", "-S", ROOT, "-B", BUILD, "-DSD_BUILD_TESTS=ON", "-DCMAKE_BUILD_TYPE=Release"]
    if os.environ.get("OPENFX_DIR"):
        args.append("-DOPENFX_DIR=" + os.environ["OPENFX_DIR"])
    subprocess.check_call(args, stdout=subprocess.DEVNULL)
    subprocess.check_call(["cmake", "--build", BUILD, "-j", str(os.cpu_count() or 4)], stdout=subprocess.DEVNULL)


def ensure_built():
    if not os.path.exists(HOST):
        build()


def write_raw(path, img):
    """img: HxWx3 or HxWx4 float, top row first."""
    img = np.asarray(img, np.float32)
    if img.shape[2] == 3:
        img = np.concatenate([img, np.ones(img.shape[:2] + (1,), np.float32)], -1)
    np.ascontiguousarray(img[::-1]).tofile(path)


def read_raw(path, w, h, comps=4):
    return np.fromfile(path, np.float32).reshape(h, w, comps)[::-1].copy()


class Result:
    def __init__(self, out, code):
        self.lines = out.splitlines()
        self.code = code

    def values(self, name):
        for l in self.lines:
            p = l.split(" ", 2)
            if p[0] == "VALUE" and p[1] == name:
                return p[2] if len(p) > 2 else ""
        raise KeyError(name)

    def startswith(self, prefix):
        return [l for l in self.lines if l.startswith(prefix)]

    def __str__(self):
        return "\n".join(self.lines)


def run(script, workdir, context="general", env=None):
    """script: list of command lines (without the 'load' line). env: extra
    environment variables for the host (e.g. SD_OPENCL)."""
    ensure_built()
    path = os.path.join(workdir, "script.txt")
    with open(path, "w") as f:
        f.write("load %s %s %s\n" % (os.path.join(BUILD), PLUGIN_ID, context))
        f.write("\n".join(script) + "\n")
    full_env = dict(os.environ, **(env or {}))
    p = subprocess.run([HOST, path], capture_output=True, text=True, timeout=600, env=full_env)
    return Result(p.stdout + p.stderr, p.returncode)
