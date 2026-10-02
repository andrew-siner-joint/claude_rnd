"""Evaluates the subset of Nuke's expression language BlinkFlare generates.

Enough to check the generated expressions numerically: arithmetic,
comparisons, a few functions, and knob references (``node.knob.channel``,
``parent.knob``, ``input.width``, or bare knob names on the same node), which
are resolved through a callback.
"""
import math
import re

FUNCTIONS = {
    "radians": math.radians,
    "cos": math.cos,
    "sin": math.sin,
    "max": max,
    "min": min,
    "clamp": lambda x, lo, hi: min(max(x, lo), hi),
    "noise": lambda *a: 0.0,
}
CONSTANTS = {"frame": 1.0, "pi": math.pi}
TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_0-9]+)*")


def references(expr):
    """Knob reference paths in ``expr`` (function names and constants excluded)."""
    refs = []
    for m in TOKEN.finditer(expr):
        rest = expr[m.end():].lstrip()
        if rest.startswith("(") or m.group(0) in CONSTANTS:
            continue
        refs.append(m.group(0))
    return refs


def evaluate(expr, resolve):
    """Evaluate ``expr``; ``resolve(path)`` returns the value of a knob path."""
    values = {}

    def repl(m):
        name = m.group(0)
        rest = expr[m.end():].lstrip()
        if rest.startswith("("):
            if name not in FUNCTIONS:
                raise NameError("unsupported function %s" % name)
            return name
        if name in CONSTANTS:
            return repr(CONSTANTS[name])
        key = "_v%d" % len(values)
        values[key] = float(resolve(name))
        return key

    code = TOKEN.sub(repl, expr)
    env = dict(FUNCTIONS)
    env.update(values)
    return float(eval(code, {"__builtins__": {}}, env))
