"""Minimal stand-in for the ``nuke`` module, enough to run blinkflare.builder.

It models only what the builder touches, but strictly: unknown knobs raise,
expressions on out-of-range channels raise, and the fake BlinkScript node
creates its param knobs by parsing the kernel source, like Nuke does on
compile.
"""
import json
import math
import re

import nuke_expr

STARTLINE = 0x1000
TABBEGINGROUP = 1
TABBEGINCLOSEDGROUP = 2
TABENDGROUP = -1
GUI = False
NUKE_VERSION_STRING = "15.1v3 (fake)"
env = {"nukex": True}

# How the fake BlinkScript node behaves; reset() restores these.
BLINK_COMPILE_ON = {"recompile"}  # triggers that compile: recompile, validate, file
BLINK_REJECT = []                 # source snippets the fake "compiler" rejects
BLINK_PREFIX = None               # param knob prefix; None = "<Kernel>_"
BLINK_ASYNC_POLLS = 0             # >0: compile finishes after this many knob lookups
compile_count = [0]
compile_gpu = []  # useGPUIfAvailable at each compile


class Format(object):
    def __init__(self, w, h, pa=1.0):
        self._w, self._h, self._pa = w, h, pa

    def width(self):
        return self._w

    def height(self):
        return self._h

    def pixelAspect(self):
        return self._pa


# ---------------------------------------------------------------------- knobs

class Knob(object):
    channels = 1
    default = 0.0

    def __init__(self, name, label="", *args):
        self._name = name
        self.label = label
        self.args = args
        self.values = [self.default] * self.channels
        self.expressions = {}
        self.flags = set()
        self.tooltip = ""
        self.range = None
        self.animated = False
        self.link = None
        self.node = None
        self.keys = {}  # channel -> {frame: value}
        self.visible = True

    def name(self):
        return self._name

    def _check(self, channel):
        if not 0 <= channel < self.channels:
            raise IndexError("%s has no channel %d" % (self._name, channel))

    def setValue(self, value, channel=None):
        if isinstance(value, (list, tuple)):
            if len(value) != self.channels:
                raise ValueError("%s expects %d values" % (self._name, self.channels))
            self.values = list(value)
        elif channel is not None:
            self._check(channel)
            self.values[channel] = value
        else:
            self.values = [value] * self.channels
        return True

    def value(self, channel=0):
        return self.values[channel] if self.channels > 1 else self.values[0]

    def getValue(self, channel=0):
        return self.value(channel)

    def getValueAt(self, frame, channel=0):
        return self.value(channel)

    def setAnimated(self, channel=-1):
        self.animated = True

    def setValueAt(self, value, frame, channel=0):
        self._check(channel)
        self.animated = True
        self.keys.setdefault(channel, {})[frame] = value

    def animation(self, channel):
        if channel not in self.expressions and channel not in self.keys:
            return None
        expr = self.expressions.get(channel, "curve")
        return type("AnimationCurve", (), {"expression": lambda _self: expr})()

    def setExpression(self, expr, channel=-1):
        self.set_count = getattr(self, "set_count", 0) + 1
        if channel == -1:
            for c in range(self.channels):
                self.expressions[c] = expr
        else:
            self._check(channel)
            self.expressions[channel] = expr
        return True

    def setTooltip(self, text):
        self.tooltip = text

    def setVisible(self, state):
        self.visible = bool(state)

    def setLabel(self, text):
        self.label = text

    def toScript(self):
        return json.dumps({"values": self.values, "keys": dict((str(c), dict((repr(float(f)), v) for f, v in k.items()))
                                                             for c, k in self.keys.items()),
                           "expressions": dict((str(c), e) for c, e in self.expressions.items())})

    def fromScript(self, text):
        data = json.loads(text)
        self.values = list(data["values"])
        self.keys = dict((int(c), dict((float(f), v) for f, v in k.items())) for c, k in data["keys"].items())
        self.expressions = dict((int(c), e) for c, e in data["expressions"].items())
        self.animated = bool(self.keys)

    def setFlag(self, flag):
        self.flags.add(flag)

    def clearFlag(self, flag):
        self.flags.discard(flag)

    def setRange(self, lo, hi):
        self.range = (lo, hi)

    def isAnimated(self):
        return self.animated or bool(self.expressions) or bool(self.keys)

    def hasExpression(self, channel=-1):
        return bool(self.expressions) if channel == -1 else channel in self.expressions

    def clearAnimated(self):
        self.animated = False
        self.expressions = {}
        self.keys = {}


class Double_Knob(Knob):
    pass


class Int_Knob(Knob):
    default = 0


class Boolean_Knob(Knob):
    default = False


class Color_Knob(Knob):
    channels = 3


class AColor_Knob(Knob):
    channels = 4


class XY_Knob(Knob):
    channels = 2


class XYZ_Knob(Knob):
    channels = 3


class Matrix_Knob(Knob):
    channels = 16


class BBox_Knob(Knob):
    channels = 4


class String_Knob(Knob):
    default = ""


class Tab_Knob(Knob):
    def __init__(self, name, label="", flag=0):
        Knob.__init__(self, name, label)
        self.flag = flag


class Text_Knob(Knob):
    pass


class Enumeration_Knob(Knob):
    default = 0

    def __init__(self, name, label="", items=()):
        Knob.__init__(self, name, label)
        self.items = list(items)

    def setValue(self, value, channel=None):
        if isinstance(value, str):
            if value not in self.items:
                raise ValueError("%s has no item %r" % (self._name, value))
            value = self.items.index(value)
        if not 0 <= value < len(self.items):
            raise ValueError("%s index %r out of range" % (self._name, value))
        self.values = [value]
        return True

    def value(self, channel=0):
        return self.items[self.values[0]]

    def getValue(self, channel=0):
        return float(self.values[0])

    def setValues(self, items):
        self.items = list(items)
        self.values = [min(self.values[0], len(self.items) - 1)]


class PyScript_Knob(Knob):
    def __init__(self, name, label="", script=""):
        Knob.__init__(self, name, label)
        self.script = script

    def execute(self):
        if self.node is None or self.node.Class() != "BlinkScript":
            return
        if self._name == "recompile":
            self.node._trigger("recompile")
        elif self._name == "reloadKernelSourceFile":
            with open(self.node["kernelSourceFile"].value()) as f:
                self.node["kernelSource"].setValue(f.read())
            self.node._trigger("file")


class File_Knob(String_Knob):
    pass


class Link_Knob(Knob):
    link = ""

    def setLink(self, target):
        self.link = target

    def getLink(self):
        return self.link


# ---------------------------------------------------------------------- nodes

MERGE_OPS = ["over", "plus", "screen", "max", "multiply"]


def _enum(items):
    return lambda n: Enumeration_Knob(n, "", items)


AXIS_KNOBS = {"translate": XYZ_Knob, "rotate": XYZ_Knob, "world_matrix": Matrix_Knob}

CLASS_KNOBS = {
    "Group": {"tile_color": Int_Knob, "knobChanged": String_Knob},
    "Dot": {},
    "NoOp": {},
    "Constant": {"color": AColor_Knob},
    "Expression": dict(("expr%d" % i, String_Knob) for i in range(4)),
    "Axis2": AXIS_KNOBS,
    "Camera2": dict(AXIS_KNOBS, focal=Double_Knob, haperture=Double_Knob, winroll=Double_Knob,
                    win_translate=XY_Knob, win_scale=XY_Knob),
    "TimeBlur": {"divisions": Int_Knob, "shutter": Double_Knob,
                 "shutteroffset": _enum(["centred", "start", "end", "custom"])},
    "Copy": dict(("%s%d" % (d, i), String_Knob) for d in ("from", "to") for i in range(4)),
    "Input": {"number": Int_Knob},
    "Output": {},
    "Multiply": {"channels": String_Knob, "value": Double_Knob},
    "AddChannels": {"channels": String_Knob, "channels2": String_Knob},
    "Crop": {"box": BBox_Knob, "reformat": Boolean_Knob, "crop": Boolean_Knob},
    "Reformat": {
        "type": lambda n: Enumeration_Knob(n, "", ["to format", "to box", "scale"]),
        "box_fixed": Boolean_Knob, "box_width": Double_Knob, "box_height": Double_Knob,
        "resize": lambda n: Enumeration_Knob(n, "", ["none", "width", "height", "fit", "fill", "distort"]),
        "center": Boolean_Knob,
    },
    "BlinkScript": {
        "kernelSource": String_Knob,
        "kernelSourceFile": String_Knob,
        "reloadKernelSourceFile": PyScript_Knob,
        "recompile": PyScript_Knob,
        "useGPUIfAvailable": Boolean_Knob,
        "vectorize": Boolean_Knob,
    },
    "Merge2": {"operation": lambda n: Enumeration_Knob(n, "", MERGE_OPS),
               "output": String_Knob, "mix": Double_Knob, "invert_mask": Boolean_Knob,
               "maskChannelMask": String_Knob},
    "Switch": {"which": Double_Knob},
    "Transform": {"scale": Double_Knob, "center": XY_Knob,
                  "filter": _enum(["Impulse", "Cubic", "Keys", "Simon", "Rifman"])},
    "Root": {},
}
MAX_INPUTS = {"Input": 0, "Output": 1, "Multiply": 1, "AddChannels": 1, "Crop": 1,
              "Reformat": 1, "BlinkScript": 1, "Merge2": 10, "Switch": 10, "Root": 0,
              "Dot": 1, "NoOp": 1, "Axis2": 1, "Camera2": 1, "TimeBlur": 1, "Copy": 2,
              "Constant": 0, "Expression": 1, "Transform": 1}
NO_DISABLE = ("Root", "Input", "Output", "Group")

_root = None
_context = []
_selected = []
_this = None
_this_knob = None
_layers = ["rgba", "depth"]
inputs_queue = []
messages = []
# Which output channel each Expression node exprN writes (Nuke's defaults:
# expr0 red ... expr3 alpha); tests change it to simulate other setups.
EXPRESSION_CHANNELS = [0, 1, 2, 3]
# When true, removeKnob wipes the knob's values, animation and link.
REMOVE_KNOB_RESETS = [False]
RGBA_NAMES = ("red", "green", "blue", "alpha")


def _knob_number(knob, channel=None):
    """A knob's value as a Nuke expression sees it."""
    if isinstance(knob, Enumeration_Knob):
        return knob.getValue()
    if channel is not None:
        return float(knob.value("rgb".index(channel)))
    return float(knob.value())


class Node(object):
    def __init__(self, cls, parent):
        if cls not in CLASS_KNOBS:
            raise RuntimeError("fake nuke has no node class %r" % cls)
        self._class = cls
        self._parent = parent
        self._knobs = {}
        self._inputs = {}
        self._children = []
        self._xy = (0, 0)
        self._selected = False
        self._name = None
        self._max_inputs = MAX_INPUTS.get(cls, 0)
        self._format = None
        for kname, factory in CLASS_KNOBS[cls].items():
            self._add(factory(kname))
        if cls not in NO_DISABLE:
            self._add(Boolean_Knob("disable"))
        if cls == "BlinkScript":
            self["useGPUIfAvailable"].setValue(True)  # Nuke's default
        if parent is not None:
            if cls == "Input":  # Nuke numbers a group's inputs in creation order
                self["number"].setValue(len([c for c in parent._children if c._class == "Input"]))
            parent._children.append(self)
            self.setName(cls + "1")

    def _add(self, knob):
        knob.node = self
        self._knobs[knob.name()] = knob

    # knobs
    def knob(self, name):
        return self._knobs.get(name)

    def __getitem__(self, name):
        if name not in self._knobs:
            raise NameError("%s has no knob %r" % (self._name, name))
        return self._knobs[name]

    def allKnobs(self):
        """Knobs in panel order (built-in ones first, then user knobs)."""
        user = [self._knobs[n] for n in self.user_knob_order]
        return [k for k in self._knobs.values() if k not in user] + user

    def knobs(self):
        if getattr(self, "_pending", 0):
            self._pending -= 1  # a deferred compile progresses as Nuke runs
            if self._pending == 0:
                self._compile()
        return dict(self._knobs)

    def addKnob(self, knob):
        if knob.name() in self._knobs:
            raise ValueError("duplicate knob %r" % knob.name())
        self._add(knob)
        self.user_knob_order.append(knob.name())

    # pixels (just enough to read back BlinkFlare's element table)
    def sample(self, channel, x, y, dx=1.0, dy=1.0, frame=None):
        return self._pixel(int(math.floor(x)), int(math.floor(y)))[
            RGBA_NAMES.index(channel.split(".")[-1])]

    def _pixel(self, px, py):
        if self._class == "Constant":
            return [float(v) for v in self["color"].values]
        if self._class == "Crop":
            b = self["box"].values
            if b[0] <= px < b[2] and b[1] <= py < b[3]:
                return self.input(0)._pixel(px, py)
            return [0.0] * 4
        if self._class == "Transform":
            assert self["filter"].value() == "Impulse"
            s = self["scale"].value()
            cx, cy = self["center"].values
            return self.input(0)._pixel(int(math.floor((px + 0.5 - cx) / s + cx)),
                                        int(math.floor((py + 0.5 - cy) / s + cy)))
        if self._class == "Expression":
            prev = self.input(0)._pixel(px, py)
            env = {"x": float(px), "y": float(py)}
            env.update(zip("rgba", prev))

            def resolve(path):
                if path in env:
                    return env[path]
                parts = path.split(".")
                target = self._parent if parts[0] == "parent" else self._parent.node(parts[0])
                return _knob_number(target[parts[1]], parts[2] if len(parts) > 2 else None)
            out = list(prev)
            for i in range(4):
                out[EXPRESSION_CHANNELS[i]] = nuke_expr.evaluate(self["expr%d" % i].value(), resolve)
            return out
        raise RuntimeError("fake nuke can't compute pixels of a %s" % self._class)

    def removeKnob(self, knob):
        name = knob.name()
        if self._knobs.get(name) is not knob:
            raise ValueError("knob %r is not on %s" % (name, self._name))
        del self._knobs[name]
        self.user_knob_order.remove(name)
        if REMOVE_KNOB_RESETS[0]:  # simulate a Nuke that drops a removed knob's state
            knob.values = [knob.default] * knob.channels
            knob.keys, knob.expressions, knob.animated = {}, {}, False
            knob.link = None

    @property
    def user_knob_order(self):
        if not hasattr(self, "_user_order"):
            self._user_order = []
        return self._user_order

    # graph
    def Class(self):
        return self._class

    def name(self):
        return self._name

    def fullName(self):
        parts = []
        node = self
        while node is not None and node._parent is not None:
            parts.append(node._name)
            node = node._parent
        return ".".join(reversed(parts))

    def node(self, name):
        return self.child(name)

    def setName(self, name, uncollide=True):
        siblings = set(n._name for n in self._parent._children if n is not self)
        if name in siblings:
            m = re.match(r"(.*?)(\d*)$", name)
            base, num = m.group(1), int(m.group(2) or 0)
            while "%s%d" % (base, num) in siblings:
                num += 1
            name = "%s%d" % (base, num)
        self._name = name

    def maxInputs(self):
        if self._class == "Group":
            return len([c for c in self._children if c._class == "Input"])
        return self._max_inputs

    def setInput(self, i, node):
        if node is None:
            self._inputs.pop(i, None)
            return True
        if i >= self.maxInputs():
            raise IndexError("%s has only %d inputs" % (self._name, self.maxInputs()))
        self._inputs[i] = node
        return True

    def input(self, i):
        return self._inputs.get(i)

    def setXYpos(self, x, y):
        self._xy = (x, y)

    def xpos(self):
        return self._xy[0]

    def ypos(self):
        return self._xy[1]

    def format(self):
        return self._format or root().format()

    def width(self):
        return self.format().width()

    def height(self):
        return self.format().height()

    def setSelected(self, state):
        self._selected = state
        if state and self not in _selected:
            _selected.append(self)
        if not state and self in _selected:
            _selected.remove(self)

    def showControlPanel(self):
        pass

    def children(self):
        return list(self._children)

    def nodes(self):
        return list(self._children)

    def child(self, name):
        for c in self._children:
            if c._name == name:
                return c
        return None

    def __enter__(self):
        _context.append(self)
        return self

    def __exit__(self, *exc):
        _context.pop()

    # BlinkScript
    def forceValidate(self):
        if self._class == "BlinkScript":
            self._trigger("validate")

    def hasError(self):
        return getattr(self, "_error", False)

    def _trigger(self, kind):
        if kind in BLINK_COMPILE_ON:
            if BLINK_ASYNC_POLLS:
                self._pending = BLINK_ASYNC_POLLS
            else:
                self._compile()

    def _compile(self):
        compile_count[0] += 1
        gpu = self.knob("useGPUIfAvailable")
        compile_gpu.append(bool(gpu.value()) if gpu is not None else None)
        for name in getattr(self, "_param_knobs", []):
            self._knobs.pop(name, None)
        self._param_knobs = []
        src = self["kernelSource"].value()
        self._error = any(bad in src for bad in BLINK_REJECT)
        if self._error:
            return  # like Nuke: no exception, the node is just in error
        kernel = re.search(r"kernel\s+(\w+)\s*:", src).group(1)
        prefix = kernel + "_" if BLINK_PREFIX is None else BLINK_PREFIX
        types = dict((name, t) for t, name in re.findall(
            r"^\s*(float[234]?|int|bool)\s+(\w+);", src.split("param:")[1].split("local:")[0], re.M))
        widths = {"float": 1, "float2": 2, "float3": 3, "float4": 4, "int": 1, "bool": 1}
        for var, label in re.findall(r'defineParam\((\w+),\s*"(\w+)"', src):
            knob = Knob(prefix + label)
            knob.channels = widths[types[var]]
            knob.values = [0.0] * knob.channels
            self._add(knob)
            self._param_knobs.append(knob.name())
        self._max_inputs = len(re.findall(r"Image<eRead", src))


def _current():
    return _context[-1] if _context else root()


def root():
    global _root
    if _root is None:
        _root = Node("Root", None)
        _root._name = "root"
        _root._format = Format(1920, 1080)
    return _root


def reset(fmt=None):
    global _root, _this, _this_knob, BLINK_COMPILE_ON, BLINK_PREFIX, BLINK_ASYNC_POLLS
    BLINK_COMPILE_ON = {"recompile"}
    BLINK_PREFIX = None
    BLINK_ASYNC_POLLS = 0
    compile_count[0] = 0
    del compile_gpu[:]
    del BLINK_REJECT[:]
    env.clear()
    env["nukex"] = True
    del filenames_queue[:]
    del asks_queue[:]
    _menus.clear()
    _root = None
    _this = None
    _this_knob = None
    del _context[:]
    del _selected[:]
    del inputs_queue[:]
    del messages[:]
    EXPRESSION_CHANNELS[:] = [0, 1, 2, 3]
    REMOVE_KNOB_RESETS[0] = False
    Undo._disabled[0] = False
    _layers[:] = ["rgba", "depth"]
    root()._format = fmt or Format(1920, 1080)
    root()._add(Int_Knob("first_frame"))
    root()._add(Int_Knob("last_frame"))
    root()["first_frame"].setValue(1001)
    root()["last_frame"].setValue(1010)


def run_script(script, node, knob=None):
    """Execute a knob script the way Nuke runs callbacks and buttons."""
    global _this, _this_knob
    _this, _this_knob = node, knob
    try:
        exec(script, {"nuke": __import__(__name__)})
    finally:
        _this, _this_knob = None, None


def thisKnob():
    return _this_knob


def layers():
    return list(_layers)


def Layer(name, channels):
    if name in _layers:
        raise ValueError("layer %s exists" % name)
    _layers.append(name)


def getInput(prompt, default=""):
    return inputs_queue.pop(0) if inputs_queue else None


filenames_queue = []
asks_queue = []


def getFilename(message, pattern=None, default=None):
    return filenames_queue.pop(0) if filenames_queue else None


def ask(question):
    return asks_queue.pop(0) if asks_queue else True


class ProgressTask(object):
    def __init__(self, title):
        self.messages = []

    def setMessage(self, text):
        self.messages.append(text)

    def setProgress(self, pct):
        pass

    def isCancelled(self):
        return False


class _Menu(object):
    def __init__(self):
        self.items = {}

    def findItem(self, path):
        node = self
        for part in path.split("/"):
            node = node.items.get(part) if isinstance(node, _Menu) else None
            if node is None:
                return None
        return node

    def addMenu(self, name, **kw):
        return self.items.setdefault(name, _Menu())

    def addCommand(self, name, command="", *args, **kw):
        self.items[name] = command
        return command


_menus = {}


def menu(name):
    return _menus.setdefault(name, _Menu())


def pluginPath():
    return []


class _Nodes(object):
    def __getattr__(self, cls):
        def make(**kw):
            node = Node(cls, _current())
            inputs = kw.pop("inputs", None)
            name = kw.pop("name", None)
            if name:
                node.setName(name)
            for k, v in kw.items():
                node[k].setValue(v)
            for i, n in enumerate(inputs or []):
                node.setInput(i, n)
            return node
        return make


nodes = _Nodes()


def selectedNode():
    if not _selected:
        raise ValueError("no node selected")
    return _selected[-1]


def selectedNodes():
    return list(_selected)


def delete(node):
    if node not in node._parent._children:
        raise ValueError("PythonObject not attached to a node")
    node._parent._children.remove(node)
    node.setSelected(False)
    for child in list(node._children):
        child.setSelected(False)


def toNode(name):
    parts = name.split(".")
    node = root() if parts[0] == "root" else _current()
    for part in parts[1:] if parts[0] == "root" else parts:
        node = node.child(part) if node is not None else None
    return node


def thisNode():
    return _this


def message(text):
    messages.append(text)


def nodeCopy(path):
    """Serialize the selected nodes, roughly like a .nk snippet."""
    data = []
    for n in _selected:
        knobs = {}
        for name, k in n._knobs.items():
            knobs[name] = {"values": k.values, "channels": k.channels,
                           "expressions": dict((str(c), e) for c, e in k.expressions.items())}
        data.append({"class": n._class, "name": n._name, "knobs": knobs,
                     "params": getattr(n, "_param_knobs", [])})
    with open(path, "w") as f:
        json.dump(data, f)


def nodePaste(path):
    """Recreate copied nodes in the current context and select them.
    BlinkScript param knobs come back as Nuke restores them from the saved
    kernel description: without a recompile."""
    with open(path) as f:
        data = json.load(f)
    for n in list(_selected):
        n.setSelected(False)
    for entry in data:
        node = Node(entry["class"], _current())
        node.setName(entry["name"])
        for name, saved in entry["knobs"].items():
            knob = node.knob(name)
            if knob is None:
                knob = Knob(name)
                knob.channels = saved["channels"]
                node._add(knob)
            knob.values = list(saved["values"])
            knob.expressions = dict((int(c), e) for c, e in saved["expressions"].items())
        if entry["params"]:
            node._param_knobs = list(entry["params"])
            node._max_inputs = len(re.findall(r"Image<eRead", node["kernelSource"].value()))
        node.setSelected(True)


class Undo(object):
    log = []
    _disabled = [False]

    def disable(self):
        assert not Undo._disabled[0], "nested Undo.disable"
        Undo._disabled[0] = True
        Undo.log.append(("disable", ""))

    def enable(self):
        Undo._disabled[0] = False
        Undo.log.append(("enable", ""))

    def disabled(self):
        return Undo._disabled[0]

    def begin(self, name=""):
        Undo.log.append(("begin", name))

    def end(self):
        Undo.log.append(("end", ""))
