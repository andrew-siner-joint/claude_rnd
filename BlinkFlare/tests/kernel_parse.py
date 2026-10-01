"""Reads parameter declarations and defaults out of BlinkFlare.blink."""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

KERNEL_PATH = os.path.join(ROOT, "blinkflare", "kernel", "BlinkFlare.blink")


def source():
    with open(KERNEL_PATH) as f:
        return f.read()


def declared_params(src=None):
    """{name: type} from the kernel's param: section, in order."""
    src = src or source()
    section = src.split("param:")[1].split("local:")[0]
    return dict((name, t) for t, name in
                re.findall(r"^\s*(float[234]?|int|bool)\s+(\w+);", section, re.M))


def _literal(text):
    text = text.strip()
    m = re.match(r"float[234]\((.*)\)$", text)
    if m:
        return tuple(_literal(p) for p in m.group(1).split(","))
    if text in ("true", "false"):
        return text == "true"
    if text.endswith("f"):
        return float(text[:-1])
    return int(text)


def define_calls(src=None):
    """[(var, label, default)] for every defineParam call."""
    src = src or source()
    return [(var, label, _literal(default)) for var, label, default in
            re.findall(r'defineParam\((\w+),\s*"(\w+)",\s*(.+?)\);', src)]
