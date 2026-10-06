"""Synthetic test data: an HDR equirect scene, fisheye views of it from a
Nodal Ninja style rig, and bracketed DNG "raws" that LibRaw can decode.

Conventions match Hugin's equirectangular output: longitude increases to the
right (x = (lon + 180) / 360 * W), latitude up (y = (90 - lat) / 180 * H).
Camera frame: x right, y up, z forward.
"""
import math

import numpy as np


def make_scene(width=2048, seed=1, sun=(40.0, 35.0), sun_value=2.0e4):
    """Textured HDR equirect: bright sky, darker ground, lots of features."""
    rng = np.random.default_rng(seed)
    h = width // 2
    img = np.zeros((h, width, 3), np.float32)
    lat = (90.0 - (np.arange(h) + 0.5) * 180.0 / h)[:, None]
    sky = lat > 0
    img[...] = np.where(sky[..., None], [0.55, 0.75, 1.2], [0.18, 0.15, 0.12])
    # random rectangles and discs of many sizes give cpfind corners to find
    for _ in range(2600):
        w = int(rng.integers(4, width // 22))
        hh = int(rng.integers(4, h // 14))
        x0 = int(rng.integers(0, width))
        y0 = int(rng.integers(0, h - hh))
        colour = rng.uniform(0.02, 1.0, 3).astype(np.float32)
        if y0 < h // 2:
            colour = colour * 2.5
        xs = np.arange(x0, x0 + w) % width
        if rng.random() < 0.5:
            img[y0:y0 + hh][:, xs] = colour
        else:
            yy, xx = np.mgrid[0:hh, 0:w]
            disc = ((yy - hh / 2) / (hh / 2)) ** 2 + ((xx - w / 2) / (w / 2)) ** 2 < 1
            block = img[y0:y0 + hh][:, xs]
            block[disc] = colour
            img[y0:y0 + hh][:, xs] = block
    img *= rng.uniform(0.85, 1.15, img.shape[:2])[..., None].astype(np.float32)
    # the sun: a small, very bright disc
    slon, slat = sun
    sx = (slon + 180.0) / 360.0 * width
    sy = (90.0 - slat) / 180.0 * h
    yy, xx = np.mgrid[0:h, 0:width]
    rr = np.hypot((xx - sx), (yy - sy))
    img[rr < max(2.0, width / 900.0)] = sun_value
    return img


def rot(yaw, pitch, roll=0.0):
    """World-from-camera rotation for a camera at yaw/pitch/roll (degrees)."""
    y, p, r = (math.radians(a) for a in (yaw, pitch, roll))
    rz = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), math.sin(p)], [0, -math.sin(p), math.cos(p)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    return ry @ rx @ rz


def sample_equirect(img, d):
    """Bilinear lookup of world directions d (..., 3) in an equirect image."""
    h, w = img.shape[:2]
    lon = np.arctan2(d[..., 0], d[..., 2])
    lat = np.arcsin(np.clip(d[..., 1], -1, 1))
    x = (lon / (2 * np.pi) + 0.5) * w - 0.5
    y = (0.5 - lat / np.pi) * h - 0.5
    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    y0c = np.clip(y0, 0, h - 1)
    y1c = np.clip(y0 + 1, 0, h - 1)
    x0w = x0 % w
    x1w = (x0 + 1) % w
    return ((img[y0c, x0w] * (1 - fx) + img[y0c, x1w] * fx) * (1 - fy)
            + (img[y1c, x0w] * (1 - fx) + img[y1c, x1w] * fx) * fy)


def fisheye_view(scene, yaw, pitch, roll=0.0, size=(1200, 800), circle=760,
                 k1=0.0, ss=2, world=None):
    """Circular equidistant fisheye (180 deg across `circle` px), optional
    radial distortion k1, supersampled ss x ss. Outside the circle is black."""
    w, h = size
    f = (circle / 2.0) / (math.pi / 2.0)
    out = np.zeros((h, w, 3), np.float32)
    rmat = rot(yaw, pitch, roll)
    if world is not None:  # whole rig tilted (an unlevel tripod)
        rmat = world @ rmat
    for sy in range(ss):
        for sx in range(ss):
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
            dx = xx + (sx + 0.5) / ss - 0.5 - (w - 1) / 2.0
            dy = yy + (sy + 0.5) / ss - 0.5 - (h - 1) / 2.0
            r = np.hypot(dx, dy)
            rn = r / (circle / 2.0)
            theta = (r * (1.0 + k1 * rn * rn)) / f
            phi = np.arctan2(dy, dx)
            cam = np.stack([np.sin(theta) * np.cos(phi), -np.sin(theta) * np.sin(phi),
                            np.cos(theta)], -1)
            world = cam @ rmat.T
            col = sample_equirect(scene, world)
            col[theta > math.pi / 2] = 0.0
            out += col.astype(np.float32)
    return out / (ss * ss)


RIG = [(0, 0), (60, 0), (120, 0), (180, 0), (240, 0), (300, 0), (0, 90), (0, -90)]


# ---------------------------------------------------------------- fake raws

XYZ_FROM_SRGB = np.array([[0.4124564, 0.3575761, 0.1804375],
                          [0.2126729, 0.7151522, 0.0721750],
                          [0.0193339, 0.1191920, 0.9503041]])
# A made-up but plausible camera: XYZ -> camera (rows R, G, B), D65.
CAM_FROM_XYZ = np.array([[0.75, -0.20, -0.08],
                         [-0.45, 1.25, 0.20],
                         [-0.05, 0.12, 0.62]])


def scene_to_camera(rgb_linear):
    """Linear Rec.709 scene radiance -> camera native RGB (no white balance)."""
    return rgb_linear @ (CAM_FROM_XYZ @ XYZ_FROM_SRGB).T


def write_dng(path, cam_rgb, exposure, black=512, white=16383, iso=100,
              shutter=1 / 125, fnumber=8.0, when="2026:10:06 10:00:00", subsec="00",
              noise=0.0, rng=None, model="SynthCam"):
    """Write camera-space linear values (1.0 = clip at `exposure` 1) as a 14-bit
    RGGB Bayer DNG with the tags LibRaw needs, plus EXIF exposure data."""
    import tifffile

    h, w = cam_rgb.shape[:2]
    h -= h % 2
    w -= w % 2
    sig = cam_rgb[:h, :w] * exposure
    bayer = np.empty((h, w), np.float64)
    bayer[0::2, 0::2] = sig[0::2, 0::2, 0]
    bayer[0::2, 1::2] = sig[0::2, 1::2, 1]
    bayer[1::2, 0::2] = sig[1::2, 0::2, 1]
    bayer[1::2, 1::2] = sig[1::2, 1::2, 2]
    if noise and rng is not None:
        bayer += rng.normal(0, noise, bayer.shape)
    data = np.clip(np.round(black + bayer * (white - black)), 0, white).astype(np.uint16)

    def rat(v, den=10000):
        return (int(round(v * den)), den)

    def srat(vals):
        out = []
        for v in vals:
            out += [int(round(v * 10000)), 10000]
        return out

    cm = CAM_FROM_XYZ.flatten()
    as_shot = CAM_FROM_XYZ @ XYZ_FROM_SRGB @ np.ones(3)
    as_shot = as_shot / as_shot[1]
    inv = 1.0 / shutter
    sh = (1, int(round(inv))) if abs(inv - round(inv)) < 1e-6 else (int(round(shutter * 1e6)), 1000000)
    extratags = [
        (271, 's', 0, 'Synth', True),
        (272, 's', 0, model, True),
        (33421, 'H', 2, (2, 2), True),             # CFARepeatPatternDim
        (33422, 'B', 4, (0, 1, 1, 2), True),       # CFAPattern RGGB
        (50706, 'B', 4, (1, 4, 0, 0), True),       # DNGVersion
        (50707, 'B', 4, (1, 1, 0, 0), True),       # DNGBackwardVersion
        (50708, 's', 0, 'Synth ' + model, True),   # UniqueCameraModel
        (50714, 'H', 1, black, True),              # BlackLevel
        (50717, 'H', 1, white, True),              # WhiteLevel
        (50721, '2i', 9, srat(cm), True),          # ColorMatrix1
        (50778, 'H', 1, 21, True),                 # CalibrationIlluminant1 = D65
        (50728, '2I', 3, sum((list(rat(v)) for v in as_shot), []), True),  # AsShotNeutral
        (33434, '2I', 1, sh, True),                # ExposureTime
        (33437, '2I', 1, rat(fnumber, 10), True),  # FNumber
        (34855, 'H', 1, iso, True),                # ISO
        (36867, 's', 0, when, True),               # DateTimeOriginal
        (37521, 's', 0, subsec, True),             # SubSecTimeOriginal
    ]
    tifffile.imwrite(path, data, photometric=32803, compression=None,
                     extratags=extratags, metadata=None)
