"""Knob layout of the BlinkFlare group and how it drives the Blink kernel.

Pure data with no Nuke import, so the builder, the tests and the preview
renderer all share one definition. Each knob that maps 1:1 onto a kernel
parameter names it in ``blink``; parameters that need an expression are listed
in ``DERIVED_PARAMS``.
"""

from blinkflare import camera

KERNEL_NAME = "BlinkFlareKernel"
KERNEL_NODE = "FlareKernel"
MERGE_NODE = "Composite"
SWITCH_NODE = "OutputSwitch"
MOTION_BLUR_NODE = "MotionBlur"

VERSION = "2.0.0"

# Element index (kernel soloElement) and the layer it is written to when
# Element Layers is on.
ELEMENTS = [
    (1, "glow", "flare_glow"),
    (2, "glints", "flare_glints"),
    (3, "streaks", "flare_streaks"),
    (4, "ring", "flare_ring"),
    (5, "ghosts", "flare_ghosts"),
    (6, "spectral", "flare_spectral"),
    (7, "dirt", "flare_dirt"),
]


class Knob(object):
    """Declarative description of one group knob."""

    def __init__(self, name, kind, label="", default=None, blink=None, range=None,
                 tooltip="", items=None, script=None, link=None, newline=True, alts=()):
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
        self.alts = alts  # alternative knob names for link targets

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


def dbl(name, label, default, lo, hi, blink=None, tooltip="", **kw):
    return Knob(name, "double", label, float(default), blink, (lo, hi), tooltip, **kw)


def integer(name, label, default, blink=None, tooltip="", **kw):
    return Knob(name, "int", label, int(default), blink, None, tooltip, **kw)


def boolean(name, label, default, blink=None, tooltip="", **kw):
    return Knob(name, "bool", label, bool(default), blink, None, tooltip, **kw)


def color(name, label, default, blink=None, tooltip="", **kw):
    return Knob(name, "color", label, tuple(float(c) for c in default), blink, None, tooltip, **kw)


CENTER_AXIS_SCRIPT = (
    "n = nuke.thisNode()\n"
    "n['axis_center'].setValue([n.width() * 0.5, n.height() * 0.5])\n"
)
CENTER_LIGHT_SCRIPT = (
    "n = nuke.thisNode()\n"
    "n['light_pos'].setValue([n.width() * 0.5, n.height() * 0.5])\n"
)
APPLY_PRESET_SCRIPT = "import blinkflare\nblinkflare.apply_preset(nuke.thisNode())\n"
SAVE_PRESET_SCRIPT = "import blinkflare\nblinkflare.save_preset(nuke.thisNode())\n"
BAKE_SCRIPT = "import blinkflare\nblinkflare.bake_to_2d(nuke.thisNode())\n"


def _preset_names():
    from blinkflare import presets
    return list(presets.all_presets().keys())


KNOBS = [
    # ------------------------------------------------------------------ Flare
    tab("tab_flare", "Flare"),
    Knob("light_source", "enum", "Light Source", 0, items=["2D Position", "3D Camera + Axis"],
         tooltip="2D: the light is wherever Light Position says. 3D: the light is the "
                 "Axis (or Light, or any 3D transform) on the 'axis' input, seen "
                 "through the camera on the 'cam' input. See the 3D tab."),
    Knob("light_pos", "xy", "Light Position", None,
         tooltip="Position of the light source in pixels (2D mode). Keyframe or track "
                 "this; everything in the flare is placed relative to it."),
    Knob("articulation_mode", "enum", "Articulation", 0,
         items=["Manual", "Frame Center", "Lens Center"],
         tooltip="Where the flare axis pivots. Manual uses Articulation Point. Lens "
                 "Center follows the camera's optical center, including window "
                 "translate (lens shift); without a camera it is the frame center."),
    Knob("axis_center", "xy", "Articulation Point", None,
         tooltip="The flare axis runs from the light through this point (Manual mode). "
                 "Ghosts and axis-positioned rings slide along that axis, and 'Rotate "
                 "With Light' pivots around it."),
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
            tooltip="Dim the flare by what the 'occlusion' input says is in front of "
                    "the light, sampled in a disk around it."),
    Knob("occlusion_mode", "enum", "Mode", 0, "occlusionMode",
         items=["Alpha", "Depth (1/z)", "Depth (z)"], newline=False,
         tooltip="Alpha: the input's alpha is a matte of whatever covers the light. "
                 "Depth: the input's depth.Z is compared with the light's depth, so CG "
                 "renders occlude correctly. Nuke's ScanlineRender writes 1/z."),
    dbl("occlusion_radius", "Sample Radius", 20.0, 0.0, 200.0, "occlusionRadius",
        tooltip="Radius of the disk sampled around the light, in thousandths of the "
                "frame height. Larger values dim gradually as edges cross the light."),
    integer("occlusion_samples", "Samples", 16, "occlusionSamples",
            tooltip="Occlusion samples (1-64). More is smoother and slower."),
    boolean("occlusion_invert", "Invert", False, "occlusionInvert", newline=False,
            tooltip="Treat the occlusion result as visibility instead."),
    dbl("light_depth", "Light Depth", 100.0, 0.0, 10000.0,
        tooltip="Distance of the light from the camera for depth occlusion in 2D "
                "mode. In 3D mode the real depth is used."),

    divider("div_source", "Follow Source"),
    dbl("source_intensity", "Brightness", 0.0, 0.0, 1.0, "sourceIntensity",
        tooltip="Drive the flare brightness from the plate at the light. At 1 the "
                "flare scales with the plate brightness, so it dims when the source "
                "is partly blocked or flickers with a practical light."),
    dbl("source_color", "Color", 0.0, 0.0, 1.0, "sourceColor",
        tooltip="Tint the flare with the plate's color at the light."),
    dbl("source_radius", "Sample Radius", 10.0, 0.0, 200.0, "sourceRadius",
        tooltip="Disk sampled around the light, in thousandths of the frame height."),
    dbl("source_black", "Black Point", 0.0, 0.0, 10.0, "sourceBlack",
        tooltip="Plate brightness that gives no flare."),
    dbl("source_white", "White Point", 1.0, 0.0, 50.0, "sourceWhite",
        tooltip="Plate brightness that gives the flare at full intensity. Brighter "
                "sources push it further, up to 10x."),

    divider("div_flicker", "Flicker"),
    dbl("flicker_amount", "Amount", 0.0, 0.0, 1.0,
        tooltip="Random brightness flicker over time."),
    dbl("flicker_speed", "Speed", 0.5, 0.0, 5.0,
        tooltip="Flicker rate (noise cycles per frame)."),
    integer("flicker_seed", "Seed", 0, tooltip="Changes the flicker pattern."),

    divider("div_presets", "Presets"),
    Knob("preset", "enum", "Preset", 0, items=_preset_names,
         tooltip="Built-in and saved looks. Applying a preset resets every look knob "
                 "but leaves placement, visibility, output and render settings alone."),
    Knob("apply_preset", "button", "Apply", script=APPLY_PRESET_SCRIPT, newline=False),
    Knob("save_preset", "button", "Save...", script=SAVE_PRESET_SCRIPT, newline=False,
         tooltip="Save the current look as a preset file (see README for shared "
                 "studio preset folders)."),
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

    # --------------------------------------------------------------------- 3D
    tab("tab_3d", "3D"),
    text("info_3d", "Connect a Camera to 'cam' and an Axis, Light or any 3D transform "
                    "to 'axis', then set Light Source to 3D on the Flare tab."),
    Knob("light_offset", "link", "3D Offset", link=camera.LIGHT_XFORM + ".translate",
         tooltip="Offsets the light in the space of the 'axis' input. With nothing on "
                 "'axis' this is simply the light's world position."),
    Knob("refresh_links", "button", "Refresh Camera Link",
         tooltip="Re-link the camera's lens settings. Happens automatically when the "
                 "'cam' input changes; use this after renaming the camera."),
    Knob("bake_2d", "button", "Bake to 2D...", script=BAKE_SCRIPT, newline=False,
         tooltip="Bake the projected light (and lens center) into keyframes on the 2D "
                 "knobs over a frame range and switch to 2D, for hand-tweaking or "
                 "handing off."),

    # ----------------------------------------------------------------- Output
    tab("tab_output", "Output"),
    Knob("output_mode", "enum", "Output", 0, items=["Composite", "Flare Only"],
         tooltip="Composite: flare merged onto the source. Flare Only: the flare on "
                 "black, for stacking flares or comping it yourself."),
    Knob("operation", "link", "Operation", link=MERGE_NODE + ".operation", newline=False,
         tooltip="How the flare is merged onto the source. 'plus' is physically "
                 "correct for linear plates; 'screen' is gentler on display-referred "
                 "material."),
    Knob("mix", "link", "Mix", link=MERGE_NODE + ".mix",
         tooltip="Dissolve between the source and the composite."),
    Knob("invert_mask", "link", "Invert Mask", link=MERGE_NODE + ".invert_mask",
         alts=("invertMask",), tooltip="Invert the 'mask' input."),
    Knob("solo", "enum", "Solo", 0, "soloElement",
         items=["All"] + [e[1].capitalize() for e in ELEMENTS],
         tooltip="Show a single element while you tune it. Affects the render, so set "
                 "it back to All."),
    boolean("element_layers", "Element Layers", False,
            tooltip="Also write each element to its own layer (flare_glow, "
                    "flare_ghosts, ...) for grading downstream. Adds one kernel per "
                    "element, so it is slower."),
    Knob("render_region", "enum", "Render Region", 0, items=["Format", "Input BBox"],
         tooltip="Format renders the flare inside the frame. Input BBox renders it over "
                 "the whole input bounding box, for overscan comps."),

    divider("div_motion_blur", "Motion Blur"),
    boolean("motion_blur", "Motion Blur", False,
            tooltip="Render sub-frames and average them so the flare blurs with light "
                    "and camera motion. Cost scales with Samples."),
    Knob("mb_samples", "link", "Samples", link=MOTION_BLUR_NODE + ".divisions"),
    Knob("mb_shutter", "link", "Shutter", link=MOTION_BLUR_NODE + ".shutter"),
    Knob("mb_offset", "link", "Shutter Offset", link=MOTION_BLUR_NODE + ".shutteroffset",
         newline=False),

    divider("div_render", "Render"),
    Knob("use_gpu", "link", "Use GPU if available", link=KERNEL_NODE + ".useGPUIfAvailable",
         alts=("useGPU", "gpu"),
         tooltip="Run the kernel on the GPU (CUDA/Metal) when one is available."),
    Knob("vectorize", "link", "Vectorize on CPU", link=KERNEL_NODE + ".vectorize",
         alts=("vectorise",), newline=False, tooltip="Use SIMD on the CPU fallback path."),
]

# Kernel params driven by expressions rather than a single group knob.
# Values are per-channel expressions evaluated on the kernel node; the
# 2D/3D switches are arithmetic blends of 0/1 menu indices.
FLICKER_EXPR = (
    "parent.master_intensity * (1 - parent.flicker_amount * (0.5 + 0.5 * ("
    "0.65 * noise(frame * parent.flicker_speed, parent.flicker_seed + 0.37) + "
    "0.35 * noise(frame * parent.flicker_speed * 3.1, parent.flicker_seed + 5.71))))"
)
BEHIND_CAMERA_EXPR = "(1 - parent.light_source * (1 - %s.visible))" % camera.PROJECTION_NODE


def _light_expr(c):
    return "parent.light_pos.{c} + parent.light_source * ({p}.screen.{c} - parent.light_pos.{c})".format(
        c=c, p=camera.PROJECTION_NODE)


def _axis_expr(c, half):
    return ("(parent.articulation_mode == 0) * parent.axis_center.{c} + "
            "(parent.articulation_mode == 1) * input.{half} / 2 + "
            "(parent.articulation_mode == 2) * {p}.lens_center.{c}").format(
        c=c, half=half, p=camera.PROJECTION_NODE)


DERIVED_PARAMS = {
    "formatSize": ["input.width", "input.height"],
    "lightPos": [_light_expr("x"), _light_expr("y")],
    "axisCenter": [_axis_expr("x", "width"), _axis_expr("y", "height")],
    "lightDepth": ["parent.light_depth + parent.light_source * (%s.depth - parent.light_depth)"
                   % camera.PROJECTION_NODE],
    "masterIntensity": [FLICKER_EXPR + " * " + BEHIND_CAMERA_EXPR],
}

# Knobs that describe placement, visibility or pipeline settings; presets
# never touch them.
NON_LOOK_KNOBS = {
    "light_source", "light_pos", "articulation_mode", "axis_center", "pixel_aspect",
    "offscreen_fade", "occlusion_enable", "occlusion_mode", "occlusion_radius",
    "occlusion_samples", "occlusion_invert", "light_depth",
    "source_intensity", "source_color", "source_radius", "source_black", "source_white",
    "preset", "dirt_enable", "output_mode", "solo", "element_layers", "render_region",
    "motion_blur",
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


def resolve_params(values, width, height, scene=None):
    """Kernel param values for a set of group knob values.

    Mirrors the expressions the builder writes, for rendering outside Nuke.
    ``scene`` (keyword arguments for camera.project, minus the format) stands
    in for the cam/axis inputs in 3D mode. Flicker is ignored.
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
    proj = camera.project(width=width, height=height, pixel_aspect=values["pixel_aspect"],
                          **(scene or {"world_matrix": camera.matrix_trs(),
                                       "light_world": (0.0, 0.0, -1.0)}))
    mode3d = values["light_source"] == 1
    out["formatSize"] = (float(width), float(height))
    out["lightPos"] = proj["screen"] if mode3d else tuple(values["light_pos"])
    out["axisCenter"] = {
        0: tuple(values["axis_center"]),
        1: (width / 2.0, height / 2.0),
        2: proj["lens_center"],
    }[values["articulation_mode"]]
    out["lightDepth"] = proj["depth"] if mode3d else values["light_depth"]
    out["masterIntensity"] = values["master_intensity"] * (proj["visible"] if mode3d else 1.0)
    return out
