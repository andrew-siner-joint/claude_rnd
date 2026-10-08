"""Knob layout of the BlinkFlare group and how it drives the Blink kernel.

Pure data with no Nuke import, so the builder, the tests and the preview
renderer all share one definition. Each knob that maps 1:1 onto a kernel
parameter names it in ``blink``; parameters that need an expression are listed
in ``DERIVED_PARAMS``.

These are the node's fixed knobs. The flare elements themselves are added
and removed at runtime (see elements.py and builder.py).
"""

from blinkflare import camera, elements

KERNEL_NAME = "BlinkFlareKernel"
KERNEL_NODE = "FlareKernel"
MERGE_NODE = "Composite"
SWITCH_NODE = "OutputSwitch"
MOTION_BLUR_NODE = "MotionBlur"

VERSION = "3.1.0"

# Render passes (kernel soloPass) and the layers Element Layers writes.
PASSES = elements.PASSES

# Kernel params the builder sets directly (from the element stack).
BUILDER_PARAMS = ("elementCount",)


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
ADD_SCRIPT = ("import blinkflare\nn = nuke.thisNode()\n"
              "blinkflare.add_element(n, n['add_type'].value())\n")
CLEAR_SCRIPT = ("import blinkflare\nif nuke.ask('Remove every element?'):\n"
                "    blinkflare.clear_elements(nuke.thisNode())\n")
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

    # ------------------------------------------------------------------- Lens
    tab("tab_lens", "Lens"),
    text("lens_info", "One lens behind every element: its aperture shapes the ghosts and "
                      "the starburst spikes, its coatings colour the ghosts."),
    divider("div_aperture", "Aperture"),
    integer("aperture_blades", "Blades", 6, "apertureBlades",
            tooltip="Aperture blades. Ghosts take this polygon shape; a starburst gets one "
                    "spike per blade (two for odd counts). Below 3 is round."),
    dbl("aperture_roundness", "Roundness", 0.2, 0.0, 1.0, "apertureRoundness",
        tooltip="Curved blades: 0 is a sharp polygon, 1 a circle."),
    dbl("aperture_rotation", "Rotation", 0.0, -180.0, 180.0, "apertureRotation"),
    dbl("anamorphic", "Anamorphic Squeeze", 1.0, 0.25, 2.0, "anamorphic",
        tooltip="Horizontal scale of round elements. Below 1 gives the tall oval ghosts "
                "and bokeh of anamorphic lenses (0.5 for a 2x squeeze)."),
    dbl("pixel_aspect", "Pixel Aspect", 1.0, 0.5, 2.0, "pixelAspect",
        tooltip="Pixel aspect of the plate so elements stay round in the viewer. "
                "Set automatically when the node is created."),
    divider("div_glass", "Glass"),
    dbl("dispersion", "Dispersion", 1.0, 0.0, 3.0, "dispersion",
        tooltip="Scales every element's colour fringing and spectral spread."),
    dbl("dust", "Dust", 0.3, 0.0, 2.0, "dust",
        tooltip="Mottled texture and bright specks inside ghosts."),
    dbl("barrel_clip", "Barrel Clip", 0.5, 0.0, 2.0, "barrelClip",
        tooltip="Cat's-eye clipping of ghosts by the lens barrel. It grows as the light "
                "moves away from the centre, as with real optical vignetting."),
    divider("div_coatings", "Coatings"),
    color("coating_a", "Coating A", (1.0, 0.45, 0.85), "coatingA",
          tooltip="Ghost Set elements pick their colours from these three (see each "
                  "element's Coating Mix). Typical coatings reflect magenta, green and amber."),
    color("coating_b", "Coating B", (0.45, 1.0, 0.55), "coatingB"),
    color("coating_c", "Coating C", (1.0, 0.7, 0.3), "coatingC"),

    # --------------------------------------------------------------- Elements
    # Elements are added at runtime. Nuke can only append knobs, so the
    # builder lifts the knobs after this tab (AFTER_ELEMENTS) off and puts
    # them back around every addition.
    tab("tab_elements", "Elements"),
    Knob("add_type", "enum", "New Element", 0, items=elements.TYPE_NAMES,
         tooltip="Pick a type, then Add. Elements are additive, so their order doesn't "
                 "change the image."),
    Knob("add_element", "button", "Add", script=ADD_SCRIPT, newline=False),
    Knob("clear_elements", "button", "Remove All", script=CLEAR_SCRIPT, newline=False),
    text("elements_info", "Each element below opens to show its controls."),

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
    Knob("solo", "enum", "Solo", 0, "soloPass",
         items=["All"] + [p[1] for p in PASSES],
         tooltip="Show one render pass while you tune it (each element's Layer menu "
                 "picks its pass). Affects the render, so set it back to All."),
    boolean("element_layers", "Element Layers", False,
            tooltip="Also write each render pass to its own layer (flare_glow, "
                    "flare_rays, flare_streaks, flare_ghosts, flare_rings, flare_other) "
                    "for grading downstream. Renders the flare once per pass, so it is "
                    "slower."),
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

# Knobs on the tabs after Elements (3D, Output). Element knobs are inserted
# before them.
AFTER_ELEMENTS = [k.name for k in KNOBS[[k.name for k in KNOBS].index("tab_3d"):]]
ELEMENTS_TAB_END = "elements_info"

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
    "light_source", "light_pos", "articulation_mode", "axis_center", "pixel_aspect", "add_type",
    "offscreen_fade", "occlusion_enable", "occlusion_mode", "occlusion_radius",
    "occlusion_samples", "occlusion_invert", "light_depth",
    "source_intensity", "source_color", "source_radius", "source_black", "source_white",
    "preset", "output_mode", "solo", "element_layers", "render_region",
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
