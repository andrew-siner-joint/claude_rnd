"""Knob layout of the BlinkFlare group and how it drives the Blink kernel.

Pure data with no Nuke import, so the builder, the tests and the preview
renderer all share one definition. Each knob that maps 1:1 onto a kernel
parameter names it in ``blink``; parameters that need an expression are listed
in ``DERIVED_PARAMS``.
"""

KERNEL_NAME = "BlinkFlareKernel"
KERNEL_NODE = "FlareKernel"
MERGE_NODE = "Composite"
SWITCH_NODE = "OutputSwitch"

VERSION = "1.0.0"


class Knob(object):
    """Declarative description of one group knob."""

    def __init__(self, name, kind, label="", default=None, blink=None, range=None,
                 tooltip="", items=None, script=None, link=None, newline=True):
        self.name = name
        self.kind = kind
        self.label = label
        self.default = default
        self.blink = blink
        self.range = range
        self.tooltip = tooltip
        self.items = items
        self.script = script
        self.link = link
        self.newline = newline

    @property
    def is_value(self):
        return self.kind in ("double", "int", "bool", "color", "xy", "enum")

    def __repr__(self):
        return "Knob(%r, %r)" % (self.name, self.kind)


def tab(name, label):
    return Knob(name, "tab", label)


def divider(name, label=""):
    return Knob(name, "divider", label)


def text(name, label):
    return Knob(name, "text", label)


def dbl(name, label, default, lo, hi, blink=None, tooltip=""):
    return Knob(name, "double", label, float(default), blink, (lo, hi), tooltip)


def integer(name, label, default, blink=None, tooltip=""):
    return Knob(name, "int", label, int(default), blink, None, tooltip)


def boolean(name, label, default, blink=None, tooltip=""):
    return Knob(name, "bool", label, bool(default), blink, None, tooltip)


def color(name, label, default, blink=None, tooltip=""):
    return Knob(name, "color", label, tuple(float(c) for c in default), blink, None, tooltip)


CENTER_AXIS_SCRIPT = (
    "n = nuke.thisNode()\n"
    "n['axis_center'].setValue([n.width() * 0.5, n.height() * 0.5])\n"
)
CENTER_LIGHT_SCRIPT = (
    "n = nuke.thisNode()\n"
    "n['light_pos'].setValue([n.width() * 0.5, n.height() * 0.5])\n"
)
APPLY_PRESET_SCRIPT = (
    "import blinkflare\n"
    "blinkflare.apply_preset(nuke.thisNode())\n"
)


def _preset_names():
    from blinkflare import presets
    return list(presets.PRESETS.keys())


KNOBS = [
    # ------------------------------------------------------------------ Flare
    tab("tab_flare", "Flare"),
    Knob("light_pos", "xy", "Light Position", None, "lightPos",
         tooltip="Position of the light source in pixels. Keyframe or track this; "
                 "everything in the flare is placed relative to it."),
    Knob("axis_center", "xy", "Articulation Point", None, "axisCenter",
         tooltip="The flare axis runs from the light through this point. Ghosts and "
                 "axis-positioned rings slide along that axis, and 'Rotate With Light' "
                 "pivots around it. Normally the optical center of the lens "
                 "(frame center); keyframe it for lens shifts or reframes."),
    Knob("center_axis", "button", "Center Articulation", script=CENTER_AXIS_SCRIPT,
         tooltip="Snap the articulation point to the frame center."),
    Knob("center_light", "button", "Center Light", script=CENTER_LIGHT_SCRIPT, newline=False,
         tooltip="Snap the light to the frame center."),

    divider("div_global", "Global"),
    dbl("master_intensity", "Intensity", 1.0, 0.0, 4.0,
        tooltip="Overall brightness of the flare (flicker is applied on top)."),
    dbl("master_scale", "Scale", 1.0, 0.1, 4.0, "masterScale",
        tooltip="Scales the size and length of every element."),
    color("master_tint", "Tint", (1.0, 1.0, 1.0), "masterTint",
          tooltip="Color multiplier for the whole flare."),
    dbl("master_rotation", "Rotation", 0.0, -180.0, 180.0, "masterRotation",
        tooltip="Rotates glints, streaks, spectral streaks and ghost blades (degrees)."),
    boolean("spin_with_light", "Rotate With Light", False, "spinWithLight",
            tooltip="Lock the glint and spectral streak rotation to the flare axis, so "
                    "they turn as the light orbits the articulation point."),
    dbl("element_aspect", "Element Aspect", 1.0, 0.25, 4.0, "elementAspect",
        tooltip="Horizontal stretch of round elements (glow, ring, ghosts). Values "
                "below 1 give the tall oval ghosts of anamorphic lenses."),
    dbl("pixel_aspect", "Pixel Aspect", 1.0, 0.5, 2.0, "pixelAspect",
        tooltip="Pixel aspect of the plate so elements stay round in the viewer. "
                "Set automatically when the node is created."),

    divider("div_visibility", "Visibility"),
    dbl("offscreen_fade", "Off-screen Fade", 0.5, 0.0, 1.0, "offscreenFade",
        tooltip="Distance (in frame heights) outside the frame over which the flare "
                "fades out. 0 disables the fade."),
    boolean("occlusion_enable", "Use Occlusion Input", False, "occlusionEnable",
            tooltip="Dim the flare by the alpha of the 'occlusion' input sampled "
                    "around the light, e.g. a roto of whatever passes in front of it."),
    dbl("occlusion_radius", "Sample Radius", 20.0, 0.0, 200.0, "occlusionRadius",
        tooltip="Radius in pixels of the disk sampled around the light. Larger values "
                "give a gradual dim as edges cross the light."),
    integer("occlusion_samples", "Samples", 16, "occlusionSamples",
            tooltip="Occlusion samples (1-64). More is smoother and slower."),
    boolean("occlusion_invert", "Invert", False, "occlusionInvert",
            tooltip="Treat the occlusion alpha as visibility instead."),

    divider("div_flicker", "Flicker"),
    dbl("flicker_amount", "Amount", 0.0, 0.0, 1.0,
        tooltip="Random brightness flicker over time."),
    dbl("flicker_speed", "Speed", 0.5, 0.0, 5.0,
        tooltip="Flicker rate (noise cycles per frame)."),
    integer("flicker_seed", "Seed", 0, tooltip="Changes the flicker pattern."),

    divider("div_output", "Output"),
    Knob("output_mode", "enum", "Output", 0, items=["Composite", "Flare Only"],
         tooltip="Composite: flare merged onto the source. Flare Only: the flare on "
                 "black, for stacking flares or comping it yourself."),
    Knob("operation", "link", "Operation", link=MERGE_NODE + ".operation", newline=False,
         tooltip="How the flare is merged onto the source. 'plus' is physically "
                 "correct for linear plates; 'screen' is gentler on display-referred "
                 "material."),

    divider("div_render", "Render"),
    Knob("use_gpu", "link", "Use GPU if available", link=KERNEL_NODE + ".useGPUIfAvailable",
         tooltip="Run the kernel on the GPU (CUDA/Metal) when one is available."),
    Knob("vectorize", "link", "Vectorize on CPU", link=KERNEL_NODE + ".vectorize", newline=False,
         tooltip="Use SIMD on the CPU fallback path."),

    divider("div_presets", "Presets"),
    Knob("preset", "enum", "Preset", 0, items=_preset_names,
         tooltip="Starting looks. Applying a preset resets every element knob but "
                 "leaves the positions, output and render settings alone."),
    Knob("apply_preset", "button", "Apply", script=APPLY_PRESET_SCRIPT, newline=False),
    text("version_info", "BlinkFlare v" + VERSION),

    # ------------------------------------------------------------------- Glow
    tab("tab_glow", "Glow"),
    boolean("glow_enable", "Enable Glow", True, "glowEnable"),
    dbl("glow_intensity", "Intensity", 0.5, 0.0, 2.0, "glowIntensity"),
    dbl("glow_size", "Size", 0.1, 0.0, 0.5, "glowSize",
        tooltip="Radius of the soft glow, in frame heights."),
    dbl("glow_falloff", "Falloff", 1.0, 0.2, 4.0, "glowFalloff",
        tooltip="How quickly the glow fades. Low values leave a long haze across the "
                "frame; high values keep it tight."),
    color("glow_color", "Color", (1.0, 0.82, 0.62), "glowColor"),
    divider("div_core", "Hot Core"),
    dbl("core_intensity", "Intensity", 4.0, 0.0, 20.0, "coreIntensity"),
    dbl("core_size", "Size", 0.012, 0.0, 0.1, "coreSize"),
    color("core_color", "Color", (1.0, 0.97, 0.92), "coreColor"),

    # ----------------------------------------------------------------- Glints
    tab("tab_glints", "Glints"),
    boolean("glint_enable", "Enable Glints", True, "glintEnable"),
    dbl("glint_intensity", "Intensity", 0.6, 0.0, 4.0, "glintIntensity"),
    integer("glint_count", "Rays", 12, "glintCount", tooltip="Number of main rays (1-256)."),
    dbl("glint_length", "Length", 0.3, 0.0, 1.5, "glintLength",
        tooltip="Length of the longest rays, in frame heights."),
    dbl("glint_length_random", "Length Random", 0.6, 0.0, 0.95, "glintLengthRandom"),
    dbl("glint_width", "Width", 2.0, 0.0, 10.0, "glintWidth",
        tooltip="Ray thickness at the base, in thousandths of the frame height."),
    dbl("glint_rotation", "Rotation", 0.0, -180.0, 180.0, "glintRotation"),
    dbl("glint_fine", "Fine Rays", 0.4, 0.0, 2.0, "glintFine",
        tooltip="Adds a layer of shorter, thinner rays between the main ones."),
    integer("glint_seed", "Seed", 1, "glintSeed"),
    color("glint_color", "Color", (1.0, 0.9, 0.78), "glintColor"),

    # ---------------------------------------------------------------- Streaks
    tab("tab_streaks", "Streaks"),
    boolean("streak_enable", "Enable Streaks", True, "streakEnable"),
    dbl("streak_intensity", "Intensity", 0.6, 0.0, 4.0, "streakIntensity"),
    integer("streak_count", "Count", 1, "streakCount",
            tooltip="1 is the classic anamorphic line, 2 a cross, more a star (1-16)."),
    dbl("streak_length", "Length", 0.9, 0.0, 3.0, "streakLength",
        tooltip="Half-length of each streak, in frame heights."),
    dbl("streak_thickness", "Thickness", 2.0, 0.0, 10.0, "streakThickness",
        tooltip="Core thickness, in thousandths of the frame height."),
    dbl("streak_angle", "Angle", 0.0, -180.0, 180.0, "streakAngle"),
    dbl("streak_glow", "Glow", 0.8, 0.0, 2.0, "streakGlow",
        tooltip="Soft haze around the streak core."),
    color("streak_color", "Color", (0.55, 0.75, 1.0), "streakColor"),

    # ------------------------------------------------------------------- Ring
    tab("tab_ring", "Ring"),
    boolean("ring_enable", "Enable Ring", True, "ringEnable"),
    dbl("ring_intensity", "Intensity", 0.05, 0.0, 1.0, "ringIntensity"),
    dbl("ring_radius", "Radius", 0.25, 0.0, 1.0, "ringRadius",
        tooltip="Ring radius in frame heights."),
    dbl("ring_thickness", "Thickness", 0.025, 0.0, 0.2, "ringThickness"),
    dbl("ring_spectral", "Spectral", 0.8, 0.0, 1.0, "ringSpectral",
        tooltip="Blend from a plain ring to a rainbow halo."),
    dbl("ring_axis_pos", "Axis Position", 0.0, -1.0, 3.0, "ringAxisPos",
        tooltip="Where the ring sits on the flare axis: 0 around the light, 1 on the "
                "articulation point, 2 mirrored to the far side."),
    dbl("ring_arc", "Arc", 0.0, 0.0, 1.0, "ringArc",
        tooltip="Fades the ring into a partial arc (hoop) facing along the flare axis."),
    dbl("ring_arc_rotation", "Arc Rotation", 0.0, -180.0, 180.0, "ringArcRotation"),
    color("ring_color", "Color", (1.0, 1.0, 1.0), "ringColor"),

    # ----------------------------------------------------------------- Ghosts
    tab("tab_ghosts", "Ghosts"),
    boolean("ghost_enable", "Enable Ghosts", True, "ghostEnable"),
    dbl("ghost_intensity", "Intensity", 0.12, 0.0, 1.0, "ghostIntensity"),
    integer("ghost_count", "Count", 9, "ghostCount", tooltip="Number of ghosts (0-64)."),
    integer("ghost_seed", "Seed", 1, "ghostSeed"),
    dbl("ghost_size", "Size", 0.06, 0.0, 0.3, "ghostSize",
        tooltip="Radius of the largest ghosts, in frame heights."),
    dbl("ghost_size_random", "Size Random", 0.7, 0.0, 0.95, "ghostSizeRandom"),
    dbl("ghost_spread_min", "Axis Start", 0.3, -1.0, 3.0, "ghostSpreadMin",
        tooltip="Ghosts are scattered along the axis between Start and End. "
                "0 = light, 1 = articulation point, 2 = mirrored."),
    dbl("ghost_spread_max", "Axis End", 2.1, -1.0, 3.0, "ghostSpreadMax"),
    integer("ghost_blades", "Aperture Blades", 6, "ghostBlades",
            tooltip="Polygon sides of the iris shape. Below 3 gives round ghosts."),
    dbl("ghost_roundness", "Roundness", 0.25, 0.0, 1.0, "ghostRoundness"),
    dbl("ghost_rotation", "Blade Rotation", 0.0, -180.0, 180.0, "ghostRotation"),
    dbl("ghost_softness", "Softness", 0.08, 0.0, 1.0, "ghostSoftness"),
    dbl("ghost_hollow", "Hollow", 0.35, 0.0, 1.0, "ghostHollow",
        tooltip="Darkens the ghost centers so they read as rings."),
    dbl("ghost_chroma", "Chromatic Fringe", 0.04, -0.5, 0.5, "ghostChroma",
        tooltip="Red/blue fringing on ghost edges. Negative flips the order."),
    dbl("ghost_hue_random", "Hue Random", 0.5, 0.0, 1.0, "ghostHueRandom"),
    dbl("ghost_intensity_random", "Intensity Random", 0.6, 0.0, 1.0, "ghostIntensityRandom"),
    color("ghost_color", "Color", (1.0, 0.95, 0.85), "ghostColor"),

    # --------------------------------------------------------------- Spectral
    tab("tab_spectral", "Spectral"),
    boolean("spectral_enable", "Enable Spectral Streaks", True, "spectralEnable"),
    dbl("spectral_intensity", "Intensity", 0.4, 0.0, 2.0, "spectralIntensity"),
    integer("spectral_count", "Count", 12, "spectralCount", tooltip="Number of streaks (0-64)."),
    integer("spectral_seed", "Seed", 1, "spectralSeed"),
    dbl("spectral_length", "Length", 0.04, 0.0, 0.3, "spectralLength",
        tooltip="Half-length of each streak, in frame heights."),
    dbl("spectral_width", "Width", 1.5, 0.0, 10.0, "spectralWidth",
        tooltip="Thickness in thousandths of the frame height."),
    dbl("spectral_offset", "Distance", 0.06, 0.0, 0.5, "spectralOffset",
        tooltip="Distance from the light to the nearest streaks."),
    dbl("spectral_spread", "Distance Random", 0.05, 0.0, 0.5, "spectralSpread"),
    dbl("spectral_orient", "Orientation", 0.0, 0.0, 1.0, "spectralOrient",
        tooltip="0 points the streaks at the light; 1 curves them into rainbow arcs "
                "around it."),
    dbl("spectral_random", "Randomness", 0.7, 0.0, 1.0, "spectralRandom"),

    # ------------------------------------------------------------------- Dirt
    tab("tab_dirt", "Dirt"),
    text("dirt_info", "Connect a lens dirt / smudge texture to the 'dirt' input."),
    boolean("dirt_enable", "Enable Dirt", False, "dirtEnable"),
    dbl("dirt_intensity", "Intensity", 1.0, 0.0, 4.0, "dirtIntensity"),
    dbl("dirt_spread", "Spread", 0.45, 0.0, 2.0, "dirtSpread",
        tooltip="Radius around the light where the dirt is lit, in frame heights."),
    dbl("dirt_response", "Flare Response", 1.0, 0.0, 4.0, "dirtResponse",
        tooltip="How much the flare elements themselves light up the dirt."),
]

# Kernel params driven by expressions rather than a single group knob.
# Values are per-channel expressions evaluated on the kernel node.
FLICKER_EXPR = (
    "parent.master_intensity * (1 - parent.flicker_amount * (0.5 + 0.5 * ("
    "0.65 * noise(frame * parent.flicker_speed, parent.flicker_seed + 0.37) + "
    "0.35 * noise(frame * parent.flicker_speed * 3.1, parent.flicker_seed + 5.71))))"
)
DERIVED_PARAMS = {
    "formatSize": ["input.width", "input.height"],
    "masterIntensity": [FLICKER_EXPR],
}

# Knobs that describe placement or pipeline settings; presets never touch them.
NON_LOOK_KNOBS = {
    "light_pos", "axis_center", "pixel_aspect", "output_mode", "preset",
    "occlusion_enable", "occlusion_radius", "occlusion_samples", "occlusion_invert",
    "dirt_enable",
}

CHANNELS = {
    "xy": (".x", ".y"),
    "color": (".r", ".g", ".b"),
}


def value_knobs():
    return [k for k in KNOBS if k.is_value]


def knob(name):
    for k in KNOBS:
        if k.name == name:
            return k
    raise KeyError(name)


def enum_items(k):
    return k.items() if callable(k.items) else list(k.items)


def defaults():
    """Default value of every value knob (positions are left as None)."""
    return dict((k.name, k.default) for k in value_knobs())


def blink_expressions(k):
    """Per-channel expressions linking a kernel param to group knob ``k``."""
    if k.kind in CHANNELS:
        exprs = ["parent.%s%s" % (k.name, c) for c in CHANNELS[k.kind]]
        if k.kind == "color":
            exprs.append("1")
        return exprs
    return ["parent." + k.name]


def resolve_params(values, width, height):
    """Kernel param values for a set of group knob values.

    Mirrors the expressions the builder writes, for rendering outside Nuke.
    Flicker is ignored.
    """
    out = {}
    for k in value_knobs():
        if not k.blink:
            continue
        v = values[k.name]
        if k.kind == "color":
            v = tuple(v) + (1.0,)
        elif k.kind == "xy":
            v = tuple(v)
        elif k.kind == "bool":
            v = 1.0 if v else 0.0
        out[k.blink] = v
    out["formatSize"] = (float(width), float(height))
    out["masterIntensity"] = values["master_intensity"]
    return out
