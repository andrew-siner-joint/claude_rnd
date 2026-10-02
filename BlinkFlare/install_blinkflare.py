"""BlinkFlare installer and diagnostic. Run it inside NukeX (or Nuke Studio).

How to run it, either:
  * Script Editor: click "Source a script" (the folder-with-arrow icon) and
    pick this file, or
  * open this file in a text editor, copy all of it, paste it into the
    Script Editor and press Run (Ctrl/Cmd+Enter).

What it does:
  1. Asks where BlinkFlare is: pick the BlinkFlare folder, or the
     BlinkFlare.blink file inside it (blinkflare/kernel/BlinkFlare.blink).
  2. Checks that this Nuke can compile Blink kernels from Python, using a few
     tiny test kernels, and records how it names kernel parameter knobs.
  3. Compiles the BlinkFlare kernel. If Nuke rejects it, narrows down which
     function(s) Nuke rejects.
  4. Builds a BlinkFlare node.
  5. Offers to add BlinkFlare to ~/.nuke/init.py and its menu entries.

A report is printed here, saved to ~/.nuke/blinkflare_report.txt and copied
to the clipboard. If anything fails, send that report back.

Nothing is left in your script except the BlinkFlare node (if you keep it);
test nodes are built inside a temporary group that is deleted at the end.
"""

import os
import platform
import re
import sys
import tempfile
import traceback

import nuke

KERNEL_REL = os.path.join("blinkflare", "kernel", "BlinkFlare.blink")
FIRST_PARAM = "lightPos"


def env_flag(key):
    """nuke.env[key], or None where this Nuke doesn't have it."""
    try:
        return nuke.env[key]
    except Exception:
        return None


# ----------------------------------------------------------------- reporting

class Report(object):
    def __init__(self):
        self.lines = []
        self.problems = []

    def __call__(self, text=""):
        print(text)
        self.lines.append(text)

    def section(self, title):
        self("")
        self("== " + title)

    def problem(self, text):
        self("PROBLEM: " + text)
        self.problems.append(text)

    def text(self):
        return "\n".join(self.lines) + "\n"


def report_path():
    return os.path.join(os.path.expanduser("~"), ".nuke", "blinkflare_report.txt")


# ------------------------------------------------------------------ locating

def find_root(path):
    """The BlinkFlare folder containing ``path`` (a file or folder), or None."""
    path = os.path.abspath(path.rstrip("/\\") or path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    for _ in range(6):
        if os.path.isfile(os.path.join(path, KERNEL_REL)):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def locate():
    here = globals().get("__file__")
    if here:
        root = find_root(here)
        if root:
            return root
    picked = nuke.getFilename(
        "Locate BlinkFlare: pick the BlinkFlare folder or BlinkFlare.blink", "*.blink")
    return find_root(picked) if picked else None


# ------------------------------------------------------------------ compiling

def param_knob(node, param):
    """Name of the knob BlinkScript made for ``param``, however it's prefixed."""
    low = param.lower()
    for name in sorted(node.knobs()):
        if name.lower() == low or name.lower().endswith("_" + low):
            return name
    return None


def process_events():
    for module in ("PySide6", "PySide2"):
        try:
            widgets = __import__(module + ".QtWidgets", fromlist=["QtWidgets"])
        except ImportError:
            continue
        app = widgets.QApplication.instance()
        if app is not None:
            app.processEvents()
        return


def compile_steps(node, source):
    """The same compile triggers blinkflare.builder tries, in the same order."""
    def recompile():
        node["recompile"].execute()

    def validate():
        node.forceValidate()

    def from_file():
        path = os.path.join(tempfile.gettempdir(), "BlinkFlare_diag_%d.blink" % os.getpid())
        with open(path, "w") as f:
            f.write(source)
        node["kernelSourceFile"].setValue(path)
        node["reloadKernelSourceFile"].execute()
        node["recompile"].execute()

    return [("recompile", recompile), ("validate", validate), ("events", process_events),
            ("file", from_file)]


class CompileResult(object):
    def __init__(self):
        self.step = None
        self.knob = None
        self.new_knobs = []
        self.errors = []
        self.in_error = None

    @property
    def ok(self):
        return self.step is not None


def try_compile(source, param):
    """Compile ``source`` on a fresh BlinkScript node and delete it again."""
    result = CompileResult()
    node = nuke.nodes.BlinkScript()
    try:
        before = set(node.knobs())
        node["kernelSource"].setValue(source)
        for name, step in compile_steps(node, source):
            try:
                step()
            except Exception as e:
                result.errors.append("%s: %s" % (name, e))
            result.knob = param_knob(node, param)
            if result.knob:
                result.step = name
                break
        result.new_knobs = sorted(set(node.knobs()) - before)
        try:
            result.in_error = node.hasError()
        except Exception:
            pass
    finally:
        nuke.delete(node)
    return result


# Each probe exercises one group of Blink features BlinkFlare relies on.
PROBES = [
    ("basic kernel, float param", "probeGain", """
kernel ProbeBasic : ImageComputationKernel<ePixelWise>
{
  Image<eRead, eAccessPoint, eEdgeClamped> src;
  Image<eWrite> dst;

  param:
    float probeGain;

  void define()
  {
    defineParam(probeGain, "probeGain", 1.0f);
  }

  void process()
  {
    dst() = src() * probeGain;
  }
};
"""),
    ("float2/float4/int/bool params, locals, init()", "probePos", """
kernel ProbeTypes : ImageComputationKernel<ePixelWise>
{
  Image<eRead, eAccessPoint, eEdgeClamped> src;
  Image<eWrite> dst;

  param:
    float2 probePos;
    float4 probeColor;
    int    probeCount;
    bool   probeFlag;

  local:
    float2 _p;
    float  _k;

  void define()
  {
    defineParam(probePos, "probePos", float2(1.0f, 2.0f));
    defineParam(probeColor, "probeColor", float4(1.0f, 1.0f, 1.0f, 1.0f));
    defineParam(probeCount, "probeCount", 3);
    defineParam(probeFlag, "probeFlag", true);
  }

  void init()
  {
    _p = probePos * 0.5f;
    _k = probeFlag ? float(probeCount) : 0.0f;
  }

  void process(int2 pos)
  {
    float4 s = src();
    float v = _k + _p.x * 0.0f + float(pos.x) * 0.0f;
    dst() = float4(s.x * probeColor.x * v, s.y, s.z, s.w);
  }
};
"""),
    ("member functions, loops, ternaries, vector math", "probeAmount", """
kernel ProbeFunctions : ImageComputationKernel<ePixelWise>
{
  Image<eRead, eAccessPoint, eEdgeClamped> src;
  Image<eWrite> dst;

  param:
    float probeAmount;

  void define()
  {
    defineParam(probeAmount, "probeAmount", 1.0f);
  }

  float helper(float x)
  {
    return x - floor(x);
  }

  int clampi(int x, int lo, int hi)
  {
    return x < lo ? lo : (x > hi ? hi : x);
  }

  float3 tint(float4 c)
  {
    return float3(c.x, c.y, c.z);
  }

  void process(int2 pos)
  {
    float3 col = float3(0.0f, 0.0f, 0.0f);
    float2 d = float2(float(pos.x), float(pos.y));
    d.x = d.x * 0.5f;
    int n = clampi(pos.y, 1, 4);
    for (int i = 0; i < n; i++) {
      if (i == 2) {
        continue;
      }
      col += tint(src()) * helper(d.x + float(i)) * probeAmount;
    }
    bool showAll = d.y > 0.0f;
    float a = showAll ? 1.0f : 0.0f;
    col = col * float3(1.0f, 0.5f, 0.25f);
    dst() = float4(col.x, col.y, col.z, a);
  }
};
"""),
    ("math functions", "probeScale", """
kernel ProbeMath : ImageComputationKernel<ePixelWise>
{
  Image<eRead, eAccessPoint, eEdgeClamped> src;
  Image<eWrite> dst;

  param:
    float probeScale;

  void define()
  {
    defineParam(probeScale, "probeScale", 1.0f);
  }

  void process(int2 pos)
  {
    float2 d = float2(float(pos.x) + 0.5f, float(pos.y) + 0.5f);
    float r = length(d) + dot(d, d) * 0.0f;
    float a = atan2(d.y, d.x) + exp(-r) + pow(1.0f + r, -1.2f) + sqrt(r) + sin(r) * cos(r);
    a = clamp(min(a, 10.0f), max(-1.0f, -2.0f), 10.0f);
    dst() = float4(a * probeScale, 0.0f, 0.0f, 1.0f);
  }
};
"""),
    ("random access, bilinear, three inputs", "probeAt", """
kernel ProbeRandom : ImageComputationKernel<ePixelWise>
{
  Image<eRead, eAccessRandom, eEdgeClamped> src;
  Image<eRead, eAccessRandom, eEdgeConstant> occlusion;
  Image<eRead, eAccessPoint, eEdgeConstant> dirt;
  Image<eWrite> dst;

  param:
    float2 probeAt;

  void define()
  {
    defineParam(probeAt, "probeAt", float2(10.0f, 10.0f));
  }

  void process(int2 pos)
  {
    float4 a = bilinear(src, probeAt.x, probeAt.y);
    float4 o = bilinear(occlusion, probeAt.x, probeAt.y);
    float4 dv = dirt();
    dst() = float4(a.x + o.w + dv.x, a.y, a.z, 1.0f);
  }
};
"""),
]


# ----------------------------------------------------------------- bisecting

FUNC_RE = re.compile(r"^  (float3|float2|float4|float|int|bool|void) (\w+)\([^)]*\)\n  \{\n.*?^  \}\n",
                     re.M | re.S)
STUBS = {
    "float": "    return 0.0f;\n",
    "float2": "    return float2(0.0f, 0.0f);\n",
    "float3": "    return float3(0.0f, 0.0f, 0.0f);\n",
    "float4": "    return float4(0.0f, 0.0f, 0.0f, 0.0f);\n",
    "int": "    return 0;\n",
    "bool": "    return false;\n",
    "void": "",
}


def kernel_functions(source):
    return [m.group(2) for m in FUNC_RE.finditer(source)]


def stub_functions(source, keep):
    """Empty every function body except define() and those named in ``keep``."""
    out, last = [], 0
    for m in FUNC_RE.finditer(source):
        rtype, name = m.group(1), m.group(2)
        out.append(source[last:m.start()])
        if name == "define" or name in keep:
            out.append(m.group(0))
        else:
            header = m.group(0).split("\n  {\n", 1)[0]
            body = "    dst() = float4(0.0f, 0.0f, 0.0f, 0.0f);\n" if name == "process" else STUBS[rtype]
            out.append(header + "\n  {\n" + body + "  }\n")
        last = m.end()
    out.append(source[last:])
    return "".join(out)


def bisect(rep, source, task=None):
    """Find which kernel functions Nuke rejects. Returns their names."""
    names = [n for n in kernel_functions(source) if n != "define"]
    rep("Compiling copies of the kernel with function bodies emptied out...")
    base = try_compile(stub_functions(source, ()), FIRST_PARAM)
    if not base.ok:
        rep.problem("Nuke rejects the kernel even with every function emptied, so the problem "
                    "is in its declarations (inputs, params, locals) or define().")
        return []
    culprits = []
    for i, name in enumerate(names):
        if task is not None:
            if task.isCancelled():
                rep("(cancelled)")
                break
            task.setMessage("Checking %s" % name)
            task.setProgress(int(60 + 35 * i / max(len(names), 1)))
        if not try_compile(stub_functions(source, (name,)), FIRST_PARAM).ok:
            culprits.append(name)
    if culprits:
        rep.problem("Nuke rejects these kernel functions: " + ", ".join(culprits))
        for m in FUNC_RE.finditer(source):
            if m.group(2) in culprits:
                line = source.count("\n", 0, m.start()) + 1
                rep("  %s starts at line %d of BlinkFlare.blink" % (m.group(2), line))
    else:
        rep("Every function compiles on its own; the failure only shows up in combination.")
    return culprits


# --------------------------------------------------------------------- steps

def environment(rep, root):
    rep.section("Environment")
    rep("Nuke: %s" % getattr(nuke, "NUKE_VERSION_STRING", "?"))
    flags = ["%s=%s" % (key, env_flag(key)) for key in ("nukex", "studio", "hiero", "ple", "indie", "gui")]
    rep("Licence flags: " + ", ".join(flags))
    rep("OS: %s | Python %s" % (platform.platform(), sys.version.split()[0]))
    rep("BlinkFlare folder: %s" % root)
    kernel = os.path.join(root, KERNEL_REL)
    rep("Kernel file: %s (%d bytes)" % (kernel, os.path.getsize(kernel)))
    try:
        paths = [p for p in nuke.pluginPath() if "blinkflare" in p.lower()]
        rep("Plugin path entries mentioning BlinkFlare: %s" % (paths or "none"))
    except Exception:
        pass
    loaded = sys.modules.get("blinkflare")
    if loaded is not None:
        where = os.path.dirname(os.path.dirname(os.path.abspath(loaded.__file__)))
        rep("blinkflare package already imported from: %s" % where)
        if os.path.normcase(where) != os.path.normcase(root):
            rep.problem("Nuke has a different BlinkFlare copy loaded (%s). Remove the old "
                        "pluginAddPath line, or make it point here." % where)
    if not (env_flag("nukex") or env_flag("studio")):
        rep.problem("This doesn't look like NukeX or Nuke Studio. Plain Nuke can run BlinkScript "
                    "nodes but can't compile kernels; build the node on a NukeX seat (Save ToolSet).")


def blinkscript_knobs(rep):
    rep.section("BlinkScript node")
    node = nuke.nodes.BlinkScript()
    try:
        names = sorted(node.knobs())
        rep("Knobs on a fresh BlinkScript node: " + ", ".join(names))
        for name in names:
            if "gpu" in name.lower():
                try:
                    rep("  %s = %r" % (name, node[name].value()))
                except Exception:
                    pass
        for needed in ("kernelSource", "recompile"):
            if needed not in names:
                rep.problem("BlinkScript has no '%s' knob in this Nuke version." % needed)
    finally:
        nuke.delete(node)


def run_probes(rep):
    rep.section("Test kernels")
    all_ok = True
    for title, param, source in PROBES:
        r = try_compile(source, param)
        if r.ok:
            rep("ok    %s  (compiled via '%s'; param knob named %r)" % (title, r.step, r.knob))
        else:
            all_ok = False
            rep("FAIL  %s  (node in error: %s; new knobs: %s; errors: %s)"
                % (title, r.in_error, r.new_knobs or "none", r.errors or "none"))
    if not all_ok:
        rep.problem("Some test kernels didn't compile from Python (see above).")
    return all_ok


def compile_blinkflare(rep, root):
    rep.section("BlinkFlare kernel")
    with open(os.path.join(root, KERNEL_REL)) as f:
        source = f.read()
    expected = len(re.findall(r"defineParam\(", source))
    r = try_compile(source, FIRST_PARAM)
    if r.ok:
        rep("Compiled via '%s'. %d new knobs (kernel defines %d params); e.g. %r."
            % (r.step, len(r.new_knobs), expected, r.knob))
    else:
        rep.problem("The BlinkFlare kernel did not compile (node in error: %s; errors: %s)."
                    % (r.in_error, r.errors or "none"))
    return r.ok, source


def build_node(rep, root):
    rep.section("Building a BlinkFlare node")
    if root not in sys.path:
        sys.path.insert(0, root)
    for name in list(sys.modules):
        if name == "blinkflare" or name.startswith("blinkflare."):
            del sys.modules[name]  # make sure this copy is the one imported
    try:
        import blinkflare
        for n in nuke.selectedNodes():
            n.setSelected(False)
        node = blinkflare.create()
    except Exception:
        rep.problem("blinkflare.create() failed:")
        rep(traceback.format_exc())
        return None
    rep("Built %s (BlinkFlare v%s)." % (node.name(), blinkflare.VERSION))
    return node


def install_startup(rep, root):
    rep.section("Startup")
    init_py = os.path.join(os.path.expanduser("~"), ".nuke", "init.py")
    text = ""
    if os.path.isfile(init_py):
        with open(init_py) as f:
            text = f.read()
    forward = root.replace("\\", "/")
    if root in text or forward in text:
        rep("%s already adds this folder to the plugin path." % init_py)
    elif nuke.ask("Add BlinkFlare to %s so it loads every time Nuke starts?\n\n%s" % (init_py, root)):
        folder = os.path.dirname(init_py)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(init_py, "a") as f:
            f.write('\n# BlinkFlare\nimport nuke\nnuke.pluginAddPath(r"%s")\n' % forward)
        rep("Added BlinkFlare to %s." % init_py)
    else:
        rep("Left %s unchanged." % init_py)

    try:
        has_menu = nuke.menu("Nodes").findItem("Draw/BlinkFlare") is not None
    except Exception:
        has_menu = True
    if not has_menu:
        if root not in sys.path:
            sys.path.insert(0, root)
        menu_py = os.path.join(root, "menu.py")
        with open(menu_py) as f:
            code = compile(f.read(), menu_py, "exec")
        exec(code, {"nuke": nuke, "__name__": "__main__"})
        rep("Added the Nodes > Draw > BlinkFlare menu for this session.")


def finish(rep):
    path = report_path()
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, "w") as f:
        f.write(rep.text())
    copied = False
    for module in ("PySide6", "PySide2"):
        try:
            widgets = __import__(module + ".QtWidgets", fromlist=["QtWidgets"])
            app = widgets.QApplication.instance()
            if app is not None:
                app.clipboard().setText(rep.text())
                copied = True
            break
        except Exception:
            continue
    where = "Report saved to %s%s." % (path, " and copied to the clipboard" if copied else "")
    rep("")
    rep(where)
    if rep.problems:
        nuke.message("BlinkFlare found problems:\n\n- " + "\n- ".join(rep.problems[:6]) +
                     "\n\n" + where + "\nPlease send that report back.")
    else:
        nuke.message("BlinkFlare is installed and working.\n\n" + where)
    return path


def main():
    rep = Report()
    rep("BlinkFlare install / diagnostic")
    root = locate()
    if not root:
        nuke.message("Couldn't find BlinkFlare there. Pick the BlinkFlare folder (the one "
                     "containing blinkflare/kernel/BlinkFlare.blink), or that .blink file.")
        return None
    environment(rep, root)

    task = nuke.ProgressTask("BlinkFlare diagnostic")
    ok = False
    source = ""
    with nuke.root():
        sandbox = nuke.nodes.Group(name="BlinkFlare_diagnostic")
    try:
        with sandbox:
            task.setMessage("Inspecting BlinkScript")
            blinkscript_knobs(rep)
            task.setMessage("Compiling test kernels")
            task.setProgress(20)
            run_probes(rep)
            task.setMessage("Compiling BlinkFlare")
            task.setProgress(50)
            ok, source = compile_blinkflare(rep, root)
            if not ok:
                bisect(rep, source, task)
    except Exception:
        rep.problem("The diagnostic itself failed:")
        rep(traceback.format_exc())
    finally:
        nuke.delete(sandbox)
        del task

    node = build_node(rep, root) if ok else None
    if node is not None and not nuke.ask("BlinkFlare built fine. Keep the test node %s?" % node.name()):
        nuke.delete(node)
    install_startup(rep, root)
    return finish(rep)


if __name__ == "__main__":
    main()
