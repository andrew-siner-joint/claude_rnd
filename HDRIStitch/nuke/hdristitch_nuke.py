"""HDRIStitch clean-up tools for Nuke (any edition; no NukeX needed).

* pole_patch: the nadir (or zenith) is a smeared line along the bottom (top)
  of a lat-long, impossible to paint. This rolls the sphere 90 degrees so
  both poles sit on the horizon, a quarter of the way in from the left and
  right edges, where they look like normal photos; you paint there, and it
  rolls back. Only a soft band around the pole is replaced, so the rest of
  the HDRI never gets resampled.
* orient: SphericalTransform for levelling the horizon / turning the HDRI.
* gray_card: scales (and optionally neutralises) the HDRI so a sampled gray
  card reads its true value.
* write: EXR Write set up to pass scene-linear data through untouched.

Pixel values go through Read/Write as raw data: no colour conversion.
"""
import os

import nuke

TAG = "HDRIStitch"
BAND = 0.2  # fraction of the image height replaced around a pole


# ------------------------------------------------------------------ helpers

def _knob(node, *names):
    for name in names:
        knob = node.knob(name)
        if knob is not None:
            return knob
    return None


def _set(node, names, value, required=True):
    knob = _knob(node, *names)
    if knob is None:
        if required:
            nuke.tprint("%s: %s has none of the knobs %s; set it by hand"
                        % (TAG, node.name(), ", ".join(names)))
        return False
    knob.setValue(value)
    return True


def _set_enum(node, names, *needles):
    """Set a menu knob to the first entry containing all `needles`."""
    knob = _knob(node, *names)
    if knob is None:
        nuke.tprint("%s: %s has none of the knobs %s" % (TAG, node.name(), ", ".join(names)))
        return False
    for value in knob.values():
        low = value.lower()
        if all(n in low for n in needles):
            knob.setValue(value)
            return True
    nuke.tprint("%s: %s.%s has no entry matching %s (has %s)"
                % (TAG, node.name(), knob.name(), needles, knob.values()))
    return False


def _match_format(node, fmt):
    """Point every format knob of `node` at `fmt` (SphericalTransform would
    otherwise render at the project format, e.g. HD)."""
    done = False
    for knob in node.allKnobs():
        if knob.Class() == "Format_Knob":
            knob.setValue(fmt)
            done = True
    return done


def _latlong_transform(src, name, rx=0.0, ry=0.0, rz=0.0):
    node = nuke.nodes.SphericalTransform(name=name)
    node.setInput(0, src)
    _set_enum(node, ["input", "in_type", "input_type"], "lat")
    _set_enum(node, ["output", "out_type", "output_type"], "lat")
    _match_format(node, src.format())
    for knob_names, value in ((["rx", "in_rx"], rx), (["ry", "in_ry"], ry),
                              (["rz", "in_rz"], rz)):
        if value:
            _set(node, knob_names, value)
    return node


def _selected_read():
    try:
        node = nuke.selectedNode()
    except ValueError:
        return None
    return node


def _new_read(path=None):
    path = path or nuke.getFilename("HDRIStitch EXR", "*.exr")
    if not path:
        return None
    read = nuke.nodes.Read(file=path)
    _set(read, ["raw"], True, required=False)
    return read


def _source():
    node = _selected_read()
    return node if node is not None else _new_read()


# ------------------------------------------------------------------ tools

def pole_patch(pole="nadir", src=None, holes=None):
    """Rotate `pole` to the horizon, paint, rotate back, merge in a soft band.
    `holes`: optional path to <name>_holes.png to show what had no photo."""
    src = src or _source()
    if src is None:
        return None
    fmt = src.format()
    w, h = fmt.width(), fmt.height()
    # Roll about the viewing axis: both poles land on the horizon at +-90
    # degrees, clear of the left/right seam whichever way Nuke turns.
    to_horizon = _latlong_transform(src, "%s_to_horizon" % pole.capitalize(), rz=90.0)
    paint = nuke.nodes.RotoPaint(name="Paint_%s" % pole.capitalize())
    paint.setInput(0, to_horizon)
    back = _latlong_transform(paint, "%s_back" % pole.capitalize(), rz=-90.0)

    band = nuke.nodes.Rectangle(name="%s_band" % pole.capitalize())
    band.setInput(0, src)
    soft = BAND * h * 0.5
    if pole == "nadir":   # Nuke's y runs bottom-up
        area = [-10, -10, w + 10, BAND * h]
    else:
        area = [-10, h * (1 - BAND), w + 10, h + 10]
    _set(band, ["area"], area)
    _set(band, ["softness"], soft, required=False)
    _set(band, ["output"], "alpha", required=False)

    mix = nuke.nodes.Keymix(name="%s_patch" % pole.capitalize())
    mix.setInput(0, src)    # B: untouched HDRI
    mix.setInput(1, back)   # A: painted, rotated back
    mix.setInput(2, band)   # mask
    _set(mix, ["channels"], "rgb", required=False)

    if holes and os.path.exists(holes):
        hint = nuke.nodes.Read(file=holes, name="%s_no_photo" % pole.capitalize())
        hint["label"].setValue("white = no photo here\n(view it, paint over it)")
    _note(paint, "View this node: the %s now sits on the horizon, a quarter of the\n"
                 "way in from the left or right edge. Clone/paint it out here.\n"
                 "Only a soft band around the %s is merged back." % (pole, pole))
    return mix


def orient(src=None):
    """Level the horizon / re-centre the HDRI."""
    src = src or _source()
    if src is None:
        return None
    node = _latlong_transform(src, "Orient_HDRI")
    node["label"].setValue("ry: turn   rx/rz: level the horizon")
    return node


def gray_card(src=None, target=0.18):
    """Group that multiplies the HDRI so a sampled gray card reads `target`.
    Pick the card's average colour into 'sample' with the colour picker
    (Ctrl/Cmd+Shift drag a box in the Viewer)."""
    src = src or _source()
    group = nuke.nodes.Group(name="GrayCard_Calibrate")
    if src is not None:
        group.setInput(0, src)
    sample = nuke.Color_Knob("sample", "card sample")
    sample.setValue([target, target, target])
    tgt = nuke.Double_Knob("target", "card value")
    tgt.setValue(target)
    neutral = nuke.Boolean_Knob("neutralise", "also white-balance to the card")
    neutral.setValue(True)
    for knob in (sample, tgt, neutral):
        group.addKnob(knob)
    group.begin()
    inp = nuke.nodes.Input(name="Input1")
    mult = nuke.nodes.Multiply(name="Gain")
    mult.setInput(0, inp)
    luma = "(0.2126*parent.sample.r+0.7152*parent.sample.g+0.0722*parent.sample.b)"
    for i, ch in enumerate("rgb"):
        mult["value"].setExpression(
            "parent.target / max(1e-9, parent.neutralise ? parent.sample.%s : %s)" % (ch, luma),
            i)
    out = nuke.nodes.Output(name="Output1")
    out.setInput(0, mult)
    group.end()
    return group


def write(src=None, path=None):
    src = src or _source()
    node = nuke.nodes.Write(name="Write_HDRI")
    if src is not None:
        node.setInput(0, src)
    if path:
        node["file"].setValue(path)
    _set(node, ["file_type"], "exr")
    _set(node, ["raw"], True, required=False)
    _set_enum(node, ["datatype"], "32")
    _set_enum(node, ["compression"], "zip", "1")
    _set(node, ["channels"], "rgb", required=False)
    # keep the HDRIStitch EXR attributes (colour space, clip level, EV)
    if node.knob("metadata") is not None:
        _set_enum(node, ["metadata"], "all")
    return node


def cleanup_script(path=None):
    """Read -> orient -> nadir patch -> gray card -> Write, from one file."""
    read = _new_read(path)
    if read is None:
        return None
    exr = read["file"].value()
    base = exr[:-4]
    holes = base + "_holes.png"
    node = orient(read)
    node = pole_patch("nadir", node, holes=holes)
    card = gray_card(node)
    card["disable"].setValue(True)
    card["label"].setValue("enable after sampling a gray card")
    out = write(card, base + "_clean.exr")
    _note(read, "HDRIStitch cleanup: paint in Paint_Nadir, level in Orient_HDRI,\n"
                "render Write_HDRI. Values pass through as raw scene-linear data.")
    return out


def _note(node, text):
    try:
        dot = nuke.nodes.StickyNote(label=text)
        dot.setXYpos(node.xpos() + 150, node.ypos())
    except Exception:  # StickyNote placement is cosmetic
        pass
