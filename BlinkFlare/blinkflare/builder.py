"""Builds the BlinkFlare group inside Nuke.

Node graph inside the group:

    src ─┬─ Black (Multiply 0) ─ AddChannels ─ Canvas (Crop to format) ─┐
         │                                       occlusion ─ AddChannels ┼─ FlareKernel ─┬─ OutputSwitch ─ Output
         │                         dirt ─ AddChannels ─ Reformat (fill) ─┘               │
         └──────────────────────────────── Composite (Merge, B) ── A ────────────────────┘

The kernel always renders the flare alone onto a black canvas the size of the
source format; the Merge composites it so the operation stays user-selectable
and the source alpha passes through untouched.
"""

import os

import nuke

from blinkflare import presets, spec

KERNEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kernel", "BlinkFlare.blink")

GPU_KNOB_CANDIDATES = ("useGPUIfAvailable", "useGPU", "gpu")
VECTORIZE_KNOB_CANDIDATES = ("vectorize", "vectorise")


class BuildError(RuntimeError):
    pass


def kernel_source():
    with open(KERNEL_PATH) as f:
        return f.read()


# --------------------------------------------------------------------- kernel

def _find_knob(node, candidates, contains=None):
    knobs = node.knobs()
    for name in candidates:
        if name in knobs:
            return name
    if contains:
        for name in sorted(knobs):
            if contains in name.lower():
                return name
    return None


def param_knob(blink, param):
    """The BlinkScript knob for kernel parameter ``param``."""
    knob = blink.knob("%s_%s" % (spec.KERNEL_NAME, param))
    if knob is None:
        # Fall back in case a Nuke version prefixes param knobs differently.
        suffix = "_" + param
        for name, k in blink.knobs().items():
            if name.endswith(suffix):
                return k
        raise BuildError("BlinkScript node has no knob for kernel param '%s'. "
                         "Did the kernel compile?" % param)
    return knob


def compile_kernel(blink):
    blink["kernelSource"].setValue(kernel_source())
    try:
        blink["recompile"].execute()
    except Exception as e:  # Nuke raises plain RuntimeError here
        raise BuildError(
            "Could not compile the BlinkFlare kernel (%s).\n\n"
            "Compiling Blink kernels needs NukeX or Nuke Studio. A NukeX user can "
            "run Nodes > Draw > BlinkFlare > Save ToolSet and share the .nk." % e)
    param_knob(blink, "lightPos")  # raises if params didn't appear


def link_kernel_params(blink):
    for k in spec.value_knobs():
        if k.blink:
            _set_expressions(param_knob(blink, k.blink), spec.blink_expressions(k))
    for param, exprs in spec.DERIVED_PARAMS.items():
        _set_expressions(param_knob(blink, param), exprs)


def _set_expressions(knob, exprs):
    if len(exprs) == 1:
        knob.setExpression(exprs[0])
    else:
        for channel, expr in enumerate(exprs):
            knob.setExpression(expr, channel)


# ---------------------------------------------------------------- group knobs

def make_knob(k, links):
    if k.kind == "tab":
        knob = nuke.Tab_Knob(k.name, k.label)
    elif k.kind == "divider":
        knob = nuke.Text_Knob(k.name, k.label, "")
    elif k.kind == "text":
        knob = nuke.Text_Knob(k.name, "", k.label)
    elif k.kind == "double":
        knob = nuke.Double_Knob(k.name, k.label)
        knob.setRange(k.range[0], k.range[1])
    elif k.kind == "int":
        knob = nuke.Int_Knob(k.name, k.label)
    elif k.kind == "bool":
        knob = nuke.Boolean_Knob(k.name, k.label)
    elif k.kind == "color":
        knob = nuke.Color_Knob(k.name, k.label)
    elif k.kind == "xy":
        knob = nuke.XY_Knob(k.name, k.label)
    elif k.kind == "enum":
        knob = nuke.Enumeration_Knob(k.name, k.label, spec.enum_items(k))
    elif k.kind == "button":
        knob = nuke.PyScript_Knob(k.name, k.label, k.script)
    elif k.kind == "link":
        target = links.get(k.name)
        if target is None:
            return None
        knob = nuke.Link_Knob(k.name, k.label)
        knob.setLink(target)
    else:
        raise BuildError("unknown knob kind %r" % k.kind)

    if k.tooltip:
        knob.setTooltip(k.tooltip)
    if k.kind not in ("tab", "divider", "text"):
        if k.newline:
            knob.setFlag(nuke.STARTLINE)
        else:
            knob.clearFlag(nuke.STARTLINE)
    return knob


def set_knob_value(knob, k, value):
    if k.kind in ("color", "xy"):
        knob.setValue(list(value))
    elif k.kind == "bool":
        knob.setValue(bool(value))
    elif k.kind == "int":
        knob.setValue(int(value))
    else:
        knob.setValue(value)


def add_group_knobs(group, blink, fmt):
    gpu = _find_knob(blink, GPU_KNOB_CANDIDATES, contains="gpu")
    vec = _find_knob(blink, VECTORIZE_KNOB_CANDIDATES, contains="vectori")
    links = {"operation": spec.MERGE_NODE + ".operation"}
    if gpu:
        links["use_gpu"] = "%s.%s" % (spec.KERNEL_NODE, gpu)
    if vec:
        links["vectorize"] = "%s.%s" % (spec.KERNEL_NODE, vec)

    w, h = fmt.width(), fmt.height()
    values = spec.defaults()
    values["light_pos"] = (w * 0.7, h * 0.7)
    values["axis_center"] = (w * 0.5, h * 0.5)
    values["pixel_aspect"] = fmt.pixelAspect()

    for k in spec.KNOBS:
        knob = make_knob(k, links)
        if knob is None:
            continue
        group.addKnob(knob)
        if k.is_value:
            set_knob_value(knob, k, values[k.name])


# -------------------------------------------------------------------- graph

def _input_format():
    try:
        sel = nuke.selectedNode()
    except ValueError:
        sel = None
    fmt = sel.format() if sel is not None else nuke.root().format()
    return sel, fmt


def build_internals(group):
    """Create the node graph inside ``group``. Returns the BlinkScript node."""
    with group:
        src = nuke.nodes.Input(name="src")
        occ_in = nuke.nodes.Input(name="occlusion")
        dirt_in = nuke.nodes.Input(name="dirt")
        src.setXYpos(0, 0)
        occ_in.setXYpos(220, 0)
        dirt_in.setXYpos(440, 0)

        black = nuke.nodes.Multiply(name="Black", inputs=[src])
        black["channels"].setValue("rgba")
        black["value"].setValue(0)
        black.setXYpos(0, 80)

        canvas_ch = nuke.nodes.AddChannels(name="CanvasChannels", inputs=[black])
        canvas_ch["channels"].setValue("rgba")
        canvas_ch.setXYpos(0, 120)

        canvas = nuke.nodes.Crop(name="Canvas", inputs=[canvas_ch])
        box = canvas["box"]
        box.setValue(0, 0)
        box.setValue(0, 1)
        box.setExpression("input.width", 2)
        box.setExpression("input.height", 3)
        canvas["reformat"].setValue(False)
        canvas["crop"].setValue(False)
        canvas.setXYpos(0, 160)

        occ = nuke.nodes.AddChannels(name="OcclusionChannels", inputs=[occ_in])
        occ["channels"].setValue("rgba")
        occ.setXYpos(220, 120)

        dirt_ch = nuke.nodes.AddChannels(name="DirtChannels", inputs=[dirt_in])
        dirt_ch["channels"].setValue("rgba")
        dirt_ch.setXYpos(440, 80)

        dirt = nuke.nodes.Reformat(name="DirtFit", inputs=[dirt_ch])
        dirt["type"].setValue("to box")
        dirt["box_fixed"].setValue(True)
        dirt["box_width"].setExpression("Canvas.width")
        dirt["box_height"].setExpression("Canvas.height")
        dirt["resize"].setValue("fill")
        dirt["center"].setValue(True)
        dirt.setXYpos(440, 120)

        blink = nuke.nodes.BlinkScript(name=spec.KERNEL_NODE)
        blink.setXYpos(220, 240)
        compile_kernel(blink)
        blink.setInput(0, canvas)
        blink.setInput(1, occ)
        blink.setInput(2, dirt)
        link_kernel_params(blink)

        merge = nuke.nodes.Merge2(name=spec.MERGE_NODE, inputs=[src, blink])
        merge["operation"].setValue("plus")
        merge["output"].setValue("rgb")
        merge.setXYpos(0, 320)

        switch = nuke.nodes.Switch(name=spec.SWITCH_NODE, inputs=[merge, blink])
        switch["which"].setExpression("parent.output_mode")
        switch.setXYpos(0, 400)

        out = nuke.nodes.Output(inputs=[switch])
        out.setXYpos(0, 480)
    return blink


def create():
    """Create a BlinkFlare node below the selected node (or on its own)."""
    sel, fmt = _input_format()
    group = nuke.nodes.Group()
    try:
        group.setName("BlinkFlare1")
        group["tile_color"].setValue(0xE8A33DFF)
        blink = build_internals(group)
        add_group_knobs(group, blink, fmt)
    except Exception:
        nuke.delete(group)
        raise

    if sel is not None:
        group.setInput(0, sel)
        group.setXYpos(sel.xpos(), sel.ypos() + 80)
    for n in nuke.selectedNodes():
        n.setSelected(False)
    group.setSelected(True)
    if nuke.GUI:
        group.showControlPanel()
    return group


# ------------------------------------------------------------------ presets

def apply_preset(node, name=None):
    """Reset the look knobs of ``node`` to a preset (undoable)."""
    if name is None:
        name = node["preset"].value()
    if name not in presets.PRESETS:
        raise KeyError("Unknown BlinkFlare preset %r" % name)
    values = spec.defaults()
    values.update(presets.PRESETS[name])

    undo = nuke.Undo()
    undo.begin("BlinkFlare preset: " + name)
    try:
        for k in spec.value_knobs():
            if k.name in spec.NON_LOOK_KNOBS or values[k.name] is None:
                continue
            knob = node.knob(k.name)
            if knob is None:
                continue
            if knob.isAnimated():
                knob.clearAnimated()
            set_knob_value(knob, k, values[k.name])
    finally:
        undo.end()


# ------------------------------------------------------------------ toolset

def save_toolset(path=None):
    """Build a BlinkFlare node and save it as a ToolSet .nk.

    The saved node carries the compiled kernel, so it can be shared with
    seats that can't compile Blink kernels themselves.
    """
    if path is None:
        path = os.path.join(os.path.expanduser("~"), ".nuke", "ToolSets", "BlinkFlare.nk")
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    for n in nuke.selectedNodes():
        n.setSelected(False)
    node = create()
    node.setInput(0, None)
    try:
        for n in nuke.selectedNodes():
            n.setSelected(False)
        node.setSelected(True)
        nuke.nodeCopy(path)
    finally:
        nuke.delete(node)
    return path
