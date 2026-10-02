"""Builds the BlinkFlare group inside Nuke.

Node graph inside the group:

    src ── SrcChannels ── Canvas (Crop to format) ─────────────────┐
    occlusion ── OcclusionChannels ── OcclusionDepth (depth→red) ──┼── FlareKernel ── [element layers] ── MotionBlur ──┐
    dirt ── DirtChannels ── DirtFit (Reformat fill) ───────────────┘                                                  │
    cam ── CameraXform (Axis) ┐                                                                                       │
    axis ── LightXform (Axis) ┴── Projection (NoOp, expressions only)                                                  │
    src ─────────────────── Composite (Merge, B) ── A ────────────────────────────────────────────────────────────────┤
    mask ─ (Composite mask, connected only when the outer input is)                     [element layers] ── OutputSwitch ── Output

The kernel renders the flare alone; the Merge composites it so the operation,
mix and mask behave like any Nuke merge and the source alpha passes through.
Element Layers adds one solo kernel per element plus Copy nodes, built the
first time it is switched on.
"""

import os
import tempfile

import nuke

from blinkflare import camera, presets, spec

KERNEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kernel", "BlinkFlare.blink")

AXIS_CLASSES = ("Axis2", "Axis3", "Axis")
RGBA = ("red", "green", "blue", "alpha")


class BuildError(RuntimeError):
    pass


def kernel_source():
    with open(KERNEL_PATH) as f:
        return f.read()


# --------------------------------------------------------------------- kernel

def param_knob_name(blink, param):
    """Name of the BlinkScript knob for kernel parameter ``param``, or None.

    Nuke names them <Kernel>_<param>; plain and case-insensitive matches are
    accepted too in case a version differs.
    """
    knobs = blink.knobs()
    for name in ("%s_%s" % (spec.KERNEL_NAME, param), param):
        if name in knobs:
            return name
    low = param.lower()
    for name in sorted(knobs):
        if name.lower() == low or name.lower().endswith("_" + low):
            return name
    return None


def param_knob(blink, param):
    """The BlinkScript knob for kernel parameter ``param``."""
    name = param_knob_name(blink, param)
    if name is None:
        raise BuildError("BlinkScript node %s has no knob for kernel param '%s'."
                         % (blink.name(), param))
    return blink[name]


def _process_events():
    """Let Nuke run deferred UI work (some versions compile asynchronously)."""
    for module in ("PySide6", "PySide2"):
        try:
            widgets = __import__(module + ".QtWidgets", fromlist=["QtWidgets"])
        except ImportError:
            continue
        app = widgets.QApplication.instance()
        if app is not None:
            app.processEvents()
        return


def _compile_steps(blink, source):
    """Ways of making a BlinkScript node compile from Python, in the order
    tried. Which one takes effect synchronously differs between versions."""
    def recompile():
        blink["recompile"].execute()

    def validate():
        blink.forceValidate()

    def from_file():
        path = os.path.join(tempfile.gettempdir(), "BlinkFlare_%d.blink" % os.getpid())
        with open(path, "w") as f:
            f.write(source)
        blink["kernelSourceFile"].setValue(path)
        blink["reloadKernelSourceFile"].execute()
        blink["recompile"].execute()

    return [("recompile", recompile), ("validate", validate), ("events", _process_events),
            ("file", from_file)]


def compile_kernel(blink, source=None, probe="lightPos"):
    """Compile ``source`` (default: BlinkFlare) on ``blink``. Returns the step
    that worked; raises BuildError explaining what happened otherwise."""
    if source is None:
        source = kernel_source()
    blink["kernelSource"].setValue(source)
    errors = []
    for name, step in _compile_steps(blink, source):
        try:
            step()
        except Exception as e:  # Nuke raises plain RuntimeError from knob scripts
            errors.append("%s: %s" % (name, e))
        if param_knob_name(blink, probe) is not None:
            if name == "file" and blink.knob("kernelSourceFile") is not None:
                blink["kernelSourceFile"].setValue("")
            return name
    raise BuildError(compile_failure_message(blink, probe, errors))


def compile_failure_message(blink, probe, errors=()):
    def flag(key):
        try:
            return nuke.env[key]
        except Exception:
            return None
    version = getattr(nuke, "NUKE_VERSION_STRING", "?")
    if flag("nukex") or flag("studio"):
        licence = "NukeX/Studio"
    else:
        licence = "not NukeX - compiling Blink kernels needs NukeX or Nuke Studio"
    has_error = blink.hasError() if hasattr(blink, "hasError") else "?"
    lines = [
        "BlinkFlare's Blink kernel did not compile: the BlinkScript node has no "
        "knob for its '%s' parameter." % probe,
        "Nuke %s (%s). BlinkScript node in error: %s." % (version, licence, has_error),
    ]
    if errors:
        lines.append("Compile attempts raised: " + "; ".join(errors))
    lines.append("For a full diagnosis run Nodes > Draw > BlinkFlare > Check Install... "
                 "(or install_blinkflare.py from the Script Editor) and send the report it saves.")
    return "\n".join(lines)


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


def make_kernel(name, inputs, solo=None):
    blink = nuke.nodes.BlinkScript(name=name)
    compile_kernel(blink)
    for i, node in enumerate(inputs):
        blink.setInput(i, node)
    link_kernel_params(blink)
    if solo is not None:
        knob = param_knob(blink, "soloElement")
        knob.clearAnimated()
        knob.setValue(solo)
    return blink


# ---------------------------------------------------------------- group knobs

def _resolve_link(group, k):
    """'Node.knob' for a link knob, trying the spec's alternative knob names."""
    node_name, knob_name = k.link.split(".", 1)
    node = group.node(node_name)
    if node is None:
        return None
    for name in (knob_name,) + tuple(k.alts):
        if node.knob(name) is not None:
            return "%s.%s" % (node_name, name)
    return None


def make_knob(k, link_target=None, script=None):
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
        knob = nuke.PyScript_Knob(k.name, k.label, script or k.script)
    elif k.kind == "link":
        if link_target is None:
            return None
        knob = nuke.Link_Knob(k.name, k.label)
        knob.setLink(link_target)
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
    elif k.kind in ("int", "enum"):
        knob.setValue(int(value))
    else:
        knob.setValue(value)


def get_knob_value(knob, k):
    if k.kind == "color":
        return tuple(knob.value(i) for i in range(3))
    if k.kind == "xy":
        return tuple(knob.value(i) for i in range(2))
    if k.kind == "enum":
        return int(knob.getValue())
    if k.kind == "bool":
        return bool(knob.value())
    if k.kind == "int":
        return int(knob.value())
    return float(knob.value())


def add_group_knobs(group, fmt):
    knob_changed, refresh = camera.link_scripts(spec.MERGE_NODE)
    scripts = {"refresh_links": refresh}

    w, h = fmt.width(), fmt.height()
    values = spec.defaults()
    values["light_pos"] = (w * 0.7, h * 0.7)
    values["axis_center"] = (w * 0.5, h * 0.5)
    values["pixel_aspect"] = fmt.pixelAspect()

    for k in spec.KNOBS:
        target = _resolve_link(group, k) if k.kind == "link" else None
        knob = make_knob(k, target, scripts.get(k.name))
        if knob is None:
            continue
        group.addKnob(knob)
        if k.is_value:
            set_knob_value(knob, k, values[k.name])
    group["knobChanged"].setValue(knob_changed)


# -------------------------------------------------------------------- graph

def _input_format():
    try:
        sel = nuke.selectedNode()
    except ValueError:
        sel = None
    fmt = sel.format() if sel is not None else nuke.root().format()
    return sel, fmt


def _make_axis(name, parent):
    """A classic-3D Axis whose world_matrix mirrors whatever feeds ``parent``."""
    for cls in AXIS_CLASSES:
        try:
            node = getattr(nuke.nodes, cls)(name=name)
        except Exception:
            continue
        if node.knob("world_matrix") is not None:
            node.setInput(0, parent)
            return node
        nuke.delete(node)
    raise BuildError("No Axis node with a world_matrix knob is available.")


def _set_if_present(node, knob, value):
    if node.knob(knob) is not None:
        node[knob].setValue(value)


def build_projection(canvas, cam_in, axis_in):
    _make_axis(camera.CAMERA_XFORM, cam_in).setXYpos(660, 80)
    _make_axis(camera.LIGHT_XFORM, axis_in).setXYpos(880, 80)
    proj = nuke.nodes.NoOp(name=camera.PROJECTION_NODE, inputs=[canvas])
    proj.setXYpos(770, 160)
    defaults = dict((name, default) for name, _, _, default in camera.LENS_KNOBS)
    for name, kind, exprs in camera.PROJECTION_KNOBS:
        if kind in ("lens", "double"):
            knob = nuke.Double_Knob(name, name)
        else:
            # XYZ knobs: no proxy scaling, unlike 2D position knobs.
            knob = nuke.XYZ_Knob(name, name)
        proj.addKnob(knob)
        if kind == "lens":
            knob.setValue(defaults[name])
        else:
            if kind == "xy":
                exprs = exprs + ["0"]
            _set_expressions(knob, exprs)
    return proj


def build_internals(group):
    """Create the node graph inside ``group``. Returns the main kernel."""
    with group:
        names = ["src", "occlusion", "dirt", "cam", "axis", "mask"]
        ins = {}
        for i, name in enumerate(names):
            ins[name] = nuke.nodes.Input(name=name)
            ins[name].setXYpos(220 * i, 0)

        src_ch = nuke.nodes.AddChannels(name="SrcChannels", inputs=[ins["src"]])
        src_ch["channels"].setValue("rgba")
        src_ch.setXYpos(0, 80)

        canvas = nuke.nodes.Crop(name="Canvas", inputs=[src_ch])
        box = canvas["box"]
        box.setValue(0, 0)
        box.setValue(0, 1)
        box.setExpression("input.width", 2)
        box.setExpression("input.height", 3)
        canvas["reformat"].setValue(False)
        canvas["crop"].setValue(False)
        canvas["disable"].setExpression("parent.render_region")
        canvas.setXYpos(0, 160)

        occ_ch = nuke.nodes.AddChannels(name="OcclusionChannels", inputs=[ins["occlusion"]])
        occ_ch["channels"].setValue("rgba")
        occ_ch["channels2"].setValue("depth")
        occ_ch.setXYpos(220, 80)
        occ = nuke.nodes.Copy(name="OcclusionDepth", inputs=[occ_ch, occ_ch])
        occ["from0"].setValue("depth.Z")
        occ["to0"].setValue("rgba.red")
        occ.setXYpos(220, 160)

        dirt_ch = nuke.nodes.AddChannels(name="DirtChannels", inputs=[ins["dirt"]])
        dirt_ch["channels"].setValue("rgba")
        dirt_ch.setXYpos(440, 80)
        dirt = nuke.nodes.Reformat(name="DirtFit", inputs=[dirt_ch])
        dirt["type"].setValue("to box")
        dirt["box_fixed"].setValue(True)
        dirt["box_width"].setExpression("Canvas.width")
        dirt["box_height"].setExpression("Canvas.height")
        dirt["resize"].setValue("fill")
        dirt["center"].setValue(True)
        dirt.setXYpos(440, 160)

        build_projection(canvas, ins["cam"], ins["axis"])

        blink = make_kernel(spec.KERNEL_NODE, [canvas, occ, dirt])
        blink.setXYpos(220, 260)

        mb = nuke.nodes.TimeBlur(name=spec.MOTION_BLUR_NODE, inputs=[blink])
        _set_if_present(mb, "divisions", 8)
        _set_if_present(mb, "shutter", 0.5)
        _set_if_present(mb, "shutteroffset", "centred")
        mb["disable"].setExpression("1 - parent.motion_blur")
        mb.setXYpos(220, 340)

        merge = nuke.nodes.Merge2(name=spec.MERGE_NODE, inputs=[ins["src"], mb])
        merge["operation"].setValue("plus")
        merge["output"].setValue("rgb")
        # The mask input is only connected while the group's 'mask' input is.
        _set_if_present(merge, "maskChannelMask", "rgba.alpha")
        merge.setXYpos(0, 420)

        switch = nuke.nodes.Switch(name=spec.SWITCH_NODE, inputs=[merge, mb])
        switch["which"].setExpression("parent.output_mode")
        switch.setXYpos(0, 500)

        out = nuke.nodes.Output(inputs=[switch])
        out.setXYpos(0, 580)
    return blink


def create():
    """Create a BlinkFlare node below the selected node (or on its own)."""
    sel, fmt = _input_format()
    group = nuke.nodes.Group()
    try:
        group.setName("BlinkFlare1")
        group["tile_color"].setValue(0xE8A33DFF)
        build_internals(group)
        add_group_knobs(group, fmt)
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


# ------------------------------------------------------------ element layers

def build_element_layers(group):
    """Add one solo kernel per element and copy each into its own layer.

    Built on demand because every extra kernel has to compile. Safe to call
    again; it does nothing once the layers exist.
    """
    first = spec.ELEMENTS[0][1]
    if group.node("%s_%s" % (spec.KERNEL_NODE, first)) is not None:
        return
    with group:
        main = group.node(spec.KERNEL_NODE)
        mb = group.node(spec.MOTION_BLUR_NODE)
        switch = group.node(spec.SWITCH_NODE)
        inputs = [main.input(i) for i in range(3)]
        prev_stream = main
        prev_comp = group.node(spec.MERGE_NODE)
        off = "1 - parent.element_layers"
        for i, (index, name, layer) in enumerate(spec.ELEMENTS):
            if layer not in nuke.layers():
                nuke.Layer(layer, ["%s.%s" % (layer, c) for c in RGBA])
            inst = make_kernel("%s_%s" % (spec.KERNEL_NODE, name), inputs, solo=index)
            inst["disable"].setExpression(off)
            for knob in ("useGPUIfAvailable", "vectorize"):
                if inst.knob(knob) is not None:
                    inst[knob].setExpression("%s.%s" % (spec.KERNEL_NODE, knob))
            inst.setXYpos(440 + 110 * i, 260)

            stream = nuke.nodes.Copy(name="Layer_" + name, inputs=[prev_stream, inst])
            comp = nuke.nodes.Copy(name="CompLayer_" + name, inputs=[prev_comp, mb])
            for c, chan in enumerate(RGBA):
                stream["from%d" % c].setValue("rgba." + chan)
                stream["to%d" % c].setValue("%s.%s" % (layer, chan))
                comp["from%d" % c].setValue("%s.%s" % (layer, chan))
                comp["to%d" % c].setValue("%s.%s" % (layer, chan))
            for node in (stream, comp):
                node["disable"].setExpression(off)
            stream.setXYpos(440 + 110 * i, 300)
            comp.setXYpos(-220, 420 + 30 * i)
            prev_stream, prev_comp = stream, comp
        mb.setInput(0, prev_stream)
        switch.setInput(0, prev_comp)


# ---------------------------------------------------------------- 3D -> 2D

def _parse_range(text):
    first, _, last = text.replace(" ", "").partition("-")
    first = int(first)
    return range(first, int(last or first) + 1)


def bake_to_2d(node, frames=None):
    """Bake the projected light (and lens center) into 2D keyframes."""
    if int(node["light_source"].getValue()) != 1:
        nuke.message("Set Light Source to 3D before baking.")
        return
    if frames is None:
        root = nuke.root()
        text = nuke.getInput("Bake frame range", "%d-%d" % (
            int(root["first_frame"].value()), int(root["last_frame"].value())))
        if not text:
            return
        frames = _parse_range(text)
    proj = node.node(camera.PROJECTION_NODE)
    targets = [("light_pos", "screen")]
    if int(node["articulation_mode"].getValue()) == 2:
        targets.append(("axis_center", "lens_center"))

    undo = nuke.Undo()
    undo.begin("BlinkFlare bake to 2D")
    try:
        for dst_name, src_name in targets:
            src = proj[src_name]
            samples = [(f, src.getValueAt(f, 0), src.getValueAt(f, 1)) for f in frames]
            dst = node[dst_name]
            dst.clearAnimated()
            dst.setAnimated()
            for f, x, y in samples:
                dst.setValueAt(x, f, 0)
                dst.setValueAt(y, f, 1)
        if len(targets) > 1:
            node["articulation_mode"].setValue(0)
        node["light_source"].setValue(0)
    finally:
        undo.end()


# ------------------------------------------------------------------ presets

def apply_preset(node, name=None):
    """Reset the look knobs of ``node`` to a preset (undoable)."""
    if name is None:
        name = node["preset"].value()
    available = presets.all_presets()
    if name not in available:
        raise KeyError("Unknown BlinkFlare preset %r" % name)
    values = spec.defaults()
    values.update(available[name])

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


def refresh_presets(node, select=None):
    knob = node.knob("preset")
    if knob is None:
        return
    current = select or knob.value()
    items = list(presets.all_presets().keys())
    knob.setValues(items)
    if current in items:
        knob.setValue(current)


def save_preset(node, name=None):
    """Save the current look of ``node`` as a preset file."""
    if name is None:
        name = nuke.getInput("Preset name", "")
        if not name:
            return None
    values = {}
    for k in spec.value_knobs():
        if k.name not in spec.NON_LOOK_KNOBS and node.knob(k.name) is not None:
            values[k.name] = get_knob_value(node[k.name], k)
    path = presets.write_preset(name, values)
    refresh_presets(node, select=name)
    return path


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
