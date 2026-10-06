"""Find the sun in an equirect HDRI, measure it, and paint it out.

For CG lighting the sun works best as a real light (a Blender Sun lamp):
sharp shadows, far less render noise, and an intensity you can calibrate.
This module gives you its direction and irradiance (in the HDRI's own units,
so it matches a World strength of 1) and an HDRI with the sun removed, so the
energy isn't counted twice.

A camera can rarely capture the sun's core unclipped. When it's clipped the
measured irradiance is only a lower bound; the suggested strength then comes
from a gray-card sun/shade ratio if you measured one, else from a typical
clear-sky ratio of direct sun to sky light (a starting point to calibrate).
"""
import json
import math

import numpy as np

from .color import GAMUTS, luminance
from .render import fill_holes

SUN_DIAMETER_DEG = 0.53


def _directions(rows, width, height):
    lat = (0.5 - (rows + 0.5) / height) * math.pi
    lon = ((np.arange(width) + 0.5) / width - 0.5) * 2 * math.pi
    cl = np.cos(lat)[:, None]
    return np.stack([cl * np.sin(lon)[None, :], np.repeat(np.sin(lat)[:, None], width, 1),
                     cl * np.cos(lon)[None, :]], -1)


def _vector(lon_deg, lat_deg):
    lon, lat = math.radians(lon_deg), math.radians(lat_deg)
    return np.array([math.cos(lat) * math.sin(lon), math.sin(lat), math.cos(lat) * math.cos(lon)])


def pixel_solid_angle(rows, width, height):
    lat = (0.5 - (rows + 0.5) / height) * math.pi
    return (2 * math.pi / width) * (math.pi / height) * np.cos(lat)


def sky_irradiance(rgb, gamut="rec709", exclude=None):
    """Horizontal irradiance from the upper hemisphere (per channel)."""
    h, w = rgb.shape[:2]
    rows = np.arange(h // 2)
    lat = (0.5 - (rows + 0.5) / h) * math.pi
    weight = pixel_solid_angle(rows, w, h) * np.sin(lat)
    sky = rgb[: h // 2].astype(np.float64)
    if exclude is not None:
        sky = sky * (~exclude[: h // 2])[..., None]
    return (sky * weight[:, None, None]).sum(axis=(0, 1))


def find_sun(rgb, gamut="rec709", radius_deg=3.0, min_contrast=50.0, clip_level=None):
    """Sun info dict, or None if nothing stands out from the sky.

    `clip_level`: the brightest value the merge could record (HDRIStitch
    stores it in the EXR). With it, "clipped" is exact; without it, a flat
    top on the sun is taken as clipping."""
    import cv2

    h, w = rgb.shape[:2]
    y = luminance(rgb, gamut).astype(np.float32)
    smooth = cv2.blur(y, (3, 3))
    py, px = np.unravel_index(int(np.argmax(smooth)), smooth.shape)
    peak = float(smooth[py, px])
    sky = y[: h // 2]
    sky_level = float(np.median(sky[sky > 0])) if np.any(sky > 0) else 0.0
    if peak <= 0 or (sky_level > 0 and peak < min_contrast * sky_level):
        return None

    # centroid of the bright core, with the image rolled so the seam can't cut it
    shift = w // 2 - px
    rolled = np.roll(y, shift, axis=1)
    core = (rolled > 0.5 * peak).astype(np.uint8)
    _, labels = cv2.connectedComponents(core)
    blob = labels == labels[py, w // 2]
    ys, xs = np.nonzero(blob)
    wts = rolled[ys, xs].astype(np.float64)
    cy = float((ys * wts).sum() / wts.sum())
    cx = float((xs * wts).sum() / wts.sum()) - shift
    lon = ((cx + 0.5) / w - 0.5) * 360.0
    lon = (lon + 180.0) % 360.0 - 180.0
    lat = (0.5 - (cy + 0.5) / h) * 180.0
    if clip_level:
        clipped = peak >= 0.9 * clip_level
    else:
        clipped = blob.sum() >= 4 and np.mean(wts >= 0.98 * peak) > 0.3

    # energy above the local sky, within radius_deg
    band = _band(lat, 2.2 * radius_deg, h)
    dirs = _directions(band, w, h)
    ang = np.degrees(np.arccos(np.clip(dirs @ _vector(lon, lat), -1, 1)))
    inner = ang <= radius_deg
    ring = (ang > 1.3 * radius_deg) & (ang <= 2.0 * radius_deg)
    region = rgb[band[0]: band[-1] + 1].astype(np.float64)
    background = np.median(region[ring], axis=0) if ring.any() else np.zeros(3)
    domega = pixel_solid_angle(band, w, h)[:, None]
    excess = np.clip(region - background, 0, None) * inner[..., None] * domega[..., None]
    irradiance = excess.sum(axis=(0, 1))
    core_deg = 2 * math.degrees(math.sqrt(blob.sum() * (2 * math.pi / w) * (math.pi / h)
                                          / max(math.cos(math.radians(lat)), 0.05) / math.pi))
    return {
        "lon_deg": lon, "lat_deg": lat, "u": (cx % w + 0.5) / w, "v": (cy + 0.5) / h,
        "peak": peak, "sky_median": sky_level, "clipped": bool(clipped),
        "measured_irradiance_rgb": [float(v) for v in irradiance],
        "core_diameter_deg": core_deg, "radius_deg": radius_deg,
    }


def _band(lat, half_deg, h):
    top = max(0, int(math.floor((0.5 - min(90.0, lat + half_deg) / 180.0) * h)) - 1)
    bottom = min(h - 1, int(math.ceil((0.5 - max(-90.0, lat - half_deg) / 180.0) * h)) + 1)
    return np.arange(top, bottom + 1)


def sun_mask(shape, lon, lat, radius_deg):
    h, w = shape[:2]
    band = _band(lat, radius_deg * 1.05, h)
    dirs = _directions(band, w, h)
    ang = np.degrees(np.arccos(np.clip(dirs @ _vector(lon, lat), -1, 1)))
    mask = np.zeros((h, w), bool)
    mask[band[0]: band[-1] + 1] = ang <= radius_deg
    return mask


def remove_sun(rgb, info):
    """Copy of rgb with the sun disc + glow replaced by the surrounding sky."""
    import cv2

    out = rgb.copy()
    mask = sun_mask(rgb.shape, info["lon_deg"], info["lat_deg"], info["radius_deg"])
    h, w = rgb.shape[:2]
    shift = w // 2 - int(info["u"] * w)
    rows = np.flatnonzero(mask.any(axis=1))
    r0 = max(0, rows[0] - (rows[-1] - rows[0]) - 4)
    r1 = min(h, rows[-1] + (rows[-1] - rows[0]) + 5)
    region = np.roll(out[r0:r1], shift, axis=1)
    m = np.roll(mask[r0:r1], shift, axis=1)
    filled = fill_holes(region.copy(), ~m)
    # soft edge: blend over the outer 30% of the radius
    inside = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 5)
    edge = max(2.0, 0.3 * info["radius_deg"] / 180.0 * h)
    a = np.clip(inside / edge, 0, 1)[..., None]
    region = filled * a + region * (1 - a)
    out[r0:r1] = np.roll(region, -shift, axis=1)
    return out


def suggest(info, rgb, gamut="rec709", card_ratio=None, clear_sky_ratio=7.0):
    """Fill in the suggested Sun lamp strength/colour and Blender rotation."""
    luma = np.array(GAMUTS[gamut]["luma"])
    mask = sun_mask(rgb.shape, info["lon_deg"], info["lat_deg"], info["radius_deg"])
    e_sky = sky_irradiance(rgb, gamut, exclude=mask)
    info["sky_horizontal_irradiance_rgb"] = [float(v) for v in e_sky]
    measured = np.array(info["measured_irradiance_rgb"])
    if info["clipped"] or measured.max() <= 0:
        colour = np.ones(3)   # a clipped sun's colour can't be measured: white-balanced white
    else:
        colour = measured / measured.max()
    elev = math.radians(max(info["lat_deg"], 1.0))
    if card_ratio:
        total = float(e_sky @ luma) * max(card_ratio - 1.0, 0.0) / math.sin(elev)
        how = "gray card sun/shade ratio %.2f" % card_ratio
    elif info["clipped"]:
        total = max(clear_sky_ratio * float(e_sky @ luma), float(measured @ luma))
        how = ("sun clipped: clear-sky estimate (%.0fx the sky light); calibrate it"
               % clear_sky_ratio)
    else:
        total = float(measured @ luma)
        how = "measured from the HDRI"
    strength = total / max(float(colour @ luma), 1e-12)
    info.update({
        "strength": strength,
        "color": [float(c) for c in colour],
        "strength_source": how,
        "angle_deg": SUN_DIAMETER_DEG,
        "gamut": gamut,
        # Blender: Sun lamp rotation (XYZ Euler, degrees) for a World whose
        # Mapping node has Z rotation 0. Subtract the Mapping Z rotation from
        # the Z value if you rotate the HDRI (the add-on does this for you).
        "blender_rotation_deg": [90.0 - info["lat_deg"], 0.0, 90.0 - info["lon_deg"]],
    })
    return info


def write_json(path, info):
    with open(path, "w") as f:
        json.dump(info, f, indent=2)
