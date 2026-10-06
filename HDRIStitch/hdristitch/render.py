"""Render the stitched equirectangular HDR in floating point.

Hugin's nona clamps pixel values to 0..1, which would destroy an HDR. So nona
never sees the HDR images: it remaps a coordinate ramp (an image whose pixels
hold their own x, y position, all within 0..1) through each camera's geometry,
giving exact sub-pixel lookup maps with the project's crop and masks baked
into alpha. The HDR pixels are then resampled here with those maps and
blended, weighting each camera towards its optical centre (where a fisheye
is sharpest and least vignetted) with feathered mask edges.
"""
import math
from pathlib import Path

import numpy as np

from . import imageio
from . import pto as ptolib

BAND = 512  # rows per resampling band (bounds memory)


def write_ramp(path, width, height):
    import tifffile

    xs = ((np.arange(width, dtype=np.float64) + 0.5) / width).astype(np.float32)
    ys = ((np.arange(height, dtype=np.float64) + 0.5) / height).astype(np.float32)
    ramp = np.empty((height, width, 3), np.float32)
    ramp[..., 0] = xs[None, :]
    ramp[..., 1] = ys[:, None]
    ramp[..., 2] = 0.5
    tifffile.imwrite(str(path), ramp, photometric="rgb")


def read_coords(path):
    """(data HxWx4 float32, x0, y0) from a cropped nona TIFF."""
    import tifffile

    with tifffile.TiffFile(str(path)) as tif:
        page = tif.pages[0]
        data = page.asarray()
        tags = page.tags

        def offset(name, res_name):
            if name not in tags:
                return 0
            num, den = tags[name].value
            res = tags[res_name].value if res_name in tags else (1, 1)
            return int(round(num / den * res[0] / res[1]))

        x0 = offset("XPosition", "XResolution")
        y0 = offset("YPosition", "YResolution")
    return data, x0, y0


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _remap(src, mx, my, mode):
    import cv2

    border = cv2.BORDER_REPLICATE
    if mode == "linear":
        return cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=border)
    cub = cv2.remap(src, mx, my, cv2.INTER_CUBIC, borderMode=border)
    if mode == "cubic":
        return np.maximum(cub, 0.0)
    lin = cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=border)
    # cubic overshoots next to very bright pixels (the sun): fall back to
    # linear wherever the two disagree strongly
    bad = ((cub < 0) | (np.abs(cub - lin) > 0.3 * lin + 1e-4)).any(axis=2)
    cub[bad] = lin[bad]
    return cub


def image_centre(project, k, width, height):
    crop = project.crop(k)
    if crop is not None and int(project.value(k, "f")) == 2:
        left, right, top, bottom = crop
        return (left + right) / 2.0 - 0.5, (top + bottom) / 2.0 - 0.5, \
            max(right - left, bottom - top) / 2.0
    return (width - 1) / 2.0, (height - 1) / 2.0, math.hypot(width, height) / 2.0


def fill_holes(rgb, valid):
    """Push-pull fill of the pixels where `valid` is False (smooth, wide blur
    of the surroundings). Returns rgb (modified in place)."""
    import cv2

    if valid.all():
        return rgb
    colours = [rgb * valid[..., None]]
    weights = [valid.astype(np.float32)]
    while min(colours[-1].shape[:2]) > 2:
        h, w = colours[-1].shape[:2]
        size = (max(1, w // 2), max(1, h // 2))
        colours.append(cv2.resize(colours[-1], size, interpolation=cv2.INTER_AREA))
        weights.append(cv2.resize(weights[-1], size, interpolation=cv2.INTER_AREA))
    w_top = weights[-1]
    filled = colours[-1] / np.maximum(w_top, 1e-8)[..., None]
    if not (w_top > 0).any():
        filled[...] = 0
    for c, w in zip(reversed(colours[:-1]), reversed(weights[:-1])):
        h, wd = c.shape[:2]
        up = cv2.resize(filled, (wd, h), interpolation=cv2.INTER_LINEAR)
        a = np.clip(w, 0.0, 1.0)[..., None]
        filled = np.where(w[..., None] > 1e-8, c / np.maximum(w, 1e-8)[..., None], 0) * a \
            + up * (1 - a)
    holes = ~valid
    rgb[holes] = filled[holes]
    return rgb


def render(hugin, project_path, hdr_paths, width, stitch_cfg, log=print):
    """Stitch merged HDR positions into an equirect. Returns (rgb, holes)."""
    import cv2

    workdir = Path(hugin.workdir)
    rdir = workdir / "render"
    rdir.mkdir(exist_ok=True)
    project = ptolib.Project.load(project_path)
    images = project.images
    if len(images) != len(hdr_paths):
        raise ValueError("Project has %d images but %d merged positions were given"
                         % (len(images), len(hdr_paths)))
    height = width // 2
    sizes = [(int(float(im.raw("w"))), int(float(im.raw("h")))) for im in images]
    if len(set(sizes)) != 1:
        raise ValueError("All positions must have the same image size")
    iw, ih = sizes[0]
    write_ramp(rdir / "ramp.tif", iw, ih)
    coords = ptolib.coordinate_project(project, "ramp.tif", width, height)
    coords.save(rdir / "coords.pto")

    acc = np.zeros((height, width, 3), np.float32)
    wsum = np.zeros((height, width), np.float32)
    sharp = float(stitch_cfg["blend_sharpness"])
    feather = max(2.0, float(stitch_cfg["feather"]) * width)
    mode = stitch_cfg["interpolation"]
    for k, hdr_path in enumerate(hdr_paths):
        log("  remapping position %d/%d" % (k + 1, len(hdr_paths)))
        out = rdir / ("coord_%04d.tif" % k)
        if out.exists():
            out.unlink()
        hugin.run("nona", "-i", k, "-m", "TIFF_m", "-r", "ldr", "--ignore-exposure",
                  "-p", "FLOAT", "-z", "NONE", "-o", "render/coord_", "render/coords.pto")
        data, x0, y0 = read_coords(out)
        out.unlink()
        src, _ = imageio.read_exr(hdr_path)
        if src.shape[:2] != (ih, iw):
            raise ValueError("%s is %dx%d but the project expects %dx%d"
                             % (hdr_path, src.shape[1], src.shape[0], iw, ih))
        cx, cy, radius = image_centre(project, k, iw, ih)
        alpha = data[..., 3] > 0.5
        dist = cv2.distanceTransform(alpha.astype(np.uint8), cv2.DIST_L2, 5)
        edge = _smoothstep(0.0, feather, dist)
        ph, pw = alpha.shape
        ph = min(ph, height - y0)
        pw = min(pw, width - x0)
        for r0 in range(0, ph, BAND):
            r1 = min(ph, r0 + BAND)
            mx = data[r0:r1, :pw, 0] * np.float32(iw) - np.float32(0.5)
            my = data[r0:r1, :pw, 1] * np.float32(ih) - np.float32(0.5)
            rad = np.hypot(mx - cx, my - cy) / radius
            w = np.clip(1.0 - rad, 0.0, 1.0) ** sharp * edge[r0:r1, :pw]
            w[~alpha[r0:r1, :pw]] = 0.0
            w = w.astype(np.float32)
            if not w.any():
                continue
            patch = _remap(src, np.ascontiguousarray(mx), np.ascontiguousarray(my), mode)
            acc[y0 + r0:y0 + r1, x0:x0 + pw] += patch * w[..., None]
            wsum[y0 + r0:y0 + r1, x0:x0 + pw] += w
        del src, data
    valid = wsum > 1e-12
    acc /= np.maximum(wsum, 1e-12)[..., None]
    acc[~valid] = 0.0
    (rdir / "ramp.tif").unlink()
    return acc, ~valid


def native_width(project_path):
    """Equirect width matching the source resolution at the image centre."""
    project = ptolib.Project.load(project_path)
    w = float(project.images[0].raw("w"))
    hfov = project.value(0, "v")
    px_per_deg = w / hfov
    return int(math.ceil(360.0 * px_per_deg / 1024.0) * 1024)
