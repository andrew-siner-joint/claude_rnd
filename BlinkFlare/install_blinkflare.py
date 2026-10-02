"""BlinkFlare installer and diagnostic. Run it inside NukeX (or Nuke Studio).

How to run it, either:
  * Script Editor: click "Source a script" (the folder-with-arrow icon) and
    pick this file, or
  * open this file in a text editor, copy all of it, paste it into the
    Script Editor and press Run (Ctrl/Cmd+Enter), or
  * once installed: Nodes > Draw > BlinkFlare > Check Install...

What it does:
  1. Asks where BlinkFlare is: pick the BlinkFlare folder, or the
     BlinkFlare.blink file inside it (blinkflare/kernel/BlinkFlare.blink).
  2. Compiles a tiny test kernel and the BlinkFlare kernel, timing how long
     Nuke takes. If BlinkFlare is rejected, it compiles a few more test
     kernels and narrows down which kernel function Nuke rejects.
  3. Builds a BlinkFlare node from the compiled kernel (and caches it, so
     later BlinkFlare nodes appear instantly).
  4. Offers to add BlinkFlare to ~/.nuke/init.py and its menu entries.

Nuke stays responsive while it works: compiles run in the background and a
progress window (with Cancel) shows what is happening. The report is written
to ~/.nuke/blinkflare_report.txt line by line as it goes, so even if Nuke
stops responding, that file shows how far it got. It is also printed here and
copied to the clipboard at the end. If anything fails, send that report back.

Test nodes are built inside a temporary group that is deleted at the end.
"""

import os
import platform
import re
import sys
import time
import traceback

import nuke

KERNEL_REL = os.path.join("blinkflare", "kernel", "BlinkFlare.blink")
FIRST_PARAM = "lightPos"
PROBE_TIMEOUT = 120.0
KERNEL_TIMEOUT = 300.0


def env_flag(key):
    """nuke.env[key], or None where this Nuke doesn't have it."""
    try:
        return nuke.env[key]
    except Exception:
        return None


# ----------------------------------------------------------------- reporting

def report_path():
    return os.path.join(os.path.expanduser("~"), ".nuke", "blinkflare_report.txt")


class Report(object):
    """Prints each line and appends it to the report file straight away."""

    def __init__(self, path):
        self.path = path
        self.lines = []
        self.problems = []
        self.start = time.time()
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        open(path, "w").close()

    def __call__(self, text=""):
        line = "[%6.1fs] %s" % (time.time() - self.start, text) if text else ""
        print(line)
        self.lines.append(line)
        with open(self.path, "a") as f:
            f.write(line + "\n")

    def section(self, title):
        self("")
        self("== " + title)

    def problem(self, text):
        self("PROBLEM: " + text)
        self.problems.append(text)

    def text(self):
        return "\n".join(self.lines) + "\n"


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




# --------------------------------------------------------------- the checks

def environment(rep, root):
    rep.section("Environment")
    rep("Nuke: %s" % getattr(nuke, "NUKE_VERSION_STRING", "?"))
    rep("Licence flags: " + ", ".join(
        "%s=%s" % (key, env_flag(key)) for key in ("nukex", "studio", "hiero", "ple", "indie", "gui")))
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
    if loaded is not None and getattr(loaded, "__file__", None):
        where = os.path.dirname(os.path.dirname(os.path.abspath(loaded.__file__)))
        rep("blinkflare package already imported from: %s" % where)
        if os.path.normcase(where) != os.path.normcase(root):
            rep.problem("Nuke had a different BlinkFlare copy loaded (%s). Remove the old "
                        "pluginAddPath line, or make it point here." % where)
    if not (env_flag("nukex") or env_flag("studio")):
        rep.problem("This doesn't look like NukeX or Nuke Studio. Plain Nuke can run BlinkScript "
                    "nodes but can't compile kernels; build the node on a NukeX seat (Save ToolSet).")


def import_package(rep, root):
    """Import this folder's copy of the package (fresh, in case it changed)."""
    if root not in sys.path:
        sys.path.insert(0, root)
    for name in list(sys.modules):
        if name == "blinkflare" or name.startswith("blinkflare."):
            del sys.modules[name]
    try:
        import blinkflare
        from blinkflare import builder, compiling
    except Exception:
        rep.problem("Importing the blinkflare package failed:")
        rep(traceback.format_exc())
        return None
    rep("blinkflare package v%s imported from %s" % (blinkflare.VERSION, root))
    return blinkflare, builder, compiling


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


def copy_to_clipboard(text):
    for module in ("PySide6", "PySide2"):
        try:
            widgets = __import__(module + ".QtWidgets", fromlist=["QtWidgets"])
        except ImportError:
            continue
        app = widgets.QApplication.instance()
        if app is None:
            return False
        app.clipboard().setText(text)
        return True
    return False


class Diagnostic(object):
    """Runs the checks one at a time on Nuke's event loop.

    Each step either finishes synchronously and calls next(), or starts a
    background compile whose callback calls next(). Nothing waits in a loop.
    """

    def __init__(self, rep, root, package):
        self.rep = rep
        self.root = root
        self.blinkflare, self.builder, self.compiling = package
        self.progress = self.compiling.Progress("BlinkFlare check", "Starting...")
        with open(os.path.join(root, KERNEL_REL)) as f:
            self.source = f.read()
        with nuke.root():
            self.sandbox = nuke.nodes.Group(name="BlinkFlare_check")
        self.steps = [self.step_basic, self.step_kernel]
        self.kernel_ok = False
        self.basic_ok = False
        self.culprits = []
        self.built = None

    # -- driving
    def start(self):
        self.next()

    def next(self):
        if self.progress.cancelled():
            self.rep("Cancelled.")
            self.steps = []
        if not self.steps:
            self.compiling.later(0, self.finish)
            return
        step = self.steps.pop(0)
        self.compiling.later(0, lambda: self.guard(step))

    def guard(self, step):
        try:
            step()
        except Exception:
            self.rep.problem("A check failed unexpectedly:")
            self.rep(traceback.format_exc())
            self.next()

    def compile(self, label, source, param, timeout, on_result, keep=False):
        """Compile on a fresh test node; on_result(result, node); then next()."""
        with self.sandbox:
            node = nuke.nodes.BlinkScript()
            if node.knob("useGPUIfAvailable") is not None:
                node["useGPUIfAvailable"].setValue(False)
        self.progress.text(label)
        self.rep("compiling: %s" % label)

        def done(result):
            try:
                on_result(result, node)
            except Exception:
                self.rep.problem("Handling the compile result failed:")
                self.rep(traceback.format_exc())
            if not keep:
                try:
                    nuke.delete(node)
                except Exception:
                    pass
            self.next()

        self.compiling.compile_async(node, source, param, done, timeout=timeout,
                                     progress=self.progress, label=label)

    # -- steps
    def step_basic(self):
        self.rep.section("Test kernel")

        def result(r, node):
            self.basic_ok = r.ok
            if r.ok:
                self.rep("ok    basic kernel: %s" % r.describe())
            else:
                self.rep("FAIL  basic kernel: %s" % r.describe())
                self.rep.problem("Even a minimal test kernel didn't compile from Python.")
        self.compile("basic test kernel", PROBES[0][2], PROBES[0][1], PROBE_TIMEOUT, result)

    def step_kernel(self):
        self.rep.section("BlinkFlare kernel")

        def result(r, node):
            self.kernel_ok = r.ok
            if r.ok:
                self.rep("ok    BlinkFlare kernel: %s" % r.describe())
                self.steps.insert(0, lambda: self.step_build(node))
            else:
                self.rep.problem("The BlinkFlare kernel did not compile: %s" % r.describe())
                if self.basic_ok:
                    self.steps[0:0] = [self.step_probes, self.step_bisect]
        self.compile("BlinkFlare kernel", self.source, FIRST_PARAM, KERNEL_TIMEOUT, result, keep=True)

    def step_build(self, node):
        self.rep.section("Building a BlinkFlare node")
        for n in nuke.selectedNodes():
            n.setSelected(False)
        errors = []
        try:
            group = self.builder.create_from_kernel(node, on_error=errors.append)
        except Exception:
            errors.append(traceback.format_exc())
            group = None
        if group is not None:
            self.built = group
            self.rep("Built %s; the compiled kernel is cached, so new BlinkFlare nodes are "
                     "instant from now on." % group.name())
        else:
            self.rep.problem("Building the node failed:")
            for e in errors:
                self.rep(e)
        try:
            nuke.delete(node)
        except Exception:
            pass
        self.next()

    def step_probes(self):
        self.rep.section("More test kernels")
        pending = list(PROBES[1:])

        def run_next():
            if not pending:
                return self.next()
            title, param, source = pending.pop(0)

            def result(r, node):
                self.rep("%s  %s: %s" % ("ok  " if r.ok else "FAIL", title, r.describe()))
                if not r.ok:
                    self.rep.problem("Test kernel failed: " + title)
            self.steps.insert(0, run_next)
            self.compile(title, source, param, PROBE_TIMEOUT, result)
        run_next()

    def step_bisect(self):
        self.rep.section("Which part of the kernel does Nuke reject?")
        names = [n for n in kernel_functions(self.source) if n != "define"]

        def base_result(r, node):
            if not r.ok:
                self.rep.problem("Nuke rejects the kernel even with every function emptied, so "
                                 "the problem is in its declarations (inputs, params, locals) or define().")
                return
            self.rep("With every function emptied it compiles; checking functions one by one.")
            self.steps[0:0] = [self.function_step(n) for n in names] + [self.step_culprits]
        self.compile("kernel with all functions emptied", stub_functions(self.source, ()),
                     FIRST_PARAM, PROBE_TIMEOUT, base_result)

    def function_step(self, name):
        def step():
            def result(r, node):
                if not r.ok:
                    self.culprits.append(name)
            self.compile("only %s kept" % name, stub_functions(self.source, (name,)),
                         FIRST_PARAM, PROBE_TIMEOUT, result)
        return step

    def step_culprits(self):
        if self.culprits:
            self.rep.problem("Nuke rejects these kernel functions: " + ", ".join(self.culprits))
            for m in FUNC_RE.finditer(self.source):
                if m.group(2) in self.culprits:
                    line = self.source.count("\n", 0, m.start()) + 1
                    self.rep("  %s starts at line %d of BlinkFlare.blink" % (m.group(2), line))
        else:
            self.rep("Every function compiles on its own; the failure only shows up in combination.")
        self.next()

    def finish(self):
        self.progress.close()
        try:
            nuke.delete(self.sandbox)
        except Exception:
            pass
        if self.built is not None and not nuke.ask(
                "BlinkFlare built fine. Keep the test node %s?" % self.built.name()):
            nuke.delete(self.built)
        try:
            install_startup(self.rep, self.root)
        except Exception:
            self.rep.problem("Updating init.py / the menu failed:")
            self.rep(traceback.format_exc())
        done(self.rep)


def done(rep):
    copied = copy_to_clipboard(rep.text())
    where = "Report saved to %s%s." % (rep.path, " and copied to the clipboard" if copied else "")
    rep("")
    rep(where)
    if rep.problems:
        nuke.message("BlinkFlare found problems:\n\n- " + "\n- ".join(rep.problems[:6]) +
                     "\n\n" + where + "\nPlease send that report back.")
    else:
        nuke.message("BlinkFlare is installed and working.\n\n" + where)


def main():
    root = locate()
    if not root:
        nuke.message("Couldn't find BlinkFlare there. Pick the BlinkFlare folder (the one "
                     "containing blinkflare/kernel/BlinkFlare.blink), or that .blink file.")
        return None
    rep = Report(report_path())
    rep("BlinkFlare install / diagnostic")
    environment(rep, root)
    package = import_package(rep, root)
    if package is None:
        done(rep)
        return rep.path
    blinkscript_knobs(rep)
    Diagnostic(rep, root, package).start()
    return rep.path


if __name__ == "__main__":
    main()
