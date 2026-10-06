"""Exposure metadata for raw files: exiftool if available, else a pure-Python
read of the TIFF/EXIF tags (ARW and DNG are TIFF based)."""
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TAGS = ["DateTimeOriginal", "SubSecTimeOriginal", "ExposureTime", "FNumber", "ISO",
        "FocalLength", "Model", "LensModel"]


@dataclass
class Shot:
    path: Path
    time: float          # seconds since epoch (local clock, sub-second if known)
    exposure_time: float  # seconds
    fnumber: float
    iso: float
    focal_length: float = 0.0
    model: str = ""
    lens: str = ""

    @property
    def exposure(self):
        """Relative exposure H = t * ISO/100 / N^2 (EV100 = -log2 H)."""
        return self.exposure_time * (self.iso / 100.0) / (self.fnumber ** 2)

    @property
    def ev100(self):
        return -math.log2(self.exposure)

    @property
    def name(self):
        return self.path.name


def _parse_time(text, subsec=""):
    if not text:
        return 0.0
    text = str(text).strip()
    main = text[:19]
    try:
        stamp = datetime.strptime(main, "%Y:%m:%d %H:%M:%S").timestamp()
    except ValueError:
        return 0.0
    frac = str(subsec or "").strip()
    if not frac and len(text) > 20 and text[19] == ".":
        frac = "".join(ch for ch in text[20:] if ch.isdigit())
    if frac and frac.isdigit():
        stamp += float("0." + frac)
    return stamp


def _number(value, default=0.0):
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return float(value[0]) / float(value[1]) if value[1] else default
    text = str(value).strip()
    if "/" in text:
        num, den = text.split("/", 1)
        return float(num) / float(den)
    try:
        return float(text.split()[0])
    except ValueError:
        return default


def find_exiftool(configured=""):
    if configured:
        return configured if Path(configured).exists() else None
    for candidate in (shutil.which("exiftool"), "/opt/homebrew/bin/exiftool",
                      "/usr/local/bin/exiftool"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _read_exiftool(paths, exiftool):
    shots = []
    for start in range(0, len(paths), 200):
        chunk = [str(p) for p in paths[start:start + 200]]
        cmd = [exiftool, "-json", "-n", "-q"] + ["-" + t for t in TAGS] + chunk
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if out.returncode not in (0, 1) or not out.stdout.strip():
            raise RuntimeError("exiftool failed: %s" % out.stderr.strip())
        for rec in json.loads(out.stdout):
            shots.append(_shot_from_tags(Path(rec["SourceFile"]), rec))
    order = {str(Path(p)): i for i, p in enumerate(paths)}
    shots.sort(key=lambda s: order.get(str(s.path), 0))
    return shots


def _shot_from_tags(path, tags):
    exposure_time = _number(tags.get("ExposureTime"))
    fnumber = _number(tags.get("FNumber"), 0.0)
    iso = _number(tags.get("ISO") or tags.get("ISOSpeedRatings")
                  or tags.get("PhotographicSensitivity"), 100.0)
    if exposure_time <= 0:
        raise ValueError("%s has no exposure time in its EXIF" % path.name)
    if fnumber <= 0:
        # Manual lenses (many fisheyes) report no aperture. All brackets share
        # one aperture, so any constant works for the relative exposure.
        fnumber = 8.0
    return Shot(path=path,
                time=_parse_time(tags.get("DateTimeOriginal"), tags.get("SubSecTimeOriginal")),
                exposure_time=exposure_time, fnumber=fnumber, iso=iso,
                focal_length=_number(tags.get("FocalLength")),
                model=str(tags.get("Model") or ""), lens=str(tags.get("LensModel") or ""))


def _read_tifffile(path):
    import tifffile

    names = {"DateTimeOriginal", "SubsecTimeOriginal", "SubSecTimeOriginal", "ExposureTime",
             "FNumber", "ISOSpeedRatings", "PhotographicSensitivity", "FocalLength", "Model",
             "LensModel"}
    tags = {}
    with tifffile.TiffFile(path) as tif:
        for page in tif.pages:
            for tag in page.tags.values():
                if tag.name in names:
                    tags.setdefault(tag.name, tag.value)
                if tag.name == "ExifTag" and isinstance(tag.value, dict):
                    for key, value in tag.value.items():
                        if key in names:
                            tags.setdefault(key, value)
    if "SubsecTimeOriginal" in tags:
        tags["SubSecTimeOriginal"] = tags.pop("SubsecTimeOriginal")
    iso = tags.get("ISOSpeedRatings") or tags.get("PhotographicSensitivity")
    if isinstance(iso, (tuple, list)):
        iso = iso[0]
    tags["ISO"] = iso
    return _shot_from_tags(Path(path), tags)


def read_shots(paths, exiftool=""):
    """Exposure info for each raw, in the order given."""
    paths = [Path(p) for p in paths]
    tool = find_exiftool(exiftool)
    if tool:
        return _read_exiftool(paths, tool)
    return [_read_tifffile(p) for p in paths]
