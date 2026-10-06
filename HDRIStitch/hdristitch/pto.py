"""Just enough of Hugin's .pto format to read image geometry, swap images,
and write the projects HDRIStitch renders from."""
import re
from pathlib import Path

_TOKEN = re.compile(r'(\S+?"[^"]*"|\S+)')
_IMAGE_KEYS = sorted(
    ["w", "h", "f", "v", "y", "p", "r", "TrX", "TrY", "TrZ", "Tpy", "Tpp", "j", "a", "b", "c",
     "d", "e", "g", "t", "Va", "Vb", "Vc", "Vd", "Vx", "Vy", "Vm", "Vf", "Ra", "Rb", "Rc", "Rd",
     "Re", "Eev", "Er", "Eb", "S", "C", "n", "K", "Ns"], key=len, reverse=True)
# Photometric parameters reset to "no change" when remapping coordinates.
_NEUTRAL = {"Eev": "0", "Er": "1", "Eb": "1", "Ra": "0", "Rb": "0", "Rc": "0", "Rd": "0",
            "Re": "0", "Va": "1", "Vb": "0", "Vc": "0", "Vd": "0", "Vx": "0", "Vy": "0",
            "Vm": "5"}


def _split(line):
    return _TOKEN.findall(line)


def _key(token):
    for key in _IMAGE_KEYS:
        if token.startswith(key):
            return key, token[len(key):]
    return None, token


class ImageLine:
    def __init__(self, line):
        tokens = _split(line)
        assert tokens[0] == "i"
        self.items = []  # [key, raw value] in original order
        for tok in tokens[1:]:
            key, value = _key(tok)
            self.items.append([key, value])

    def raw(self, key):
        for k, v in self.items:
            if k == key:
                return v
        return None

    def set(self, key, value):
        for item in self.items:
            if item[0] == key:
                item[1] = value
                return
        self.items.insert(len(self.items) - 1 if self.items and self.items[-1][0] == "n"
                          else len(self.items), [key, value])

    def remove(self, key):
        self.items = [it for it in self.items if it[0] != key]

    @property
    def filename(self):
        value = self.raw("n")
        return value.strip('"') if value else ""

    @filename.setter
    def filename(self, name):
        self.set("n", '"%s"' % name)

    def text(self):
        return "i " + " ".join((k or "") + v for k, v in self.items)


class Project:
    def __init__(self, text):
        self.lines = text.splitlines()

    @classmethod
    def load(cls, path):
        return cls(Path(path).read_text())

    def save(self, path):
        Path(path).write_text("\n".join(self.lines) + "\n")

    # -- images
    def _image_indices(self):
        return [k for k, line in enumerate(self.lines) if line.startswith("i ")]

    @property
    def images(self):
        return [ImageLine(self.lines[k]) for k in self._image_indices()]

    def set_images(self, images):
        idx = self._image_indices()
        assert len(idx) == len(images)
        for k, img in zip(idx, images):
            self.lines[k] = img.text()

    def value(self, image_index, key, default=0.0):
        """Numeric value of an image variable, following links like v=0."""
        images = self.images
        seen = set()
        k = image_index
        while True:
            raw = images[k].raw(key)
            if raw is None or raw == "":
                return default
            if raw.startswith("="):
                k = int(raw[1:])
                if k in seen:
                    return default
                seen.add(k)
                continue
            return float(raw)

    def crop(self, image_index):
        raw = self.images[image_index].raw("S")
        if not raw:
            return None
        return [int(round(float(v))) for v in raw.split(",")]

    def pose(self, image_index):
        return tuple(self.value(image_index, k) for k in ("y", "p", "r"))

    # -- other lines
    def drop(self, prefix):
        self.lines = [ln for ln in self.lines if not ln.startswith(prefix)]

    def count(self, prefix):
        return sum(1 for ln in self.lines if ln.startswith(prefix))

    def set_line(self, prefix, text):
        for k, line in enumerate(self.lines):
            if line.startswith(prefix):
                self.lines[k] = text
                return
        self.lines.insert(0, text)

    def panorama_line(self):
        for line in self.lines:
            if line.startswith("p "):
                return line
        return ""


def apply_template(template, filenames, sizes=None):
    """A copy of `template` (Project) with its images replaced, in order."""
    proj = Project("\n".join(template.lines))
    images = proj.images
    if len(images) != len(filenames):
        raise ValueError("The rig template has %d images but this shoot has %d positions"
                         % (len(images), len(filenames)))
    for k, (img, name) in enumerate(zip(images, filenames)):
        if sizes is not None:
            w, h = sizes[k]
            tw, th = int(float(img.raw("w"))), int(float(img.raw("h")))
            if (tw, th) != (w, h):
                raise ValueError("The rig template expects %dx%d images, this shoot has %dx%d. "
                                 "Different camera, or crop mode?" % (tw, th, w, h))
        img.filename = name
    proj.set_images(images)
    proj.drop("c ")
    return proj


def as_template(project):
    """Strip a finished project down to a reusable rig template."""
    proj = Project("\n".join(project.lines))
    proj.drop("c ")
    proj.lines.insert(0, "# HDRIStitch rig template: lens, positions and masks; images are "
                         "replaced on use")
    return proj


def coordinate_project(project, ramp_name, width, height):
    """Project that renders the coordinate ramp through every image's
    geometry: equirect 360x180 canvas, bilinear, photometrics neutralised."""
    proj = Project("\n".join(project.lines))
    images = proj.images
    for img in images:
        img.filename = ramp_name
        for key, value in _NEUTRAL.items():
            img.set(key, value)
        img.remove("Vf")
    proj.set_images(images)
    proj.set_line("p ", 'p f2 w%d h%d v360 k0 E0 R0 n"TIFF_m c:NONE r:CROP"' % (width, height))
    proj.set_line("m ", "m i5")
    proj.drop("c ")
    return proj
