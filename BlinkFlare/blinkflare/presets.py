"""Preset looks: a lens setup plus a stack of elements.

A preset has "globals" (look knobs on the node, e.g. the Lens tab; anything
not listed goes back to its default) and "elements" (the stack, replacing
whatever the node had). Built-in element entries are (type, overrides);
type-specific params may be given by label, e.g. {"Falloff": 1.7}.

Saved presets are JSON files ({"name", "globals", "elements"}) read from every
folder on the BLINKFLARE_PRESET_PATH environment variable (os.pathsep
separated, e.g. a shared studio or show folder) plus the personal folder
~/.nuke/blinkflare_presets, which is also where new presets are saved
(override with BLINKFLARE_PRESET_SAVE_DIR).
"""

import json
import os
import re
from collections import OrderedDict

BLUE = (0.35, 0.6, 1.0)
WARM = (1.0, 0.75, 0.45)

PRESETS = OrderedDict([
    ("Default", {
        "globals": {"aperture_blades": 9, "aperture_roundness": 0.35},
        "elements": [
            ("Glow", {"size": 0.05, "p": {"Falloff": 1.7, "Core Intensity": 10.0}}),
            ("Veil", {"intensity": 0.012}),
            ("Starburst", {"intensity": 0.7, "size": 0.3, "p": {"Width": 1.2, "Fine Rays": 0.3}}),
            ("Ghost Set", {"intensity": 0.12, "p": {"Count": 7}}),
            ("Iris", {"axis": 1.6, "size": 0.09, "intensity": 0.06}),
            ("Ring", {"intensity": 0.025, "size": 0.3}),
            ("Spectral", {"intensity": 0.3}),
        ]}),
    ("Physical 50mm", {
        "globals": {"aperture_blades": 6, "aperture_roundness": 0.3},
        "elements": [
            ("Glow", {"size": 0.05, "p": {"Falloff": 1.6, "Core Intensity": 10.0}}),
            ("Veil", {"intensity": 0.01}),
            ("Starburst", {"intensity": 0.6, "size": 0.25, "p": {"Width": 1.2}}),
            ("Lens System", {"coating": "Single Coated", "fstop": 5.6, "intensity": 0.7}),
        ]}),
    ("Classic Anamorphic", {
        "globals": {"aperture_blades": 0, "anamorphic": 0.55, "coating_a": (0.4, 0.7, 1.0),
                    "coating_b": (1.0, 0.6, 0.3), "coating_c": (0.6, 1.0, 0.9)},
        "elements": [
            ("Glow", {"color": (0.8, 0.9, 1.0), "size": 0.04, "intensity": 0.5}),
            ("Streak", {"color": BLUE, "size": 1.6, "intensity": 1.4,
                        "p": {"Thickness": 1.6, "Haze": 1.0, "Lines": 2, "Line Spacing": 3.0}}),
            ("Streak", {"color": (0.5, 0.7, 1.0), "size": 2.6, "intensity": 0.25,
                        "p": {"Thickness": 0.6, "Hot Core": 0.4, "Haze": 0.3}}),
            ("Ghost Set", {"color": (0.6, 0.8, 1.0), "size": 0.07,
                           "p": {"Count": 6, "Coating Mix": 0.4, "Hollow": 0.5, "Rim": 0.6}}),
            ("Caustic", {"axis": 1.5, "size": 0.15, "intensity": 0.1}),
            ("Veil", {"color": (0.7, 0.85, 1.0), "intensity": 0.012}),
        ]}),
    ("Vintage Spherical", {
        "globals": {"aperture_blades": 6, "aperture_roundness": 0.6, "dust": 0.6,
                    "master_tint": (1.0, 0.93, 0.85)},
        "elements": [
            ("Glow", {"color": WARM, "size": 0.12, "p": {"Falloff": 1.0, "Core Intensity": 6.0}}),
            ("Veil", {"color": (1.0, 0.75, 0.5), "intensity": 0.025, "p": {"Lift": 0.15}}),
            ("Lens System", {"coating": "Uncoated", "fstop": 8.0, "intensity": 0.1,
                             "color": (1.0, 0.88, 0.75), "softness": 0.12}),
            ("Starburst", {"intensity": 0.3, "size": 0.2, "p": {"Width": 2.5, "Fine Rays": 0.0}}),
            ("Ring", {"color": (1.0, 0.8, 0.6), "intensity": 0.035, "dispersion": 0.5}),
        ]}),
    ("Sci-Fi Anamorphic", {
        "globals": {"aperture_blades": 8, "anamorphic": 0.65},
        "elements": [
            ("Glow", {"color": (0.7, 0.85, 1.0), "size": 0.05, "intensity": 0.6}),
            ("Streak", {"color": (0.3, 0.55, 1.0), "size": 2.0, "intensity": 2.0,
                        "p": {"Thickness": 2.0, "Haze": 1.4, "Lines": 3, "Line Spacing": 2.5}}),
            ("Ring", {"axis": 1.0, "size": 0.45, "intensity": 0.12, "dispersion": 1.0,
                      "p": {"Thickness": 0.03, "Arc": 0.35}}),
            ("Ghost Set", {"size": 0.06, "p": {"Count": 8, "Coating Mix": 0.8, "Hollow": 0.8,
                                                "Rim": 1.0}}),
            ("Spectral", {"size": 0.05, "intensity": 0.5, "p": {"Count": 6, "Width": 4.0,
                                                               "Distance": 0.14,
                                                               "Orientation": 0.8}}),
            ("Caustic", {"axis": 0.5, "size": 0.1}),
        ]}),
    ("Golden Hour Sun", {
        "globals": {"aperture_blades": 7, "aperture_roundness": 0.1},
        "elements": [
            ("Glow", {"color": WARM, "size": 0.16, "intensity": 0.8,
                      "p": {"Falloff": 0.9, "Core Intensity": 12.0}}),
            ("Veil", {"color": (1.0, 0.7, 0.4), "intensity": 0.035, "p": {"Lift": 0.2}}),
            ("Starburst", {"color": (1.0, 0.85, 0.6), "intensity": 1.4, "size": 0.6,
                           "dispersion": 0.35, "p": {"Fine Rays": 0.8}}),
            ("Shimmer", {"intensity": 0.35, "size": 0.35}),
            ("Ghost Set", {"color": (1.0, 0.8, 0.5), "size": 0.05,
                           "p": {"Count": 10, "Coating Mix": 0.5}}),
            ("Spectral", {"intensity": 0.6}),
        ]}),
    ("Night Street", {
        "globals": {"aperture_blades": 0, "anamorphic": 0.5},
        "elements": [
            ("Glow", {"color": (1.0, 0.8, 0.6), "size": 0.03, "intensity": 0.5,
                      "p": {"Core Intensity": 8.0}}),
            ("Streak", {"color": (0.3, 0.75, 1.0), "size": 1.0, "intensity": 0.9,
                        "p": {"Thickness": 1.0, "Haze": 0.5}}),
            ("Ghost Set", {"size": 0.035, "intensity": 0.08, "p": {"Count": 5, "Coating Mix": 0.6}}),
            ("Veil", {"color": (1.0, 0.8, 0.6), "intensity": 0.006}),
        ]}),
    ("Practical Light", {
        "globals": {"aperture_blades": 8},
        "elements": [
            ("Glow", {"size": 0.03, "intensity": 0.5, "p": {"Core Intensity": 6.0, "Core Size": 0.15}}),
            ("Starburst", {"intensity": 0.5, "size": 0.12, "p": {"Rays": 8, "Fine Rays": 0.2}}),
            ("Ghost Set", {"size": 0.03, "intensity": 0.08, "p": {"Count": 5}}),
            ("Veil", {"intensity": 0.006}),
        ]}),
])


def stack(preset):
    """The full element dicts of a preset (built-in or saved)."""
    from blinkflare import elements
    out = []
    for entry in preset["elements"]:
        if isinstance(entry, dict):
            out.append(dict(entry))
        else:
            kind, overrides = entry
            out.append(elements.element(kind, **dict(overrides)))
    return out


# ----------------------------------------------------------- saved presets

def save_dir():
    return os.environ.get("BLINKFLARE_PRESET_SAVE_DIR") or os.path.join(
        os.path.expanduser("~"), ".nuke", "blinkflare_presets")


def preset_dirs():
    dirs = [d for d in os.environ.get("BLINKFLARE_PRESET_PATH", "").split(os.pathsep) if d]
    personal = save_dir()
    if personal not in dirs:
        dirs.append(personal)
    return dirs


def _coerce_globals(values):
    """Keep only look knobs with sensible values; JSON lists become tuples."""
    from blinkflare import spec
    kinds = dict((k.name, k.kind) for k in spec.value_knobs())
    out = {}
    for name, v in values.items():
        kind = kinds.get(name)
        if kind is None or name in spec.NON_LOOK_KNOBS:
            continue
        if kind == "color":
            v = tuple(float(c) for c in v)
            if len(v) != 3:
                continue
        elif kind == "bool":
            v = bool(v)
        elif kind in ("int", "enum"):
            v = int(v)
        else:
            v = float(v)
        out[name] = v
    return out


def _coerce_element(entry):
    """A saved element dict, completed and cleaned; None if unusable."""
    from blinkflare import elements
    if not isinstance(entry, dict) or entry.get("type") not in elements.TYPES:
        return None
    el = elements.element(entry["type"])
    for key, value in entry.items():
        if key == "p":
            el["p"] = [float(v) for v in list(value)[:8]] + [0.0] * max(0, 8 - len(value))
        elif key == "color":
            el["color"] = tuple(float(c) for c in value)[:3]
        elif key in el:
            el[key] = value
    return el


def saved_presets():
    """{name: preset} from every preset folder; earlier folders win."""
    found = OrderedDict()
    for folder in preset_dirs():
        if not os.path.isdir(folder):
            continue
        for fname in sorted(os.listdir(folder)):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, fname)) as f:
                    data = json.load(f)
                name = str(data["name"])
                preset = {"globals": _coerce_globals(data.get("globals", {})),
                          "elements": [e for e in map(_coerce_element, data["elements"]) if e]}
            except (ValueError, KeyError, TypeError, OSError):
                continue  # skip unreadable or old-format files rather than break the menu
            if name not in found and name not in PRESETS:
                found[name] = preset
    return found


def all_presets():
    merged = OrderedDict(PRESETS)
    merged.update(saved_presets())
    return merged


def write_preset(name, globals_, stack_, folder=None):
    """Save a look (look-knob values plus element dicts). Returns the path."""
    if not name or name in PRESETS:
        raise ValueError("Choose a name that isn't a built-in preset.")
    folder = folder or save_dir()
    if not os.path.isdir(folder):
        os.makedirs(folder)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "preset"
    path = os.path.join(folder, slug + ".json")
    elements = []
    for el in stack_:
        clean = dict(el)
        clean["color"] = list(clean["color"])
        clean["p"] = list(clean["p"])
        elements.append(clean)
    with open(path, "w") as f:
        json.dump({"name": name, "globals": _coerce_globals(globals_), "elements": elements},
                  f, indent=2, sort_keys=True)
    return path
