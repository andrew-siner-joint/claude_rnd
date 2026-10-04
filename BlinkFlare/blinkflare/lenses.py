"""Ghosts computed from a lens prescription.

A ghost is light that reflects off two lens surfaces, a later one and then an
earlier one, before reaching the sensor. Using first-order ray transfer
matrices (the approach of Lee & Eisemann 2013, a fast approximation of the
ray tracing in Hullin et al. 2011, which Animal Logic's LEGO Movie 2 flares
built on), the ghost of a light at field angle u lands at  B_g * u  on the
sensor while the light itself images at  f * u , and it is spread over a
radius  |A_g| * (entrance pupil radius) . Doing that per wavelength (glass
dispersion) and weighting by the two surfaces' coating reflectances gives
each ghost's position along the flare axis, size, colour and brightness.

Prescriptions use PBRT's lens file format, one surface per line, object side
first:  radius  thickness  ior  aperture-diameter  [abbe]  (mm). Radius 0 is
flat; a line with ior 0 is the aperture stop. Extra .dat files in the folders
on BLINKFLARE_LENS_PATH appear in the Lens menu.
"""

import math
import os
from collections import OrderedDict

WAVELENGTHS = (0.610, 0.545, 0.465)  # micrometres for red, green, blue
COATING_INDEX = 1.38                  # MgF2
COATINGS = ["Single Coated", "Multi Coated", "Uncoated"]
MULTI_COAT_CENTRES = (0.48, 0.60, 0.52, 0.64, 0.56, 0.50, 0.62)
BRIGHTNESS = 250.0                    # maps ghost energy to a useful default level
MIN_RADIUS = 0.004                    # of the sensor height
MAX_RADIUS = 2.0                      # bigger ghosts are a uniform wash; dropped
SENSOR_REFLECTANCE = 0.08             # digital sensors reflect a fair amount back
SOFT_CEILING = 1.0                    # brightness roll-off for near-focused ghosts


BUILTIN = OrderedDict([
    ("Double Gauss 50mm", """
# Double Gauss f/2, the classic normal-lens design (Kolb et al. 1995 / PBRT's
# dgauss.50mm data; Abbe numbers estimated).
29.475   3.76   1.670  25.2  47.2
84.83    0.12   1      25.2
19.275   4.025  1.670  23    47.2
40.77    3.275  1.699  23    30.1
12.75    5.705  1      18
0        4.5    0      17.1
-14.495  1.18   1.603  17    38.0
40.77    6.065  1.658  20    50.9
-20.385  0.19   1      20
437.065  3.22   1.717  20    47.9
-39.73   0      1      20
"""),
    ("Cooke Triplet 50mm", """
# Cooke triplet f/5 (the common textbook / Zemax sample form).
22.0136   3.259  1.6204  20  60.3
-435.760  6.008  1       20
-22.2133  1.000  1.6200  16  36.4
20.2919   2.000  1       16
0         2.750  0       14
79.6836   2.952  1.6204  18  60.3
-18.3783  0      1       18
"""),
    ("Achromat 100mm", """
# Cemented achromatic doublet: few surfaces, few but strong ghosts.
0         2.39   0       24
73.57     7.18    1.5688  25.4  56.0
-53.43    2.99    1.6727  25.4  32.2
-155.52   0      1       25.4
"""),
])


class Surface(object):
    def __init__(self, radius, thickness, ior, aperture, abbe=None, sensor=False):
        self.radius = radius
        self.thickness = thickness
        self.sensor = sensor
        self.stop = ior == 0
        self.ior = ior
        self.aperture = aperture
        if abbe is None and ior > 1.0001:
            # Rough glass-map estimate: crowns ~60, dense flints ~30.
            abbe = max(25.0, min(70.0, 60.0 - (ior - 1.5) * 125.0))
        self.abbe = abbe

    def index(self, wavelength):
        """Refractive index of the medium after this surface (Cauchy fit)."""
        if self.stop or self.ior <= 1.0001:
            return None if self.stop else 1.0
        lf, lc, ld = 0.4861, 0.6563, 0.5876
        b = (self.ior - 1.0) / (self.abbe * (1.0 / lf ** 2 - 1.0 / lc ** 2))
        a = self.ior - b / ld ** 2
        return a + b / wavelength ** 2


def parse(text):
    surfaces = []
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        values = [float(v) for v in line.split()]
        if len(values) < 4:
            raise ValueError("lens line needs radius thickness ior aperture: %r" % line)
        surfaces.append(Surface(*values[:5]))
    if not any(s.stop for s in surfaces):
        raise ValueError("lens has no aperture stop (a line with ior 0)")
    return surfaces


def lens_files():
    found = OrderedDict()
    for folder in os.environ.get("BLINKFLARE_LENS_PATH", "").split(os.pathsep):
        if folder and os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".dat"):
                    found[os.path.splitext(name)[0]] = os.path.join(folder, name)
    return found


def lens_names():
    return list(BUILTIN) + [n for n in lens_files() if n not in BUILTIN] + ["From File"]


def load(name, path=""):
    if name == "From File" or (path and name not in BUILTIN):
        with open(path or lens_files()[name]) as f:
            return parse(f.read())
    if name in BUILTIN:
        return parse(BUILTIN[name])
    with open(lens_files()[name]) as f:
        return parse(f.read())


# ------------------------------------------------------------------ matrices

def _mul(a, b):
    return ((a[0][0] * b[0][0] + a[0][1] * b[1][0], a[0][0] * b[0][1] + a[0][1] * b[1][1]),
            (a[1][0] * b[0][0] + a[1][1] * b[1][0], a[1][0] * b[0][1] + a[1][1] * b[1][1]))


def _translate(d):
    return ((1.0, d), (0.0, 1.0))


def _refract(n1, n2, radius):
    power = (n1 - n2) / (n2 * radius) if radius else 0.0
    return ((1.0, 0.0), (power, n1 / n2))


def _reflect(radius):
    """Mirror at a surface, in unfolded coordinates (the reflected ray keeps
    travelling 'forward' through a mirrored copy of the lens)."""
    return ((1.0, 0.0), ((2.0 / radius) if radius else 0.0, 1.0))


def _media(surfaces, wavelength):
    """Index of the medium after each surface (the stop takes the medium before it)."""
    media, prev = [], 1.0
    for s in surfaces:
        n = s.index(wavelength)
        n = prev if n is None else n
        media.append(n)
        prev = n
    return media


def system(surfaces, wavelength):
    """Matrix from the entrance to just after the last surface."""
    m, prev = ((1.0, 0.0), (0.0, 1.0)), 1.0
    for s, n in zip(surfaces, _media(surfaces, wavelength)):
        m = _mul(_refract(prev, n, s.radius), m)
        m = _mul(_translate(s.thickness), m)
        prev = n
    return m


def focal_length(surfaces, wavelength=WAVELENGTHS[1]):
    return -1.0 / system(surfaces, wavelength)[1][0]


def back_focus(surfaces, wavelength=WAVELENGTHS[1]):
    m = system(surfaces, wavelength)
    return -m[0][0] / m[1][0]


def ghost_matrix(surfaces, i, j, wavelength, sensor_distance):
    """Entrance-to-sensor matrix for light reflecting at surface j, then i."""
    media = _media(surfaces, wavelength)
    n_before = [1.0] + media[:-1]
    m = ((1.0, 0.0), (0.0, 1.0))
    for k in range(j):                         # forward to surface j
        m = _mul(_refract(n_before[k], media[k], surfaces[k].radius), m)
        m = _mul(_translate(surfaces[k].thickness), m)
    m = _mul(_reflect(surfaces[j].radius), m)
    for k in range(j - 1, i - 1, -1):          # back to surface i
        m = _mul(_translate(surfaces[k].thickness), m)
        if k > i:
            m = _mul(_refract(media[k], n_before[k], -surfaces[k].radius), m)
    m = _mul(_reflect(-surfaces[i].radius), m)
    m = _mul(_translate(surfaces[i].thickness), m)
    for k in range(i + 1, len(surfaces)):      # forward to the sensor
        m = _mul(_refract(n_before[k], media[k], surfaces[k].radius), m)
        m = _mul(_translate(surfaces[k].thickness), m)
    return _mul(_translate(sensor_distance), m)


# ------------------------------------------------------------------ coatings

def reflectance(n1, n2, wavelength, coating, surface_index, sensor=False):
    """Normal-incidence reflectance of one surface."""
    if sensor:
        return SENSOR_REFLECTANCE
    if abs(n1 - n2) < 1e-6:
        return 0.0
    bare = ((n1 - n2) / (n1 + n2)) ** 2
    cemented = min(n1, n2) > 1.0001
    if coating == "Uncoated" or cemented:
        return bare
    nc = COATING_INDEX
    centre = 0.55
    if coating == "Multi Coated":
        centre = MULTI_COAT_CENTRES[surface_index % len(MULTI_COAT_CENTRES)]
    r1 = (n1 - nc) / (n1 + nc)
    r2 = (nc - n2) / (nc + n2)
    phase = math.pi * centre / wavelength  # quarter-wave layer: 2*beta = pi at the centre
    c = math.cos(phase)
    r = (r1 * r1 + r2 * r2 + 2 * r1 * r2 * c) / (1 + r1 * r1 * r2 * r2 + 2 * r1 * r2 * c)
    return r * (0.3 if coating == "Multi Coated" else 1.0)


# -------------------------------------------------------------------- ghosts

class Ghost(object):
    def __init__(self, i, j, axis, size, color):
        self.i, self.j = i, j
        self.axis = axis    # per channel: position along the flare axis (0 light, 1 centre)
        self.size = size    # per channel: radius in frame heights
        self.color = color  # per channel: brightness

    def __repr__(self):
        return "Ghost(%d,%d axis=%.3f size=%.4f)" % (self.i, self.j, self.axis[1], self.size[1])


def with_sensor(surfaces):
    """The lens focused at infinity, with the sensor added as a final,
    reflecting surface (sensor ghosts are some of the most visible ones)."""
    green = WAVELENGTHS[1]
    bfd = back_focus(surfaces, green)
    last = surfaces[-1]
    out = surfaces[:-1] + [Surface(last.radius, bfd, last.ior, last.aperture, last.abbe)]
    return out + [Surface(0.0, 0.0, 1.0, 1e9, sensor=True)]


def ghosts(surfaces, fstop=2.8, coating="Single Coated", sensor_height=24.0, max_ghosts=24):
    """The brightest ``max_ghosts`` ghosts of a lens, brightest first."""
    green = WAVELENGTHS[1]
    f = focal_length(surfaces, green)
    pupil = f / (2.0 * max(fstop, 0.5))
    surfaces = with_sensor(surfaces)
    image_scale = system(surfaces, green)[0][1]  # == f, as the sensor is at focus
    found = []
    reflective = [k for k, s in enumerate(surfaces) if not s.stop]
    for a, i in enumerate(reflective):
        for j in reflective[a + 1:]:
            axis, size, color = [], [], []
            for w in WAVELENGTHS:
                m = ghost_matrix(surfaces, i, j, w, 0.0)
                media = _media(surfaces, w)
                n_before = [1.0] + media[:-1]
                ri = reflectance(n_before[i], media[i], w, coating, i)
                rj = reflectance(n_before[j], media[j], w, coating, j, surfaces[j].sensor)
                radius = max(abs(m[0][0]) * pupil / sensor_height, MIN_RADIUS)
                axis.append(1.0 - m[0][1] / image_scale)
                size.append(radius)
                # Energy through the pupil spread over the ghost's area.
                energy = BRIGHTNESS * ri * rj * (pupil / sensor_height / radius) ** 2
                # Near-focused ghosts would outshine the light; roll them off.
                color.append(energy / (1.0 + energy / SOFT_CEILING))
            if size[1] > MAX_RADIUS or max(color) <= 0.0:
                continue
            found.append(Ghost(i, j, tuple(axis), tuple(size), tuple(color)))
    found.sort(key=lambda g: -visibility(g))
    return found[:max_ghosts]


def visibility(ghost):
    """Ranking for max_ghosts: brightness times (capped) area, discounted for
    ghosts so far along the axis that they're rarely in frame (a ghost at
    axis t sits |1 - t| light-to-centre distances from the frame centre)."""
    beyond = max(abs(1.0 - ghost.axis[1]) - 1.5, 0.0)
    return sum(ghost.color) * min(ghost.size[1], 0.25) ** 2 / (1.0 + beyond * beyond)


_cache = {}


def ghosts_for_element(el):
    key = (el.get("lens"), el.get("lens_file"), el.get("coating"), int(el.get("max_ghosts", 24)),
           float(el.get("sensor", 24.0)), float(el.get("fstop", 2.8)))
    if key not in _cache:
        surfaces = load(el.get("lens", "Double Gauss 50mm"), el.get("lens_file", ""))
        _cache[key] = ghosts(surfaces, fstop=key[5], coating=key[2], sensor_height=key[4],
                             max_ghosts=key[3])
    return _cache[key]
