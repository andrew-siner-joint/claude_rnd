"""Merge one bracket (one camera position) into a linear HDR image.

Each frame contributes value / exposure, weighted by its exposure (brighter
frames have less noise relative to signal) and faded out towards the sensor's
clip point. Pixels clipped in every frame keep the darkest frame's value
(a lower bound; usually only the sun's core). The true exposure ratio between
neighbouring frames is measured from the pixels both exposed well, which
removes the small shutter-speed errors that otherwise show up as banding.
"""
import math
import os
from dataclasses import dataclass, field

import numpy as np

from . import imageio, rawdecode

EPS_WEIGHT = 1e-6


@dataclass
class MergeResult:
    path: str
    proxy: str
    clipped_fraction: float      # of pixels saturated even in the darkest frame
    ratios: list = field(default_factory=list)   # (nominal, used) stop gaps
    peak: float = 0.0


def _smoothstep(e0, e1, x):
    """float32 smoothstep (33 MP arrays: avoid float64 temporaries)."""
    t = x - np.float32(e0)
    t *= np.float32(1.0 / max(e1 - e0, 1e-6))
    np.clip(t, 0.0, 1.0, out=t)
    out = np.float32(3.0) - np.float32(2.0) * t
    out *= t
    out *= t
    return out


def _sample(frame, step=4):
    g = frame.rgb[::step, ::step, 1].copy()   # copies: the frame is scaled in place later
    sat = frame.saturated[::step, ::step].copy()
    return g, sat, frame.clip


def _measure_ratio(prev, cur, nominal):
    """Exposure ratio cur/prev from pixels well exposed in both."""
    g0, s0, c0 = prev
    g1, s1, c1 = cur
    ok = (~s0) & (~s1) & (g0 > 0.03 * c0) & (g0 < 0.7 * c0) & (g1 > 0.03 * c1) & (g1 < 0.7 * c1)
    if np.count_nonzero(ok) < 2000:
        return nominal, False
    measured = float(np.median(g1[ok] / g0[ok]))
    if not measured > 0 or abs(math.log2(measured / nominal)) > 0.34:
        return nominal, False
    return measured, True


def merge_bracket(shots, demosaic="AHD", clip_start=0.80, clip_end=0.95, refine=True,
                  log=print):
    """Camera-space radiance, scaled so value * H = sensor level at exposure H
    (H = t * ISO/100 / N^2). Returns (rgb float32, clipped_fraction, ratios)."""
    import cv2

    order = sorted(shots, key=lambda s: s.exposure)
    nominal = [s.exposure for s in order]
    used = []
    ratios = []
    acc = wsum = None
    prev = None
    clipped = 0.0
    for i, shot in enumerate(order):
        frame = rawdecode.decode(shot.path, demosaic)
        if acc is not None and frame.rgb.shape != acc.shape:
            raise ValueError("%s has a different size from the rest of its bracket" % shot.name)
        cur = _sample(frame)
        if i == 0:
            used.append(nominal[0])
        else:
            nominal_ratio = nominal[i] / nominal[i - 1]
            ratio, measured = (_measure_ratio(prev, cur, nominal_ratio) if refine
                               else (nominal_ratio, False))
            used.append(used[-1] * ratio)
            ratios.append((math.log2(nominal_ratio), math.log2(ratio), measured))
        prev = cur

        rgb = frame.rgb
        vmax = np.maximum(np.maximum(rgb[..., 0], rgb[..., 1]), rgb[..., 2])
        clip = frame.clip
        weight = _smoothstep(clip_start * clip, clip_end * clip, vmax)
        np.subtract(np.float32(1.0), weight, out=weight)
        sat = frame.saturated.astype(np.float32)
        if sat.any():
            sat = cv2.GaussianBlur(sat, (0, 0), 1.5)
            weight *= np.clip(1.0 - 2.0 * sat, 0.0, 1.0)
        weight *= np.float32(used[i] / used[0])
        if i == 0:
            clipped = float(np.mean(frame.saturated))
            # where even the darkest frame clipped, the channels hold the clip
            # level rather than the scene colour; remember where, softly
            dark_clip = None
            if frame.saturated.any():
                # x2 so the mask is fully on over every clipped pixel and only
                # fades out beyond them
                blur = cv2.GaussianBlur(frame.saturated.astype(np.float32), (0, 0), 2.0)
                dark_clip = np.clip(2.0 * blur, 0.0, 1.0)
            acc = frame.rgb * np.float32(EPS_WEIGHT / used[0])
            wsum = np.full(vmax.shape, EPS_WEIGHT, np.float32)
        frame.rgb *= np.float32(1.0 / used[i])
        frame.rgb *= weight[..., None]
        acc += frame.rgb
        wsum += weight
        del frame
    acc /= wsum[..., None]
    mid = (len(order) - 1) // 2
    acc *= np.float32(used[mid] / nominal[mid])
    return acc, clipped, ratios, dark_clip


def neutralise_clipped(cam_rgb, wb_mult, mask):
    """Make sensor-clipped pixels white instead of magenta: clipped channels
    are equal in camera space, so white balance gains would tint them. Lift
    them to a neutral at their brightest white-balanced channel."""
    if mask is None:
        return cam_rgb
    mult = np.asarray(wb_mult, np.float32)[:3]
    rows = np.flatnonzero(mask.max(axis=1) > 0.001)
    if rows.size == 0:
        return cam_rgb
    r0, r1 = rows[0], rows[-1] + 1
    block = cam_rgb[r0:r1]
    s = np.clip(mask[r0:r1], 0.0, 1.0)[..., None]
    level = (block * mult).max(axis=2, keepdims=True)
    block += s * (level / mult - block)
    return cam_rgb


def to_output(cam_rgb, wb_mult, cam_to_out):
    """White balance + camera -> output gamut, in place, in row chunks."""
    m = (np.asarray(cam_to_out, float) @ np.diag(np.asarray(wb_mult, float)[:3])).astype(np.float32)
    rows = cam_rgb.shape[0]
    step = max(1, 4_000_000 // max(1, cam_rgb.shape[1]))
    for r0 in range(0, rows, step):
        block = cam_rgb[r0:r0 + step]
        block[...] = block @ m.T
        np.maximum(block, 0.0, out=block)
    return cam_rgb


def merge_position(job):
    """Worker entry point (runs in a subprocess). `job` is a plain dict."""
    rgb, clipped, ratios, dark_clip = merge_bracket(
        job["shots"], job["demosaic"], job["clip_start"], job["clip_end"], job["refine"])
    neutralise_clipped(rgb, job["wb_mult"], dark_clip)
    rgb *= np.float32(job["anchor"])
    to_output(rgb, job["wb_mult"], job["cam_to_out"])
    peak = float(rgb.max())
    imageio.write_exr(job["out"], rgb, gamut=job["gamut"], half=peak < 60000.0,
                      metadata={"stage": "merged position", "position": job["index"]})
    proxy8, _ = imageio.tonemap(rgb, job["gamut"], exposure=job["proxy_exposure"])
    imageio.write_jpeg(job["proxy"], proxy8, quality=93)
    return MergeResult(path=job["out"], proxy=job["proxy"], clipped_fraction=clipped,
                       ratios=ratios, peak=peak)


def default_jobs(positions, megapixels):
    """Parallel merges that fit in memory: ~110 bytes per pixel per worker
    (measured: 3.3 GB peak for 33 MP frames)."""
    cpus = os.cpu_count() or 2
    try:
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        ram = 8 << 30
    per_job = max(megapixels, 1.0) * 1e6 * 110
    by_ram = int((ram * 0.6) // per_job)
    return max(1, min(positions, max(1, cpus // 2), by_ram))
