"""HDRIStitch: bracketed fisheye raws -> stitched, scene-linear HDRI (EXR).

Pipeline: group brackets -> decode raws linearly (LibRaw) with your white
balance -> merge each camera position to HDR -> align with Hugin (or your rig
template) -> remap + blend in floating point -> equirectangular EXR.
"""

__version__ = "1.0.0"
