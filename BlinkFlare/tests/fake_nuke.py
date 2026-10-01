"""Minimal stand-in for the ``nuke`` module, enough to run blinkflare.builder.

It models only what the builder touches, but strictly: unknown knobs raise,
expressions on out-of-range channels raise, and the fake BlinkScript node
creates its param knobs by parsing the kernel source, like Nuke does on
compile.
"""
import re

STARTLINE = 0x1000
GUI = False


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

    def setExpression(self, expr, channel=-1):
        if channel == -1:
            for c in range(self.channels):
                self.expressions[c] = expr
        else:
            self._check(channel)
            self.expressions[channel] = expr
        return True

    def setTooltip(self, text):
        self.tooltip = text

    def setFlag(self, flag):
        self.flags.add(flag)

    def clearFlag(self, flag):
        self.flags.discard(flag)

    def setRange(self, lo, hi):
        self.range = (lo, hi)

    def isAnimated(self):
        return self.animated

    def clearAnimated(self):
        self.animated = False


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


class BBox_Knob(Knob):
    channels = 4


class String_Knob(Knob):
    default = ""


class Tab_Knob(Knob):
    pass


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


class PyScript_Knob(Knob):
    def __init__(self, name, label="", script=""):
        Knob.__init__(self, name, label)
        self.script = script

    def execute(self):
        if self.node is not None and self.node.Class() == "BlinkScript" and self._name == "recompile":
            self.node._compile()


class Link_Knob(Knob):
    def setLink(self, target):
        self.link = target


# ---------------------------------------------------------------------- nodes

MERGE_OPS = ["over", "plus", "screen", "max", "multiply"]

CLASS_KNOBS = {
    "Group": {"tile_color": Int_Knob},
    "Input": {"number": Int_Knob},
    "Output": {},
    "Multiply": {"channels": String_Knob, "value": Double_Knob},
    "AddChannels": {"channels": String_Knob},
    "Crop": {"box": BBox_Knob, "reformat": Boolean_Knob, "crop": Boolean_Knob},
    "Reformat": {
        "type": lambda n: Enumeration_Knob(n, "", ["to format", "to box", "scale"]),
        "box_fixed": Boolean_Knob, "box_width": Double_Knob, "box_height": Double_Knob,
        "resize": lambda n: Enumeration_Knob(n, "", ["none", "width", "height", "fit", "fill", "distort"]),
        "center": Boolean_Knob,
    },
    "BlinkScript": {
        "kernelSource": String_Knob,
        "recompile": PyScript_Knob,
        "useGPUIfAvailable": Boolean_Knob,
        "vectorize": Boolean_Knob,
    },
    "Merge2": {"operation": lambda n: Enumeration_Knob(n, "", MERGE_OPS),
               "output": String_Knob, "mix": Double_Knob},
    "Switch": {"which": Double_Knob},
    "Root": {},
}
MAX_INPUTS = {"Input": 0, "Output": 1, "Multiply": 1, "AddChannels": 1, "Crop": 1,
              "Reformat": 1, "BlinkScript": 1, "Merge2": 10, "Switch": 10, "Root": 0}

_root = None
_context = []
_selected = []
_this = None


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
        if parent is not None:
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

    def knobs(self):
        return dict(self._knobs)

    def addKnob(self, knob):
        if knob.name() in self._knobs:
            raise ValueError("duplicate knob %r" % knob.name())
        self._add(knob)
        self.user_knob_order.append(knob.name())

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
    def _compile(self):
        src = self["kernelSource"].value()
        kernel = re.search(r"kernel\s+(\w+)\s*:", src).group(1)
        types = dict((name, t) for t, name in re.findall(
            r"^\s*(float[234]?|int|bool)\s+(\w+);", src.split("param:")[1].split("local:")[0], re.M))
        widths = {"float": 1, "float2": 2, "float3": 3, "float4": 4, "int": 1, "bool": 1}
        for var, label in re.findall(r'defineParam\((\w+),\s*"(\w+)"', src):
            knob = Knob(kernel + "_" + label)
            knob.channels = widths[types[var]]
            knob.values = [0.0] * knob.channels
            self._add(knob)
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
    global _root, _this
    _root = None
    _this = None
    del _context[:]
    del _selected[:]
    root()._format = fmt or Format(1920, 1080)


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
    node._parent._children.remove(node)
    node.setSelected(False)


def toNode(name):
    return _current().child(name)


def thisNode():
    return _this


def message(text):
    pass


def nodeCopy(path):
    with open(path, "w") as f:
        f.write("# fake toolset: %s\n" % ", ".join(n.name() for n in _selected))


class Undo(object):
    log = []

    def begin(self, name=""):
        Undo.log.append(("begin", name))

    def end(self):
        Undo.log.append(("end", ""))
