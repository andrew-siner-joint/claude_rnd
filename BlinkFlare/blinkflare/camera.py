"""3D placement: projecting a light Axis through a Camera.

Inside the group, two Axis nodes pass the world transforms of whatever is
plugged into the ``cam`` and ``axis`` inputs through (``CameraXform`` and
``LightXform``; Dots and parenting are handled by Nuke). A NoOp called
``Projection`` turns them into a screen position using plain Nuke expressions,
so the projection evaluates per frame and per motion-blur sub-frame on any
machine, render farm included, without this package installed.

The camera's lens knobs (focal, aperture, window translate/scale/roll) can't
be passed through a node, so the group's knobChanged callback (``LINK_SCRIPT``,
stored on the node itself) links them by expression whenever the ``cam`` input
changes.

``project()`` is the same math in Python. It's the reference the tests check
the generated expressions against, and what the preview renderer uses.
"""

import math

PROJECTION_NODE = "Projection"
CAMERA_XFORM = "CameraXform"
LIGHT_XFORM = "LightXform"

CAMERA_INPUT = 2
AXIS_INPUT = 3
MASK_INPUT = 4

# (Projection knob, camera knob, channel, value when no camera is connected)
LENS_KNOBS = [
    ("focal", "focal", None, 50.0),
    ("haperture", "haperture", None, 24.576),
    ("win_tx", "win_translate", 0, 0.0),
    ("win_ty", "win_translate", 1, 0.0),
    ("win_sx", "win_scale", 0, 1.0),
    ("win_sy", "win_scale", 1, 1.0),
    ("winroll", "winroll", None, 0.0),
]

NEAR = 0.0001  # camera-space depth below which the light counts as behind


def _m(i):
    return "%s.world_matrix.%d" % (CAMERA_XFORM, i)


def _window_to_pixel(u, v):
    """Expressions mapping normalized screen coords (u spans -1..1 across
    the width) through the camera window and into pixels."""
    u1 = "((%s) - win_tx) / win_sx" % u
    v1 = "((%s) - win_ty) / win_sy" % v
    r = "radians(winroll)"
    u2 = "%s * cos(%s) - %s * sin(%s)" % (u1, r, v1, r)
    v2 = "%s * sin(%s) + %s * cos(%s)" % (u1, r, v1, r)
    x = "(%s + 1) * input.width / 2" % u2
    y = "input.height / 2 + (%s) * input.width * parent.pixel_aspect / 2" % v2
    return [x, y]


def _camera_space(col):
    a, b, c = (_m(i) for i in col)
    return "(%s * delta.x + %s * delta.y + %s * delta.z) / (%s * %s + %s * %s + %s * %s)" % (
        a, b, c, a, a, b, b, c, c)


# Knobs on the Projection NoOp, in evaluation order: (name, kind, expressions).
# "lens" knobs hold plain values until a camera is linked.
PROJECTION_KNOBS = (
    [(name, "lens", None) for name, _, _, _ in LENS_KNOBS] + [
        ("cam_pos", "xyz", [_m(3), _m(7), _m(11)]),
        ("light_world", "xyz", ["%s.world_matrix.%d" % (LIGHT_XFORM, i) for i in (3, 7, 11)]),
        ("delta", "xyz", ["light_world.%s - cam_pos.%s" % (c, c) for c in "xyz"]),
        # The camera's rotation columns are its local axes. Dividing by their
        # squared length makes this exactly inverse(world_matrix) * point,
        # including scale from parents, without a general 4x4 inverse (exact
        # while the axes stay perpendicular, i.e. no shear).
        ("light_cam", "xyz", [_camera_space(col) for col in ((0, 4, 8), (1, 5, 9), (2, 6, 10))]),
        ("ndc", "xy", ["light_cam.%s * 2 * focal / haperture / max(-light_cam.z, %g)" % (c, NEAR)
                       for c in "xy"]),
        ("screen", "xy", _window_to_pixel("ndc.x", "ndc.y")),
        ("lens_center", "xy", _window_to_pixel("0", "0")),
        ("visible", "double", ["clamp(-light_cam.z / %g - 1, 0, 1)" % NEAR]),
        ("depth", "double", ["-light_cam.z"]),
    ])


# Self-contained so it keeps working on machines without this package: it is
# stored in the group's knobChanged knob and in the Refresh button.
LINK_FUNCTION = '''
def _blinkflare_links(n):
    def upstream(index):
        node = n.input(index)
        while node is not None and node.Class() == "Dot":
            node = node.input(0)
        return node
    proj = n.node("%(proj)s")
    cam = upstream(%(cam)d)
    is_cam = cam is not None and cam.knob("focal") is not None and cam.knob("haperture") is not None
    # Only touch what differs, so opening the panel doesn't dirty the script.
    for name, src, channel, default in %(lens)r:
        knob = proj[name]
        curve = knob.animation(0)
        if is_cam:
            ref = "root." + cam.fullName() + "." + src
            if channel is not None:
                ref += "." + str(channel)
            if curve is None or curve.expression() != ref:
                knob.setExpression(ref)
        else:
            if knob.isAnimated() or knob.hasExpression():
                knob.clearAnimated()
            if knob.value() != default:
                knob.setValue(default)
    merge = n.node("%(merge)s")
    mask = n.node("mask") if n.input(%(mask)d) is not None else None
    if merge.input(2) is not mask:
        merge.setInput(2, mask)
'''

KNOB_CHANGED_SCRIPT = LINK_FUNCTION + '''
_k = nuke.thisKnob()
if _k is not None and _k.name() in ("inputChange", "showPanel"):
    _blinkflare_links(nuke.thisNode())
    try:
        import blinkflare
        blinkflare.refresh_panel(nuke.thisNode())
    except ImportError:
        pass
elif _k is not None and _k.name()[:1] == "e" and _k.name()[1:].split("_")[0].isdigit():
    try:
        import blinkflare
        blinkflare.element_knob_changed(nuke.thisNode(), _k)
    except ImportError:
        pass
elif _k is not None and _k.name() == "element_layers" and _k.value():
    try:
        import blinkflare
        blinkflare.build_element_layers(nuke.thisNode())
    except ImportError:
        nuke.message("Element Layers needs the BlinkFlare package to build its extra kernels.")
'''

REFRESH_SCRIPT = LINK_FUNCTION + "\n_blinkflare_links(nuke.thisNode())\n"


def _script_values(merge_node):
    return {"proj": PROJECTION_NODE, "cam": CAMERA_INPUT, "mask": MASK_INPUT,
            "lens": LENS_KNOBS, "merge": merge_node}


def link_scripts(merge_node):
    values = _script_values(merge_node)
    return KNOB_CHANGED_SCRIPT % values, REFRESH_SCRIPT % values


def link_now(node, merge_node, nuke_module):
    """Run the stored link function on ``node`` straight away (camera lens
    knobs and mask), as its knobChanged would on an input change."""
    namespace = {"nuke": nuke_module}
    exec(LINK_FUNCTION % _script_values(merge_node), namespace)
    namespace["_blinkflare_links"](node)


# ----------------------------------------------------------------- reference

def project(world_matrix, light_world, width, height, pixel_aspect=1.0, focal=50.0,
            haperture=24.576, win_translate=(0.0, 0.0), win_scale=(1.0, 1.0), winroll=0.0):
    """Python version of the Projection expressions.

    ``world_matrix`` is the camera's 4x4 world matrix as 16 floats, row-major
    with translation in elements 3, 7 and 11 (Nuke's world_matrix layout).
    Returns a dict with screen, lens_center, visible and depth.
    """
    m = world_matrix
    delta = [light_world[i] - m[3 + 4 * i] for i in range(3)]

    def axis(col):
        a, b, c = (m[i] for i in col)
        return (a * delta[0] + b * delta[1] + c * delta[2]) / (a * a + b * b + c * c)

    cx, cy, cz = axis((0, 4, 8)), axis((1, 5, 9)), axis((2, 6, 10))
    k = 2.0 * focal / haperture / max(-cz, NEAR)

    def to_pixel(u, v):
        u1 = (u - win_translate[0]) / win_scale[0]
        v1 = (v - win_translate[1]) / win_scale[1]
        r = math.radians(winroll)
        u2 = u1 * math.cos(r) - v1 * math.sin(r)
        v2 = u1 * math.sin(r) + v1 * math.cos(r)
        return ((u2 + 1.0) * width / 2.0, height / 2.0 + v2 * width * pixel_aspect / 2.0)

    return {
        "screen": to_pixel(cx * k, cy * k),
        "lens_center": to_pixel(0.0, 0.0),
        "visible": min(max(-cz / NEAR - 1.0, 0.0), 1.0),
        "depth": -cz,
    }


def matrix_trs(translate=(0.0, 0.0, 0.0), rotate=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0)):
    """Row-major world matrix from translate, rotate (degrees, applied X then
    Y then Z) and scale. Used to build test cameras and lights."""
    rx, ry, rz = (math.radians(a) for a in rotate)

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]

    Rx = [[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]]
    Ry = [[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]]
    Rz = [[math.cos(rz), -math.sin(rz), 0], [math.sin(rz), math.cos(rz), 0], [0, 0, 1]]
    R = mul(Rz, mul(Ry, Rx))
    out = []
    for i in range(3):
        out += [R[i][j] * scale[j] for j in range(3)] + [translate[i]]
    return out + [0.0, 0.0, 0.0, 1.0]
