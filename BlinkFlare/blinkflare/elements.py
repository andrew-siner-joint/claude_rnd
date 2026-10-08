"""The element stack: element types, their parameters, and how each element
is packed into the table image the kernel reads.

A flare is any number of elements, each of one type. The kernel loops over
the columns of a small float image (one column per element, ``ROWS`` rows of
RGBA), so the number of elements isn't baked into the kernel. The group
builds that image with Expression nodes whose expressions point at each
element's knobs, so everything stays live and animatable.

Pure data and arithmetic (no Nuke): the builder, the preview renderer and the
tests share it.
"""

from collections import OrderedDict

ROWS = 6
MAX_COLUMNS = 96

# Table layout, one float4 per row:
#   row 0: type code, intensity, axis position, size
#   row 1: red, green, blue, render pass
#   row 2: rotation (deg), seed, dispersion, softness
#   row 3: p0..p3   (type-specific)
#   row 4: p4..p7   (type-specific)
#   row 5: extra    (lens ghosts: red axis, red size, blue axis, blue size)

# Render passes: what Output > Solo and Element Layers split the flare into.
PASSES = [
    (1, "Glow", "flare_glow"),
    (2, "Rays", "flare_rays"),
    (3, "Streaks", "flare_streaks"),
    (4, "Ghosts", "flare_ghosts"),
    (5, "Rings", "flare_rings"),
    (6, "Other", "flare_other"),
]
PASS_CODES = dict((name, code) for code, name, _ in PASSES)
# An element's Layer menu: Auto (its type's pass), then each pass by code.
LAYER_ITEMS = ["Auto"] + [name for _, name, _ in PASSES]

GENERIC = ("intensity", "color", "size", "axis", "rotation", "seed", "dispersion", "softness")


class Param(object):
    def __init__(self, label, default, lo=0.0, hi=1.0, tooltip=""):
        self.label = label
        self.default = float(default)
        self.lo = lo
        self.hi = hi
        self.tooltip = tooltip


class ElementType(object):
    """``generic`` maps each generic knob to (label, default) or None (hidden);
    ``params`` lists up to eight type-specific Param slots (p0..p7)."""

    def __init__(self, name, code, render_pass, generic, params, help=""):
        self.name = name
        self.code = code
        self.render_pass = render_pass
        self.generic = generic
        self.params = params
        self.help = help

    def defaults(self):
        values = {"type": self.name, "on": True, "layer": 0,
                  "p": [p.default for p in self.params] + [0.0] * (8 - len(self.params))}
        fallback = {"intensity": 1.0, "color": (1.0, 1.0, 1.0), "size": 0.1, "axis": 0.0,
                    "rotation": 0.0, "seed": 1, "dispersion": 0.0, "softness": 0.0}
        for name in GENERIC:
            spec = self.generic.get(name)
            values[name] = spec[1] if spec else fallback[name]
        return values


def _g(intensity=None, color=None, size=None, axis=None, rotation=None, seed=None,
       dispersion=None, softness=None):
    return {"intensity": intensity, "color": color, "size": size, "axis": axis,
            "rotation": rotation, "seed": seed, "dispersion": dispersion, "softness": softness}


WARM = (1.0, 0.86, 0.7)
WHITE = (1.0, 1.0, 1.0)

TYPES = OrderedDict((t.name, t) for t in [
    ElementType(
        "Glow", 1, "Glow",
        _g(intensity=("Intensity", 0.6), color=("Color", WARM), size=("Size", 0.08),
           axis=("Axis Position", 0.0), dispersion=("Chromatic Halo", 0.04)),
        [Param("Falloff", 1.4, 0.2, 4.0, "How fast the glow fades. Low values leave a long haze."),
         Param("Core Intensity", 6.0, 0.0, 50.0, "The hot, near-clipped centre."),
         Param("Core Size", 0.12, 0.0, 1.0, "Core radius as a fraction of Size."),
         Param("White Core", 0.8, 0.0, 1.0, "How much the centre burns out to white while the "
               "falloff keeps the colour.")],
        "Soft glow around the light with a hot core."),
    ElementType(
        "Veil", 2, "Glow",
        _g(intensity=("Intensity", 0.015), color=("Color", WARM), size=("Spread", 0.6),
           axis=("Axis Position", 0.0)),
        [Param("Lift", 0.1, 0.0, 1.0, "Uniform lift across the whole frame (veiling glare)."),
         Param("Falloff", 0.8, 0.1, 3.0)],
        "Veiling glare: the low-contrast wash a bright source throws over the frame."),
    ElementType(
        "Starburst", 3, "Rays",
        _g(intensity=("Intensity", 1.0), color=("Color", WARM), size=("Length", 0.4),
           rotation=("Rotation", 0.0), seed=("Seed", 1), dispersion=("Dispersion", 0.2)),
        [Param("Rays", 0, 0, 64, "0 = from the lens aperture: one spike per blade for even "
               "blade counts, two for odd."),
         Param("Length Random", 0.5, 0.0, 0.95),
         Param("Width", 1.5, 0.0, 10.0, "Thousandths of the frame height."),
         Param("Fine Rays", 0.4, 0.0, 2.0, "Thin, shorter rays between the spikes."),
         Param("Breakup", 0.3, 0.0, 1.0, "Irregular brightness along each ray.")],
        "Diffraction spikes from the aperture blades, with spectral fringing."),
    ElementType(
        "Streak", 4, "Streaks",
        _g(intensity=("Intensity", 1.2), color=("Color", (0.45, 0.65, 1.0)), size=("Length", 1.2),
           axis=("Axis Position", 0.0), rotation=("Angle", 0.0), seed=("Seed", 1),
           dispersion=("Dispersion", 0.1)),
        [Param("Thickness", 1.5, 0.0, 10.0, "Core thickness, thousandths of the frame height."),
         Param("Count", 1, 1, 8, "1 = a line, 2 = a cross, more = a star."),
         Param("Hot Core", 1.0, 0.0, 4.0, "How much brightness bunches up near the light."),
         Param("Haze", 0.6, 0.0, 2.0, "Soft wide glow around the core line."),
         Param("Lines", 1, 1, 5, "Parallel lines, as anamorphic lenses often show."),
         Param("Line Spacing", 4.0, 0.0, 40.0, "Thousandths of the frame height."),
         Param("Breakup", 0.15, 0.0, 1.0),
         Param("Haze Width", 12.0, 1.0, 60.0, "Width of the haze, in core thicknesses.")],
        "Anamorphic streak: a hard core line with exponential tails and haze."),
    ElementType(
        "Ring", 5, "Rings",
        _g(intensity=("Intensity", 0.06), color=("Color", WHITE), size=("Radius", 0.25),
           axis=("Axis Position", 0.0), rotation=("Arc Rotation", 0.0),
           dispersion=("Spectral", 0.8)),
        [Param("Thickness", 0.02, 0.0, 0.2),
         Param("Arc", 0.0, 0.0, 1.0, "Fade the ring into a partial arc (hoop) facing along "
               "the flare axis.")],
        "Halo or hoop, plain or rainbow."),
    ElementType(
        "Iris", 6, "Ghosts",
        _g(intensity=("Intensity", 0.15), color=("Color", (1.0, 0.8, 0.95)), size=("Size", 0.05),
           axis=("Axis Position", 1.4), rotation=("Rotation", 0.0), seed=("Seed", 1),
           dispersion=("Chromatic Fringe", 0.05), softness=("Softness", 0.1)),
        [Param("Blades", 0, 0, 16, "0 = the lens aperture (Lens tab)."),
         Param("Hollow", 0.3, 0.0, 1.0),
         Param("Rim", 0.3, 0.0, 2.0, "Brighter edge, like a defocused aperture image."),
         Param("Barrel Clip", 1.0, 0.0, 2.0, "Scales the Lens tab's barrel clipping."),
         Param("Dust", 1.0, 0.0, 2.0, "Scales the Lens tab's dust texture.")],
        "A single ghost: an out-of-focus image of the aperture."),
    ElementType(
        "Ghost Set", 7, "Ghosts",
        _g(intensity=("Intensity", 0.15), color=("Color", WHITE), size=("Size", 0.06),
           axis=("Axis Start", 0.3), seed=("Seed", 1), dispersion=("Chromatic Fringe", 0.05),
           softness=("Softness", 0.1)),
        [Param("Count", 8, 1, 64),
         Param("Axis End", 2.1, -1.0, 3.0),
         Param("Size Random", 0.7, 0.0, 0.95),
         Param("Coating Mix", 0.7, 0.0, 1.0, "0 = this element's colour, 1 = the lens coating "
               "colours (Lens tab)."),
         Param("Hollow", 0.25, 0.0, 1.0),
         Param("Rim", 0.3, 0.0, 2.0),
         Param("Intensity Random", 0.5, 0.0, 1.0),
         Param("Barrel Clip", 1.0, 0.0, 2.0)],
        "Ghosts scattered along the flare axis. Bigger ghosts are dimmer, as in a real lens."),
    ElementType(
        "Spectral", 8, "Rays",
        _g(intensity=("Intensity", 0.4), color=("Color", WHITE), size=("Length", 0.04),
           rotation=("Rotation", 0.0), seed=("Seed", 1)),
        [Param("Count", 12, 1, 64),
         Param("Width", 1.5, 0.0, 10.0),
         Param("Distance", 0.06, 0.0, 0.5),
         Param("Distance Random", 0.05, 0.0, 0.5),
         Param("Orientation", 0.0, 0.0, 1.0, "0 = radial streaks, 1 = rainbow arcs."),
         Param("Randomness", 0.7, 0.0, 1.0)],
        "Short rainbow streaks or arcs around the light."),
    ElementType(
        "Caustic", 9, "Rings",
        _g(intensity=("Intensity", 0.12), color=("Color", (0.9, 0.95, 1.0)), size=("Radius", 0.12),
           axis=("Axis Position", 0.7), rotation=("Rotation", 0.0), dispersion=("Spectral", 0.5),
           softness=("Softness", 0.15)),
        [Param("Thickness", 0.5, 0.0, 1.0, "Widest part of the crescent, as a fraction of "
               "Radius."),
         Param("Sharpness", 0.6, 0.0, 1.0, "How crisp the bright outer edge is.")],
        "A bright crescent arc, the curved light catches seen in anamorphic flares."),
    ElementType(
        "Shimmer", 10, "Rays",
        _g(intensity=("Intensity", 0.3), color=("Color", WARM), size=("Length", 0.25),
           rotation=("Rotation", 0.0), seed=("Seed", 1), dispersion=("Dispersion", 0.1)),
        [Param("Rays", 48, 1, 256),
         Param("Phase", 0.0, 0.0, 10.0, "Animate this to make the rays twinkle."),
         Param("Width", 0.8, 0.0, 10.0),
         Param("Length Random", 0.8, 0.0, 0.95)],
        "Many fine rays whose lengths twinkle as Phase changes."),
    ElementType(
        "Lens System", 11, "Ghosts",
        _g(intensity=("Intensity", 0.6), color=("Tint", WHITE), size=("Size Scale", 1.0),
           dispersion=("Dispersion Scale", 0.6), softness=("Softness", 0.06)),
        [Param("Barrel Clip", 1.0, 0.0, 2.0),
         Param("Dust", 1.0, 0.0, 2.0),
         Param("Hollow", 0.1, 0.0, 1.0),
         Param("Rim", 0.3, 0.0, 2.0)],
        "Ghosts computed from a real lens design: every pair of reflecting surfaces gives one "
        "ghost, with its position, size and colour from the optics and coatings."),
])

# Kernel code for ghosts generated from a Lens System (one table column each).
LENS_GHOST_CODE = 12
TYPE_NAMES = list(TYPES)


def by_code(code):
    for t in TYPES.values():
        if t.code == code:
            return t
    raise KeyError(code)


def resolve_pass(type_name, layer):
    """Render pass for an element: its Layer menu choice, or its type's."""
    return int(layer) if layer else PASS_CODES[TYPES[type_name].render_pass]


def encode(el):
    """Table rows (``ROWS`` 4-tuples) for one non-Lens-System element."""
    t = TYPES[el["type"]]
    p = list(el["p"]) + [0.0] * (8 - len(el["p"]))
    intensity = float(el["intensity"]) if el.get("on", True) else 0.0
    r, g, b = el["color"]
    return [
        (float(t.code), intensity, float(el["axis"]), float(el["size"])),
        (float(r), float(g), float(b), float(resolve_pass(el["type"], el.get("layer", 0)))),
        (float(el["rotation"]), float(el["seed"]), float(el["dispersion"]), float(el["softness"])),
        tuple(float(v) for v in p[:4]),
        tuple(float(v) for v in p[4:8]),
        (0.0, 0.0, 0.0, 0.0),
    ]


def encode_lens_ghost(el, ghost):
    """Table rows for one ghost of a Lens System element (see lenses.Ghost)."""
    intensity = float(el["intensity"]) if el.get("on", True) else 0.0
    tint = el["color"]
    scale = float(el["size"])
    disp = float(el["dispersion"])
    t_g, s_g = ghost.axis[1], ghost.size[1]
    # Dispersion Scale blends each channel's position/size toward green's.
    t_r = t_g + (ghost.axis[0] - t_g) * disp
    t_b = t_g + (ghost.axis[2] - t_g) * disp
    s_r = s_g + (ghost.size[0] - s_g) * disp
    s_b = s_g + (ghost.size[2] - s_g) * disp
    p = list(el["p"]) + [0.0] * 8
    return [
        (float(LENS_GHOST_CODE), intensity, t_g, s_g * scale),
        (ghost.color[0] * tint[0], ghost.color[1] * tint[1], ghost.color[2] * tint[2],
         float(resolve_pass(el["type"], el.get("layer", 0)))),
        (0.0, 0.0, 0.0, float(el["softness"])),
        tuple(float(v) for v in p[:4]),
        (0.0, 0.0, 0.0, 0.0),
        (t_r, s_r * scale, t_b, s_b * scale),
    ]


def table(stack, ghosts_for=None):
    """Columns (lists of ROWS 4-tuples) for a whole stack, in order.

    ``ghosts_for(element)`` returns the lenses.Ghost list of a Lens System
    element; it defaults to computing it with lenses.ghosts_for_element.
    """
    if ghosts_for is None:
        from blinkflare import lenses
        ghosts_for = lenses.ghosts_for_element
    columns = []
    for el in stack:
        if el["type"] == "Lens System":
            columns.extend(encode_lens_ghost(el, g) for g in ghosts_for(el))
        else:
            columns.append(encode(el))
    return columns[:MAX_COLUMNS]


def element(type_name, **overrides):
    """A complete element dict of ``type_name`` with defaults filled in."""
    values = TYPES[type_name].defaults()
    p = overrides.pop("p", None)
    if p is not None:
        merged = list(values["p"])
        for i, v in (p.items() if isinstance(p, dict) else enumerate(p)):
            if not isinstance(i, int):
                i = param_by_label(type_name, i)
            merged[i] = float(v)
        values["p"] = merged
    values.update(overrides)
    if type_name == "Lens System":
        values.setdefault("lens", "Double Gauss 50mm")
        values.setdefault("lens_file", "")
        values.setdefault("fstop", 4.0)
        values.setdefault("coating", "Single Coated")
        values.setdefault("max_ghosts", 16)
        values.setdefault("sensor", 24.0)
    return values


def param_by_label(type_name, label):
    for i, p in enumerate(TYPES[type_name].params):
        if p.label == label:
            return i
    raise KeyError(label)


# ------------------------------------------------------------ group knobs
# Each element is a set of knobs on the BlinkFlare group named e<id>_<field>.
# The table image is built by Expression nodes that read those knobs, so the
# expressions below mirror encode() / encode_lens_ghost() exactly.

LENS_FIELDS = ("lens", "lens_file", "fstop", "coating", "max_ghosts", "sensor")
VALUE_FIELDS = (("on", "type", "layer") + GENERIC
                + tuple("p%d" % i for i in range(8)) + LENS_FIELDS)
CHANNEL_VARS = ("r", "g", "b", "a")


def knob_name(eid, field):
    return "e%d_%s" % (eid, field)


def _ref(eid, field):
    return "parent." + knob_name(eid, field)


def pass_expression(eid, type_name):
    """The element's render pass: its Layer menu, or its type's when Auto.
    (Type changes rebuild the table, so the type's pass can be baked in.)"""
    layer = _ref(eid, "layer")
    return "(%s > 0) * %s + (%s == 0) * %d" % (
        layer, layer, layer, PASS_CODES[TYPES[type_name].render_pass])


def column_expressions(eid, type_name):
    """ROWS x 4 expressions for a (non Lens System) element's table column."""
    k = lambda f: _ref(eid, f)  # noqa: E731
    color = k("color")
    return [
        ["(%s + 1)" % k("type"), "%s * %s" % (k("intensity"), k("on")), k("axis"), k("size")],
        [color + ".r", color + ".g", color + ".b", pass_expression(eid, type_name)],
        [k("rotation"), k("seed"), k("dispersion"), k("softness")],
        [k("p0"), k("p1"), k("p2"), k("p3")],
        [k("p4"), k("p5"), k("p6"), k("p7")],
        ["0", "0", "0", "0"],
    ]


def _num(v):
    """A float as a fixed-point literal (no exponent; negatives parenthesised)."""
    text = repr(float(v))
    if "e" in text or "n" in text:
        text = ("%.12f" % float(v)).rstrip("0").rstrip(".")
    if text in ("", "-0", "-0.0", "0.0"):
        text = "0"
    return "(%s)" % text if text.startswith("-") else text


def lens_column_expressions(eid, ghost, fstop_ref):
    """ROWS x 4 expressions for one ghost of a Lens System element.

    The ghost's geometry is baked (computed at f-stop ``fstop_ref``); its
    brightness, tint, size scale, dispersion and f-stop stay live: ghost size
    scales with the entrance pupil, i.e. with 1 / f-stop.
    """
    k = lambda f: _ref(eid, f)  # noqa: E731
    fs = "(%s / %s)" % (_num(fstop_ref), k("fstop"))
    size = "%s * %s" % (k("size"), fs)
    disp = k("dispersion")
    t_g, s_g = ghost.axis[1], ghost.size[1]
    color = k("color")
    n = _num
    return [
        [str(LENS_GHOST_CODE), "%s * %s" % (k("intensity"), k("on")), n(t_g),
         "%s * %s" % (n(s_g), size)],
        ["%s * %s.r" % (n(ghost.color[0]), color), "%s * %s.g" % (n(ghost.color[1]), color),
         "%s * %s.b" % (n(ghost.color[2]), color), pass_expression(eid, "Lens System")],
        ["0", "0", "0", k("softness")],
        [k("p0"), k("p1"), k("p2"), k("p3")],
        ["0", "0", "0", "0"],
        ["%s + %s * %s" % (n(t_g), n(ghost.axis[0] - t_g), disp),
         "(%s + %s * %s) * %s" % (n(s_g), n(ghost.size[0] - s_g), disp, size),
         "%s + %s * %s" % (n(t_g), n(ghost.axis[2] - t_g), disp),
         "(%s + %s * %s) * %s" % (n(s_g), n(ghost.size[2] - s_g), disp, size)],
    ]


def row_expression(columns, row, channel):
    """Expression-node expression writing one table row of one channel.

    ``columns`` is a list of ROWS x 4 expression grids. Other rows pass the
    input through, so six chained nodes fill the table.
    """
    terms = ["(floor(x) == %d) * (%s)" % (i, col[row][channel])
             for i, col in enumerate(columns) if col[row][channel] != "0"]
    value = " + ".join(terms) if terms else "0"
    keep = CHANNEL_VARS[channel]
    return "(floor(y) == %d) * (%s) + (floor(y) != %d) * %s" % (row, value, row, keep)


def knob_values(el):
    """Knob field -> value for an element dict (see element())."""
    values = {"on": bool(el.get("on", True)), "type": TYPE_NAMES.index(el["type"]),
              "layer": int(el.get("layer", 0))}
    for field in GENERIC:
        values[field] = el[field]
    p = list(el["p"]) + [0.0] * 8
    for i in range(8):
        values["p%d" % i] = float(p[i])
    if el["type"] == "Lens System":
        for field in LENS_FIELDS:
            values[field] = el[field]
    return values
