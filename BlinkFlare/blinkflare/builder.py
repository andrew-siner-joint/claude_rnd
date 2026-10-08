"""Builds the BlinkFlare group inside Nuke.

Node graph inside the group:

    src ── SrcChannels ── Canvas (Crop to format) ─────────────────┐
    occlusion ── OcclusionChannels ── OcclusionDepth (depth→red) ──┼── FlareKernel ── [element layers] ── MotionBlur ──┐
    TableBase ── TableCrop ── TableRow0..5 (Expressions) ──────────┘                                                  │
    cam ── CameraXform (Axis) ┐                                                                                       │
    axis ── LightXform (Axis) ┴── Projection (NoOp, expressions only)                                                  │
    src ─────────────────── Composite (Merge, B) ── A ────────────────────────────────────────────────────────────────┤
    mask ─ (Composite mask, connected only when the outer input is)                     [element layers] ── OutputSwitch ── Output

The kernel renders the flare alone; the Merge composites it so the operation,
mix and mask behave like any Nuke merge and the source alpha passes through.
Element Layers adds one solo kernel per render pass plus Copy nodes, built
the first time it is switched on.

The flare's elements are knobs added at runtime (e<id>_<field>, one
collapsible group each). The TableRow Expression nodes turn them into the
small table image the kernel reads (see elements.py); adding, removing or
re-typing an element rewrites those expressions. Nuke only appends knobs, so
adding an element lifts the 3D and Output tabs off and puts them back after
it (_AfterElementsLifted).

Kernels are never compiled while a node is being built: the first create()
compiles once in the background (see compiling.py) and caches the compiled
node; every kernel after that is a paste of it.
"""

import os
import re
import traceback

import nuke

from blinkflare import camera, compiling, elements, lenses, presets, spec

KERNEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kernel", "BlinkFlare.blink")

AXIS_CLASSES = ("Axis2", "Axis3", "Axis")
RGBA = ("red", "green", "blue", "alpha")


class BuildError(RuntimeError):
    pass


class StaleTemplate(BuildError):
    """The cached compiled kernel doesn't match this version."""


def kernel_source():
    with open(KERNEL_PATH) as f:
        return f.read()


# --------------------------------------------------------------------- kernel

param_knob_name = compiling.param_knob_name


def param_knob(blink, param):
    """The BlinkScript knob for kernel parameter ``param``."""
    name = param_knob_name(blink, param)
    if name is None:
        raise BuildError("BlinkScript node %s has no knob for kernel param '%s'."
                         % (blink.name(), param))
    return blink[name]


def kernel_params():
    """Every kernel parameter the group links to."""
    return [k.blink for k in spec.value_knobs() if k.blink] + list(spec.DERIVED_PARAMS)


MANUAL_ROUTE = (
    "To build it by hand instead: create a BlinkScript node, use its Load button on "
    "blinkflare/kernel/BlinkFlare.blink, press Recompile, then with that node selected "
    "run Nodes > Draw > BlinkFlare > Build From Compiled BlinkScript.")


def compile_failure_message(result):
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
    return "\n\n".join([
        "BlinkFlare's Blink kernel did not compile: %s." % result.describe(),
        "Nuke %s (%s)." % (version, licence),
        "Run Nodes > Draw > BlinkFlare > Check Install... for a full report.",
        MANUAL_ROUTE,
    ])


def show_error(message):
    print(message)
    if nuke.GUI:
        nuke.message(message)


def _delete_quietly(node):
    try:
        nuke.delete(node)
    except Exception:
        pass  # already gone, e.g. the user deleted it while it compiled


def kernel_template(ready, on_error):
    """Call ``ready(path)`` with the saved compiled kernel, compiling and
    saving it first (in the background) if this version has none yet."""
    source = kernel_source()
    path = compiling.template_path(source)
    if os.path.isfile(path):
        ready(path)
        return
    with nuke.root():
        holder = nuke.nodes.Group(name="BlinkFlare_compiling")
    with holder:
        node = nuke.nodes.BlinkScript(name=spec.KERNEL_NODE)
        if node.knob("useGPUIfAvailable") is not None:
            node["useGPUIfAvailable"].setValue(False)  # CPU-only compiles much faster
    progress = compiling.Progress(
        "BlinkFlare", "Compiling the BlinkFlare kernel. This only happens the first time.")

    def done(result):
        progress.close()
        failure = None
        try:
            if result.ok:
                compiling.save_template(node, holder, path)
        except Exception:
            failure = "Saving the compiled kernel failed:\n" + traceback.format_exc()
        finally:
            _delete_quietly(holder)
        if failure:
            on_error(failure)
        elif result.ok:
            ready(path)
        elif result.mode != "cancelled":
            on_error(compile_failure_message(result))

    compiling.compile_async(node, source, "lightPos", done, progress=progress)


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


def make_kernel(name, inputs, template, solo=None):
    """Paste the compiled kernel from ``template`` and wire it up."""
    blink = compiling.paste_template(template, name)
    missing = [p for p in kernel_params() if param_knob_name(blink, p) is None]
    if missing:
        raise StaleTemplate("the saved kernel %s lacks params %s" % (template, ", ".join(missing)))
    for i, node in enumerate(inputs):
        blink.setInput(i, node)
    link_kernel_params(blink)
    if solo is not None:
        knob = param_knob(blink, "soloPass")
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
        if k.name == spec.ELEMENTS_TAB_END:
            # Bookkeeping, hidden at the end of the Elements tab's own knobs.
            for name, value in (("element_ids", ""), ("blinkflare_version", spec.VERSION)):
                hidden = nuke.String_Knob(name, name)
                group.addKnob(hidden)
                hidden.setValue(value)
                hidden.setVisible(False)
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


def build_table_nodes():
    """Constant -> Crop -> six chained Expression nodes, one per table row.
    Returns the last one (the table image)."""
    base = nuke.nodes.Constant(name="TableBase")
    base.setXYpos(1100, 0)
    crop = nuke.nodes.Crop(name="TableCrop", inputs=[base])
    box = crop["box"]
    for i, v in enumerate((0, 0, 1, elements.ROWS)):
        box.setValue(v, i)
    crop["reformat"].setValue(False)
    crop["crop"].setValue(False)
    crop.setXYpos(1100, 60)
    prev = crop
    for r in range(elements.ROWS):
        row = nuke.nodes.Expression(name="TableRow%d" % r, inputs=[prev])
        for c in range(4):
            row["expr%d" % c].setValue(elements.row_expression([], r, c))
        row.setXYpos(1100, 120 + 40 * r)
        prev = row
    return prev


def build_internals(group, template):
    """Create the node graph inside ``group``. Returns the main kernel."""
    with group:
        names = ["src", "occlusion", "cam", "axis", "mask"]
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

        build_projection(canvas, ins["cam"], ins["axis"])
        table = build_table_nodes()

        blink = make_kernel(spec.KERNEL_NODE, [canvas, occ, table], template)
        if blink.knob("useGPUIfAvailable") is not None:
            blink["useGPUIfAvailable"].setValue(True)
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


def build_group(sel, fmt, template, show_panel=True):
    group = nuke.nodes.Group()
    try:
        group.setName("BlinkFlare1")
        group["tile_color"].setValue(0xE8A33DFF)
        build_internals(group, template)
        add_group_knobs(group, fmt)
        apply_preset(group, list(presets.PRESETS)[0])
    except Exception:
        nuke.delete(group)
        raise

    if sel is not None:
        group.setInput(0, sel)
        group.setXYpos(sel.xpos(), sel.ypos() + 80)
    for n in nuke.selectedNodes():
        n.setSelected(False)
    group.setSelected(True)
    if nuke.GUI and show_panel:
        group.showControlPanel()
    return group


def create(on_done=None, on_error=None):
    """Create a BlinkFlare node below the selected node (or on its own).

    Returns the node when it could be built straight away (the compiled
    kernel is cached). The first time, the kernel compiles in the background
    and the node appears when it's done: ``on_done(node)`` is called then,
    or ``on_error(message)`` if it can't be built.
    """
    sel, fmt = _input_format()
    return _when_kernel_ready(lambda template: build_group(sel, fmt, template),
                              on_done, on_error or show_error)


def _when_kernel_ready(build, on_done, on_error):
    """Run ``build(template)`` once the compiled kernel is available: at once
    when it's cached (returning the result), otherwise after a background
    compile (``on_done(result)``, or ``on_error(message)``)."""
    built = []

    def ready(template, retry=True):
        try:
            result = build(template)
        except StaleTemplate:
            os.remove(template)
            if retry:
                kernel_template(lambda path: ready(path, retry=False), on_error)
            else:
                on_error("The freshly compiled kernel is missing parameters.\n\n" + MANUAL_ROUTE)
            return
        except Exception:
            on_error("Building BlinkFlare failed:\n" + traceback.format_exc())
            return
        built.append(result)
        if on_done is not None:
            on_done(result)

    kernel_template(ready, on_error)
    return built[0] if built else None


def create_from_kernel(node, on_done=None, on_error=None):
    """Build BlinkFlare around a BlinkScript node compiled by hand.

    The manual route for when compiling from Python doesn't work: load
    BlinkFlare.blink into a BlinkScript node, press Recompile, select it and
    run this. The compiled kernel is saved, so later creates are instant.
    """
    if node is None or node.Class() != "BlinkScript":
        raise BuildError("Select a BlinkScript node that has the BlinkFlare kernel compiled.")
    missing = [p for p in kernel_params() if param_knob_name(node, p) is None]
    if missing:
        raise BuildError("The selected BlinkScript node doesn't have the BlinkFlare kernel "
                         "compiled (missing knobs for %s). Load blinkflare/kernel/BlinkFlare.blink "
                         "with its Load button and press Recompile first." % ", ".join(missing[:5]))
    full = node.fullName()
    context = nuke.root() if "." not in full else nuke.toNode("root." + full.rsplit(".", 1)[0])
    compiling.save_template(node, context, compiling.template_path(kernel_source()))
    return create(on_done, on_error)


# ------------------------------------------------------------ element layers

def build_element_layers(group):
    """Add one solo kernel per element and copy each into its own layer.

    Built on demand because every extra kernel has to compile. Safe to call
    again; it does nothing once the layers exist.
    """
    first = spec.PASSES[0][1].lower()
    if group.node("%s_%s" % (spec.KERNEL_NODE, first)) is not None:
        return
    main = group.node(spec.KERNEL_NODE)
    # Clone this node's own compiled kernel: no compiles, and it matches
    # whatever version the node was built with.
    template = os.path.join(compiling.cache_dir(), "layers_%d.nk" % os.getpid())
    compiling.save_template(main, group, template)
    with group:
        mb = group.node(spec.MOTION_BLUR_NODE)
        switch = group.node(spec.SWITCH_NODE)
        inputs = [main.input(i) for i in range(main.maxInputs())]
        prev_stream = main
        prev_comp = group.node(spec.MERGE_NODE)
        off = "1 - parent.element_layers"
        for i, (index, name, layer) in enumerate(spec.PASSES):
            name = name.lower()
            if layer not in nuke.layers():
                nuke.Layer(layer, ["%s.%s" % (layer, c) for c in RGBA])
            inst = make_kernel("%s_%s" % (spec.KERNEL_NODE, name), inputs, template, solo=index)
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

def _require_v3(node):
    if node.knob("element_ids") is None:
        raise BuildError("This node was made with an older BlinkFlare. Create a new BlinkFlare "
                         "node to use element stacks and v3 presets.")


def look_knobs():
    """Static knobs a preset sets (everything that isn't placement/pipeline)."""
    return [k for k in spec.value_knobs() if k.name not in spec.NON_LOOK_KNOBS]


def apply_preset(node, name=None):
    """Replace the node's look and element stack with a preset."""
    _require_v3(node)
    if name is None:
        name = node["preset"].value()
    available = presets.all_presets()
    if name not in available:
        raise KeyError("Unknown BlinkFlare preset %r" % name)
    preset = available[name]
    values = spec.defaults()
    values.update(preset.get("globals", {}))

    with _NoUndo():
        for k in look_knobs():
            knob = node.knob(k.name)
            if knob is None or values[k.name] is None:
                continue
            if knob.isAnimated():
                knob.clearAnimated()
            set_knob_value(knob, k, values[k.name])
        clear_elements(node, rebuild=False)
        with _AfterElementsLifted(node):
            for el in presets.stack(preset):
                add_element(node, el["type"], el, rebuild=False)
        rebuild_table(node)


def refresh_presets(node, select=None):
    knob = node.knob("preset")
    if knob is None:
        return
    current = select or knob.value()
    items = list(presets.all_presets().keys())
    knob.setValues(items)
    if current in items:
        knob.setValue(current)


def refresh_panel(node):
    """Preset menu and element knob labels/visibility, re-applied whenever
    the panel opens (so they don't depend on what the script file kept)."""
    refresh_presets(node)
    if node.knob("element_ids") is not None:
        _renumber(node)


def save_preset(node, name=None):
    """Save the node's look and element stack as a preset file."""
    _require_v3(node)
    if name is None:
        name = nuke.getInput("Preset name", "")
        if not name:
            return None
    values = {}
    for k in look_knobs():
        if node.knob(k.name) is not None:
            values[k.name] = get_knob_value(node[k.name], k)
    path = presets.write_preset(name, values, stack(node))
    refresh_presets(node, select=name)
    return path


# ----------------------------------------------------------------- elements

def element_ids(node):
    """Ids of the node's elements, in order (ignoring any whose knobs are gone)."""
    ids = []
    for v in node["element_ids"].value().split(","):
        v = v.strip()
        if v.isdigit() and int(v) not in ids and node.knob(elements.knob_name(int(v), "type")):
            ids.append(int(v))
    return ids


def _next_id(node):
    used = [int(m.group(1)) for m in (re.match(r"e(\d+)_", n) for n in node.knobs()) if m]
    return max(used + element_ids(node) + [0]) + 1


class _NoUndo(object):
    """Keeps element edits out of Nuke's undo history. Nuke can't undo added
    or removed knobs, so undoing only the values would leave the stack
    inconsistent."""

    def __enter__(self):
        try:
            self.undo = nuke.Undo()
            self.was_disabled = bool(self.undo.disabled())
        except (AttributeError, RuntimeError):
            self.undo, self.was_disabled = None, False
        if self.undo is not None and not self.was_disabled:
            self.undo.disable()
        return self

    def __exit__(self, *exc):
        if self.undo is not None and not self.was_disabled:
            self.undo.enable()
        return False


def _set_element_ids(node, ids):
    node["element_ids"].setValue(",".join(str(i) for i in ids))


def _tab_flag(name, fallback):
    return getattr(nuke, name, fallback)


def _element_knobs(eid):
    """(field, knob) for a new element's knobs, in panel order."""
    k = lambda f: elements.knob_name(eid, f)  # noqa: E731
    out = [("begin", nuke.Tab_Knob(k("begin"), "", _tab_flag("TABBEGINCLOSEDGROUP", 2))),
           ("on", nuke.Boolean_Knob(k("on"), "Enable")),
           ("type", nuke.Enumeration_Knob(k("type"), "", elements.TYPE_NAMES)),
           ("layer", nuke.Enumeration_Knob(k("layer"), "Layer", elements.LAYER_ITEMS)),
           ("dup", nuke.PyScript_Knob(k("dup"), "Duplicate",
                                      "import blinkflare\nblinkflare.duplicate_element("
                                      "nuke.thisNode(), %d)\n" % eid)),
           ("del", nuke.PyScript_Knob(k("del"), "Delete",
                                      "import blinkflare\nblinkflare.remove_element("
                                      "nuke.thisNode(), %d, later=True)\n" % eid)),
           ("intensity", nuke.Double_Knob(k("intensity"), "Intensity")),
           ("color", nuke.Color_Knob(k("color"), "Color")),
           ("size", nuke.Double_Knob(k("size"), "Size")),
           ("axis", nuke.Double_Knob(k("axis"), "Axis Position")),
           ("rotation", nuke.Double_Knob(k("rotation"), "Rotation")),
           ("seed", nuke.Int_Knob(k("seed"), "Seed")),
           ("dispersion", nuke.Double_Knob(k("dispersion"), "Dispersion")),
           ("softness", nuke.Double_Knob(k("softness"), "Softness"))]
    out += [("p%d" % i, nuke.Double_Knob(k("p%d" % i), "p%d" % i)) for i in range(8)]
    out += [("lens", nuke.Enumeration_Knob(k("lens"), "Lens", lenses.lens_names())),
            ("lens_file", nuke.File_Knob(k("lens_file"), "Lens File")),
            ("fstop", nuke.Double_Knob(k("fstop"), "f-stop")),
            ("coating", nuke.Enumeration_Knob(k("coating"), "Coating", lenses.COATINGS)),
            ("max_ghosts", nuke.Int_Knob(k("max_ghosts"), "Max Ghosts")),
            ("sensor", nuke.Double_Knob(k("sensor"), "Sensor Height (mm)")),
            ("end", nuke.Tab_Knob(k("end"), "", _tab_flag("TABENDGROUP", -1)))]
    return out


GENERIC_RANGES = {"intensity": (0.0, 2.0), "size": (0.0, 1.0), "axis": (-1.0, 3.0),
                  "rotation": (-180.0, 180.0), "dispersion": (0.0, 1.0), "softness": (0.0, 1.0)}
SAME_LINE = ("type", "layer", "dup", "del")
LENS_TIPS = {
    "lens": "Lens design. Ghosts come from its real surfaces, so more air-glass surfaces "
            "mean more ghosts: the Achromat (two cemented elements) is nearly flare-free. "
            "Add your own as PBRT-format .dat files in a folder on BLINKFLARE_LENS_PATH, "
            "or pick From File.",
    "fstop": "Ghost size follows the entrance pupil (1 / f-stop). Long lenses at wide "
             "apertures give huge, faint ghosts; stop down to see them.",
    "coating": "Single coated: purple/magenta ghosts. Multi coated: fainter, varied tints. "
               "Uncoated: bright, neutral ghosts.",
    "max_ghosts": "Keeps the most visible ghosts (every surface pair gives one).",
    "sensor": "Sensor (frame) height in mm; sets ghost size relative to the frame.",
}


def _set_element_value(knob, field, value):
    if field == "color":
        knob.setValue([float(c) for c in value])
    elif field in ("on",):
        knob.setValue(bool(value))
    elif field in ("type", "layer", "seed", "max_ghosts"):
        knob.setValue(int(value))
    elif field in ("lens", "coating"):
        try:
            knob.setValue(value)
        except (ValueError, RuntimeError):
            knob.setValue(0)  # e.g. a lens file that isn't on this machine
    elif field == "lens_file":
        knob.setValue(str(value))
    else:
        knob.setValue(float(value))


class _AfterElementsLifted(object):
    """Nuke can only append knobs, so new element knobs would land on the
    last tab. While this is active, the knobs of the tabs after Elements (3D,
    Output) are off the node; on exit they go back on, after the new element
    knobs. Their values, animation and links are snapshotted and restored, in
    case removing a knob loses them. Re-entrant: an inner use finds nothing
    left to lift."""

    def __init__(self, node):
        self.node = node
        self.knobs, self.values, self.links = [], [], []

    def __enter__(self):
        node = self.node
        self.knobs = [node.knob(n) for n in spec.AFTER_ELEMENTS if node.knob(n) is not None]
        for knob in self.knobs:
            k = spec.knob(knob.name())
            if k.is_value:
                self.values.append((knob, knob.toScript()))
            elif k.kind == "link":
                self.links.append((knob, _link_target(knob)))
        lifted = []
        try:
            for knob in reversed(self.knobs):
                node.removeKnob(knob)
                lifted.insert(0, knob)
        except Exception:
            self.knobs = lifted  # put back what did come off
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        for knob in self.knobs:
            self.node.addKnob(knob)
        for knob, script in self.values:
            if knob.toScript() != script:
                knob.fromScript(script)
        for knob, target in self.links:
            if target and _link_target(knob) != target:
                knob.setLink(target)
        return False


def _link_target(knob):
    get = getattr(knob, "getLink", None)
    return get() if get is not None else None


def add_element(node, type_name, values=None, rebuild=True):
    """Add an element of ``type_name`` (with optional element dict values)."""
    _require_v3(node)
    el = dict(values) if values else elements.element(type_name)
    el["type"] = type_name
    ids = element_ids(node)
    eid = _next_id(node)
    knob_values = elements.knob_values(elements.element(type_name, **_overrides(el)))
    with _NoUndo(), _AfterElementsLifted(node):
        for field, knob in _element_knobs(eid):
            if field in GENERIC_RANGES:
                knob.setRange(*GENERIC_RANGES[field])
            if field in LENS_TIPS:
                knob.setTooltip(LENS_TIPS[field])
            node.addKnob(knob)
            if field in SAME_LINE:
                knob.clearFlag(nuke.STARTLINE)
            if field in knob_values:
                _set_element_value(knob, field, knob_values[field])
        _set_element_ids(node, ids + [eid])
        apply_type_ui(node, eid)
        if rebuild:
            rebuild_table(node)
    return eid


def _overrides(el):
    """The element dict minus 'type' (for elements.element)."""
    out = dict(el)
    out.pop("type", None)
    return out


def remove_element(node, eid, rebuild=True, later=False):
    """Remove element ``eid``. ``later`` defers it to the next event-loop
    turn, for the element's own Delete button (whose knob it removes)."""
    _require_v3(node)
    if later:
        compiling.later(0, lambda: remove_element(node, eid, rebuild))
        return
    prefix = "e%d_" % eid
    with _NoUndo():
        for name in [n for n in node.knobs() if n.startswith(prefix)]:
            node.removeKnob(node[name])
        _set_element_ids(node, [i for i in element_ids(node) if i != eid])
        if rebuild:
            rebuild_table(node)


def clear_elements(node, rebuild=True):
    for eid in element_ids(node):
        remove_element(node, eid, rebuild=False)
    if rebuild:
        rebuild_table(node)


def duplicate_element(node, eid):
    """Copy an element, keyframes and expressions included."""
    el = element_values(node, eid)
    with _NoUndo():
        new = add_element(node, el["type"], el, rebuild=False)
        for field in elements.VALUE_FIELDS:
            src = node.knob(elements.knob_name(eid, field))
            dst = node.knob(elements.knob_name(new, field))
            if src is not None and dst is not None and src.isAnimated():
                dst.fromScript(src.toScript())
        rebuild_table(node)
    return new


def element_values(node, eid):
    """The element dict for element ``eid`` (values at the current frame)."""
    k = lambda f: node[elements.knob_name(eid, f)]  # noqa: E731
    type_name = k("type").value()
    el = {"type": type_name, "on": bool(k("on").value()), "layer": int(k("layer").getValue()),
          "intensity": float(k("intensity").value()),
          "color": tuple(float(k("color").value(i)) for i in range(3)),
          "size": float(k("size").value()), "axis": float(k("axis").value()),
          "rotation": float(k("rotation").value()), "seed": int(k("seed").value()),
          "dispersion": float(k("dispersion").value()),
          "softness": float(k("softness").value()),
          "p": [float(k("p%d" % i).value()) for i in range(8)]}
    if type_name == "Lens System":
        el.update({"lens": k("lens").value(), "lens_file": k("lens_file").value(),
                   "fstop": float(k("fstop").value()), "coating": k("coating").value(),
                   "max_ghosts": int(k("max_ghosts").value()),
                   "sensor": float(k("sensor").value())})
    return el


def stack(node):
    return [element_values(node, eid) for eid in element_ids(node)]


def apply_type_ui(node, eid):
    """Labels, ranges and visibility of an element's knobs for its type."""
    k = lambda f: node.knob(elements.knob_name(eid, f))  # noqa: E731
    t = elements.TYPES[k("type").value()]
    position = element_ids(node).index(eid) + 1 if eid in element_ids(node) else 0
    k("begin").setLabel("%d. %s" % (position, t.name))
    for field in elements.GENERIC:
        spec_ = t.generic.get(field)
        k(field).setVisible(spec_ is not None)
        if spec_ is not None:
            k(field).setLabel(spec_[0])
    for i in range(8):
        knob = k("p%d" % i)
        if i < len(t.params):
            p = t.params[i]
            knob.setLabel(p.label)
            knob.setRange(p.lo, p.hi)
            knob.setTooltip(p.tooltip)
            knob.setVisible(True)
        else:
            knob.setVisible(False)
    for field in elements.LENS_FIELDS:
        k(field).setVisible(t.name == "Lens System")
    k("begin").setTooltip(t.help)


def _renumber(node):
    for eid in element_ids(node):
        apply_type_ui(node, eid)


def rebuild_table(node):
    """Rewrite the table expressions and element count for the current stack."""
    columns, problems = [], []
    for eid in element_ids(node):
        el = element_values(node, eid)
        if el["type"] == "Lens System":
            try:
                ghosts = lenses.ghosts_for_element(el)
            except Exception as e:  # bad lens file, etc.
                problems.append("Element %d (Lens System): %s" % (eid, e))
                ghosts = []
            columns += [elements.lens_column_expressions(eid, g, el["fstop"]) for g in ghosts]
        else:
            columns.append(elements.column_expressions(eid, el["type"]))
    columns = columns[:elements.MAX_COLUMNS]
    crop = node.node("TableCrop")
    crop["box"].setValue(max(len(columns), 1), 2)
    for r in range(elements.ROWS):
        row = node.node("TableRow%d" % r)
        for c in range(4):
            row["expr%d" % c].setValue(elements.row_expression(columns, r, c))
    for child in node.nodes():
        if child.Class() == "BlinkScript" and param_knob_name(child, "elementCount"):
            param_knob(child, "elementCount").setValue(len(columns))
    _renumber(node)
    if problems and nuke.GUI:
        nuke.message("\n".join(problems))
    return len(columns)


REBUILD_FIELDS = ("type",) + elements.LENS_FIELDS


def element_knob_changed(node, knob):
    """Called from the node's knobChanged for e<id>_<field> knobs."""
    name = knob.name()
    try:
        eid = int(name[1:name.index("_")])
    except ValueError:
        return
    field = name[name.index("_") + 1:]
    if eid not in element_ids(node) or field not in REBUILD_FIELDS:
        return
    with _NoUndo():
        if field == "type":
            # A new type starts from that type's defaults.
            defaults = elements.knob_values(elements.element(knob.value()))
            for f, value in defaults.items():
                target = node.knob(elements.knob_name(eid, f))
                if target is not None and f not in ("type", "on", "layer"):
                    if target.isAnimated():
                        target.clearAnimated()
                    _set_element_value(target, f, value)
            apply_type_ui(node, eid)
        rebuild_table(node)


# ---------------------------------------------------------------- upgrading
# Each node carries its own kernel and knobs, so a node built by an older
# BlinkFlare stays that version. Upgrading rebuilds it with this version and
# carries its settings, keyframes, elements and connections across.

def is_blinkflare(node):
    return (node is not None and node.Class() == "Group"
            and node.node(spec.KERNEL_NODE) is not None and node.knob("light_pos") is not None)


def node_version(node):
    """The BlinkFlare version that built ``node`` ("2" before versions were stored)."""
    knob = node.knob("blinkflare_version")
    return knob.value() if knob is not None else "2"


def _version_key(text):
    return tuple(int(p) if p.isdigit() else 0 for p in text.split("."))


def is_outdated(node):
    return is_blinkflare(node) and _version_key(node_version(node)) < _version_key(spec.VERSION)


def _input_indices(group):
    """Input name -> index, as Nuke numbers the group's Input nodes."""
    return dict((n.name(), int(n["number"].value()))
                for n in group.nodes() if n.Class() == "Input")


def _parent_of(node):
    path = node.fullName().rsplit(".", 1)
    return nuke.toNode("root." + path[0]) if len(path) == 2 else nuke.root()


def _copy_knob(src, dst):
    """Value, keyframes or expression of ``src`` onto ``dst``; False (and
    ``dst`` untouched) when it doesn't fit, e.g. a menu item that's gone."""
    backup = dst.toScript()
    try:
        dst.fromScript(src.toScript())
        return True
    except (ValueError, RuntimeError, IndexError, TypeError):
        dst.fromScript(backup)
        return False


def _transfer(old, new):
    """Copy an older node's settings, elements and connections onto ``new``.
    Returns notes on anything that couldn't come across."""
    notes = []
    for k in spec.value_knobs():
        src, dst = old.knob(k.name), new.knob(k.name)
        if src is not None and dst is not None and not _copy_knob(src, dst):
            notes.append("%s was reset (%s isn't an option any more)" % (k.label, src.toScript()))
    for k in spec.KNOBS:
        a, b = (_resolve_link(g, k) if k.kind == "link" else None for g in (old, new))
        if a and b:
            _copy_knob(old.node(a.split(".")[0])[a.split(".")[1]],
                       new.node(b.split(".")[0])[b.split(".")[1]])
    if old.knob("dirt_enable") is not None and old["dirt_enable"].value():
        notes.append("lens dirt was on; BlinkFlare no longer has it")

    if old.knob("element_ids") is not None:
        clear_elements(new, rebuild=False)
        with _NoUndo(), _AfterElementsLifted(new):
            for eid in element_ids(old):
                el = element_values(old, eid)
                nid = add_element(new, el["type"], el, rebuild=False)
                for field in elements.VALUE_FIELDS:
                    src = old.knob(elements.knob_name(eid, field))
                    dst = new.knob(elements.knob_name(nid, field))
                    if src is not None and dst is not None and src.isAnimated():
                        _copy_knob(src, dst)
        rebuild_table(new)
    else:
        notes.append("it predates element stacks, so its look starts from the Default preset")

    old_inputs, new_inputs = _input_indices(old), _input_indices(new)
    for name, index in sorted(old_inputs.items(), key=lambda item: item[1]):
        source = old.input(index)
        if source is None:
            continue
        if name in new_inputs:
            new.setInput(new_inputs[name], source)
        else:
            notes.append("its '%s' input (%s) was disconnected" % (name, source.name()))
    what = getattr(nuke, "INPUTS", 1) | getattr(nuke, "HIDDEN_INPUTS", 2)
    for user in old.dependent(what, False):
        for i in range(user.inputs()):
            if user.input(i) == old:
                user.setInput(i, new)
    for name in ("label", "tile_color", "hide_input", "postage_stamp"):
        if old.knob(name) is not None and new.knob(name) is not None:
            _copy_knob(old[name], new[name])
    return notes


def upgrade(node, on_done=None, on_error=None):
    """Rebuild ``node`` with this version of BlinkFlare, in its place and
    under its name. Returns (new_node, notes) when the compiled kernel is
    cached; otherwise ``on_done((new_node, notes))`` after it compiles."""
    if not is_blinkflare(node):
        raise BuildError("%s isn't a BlinkFlare node." % node.name())
    parent = _parent_of(node)

    def build(template):
        with parent:
            new = build_group(None, node.format(), template, show_panel=False)
            try:
                notes = _transfer(node, new)
                name, x, y = node.name(), node.xpos(), node.ypos()
                nuke.delete(node)
            except Exception:
                nuke.delete(new)
                raise
            new.setName(name)
            new.setXYpos(x, y)
            if new["element_layers"].value():
                build_element_layers(new)
            camera.link_now(new, spec.MERGE_NODE, nuke)
        return new, notes

    return _when_kernel_ready(build, on_done, on_error or show_error)


def upgrade_selected():
    """Upgrade the selected BlinkFlare nodes; with none selected, offer to
    upgrade every older one in the script. Reports what happened."""
    nodes = [n for n in nuke.selectedNodes() if is_blinkflare(n)]
    if not nodes:
        nodes = [n for n in nuke.allNodes("Group", recurseGroups=True) if is_outdated(n)]
        if not nodes:
            nuke.message("No BlinkFlare nodes need upgrading (this is v%s)." % spec.VERSION)
            return []
        if not nuke.ask("Upgrade all %d older BlinkFlare nodes in this script to v%s?"
                        % (len(nodes), spec.VERSION)):
            return []
    pending, done, failed = list(nodes), [], []

    def step(result=None):
        if result is not None:
            done.append(result)
        if not pending:
            lines = ["Upgraded %d BlinkFlare node%s to v%s." % (
                len(done), "" if len(done) == 1 else "s", spec.VERSION)]
            lines += ["%s: %s" % (new.name(), note) for new, notes in done for note in notes]
            lines += ["%s failed: %s" % (name, message) for name, message in failed]
            nuke.message("\n".join(lines))
            return
        node = pending.pop(0)
        name = node.name()

        def failed_one(message, name=name):
            failed.append((name, message))
            step()
        upgrade(node, on_done=step, on_error=failed_one)

    step()
    return done


# ------------------------------------------------------------------ toolset

def save_toolset(path=None, on_done=None, on_error=None):
    """Build a BlinkFlare node and save it as a ToolSet .nk.

    The saved node carries the compiled kernel, so it can be shared with
    seats that can't compile Blink kernels themselves.
    """
    if path is None:
        path = os.path.join(os.path.expanduser("~"), ".nuke", "ToolSets", "BlinkFlare.nk")
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    saved = []

    def write(node):
        node.setInput(0, None)
        try:
            for n in nuke.selectedNodes():
                n.setSelected(False)
            node.setSelected(True)
            nuke.nodeCopy(path)
        finally:
            nuke.delete(node)
        saved.append(path)
        if on_done is not None:
            on_done(path)
        elif nuke.GUI:
            nuke.message("Saved " + path)

    for n in nuke.selectedNodes():
        n.setSelected(False)
    create(write, on_error)
    return saved[0] if saved else None
