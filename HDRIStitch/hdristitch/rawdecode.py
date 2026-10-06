"""Linear raw decoding with LibRaw (via rawpy).

Every frame is developed identically: camera-native RGB, no white balance
(gains of 1, so no channel clips early), no gamma, no auto-brightening, no
rotation, and LibRaw's per-image white-level adjustment off. Values are
normalised so 1.0 = the sensor's white level; white balance and the colour
matrix are applied after merging, in floating point.
"""
from dataclasses import dataclass

import numpy as np

DEMOSAIC = ("AHD", "DHT", "AAHD", "VNG", "PPG", "DCB", "LINEAR")


@dataclass
class CameraInfo:
    cam_from_xyz: np.ndarray   # 3x3, XYZ -> camera (Adobe/LibRaw convention)
    as_shot: np.ndarray        # as-shot white balance gains (R, G, B), G = 1
    width: int
    height: int
    model: str = ""


@dataclass
class Frame:
    rgb: np.ndarray        # float32 HxWx3 camera RGB, 1.0 = white level
    saturated: np.ndarray  # bool HxW, sensor clipped here (dilated)
    clip: float            # normalised level where this sensor actually clips


def camera_info(path):
    import rawpy

    from .color import XYZ_FROM_REC709

    with rawpy.imread(str(path)) as raw:
        cam = np.array(raw.rgb_xyz_matrix, float)[:3, :3]
        daylight = np.array(raw.daylight_whitebalance, float)[:3]
        if not np.any(np.abs(cam) > 1e-6):
            # DNGs keep their matrix elsewhere in LibRaw; rebuild it from the
            # camera -> sRGB matrix and daylight gains LibRaw derived from it.
            rgb_cam = np.array(raw.color_matrix, float)[:3, :3]
            cam = np.diag(1.0 / daylight) @ np.linalg.inv(rgb_cam) @ np.linalg.inv(XYZ_FROM_REC709)
        wb = np.array(raw.camera_whitebalance, float)[:3]
        if not np.all(wb > 0):
            wb = daylight
        sizes = raw.sizes
        return CameraInfo(cam_from_xyz=cam, as_shot=wb / wb[1], width=sizes.width,
                          height=sizes.height)


def _dilate(mask, radius):
    import cv2

    if radius <= 0 or not mask.any():
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)


def decode(path, demosaic="AHD"):
    import rawpy

    algo = getattr(rawpy.DemosaicAlgorithm, demosaic.upper())
    with rawpy.imread(str(path)) as raw:
        black = np.array(raw.black_level_per_channel, float)
        white = float(raw.white_level)
        cfa = raw.raw_image_visible
        span = white - float(black.mean())

        # Where does this sensor really clip? Often a little under the
        # nominal white level. A pile-up of pixels at the very top marks it.
        top = int(cfa.max())
        clip_raw = white
        if top > black.mean() + 0.6 * span:
            pile = np.count_nonzero(cfa >= top - max(2, int(0.002 * span)))
            if pile > max(16, cfa.size * 1e-6):
                clip_raw = min(white, float(top))
        clip = (clip_raw - black.mean()) / span
        thresholds = np.floor(black + 0.985 * (clip_raw - black)).astype(cfa.dtype)
        if np.all(thresholds == thresholds[0]):
            sat_cfa = cfa >= thresholds[0]
        else:  # per-channel black levels
            sat_cfa = cfa >= thresholds[raw.raw_colors_visible]

        rgb16 = raw.postprocess(
            demosaic_algorithm=algo,
            output_color=rawpy.ColorSpace.raw,
            output_bps=16,
            gamma=(1, 1),
            no_auto_bright=True,
            bright=1.0,
            use_camera_wb=False,
            use_auto_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0],
            user_flip=0,
            highlight_mode=rawpy.HighlightMode.Clip,
            adjust_maximum_thr=0.0,
            median_filter_passes=0,
        )
    rgb = rgb16.astype(np.float32)
    rgb *= np.float32(1.0 / 65535.0)
    if sat_cfa.shape == rgb.shape[:2]:
        saturated = _dilate(sat_cfa, 2)
    else:  # sensor and output geometry differ (rare): judge on the RGB instead
        saturated = _dilate((rgb >= 0.985 * clip).any(axis=2), 3)
    return Frame(rgb=rgb, saturated=saturated, clip=float(clip))
