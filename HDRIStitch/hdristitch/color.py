"""White balance (kelvin/tint, Adobe's scale) and camera -> output colour
matrices.

Temperature/tint follow the DNG SDK (dng_temperature.cpp): Robertson's
isotemperature lines in CIE 1960 uv, tint = offset along the line x -3000.
So "5600, +10" here means what it means in Lightroom and Camera Raw. The
camera matrix is the single D65 matrix LibRaw carries (Adobe's ColorMatrix2),
so away from daylight the result is close to, not identical to, Lightroom.
"""
import math
import re
from pathlib import Path

import numpy as np

# (mired, u, v, slope) -- Wyszecki & Stiles, with the corrected 325 mired row.
ROBERTSON = [
    (0, 0.18006, 0.26352, -0.24341), (10, 0.18066, 0.26589, -0.25479),
    (20, 0.18133, 0.26846, -0.26876), (30, 0.18208, 0.27119, -0.28539),
    (40, 0.18293, 0.27407, -0.30470), (50, 0.18388, 0.27709, -0.32675),
    (60, 0.18494, 0.28021, -0.35156), (70, 0.18611, 0.28342, -0.37915),
    (80, 0.18740, 0.28668, -0.40955), (90, 0.18880, 0.28997, -0.44278),
    (100, 0.19032, 0.29326, -0.47888), (125, 0.19462, 0.30141, -0.58204),
    (150, 0.19962, 0.30921, -0.70471), (175, 0.20525, 0.31647, -0.84901),
    (200, 0.21142, 0.32312, -1.0182), (225, 0.21807, 0.32909, -1.2168),
    (250, 0.22511, 0.33439, -1.4512), (275, 0.23247, 0.33904, -1.7298),
    (300, 0.24010, 0.34308, -2.0637), (325, 0.24792, 0.34655, -2.4681),
    (350, 0.25591, 0.34951, -2.9641), (375, 0.26400, 0.35200, -3.5814),
    (400, 0.27218, 0.35407, -4.3633), (425, 0.28039, 0.35577, -5.3762),
    (450, 0.28863, 0.35714, -6.7262), (475, 0.29685, 0.35823, -8.5955),
    (500, 0.30505, 0.35907, -11.324), (525, 0.31320, 0.35968, -15.628),
    (550, 0.32129, 0.36011, -23.325), (575, 0.32931, 0.36038, -40.770),
    (600, 0.33724, 0.36051, -116.45),
]
TINT_SCALE = -3000.0

XYZ_FROM_REC709 = np.array([[0.4124564, 0.3575761, 0.1804375],
                            [0.2126729, 0.7151522, 0.0721750],
                            [0.0193339, 0.1191920, 0.9503041]])
# D65 exactly as the Rec.709 matrix implies it, so "d65" white balance and
# the output matrix agree to the last digit
_W = XYZ_FROM_REC709.sum(axis=1)
D65_XY = (float(_W[0] / _W.sum()), float(_W[1] / _W.sum()))
# Linear Rec.709 (D65) -> target, Bradford-adapted where white points differ.
GAMUTS = {
    "rec709": dict(matrix=np.eye(3),
                   chromaticities=(0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290),
                   luma=(0.2126, 0.7152, 0.0722), label="Linear Rec.709 (sRGB primaries)"),
    "acescg": dict(matrix=np.array([[0.6130974024, 0.3395231462, 0.0473794514],
                                    [0.0701937225, 0.9163538791, 0.0134523985],
                                    [0.0206155929, 0.1095697729, 0.8698146342]]),
                   chromaticities=(0.713, 0.293, 0.165, 0.830, 0.128, 0.044, 0.32168, 0.33767),
                   luma=(0.2722287, 0.6740818, 0.0536895), label="ACEScg (AP1)"),
    "rec2020": dict(matrix=np.array([[0.6274040, 0.3292820, 0.0433136],
                                     [0.0690970, 0.9195400, 0.0113612],
                                     [0.0163916, 0.0880132, 0.8955950]]),
                    chromaticities=(0.708, 0.292, 0.170, 0.797, 0.131, 0.046, 0.3127, 0.3290),
                    luma=(0.2627, 0.6780, 0.0593), label="Linear Rec.2020"),
}


def kelvin_tint_to_xy(temperature, tint=0.0):
    """Adobe temperature/tint -> CIE xy (DNG SDK Get_xy_coord)."""
    r = 1.0e6 / temperature
    offset = tint * (1.0 / TINT_SCALE)
    for index in range(30):
        if r < ROBERTSON[index + 1][0] or index == 29:
            r0, u0, v0, t0 = ROBERTSON[index]
            r1, u1, v1, t1 = ROBERTSON[index + 1]
            f = (r1 - r) / (r1 - r0)
            u = u0 * f + u1 * (1.0 - f)
            v = v0 * f + v1 * (1.0 - f)
            uu1, vv1 = 1.0, t0
            uu2, vv2 = 1.0, t1
            len1 = math.sqrt(1.0 + vv1 * vv1)
            len2 = math.sqrt(1.0 + vv2 * vv2)
            uu1, vv1 = uu1 / len1, vv1 / len1
            uu2, vv2 = uu2 / len2, vv2 / len2
            uu3 = uu1 * f + uu2 * (1.0 - f)
            vv3 = vv1 * f + vv2 * (1.0 - f)
            len3 = math.sqrt(uu3 * uu3 + vv3 * vv3)
            u += uu3 / len3 * offset
            v += vv3 / len3 * offset
            denom = u - 4.0 * v + 2.0
            return 1.5 * u / denom, v / denom
    raise ValueError(temperature)


def xy_to_kelvin_tint(x, y):
    """CIE xy -> Adobe temperature/tint (DNG SDK Set_xy_coord)."""
    denom = 1.5 - x + 6.0 * y
    u = 2.0 * x / denom
    v = 3.0 * y / denom
    last_dt = last_du = last_dv = 0.0
    for index in range(1, 31):
        r_i, u_i, v_i, t_i = ROBERTSON[index]
        du = 1.0
        dv = t_i
        length = math.sqrt(1.0 + dv * dv)
        du /= length
        dv /= length
        uu = u - u_i
        vv = v - v_i
        dt = -uu * dv + vv * du
        if dt <= 0.0 or index == 30:
            if dt > 0.0:
                dt = 0.0
            dt = -dt
            f = 0.0 if index == 1 else dt / (last_dt + dt)
            r_p, u_p, v_p, _ = ROBERTSON[index - 1]
            temperature = 1.0e6 / (r_p * f + r_i * (1.0 - f))
            uu = u - (u_p * f + u_i * (1.0 - f))
            vv = v - (v_p * f + v_i * (1.0 - f))
            du = du * (1.0 - f) + last_du * f
            dv = dv * (1.0 - f) + last_dv * f
            length = math.sqrt(du * du + dv * dv)
            du /= length
            dv /= length
            tint = (uu * du + vv * dv) * TINT_SCALE
            return temperature, tint
        last_dt, last_du, last_dv = dt, du, dv
    raise ValueError((x, y))


def xy_to_xyz(x, y):
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def multipliers_for_xy(cam_from_xyz, xy):
    """Per-channel camera gains that make a neutral under illuminant `xy`
    come out neutral (green = 1)."""
    neutral = np.asarray(cam_from_xyz, float)[:3, :3] @ xy_to_xyz(*xy)
    if np.any(neutral <= 0):
        raise ValueError("White point outside the camera's range")
    mult = 1.0 / neutral
    return mult / mult[1]


def xy_for_multipliers(cam_from_xyz, mult):
    """Inverse of multipliers_for_xy: the illuminant a set of gains assumes."""
    mult = np.asarray(mult, float)[:3]
    neutral = 1.0 / mult
    xyz = np.linalg.solve(np.asarray(cam_from_xyz, float)[:3, :3], neutral)
    s = xyz.sum()
    return xyz[0] / s, xyz[1] / s


def describe_multipliers(cam_from_xyz, mult):
    """"5612 K, tint +4" for a set of gains."""
    try:
        temp, tint = xy_to_kelvin_tint(*xy_for_multipliers(cam_from_xyz, mult))
        return "%d K, tint %+d" % (round(temp), round(tint))
    except (ValueError, np.linalg.LinAlgError):
        return "custom"


def camera_to_output(cam_from_xyz, gamut="rec709"):
    """3x3 matrix taking white-balanced camera RGB to the output gamut.

    dcraw's construction: camera <- Rec.709 matrix, rows normalised so that a
    white-balanced neutral (1, 1, 1) maps to (1, 1, 1), then inverted."""
    cam_from_rgb = np.asarray(cam_from_xyz, float)[:3, :3] @ XYZ_FROM_REC709
    cam_from_rgb = cam_from_rgb / cam_from_rgb.sum(axis=1, keepdims=True)
    rgb_from_cam = np.linalg.inv(cam_from_rgb)
    return GAMUTS[gamut]["matrix"] @ rgb_from_cam


def luminance(rgb, gamut="rec709"):
    w = GAMUTS[gamut]["luma"]
    return rgb[..., 0] * w[0] + rgb[..., 1] * w[1] + rgb[..., 2] * w[2]


# ---------------------------------------------------------------- XMP sidecars

_XMP_ATTR = r'crs:%s\s*=\s*"([^"]*)"'
_XMP_ELEM = r'<crs:%s>([^<]*)</crs:%s>'


def _xmp_value(text, name):
    m = re.search(_XMP_ATTR % name, text) or re.search(_XMP_ELEM % (name, name), text)
    return m.group(1).strip() if m else None


def xmp_sidecar(raw_path):
    raw_path = Path(raw_path)
    for candidate in (raw_path.with_suffix(".xmp"), raw_path.with_suffix(".XMP"),
                      raw_path.with_name(raw_path.name + ".xmp")):
        if candidate.exists():
            return candidate
    return None


def read_xmp_white_balance(raw_path):
    """(temperature, tint) from a Lightroom/Camera Raw sidecar, "asshot" if
    the sidecar says As Shot without numbers, or None if there's no sidecar
    or it carries no white balance."""
    sidecar = xmp_sidecar(raw_path)
    if sidecar is None:
        return None
    text = sidecar.read_text(errors="ignore")
    temp = _xmp_value(text, "Temperature")
    tint = _xmp_value(text, "Tint")
    mode = _xmp_value(text, "WhiteBalance")
    if temp:
        return float(temp), float(tint or 0)
    if mode:
        return "asshot"
    return None


def parse_wb(text):
    """Config/CLI white balance -> ("auto"|"asshot"|"xmp"|"d65") or (K, tint)."""
    text = str(text).strip().lower().replace(" ", "")
    if text in ("auto", "asshot", "xmp", "d65"):
        return text
    text = text.rstrip("k")
    parts = text.replace("k,", ",").split(",")
    try:
        temp = float(parts[0])
        tint = float(parts[1]) if len(parts) > 1 else 0.0
    except ValueError:
        raise ValueError("White balance must be auto, asshot, xmp, d65, a kelvin value "
                         "like 5600, or kelvin,tint like 5600,+10 (got %r)" % text)
    if not 1500 <= temp <= 50000:
        raise ValueError("Colour temperature %g K is out of range (1500-50000)" % temp)
    return temp, tint
