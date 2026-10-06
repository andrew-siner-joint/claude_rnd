"""Image files: OpenEXR in and out (with colour metadata), 8-bit previews."""
from pathlib import Path

import numpy as np

from .color import GAMUTS, luminance

HALF_MAX = 65504.0


def write_exr(path, rgb, gamut="rec709", half=False, metadata=None, latlong=False):
    import OpenEXR

    rgb = np.ascontiguousarray(rgb[..., :3])
    if half:
        rgb = np.clip(rgb, -HALF_MAX, HALF_MAX).astype(np.float16)
    else:
        rgb = rgb.astype(np.float32, copy=False)
    header = {
        "compression": OpenEXR.ZIP_COMPRESSION,
        "type": OpenEXR.scanlineimage,
        "chromaticities": tuple(float(v) for v in GAMUTS[gamut]["chromaticities"]),
    }
    if latlong:
        header["envmap"] = OpenEXR.ENVMAP_LATLONG
    for key, value in (metadata or {}).items():
        header["hdristitch:" + key] = value if isinstance(value, (int, float)) else str(value)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".part")
    OpenEXR.File(header, {"RGB": rgb}).write(str(tmp))
    tmp.replace(path)


def read_exr(path):
    """(float32 HxWx3, header dict)."""
    import OpenEXR

    with OpenEXR.File(str(path)) as f:
        header = dict(f.header())
        channels = f.channels()
        if "RGB" in channels:
            rgb = channels["RGB"].pixels
        elif "RGBA" in channels:
            rgb = channels["RGBA"].pixels[..., :3]
        else:
            names = list(channels)
            if not names:
                raise ValueError("%s has no channels" % path)
            data = channels[names[0]].pixels
            rgb = np.repeat(data[..., None], 3, axis=2) if data.ndim == 2 else data[..., :3]
        rgb = np.array(rgb, dtype=np.float32)
    return rgb, header


def exr_metadata(header):
    """HDRIStitch attributes, also after a round trip through Nuke (which may
    prefix them, e.g. "exr/hdristitch:gamut")."""
    out = {}
    for key, value in header.items():
        if "hdristitch:" in key:
            out[key.split("hdristitch:", 1)[1]] = value
    return out


def gamut_of(header, default="rec709"):
    value = exr_metadata(header).get("gamut")
    return value if value in GAMUTS else default


def srgb_encode(x):
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def tonemap(rgb, gamut="rec709", exposure=None, key=0.18):
    """Display-referred 8-bit preview: auto exposure (or `exposure` scale), a
    soft highlight roll-off and the sRGB curve. For looking at, not lighting."""
    if gamut != "rec709":
        rgb = rgb @ np.linalg.inv(GAMUTS[gamut]["matrix"]).T.astype(np.float32)
    y = luminance(np.maximum(rgb, 0), "rec709")
    if exposure is None:
        valid = y[y > 0]
        level = float(np.exp(np.mean(np.log(valid[::max(1, valid.size // 200000)] + 1e-6)))) \
            if valid.size else 1.0
        exposure = key / max(level, 1e-6)
    x = np.maximum(rgb, 0) * exposure
    # Reinhard on luminance with a white point, colour preserved
    yx = luminance(x, "rec709")
    white = 16.0
    ymapped = yx * (1 + yx / (white * white)) / (1 + yx)
    scale = np.where(yx > 1e-8, ymapped / np.maximum(yx, 1e-8), 0)[..., None]
    out = srgb_encode(x * scale)
    return (out * 255 + 0.5).astype(np.uint8), exposure


def write_jpeg(path, rgb8, quality=92):
    import cv2

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), np.ascontiguousarray(rgb8[..., ::-1]),
                     [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise IOError("Could not write %s" % path)


def write_png_mask(path, mask):
    import cv2

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), (np.asarray(mask) > 0).astype(np.uint8) * 255)


def resize_area(img, width):
    import cv2

    h, w = img.shape[:2]
    if w == width:
        return img
    height = max(1, int(round(h * width / w)))
    interp = cv2.INTER_AREA if width < w else cv2.INTER_LINEAR
    return cv2.resize(img, (width, height), interpolation=interp)
