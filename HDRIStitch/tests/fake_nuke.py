"""Minimal stand-in for the ``nuke`` module, enough to run hdristitch_nuke.

Strict about what it models: unknown knobs return None (like Nuke), menu
knobs reject values they don't list, and node inputs are recorded so tests
can check the wiring.
"""

created = []


class Format(object):
    def __init__(self, w, h):
        self._w, self._h = w, h

    def width(self):
        return self._w

    def height(self):
        return self._h


class Knob(object):
    klass = "Knob"

    def __init__(self, name, label="", value=0.0):
        self._name = name
        self.label = label
        self._value = value
        self.expressions = {}

    def name(self):
        return self._name

    def Class(self):
        return self.klass

    def setValue(self, value, channel=None):
        if channel is None:
            self._value = value
        else:
            vals = list(self._value)
            vals[channel] = value
            self._value = vals
        return True

    def value(self):
        return self._value

    def getValue(self):
        return self._value

    def setExpression(self, expr, channel=-1):
        self.expressions[channel] = expr
        return True


class Enumeration_Knob(Knob):
    klass = "Enumeration_Knob"

    def __init__(self, name, values, value=None):
        Knob.__init__(self, name, value=value or values[0])
        self._values = list(values)

    def values(self):
        return list(self._values)

    def setValue(self, value, channel=None):
        if value not in self._values:
            raise ValueError("%s: %r not in %s" % (self._name, value, self._values))
        self._value = value
        return True


class Format_Knob(Knob):
    klass = "Format_Knob"


class Color_Knob(Knob):
    klass = "Color_Knob"

    def __init__(self, name, label=""):
        Knob.__init__(self, name, label, [0.0, 0.0, 0.0])


class Double_Knob(Knob):
    klass = "Double_Knob"


class Boolean_Knob(Knob):
    klass = "Boolean_Knob"


LATLONG_TYPES = ["Angular Map 360", "Cube", "Lat Long map", "Mirror Ball", "180 Fisheye"]
NODE_KNOBS = {
    "SphericalTransform": lambda: [Enumeration_Knob("input", LATLONG_TYPES, "Lat Long map"),
                                   Enumeration_Knob("output", LATLONG_TYPES, "Cube"),
                                   Format_Knob("format"), Knob("rx"), Knob("ry"), Knob("rz"),
                                   Knob("out_rx"), Knob("out_ry"), Knob("out_rz")],
    "Read": lambda: [Knob("file", value=""), Knob("raw", value=False)],
    "Write": lambda: [Knob("file", value=""), Knob("file_type", value=""), Knob("raw", value=False),
                      Enumeration_Knob("datatype", ["16 bit half", "32 bit float"]),
                      Enumeration_Knob("compression", ["none", "Zip (1 scanline)",
                                                       "Zip (16 scanlines)", "PIZ Wavelet (32 scanlines)"]),
                      Knob("channels", value="rgba"),
                      Enumeration_Knob("metadata", ["default metadata", "default metadata and exr/*",
                                                    "no metadata", "all metadata except input/*",
                                                    "all metadata"])],
    "Rectangle": lambda: [Knob("area", value=[0, 0, 1, 1]), Knob("softness"),
                          Knob("output", value="rgba")],
    "Keymix": lambda: [Knob("channels", value="all")],
    "RotoPaint": lambda: [],
    "Multiply": lambda: [Knob("value", value=[1.0, 1.0, 1.0, 1.0])],
    "Group": lambda: [],
    "Input": lambda: [],
    "Output": lambda: [],
    "StickyNote": lambda: [],
}


class Node(object):
    def __init__(self, klass, **kwargs):
        self.klass = klass
        self._knobs = {}
        for k in NODE_KNOBS[klass]() + [Knob("name", value=kwargs.get("name", klass + "1")),
                                         Knob("label", value=""), Knob("disable", value=False)]:
            self._knobs[k.name()] = k
        self.inputs = {}
        self._format = Format(4096, 2048)
        self.children = []
        for key, value in kwargs.items():
            if key in self._knobs:
                self._knobs[key].setValue(value)
        self._xy = (0, 0)
        created.append(self)

    def knob(self, name):
        return self._knobs.get(name)

    def __getitem__(self, name):
        return self._knobs[name]

    def allKnobs(self):
        return list(self._knobs.values())

    def addKnob(self, knob):
        self._knobs[knob.name()] = knob

    def name(self):
        return self._knobs["name"].value()

    def Class(self):
        return self.klass

    def setInput(self, i, node):
        self.inputs[i] = node
        return True

    def input(self, i):
        return self.inputs.get(i)

    def format(self):
        return self._format

    def begin(self):
        _stack.append(self)

    def end(self):
        _stack.pop()

    def xpos(self):
        return self._xy[0]

    def ypos(self):
        return self._xy[1]

    def setXYpos(self, x, y):
        self._xy = (x, y)


_stack = []


class _Nodes(object):
    def __getattr__(self, klass):
        if klass not in NODE_KNOBS:
            raise AttributeError("fake nuke has no node class %s" % klass)

        def make(**kwargs):
            node = Node(klass, **kwargs)
            if _stack:
                _stack[-1].children.append(node)
            return node
        return make


nodes = _Nodes()
selection = []
messages = []
filename = [None]


def selectedNode():
    if not selection:
        raise ValueError("no node selected")
    return selection[-1]


def getFilename(title, pattern=None):
    return filename[0]


def tprint(msg):
    messages.append(msg)


def reset():
    del created[:]
    del selection[:]
    del messages[:]
    filename[0] = None
