"""install_blinkflare.py, run against the fake nuke module."""
import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import kernel_parse
import fake_nuke

sys.modules["nuke"] = fake_nuke

ROOT = kernel_parse.ROOT
HARNESS = os.path.join(ROOT, "tests", "harness")


def load_installer():
    spec = importlib.util.spec_from_file_location(
        "install_blinkflare", os.path.join(ROOT, "install_blinkflare.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClock(object):
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class InstallerTest(unittest.TestCase):
    def setUp(self):
        # The installer re-imports blinkflare, so patch time where it looks.
        self.clock = FakeClock()
        self.patches = [mock.patch("time.time", self.clock.time),
                        mock.patch("time.sleep", self.clock.sleep)]
        for p in self.patches:
            p.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ)
        os.environ["HOME"] = self.tmp.name
        os.environ["BLINKFLARE_PRESET_PATH"] = ""
        os.environ["BLINKFLARE_PRESET_SAVE_DIR"] = os.path.join(self.tmp.name, "presets")
        fake_nuke.reset()
        self.inst = load_installer()
        self.init_py = os.path.join(self.tmp.name, ".nuke", "init.py")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def run_main(self):
        with contextlib.redirect_stdout(io.StringIO()):
            path = self.inst.main()
        with open(path) as f:
            return f.read()

    def root_nodes(self):
        return [n.name() for n in fake_nuke.root().children()]

    def test_find_root(self):
        kernel = os.path.join(ROOT, "blinkflare", "kernel", "BlinkFlare.blink")
        for path in (ROOT, ROOT + os.sep, kernel, os.path.join(ROOT, "tests", "harness")):
            self.assertEqual(self.inst.find_root(path), ROOT, path)
        self.assertIsNone(self.inst.find_root(self.tmp.name))

    def test_asks_for_location_when_not_sourced_from_file(self):
        self.inst.__dict__.pop("__file__")
        fake_nuke.filenames_queue.append(os.path.join(ROOT, "blinkflare", "kernel", "BlinkFlare.blink"))
        self.assertEqual(self.inst.locate(), ROOT)
        self.assertIsNone(self.inst.locate())  # cancelled dialog

    def test_healthy_install(self):
        report = self.run_main()
        self.assertIn("ok    basic kernel: compiled at once", report)
        self.assertIn("ok    BlinkFlare kernel: compiled at once", report)
        self.assertIn("Built BlinkFlare1", report)
        self.assertRegex(report, r"ok    element table: \d+ columns \(\d+ values\) match")
        self.assertIn("inputs: 0 src, 1 occlusion, 2 cam, 3 axis, 4 mask (the node shows 5)", report)
        self.assertIn("ok    panel: tabs in order", report)
        group = fake_nuke.root().node("BlinkFlare1")
        self.assertEqual(group["output_mode"].value(), "Composite")  # probe values put back
        from blinkflare import builder, presets
        self.assertEqual(len(builder.element_ids(group)),
                         len(presets.stack(presets.PRESETS["Default"])))  # probe element removed
        self.assertNotIn("More test kernels", report)  # only run when something fails
        self.assertEqual(set(fake_nuke.compile_gpu), {False})  # test compiles are CPU-only
        self.assertNotIn("PROBLEM", report)
        self.assertIn("installed and working", fake_nuke.messages[-1])
        self.assertEqual(self.root_nodes(), ["BlinkFlare1"])  # sandbox removed, node kept
        with open(self.init_py) as f:
            self.assertIn('nuke.pluginAddPath(r"%s")' % ROOT.replace("\\", "/"), f.read())
        self.assertIsNotNone(fake_nuke.menu("Nodes").findItem("Draw/BlinkFlare/BlinkFlare"))
        self.assertIsNotNone(fake_nuke.menu("Nodes").findItem("Draw/BlinkFlare/Check Install..."))

    def test_element_table_mismatch_is_reported(self):
        fake_nuke.EXPRESSION_CHANNELS[:] = [3, 1, 2, 0]  # as if expr0 wrote alpha
        report = self.run_main()
        self.assertIn("The element table Nuke computes differs", report)
        self.assertRegex(report, r"column \d+ row \d+ (red|alpha): Nuke")
        self.assertNotIn("TableProbe", [n.name() for n in fake_nuke.root().node("BlinkFlare1").children()])
        self.assertIn("found problems", fake_nuke.messages[-1])

    def test_panel_problems_are_reported(self):
        self.run_main()
        group = fake_nuke.root().node("BlinkFlare1")
        builder = sys.modules["blinkflare.builder"]  # the copy the installer imported
        fake_nuke.REMOVE_KNOB_RESETS[0] = True

        def exit_without_restoring(lift, *exc):  # as if values and links couldn't be restored
            for knob in lift.knobs:
                lift.node.addKnob(knob)
            return False
        rep = self.inst.Report(os.path.join(self.tmp.name, "panel.txt"))
        with mock.patch.object(builder._AfterElementsLifted, "__exit__", exit_without_restoring):
            self.inst.check_panel(rep, group)
        text = rep.text()
        self.assertIn("Adding an element upset the panel: output_mode changed", text)
        self.assertIn("operation lost its link", text)
        self.assertEqual(group["output_mode"].value(), "Composite")  # put back afterwards

    def test_misnumbered_inputs_are_reported(self):
        self.run_main()
        group = fake_nuke.root().node("BlinkFlare1")
        group.node("axis")["number"].setValue(4)
        group.node("mask")["number"].setValue(3)
        rep = self.inst.Report(os.path.join(self.tmp.name, "inputs.txt"))
        self.inst.check_inputs(rep, group)
        self.assertIn("inputs: 0 src, 1 occlusion, 2 cam, 3 mask, 4 axis", rep.text())
        self.assertRegex(rep.problems[0], r"inputs aren't numbered as expected \((axis, mask|mask, axis)\)")

    def test_diagnose_entry_point_runs_installer(self):
        sys.path.insert(0, ROOT)
        import blinkflare
        with contextlib.redirect_stdout(io.StringIO()):
            blinkflare.diagnose()
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, ".nuke", "blinkflare_report.txt")))

    def test_init_py_not_duplicated(self):
        os.makedirs(os.path.dirname(self.init_py))
        with open(self.init_py, "w") as f:
            f.write('nuke.pluginAddPath("%s")\n' % ROOT)
        report = self.run_main()
        self.assertIn("already adds this folder", report)
        with open(self.init_py) as f:
            self.assertEqual(f.read().count("pluginAddPath"), 1)

    def test_decline_node_and_init(self):
        fake_nuke.asks_queue.extend([False, False])
        report = self.run_main()
        self.assertEqual(self.root_nodes(), [])
        self.assertIn("unchanged", report)
        self.assertFalse(os.path.exists(self.init_py))

    def test_deferred_compile_is_waited_for_and_timed(self):
        fake_nuke.BLINK_ASYNC_POLLS = 12
        report = self.run_main()
        self.assertRegex(report, r"ok    basic kernel: compiled after \d+\.\ds")
        self.assertIn("Built BlinkFlare1", report)

    def test_rejected_kernel_gives_up_quickly(self):
        fake_nuke.BLINK_REJECT.append("lfWrapPi(ang - a)")
        report = self.run_main()
        self.assertIn("Nuke rejected the kernel", report)
        self.assertLess(self.clock.now - 1000.0, 3 * 60)  # not a 5-minute timeout per compile

    def test_compile_that_never_finishes_times_out(self):
        fake_nuke.BLINK_COMPILE_ON = set()
        report = self.run_main()
        self.assertIn("FAIL  basic kernel: no parameter knobs after 120s", report)
        self.assertIn("did not compile: no parameter knobs after 300s", report)

    def test_report_survives_a_crashing_step(self):
        original = self.inst.Diagnostic.step_kernel
        self.inst.Diagnostic.step_kernel = lambda self_: 1 / 0
        try:
            report = self.run_main()
        finally:
            self.inst.Diagnostic.step_kernel = original
        self.assertIn("A check failed unexpectedly", report)
        self.assertIn("ZeroDivisionError", report)
        self.assertIn("Report saved to", report)
        self.assertNotIn("BlinkFlare_check", self.root_nodes())

    def test_cancel_stops_the_checks(self):
        fake_nuke.BLINK_REJECT.append("lfWrapPi(ang - a)")
        original = self.inst.import_package

        def import_then_cancel(rep, root):
            package = original(rep, root)  # the installer imports a fresh copy
            package[2].Progress.cancelled = lambda _self: True
            return package
        self.inst.import_package = import_then_cancel
        report = self.run_main()
        self.assertIn("Cancelled.", report)
        self.assertNotIn("checking functions one by one", report)

    def test_unprefixed_param_knobs(self):
        fake_nuke.BLINK_PREFIX = ""
        report = self.run_main()
        self.assertIn("param knob 'probeGain'", report)
        self.assertIn("Built BlinkFlare1", report)

    def test_rejected_function_is_found(self):
        fake_nuke.BLINK_REJECT.append("lfWrapPi(ang - a)")
        report = self.run_main()
        self.assertIn("rejects these kernel functions: spectral", report)
        self.assertIn("spectral starts at line", report)
        self.assertNotIn("Built BlinkFlare", report)
        self.assertEqual(self.root_nodes(), [])
        self.assertIn("found problems", fake_nuke.messages[-1])

    def test_rejected_declaration_is_found(self):
        fake_nuke.BLINK_REJECT.append("float  lightDepth;")
        report = self.run_main()
        self.assertIn("even with every function emptied", report)

    def test_python_compile_unavailable(self):
        fake_nuke.BLINK_COMPILE_ON = set()
        fake_nuke.env["nukex"] = False
        report = self.run_main()
        self.assertIn("FAIL  basic kernel", report)
        self.assertIn("doesn't look like NukeX", report)

    def test_other_copy_already_loaded(self):
        fake_pkg = type(sys)("blinkflare")
        fake_pkg.__file__ = os.path.join(self.tmp.name, "old", "blinkflare", "__init__.py")
        saved = sys.modules.get("blinkflare")
        sys.modules["blinkflare"] = fake_pkg
        try:
            report = self.run_main()
        finally:
            if saved is not None:
                sys.modules["blinkflare"] = saved
        self.assertIn("different BlinkFlare copy", report)


class NoBlockingCalls(unittest.TestCase):
    """Waiting on a compile by blocking Nuke's main thread is what froze it."""

    def test_no_blocking_waits(self):
        files = [os.path.join(ROOT, "install_blinkflare.py")]
        files += [os.path.join(ROOT, "blinkflare", f) for f in os.listdir(os.path.join(ROOT, "blinkflare"))
                  if f.endswith(".py")]
        for path in files:
            with open(path) as f:
                code = "\n".join(line.split("#")[0] for line in f.read().splitlines())
            for call in ("forceValidate(", "processEvents(", "time.sleep(", "nuke.ProgressTask("):
                self.assertNotIn(call, code, "%s calls %s" % (path, call))


class StubbedKernelsCompile(unittest.TestCase):
    """The emptied-out kernels the bisect compiles must themselves be valid."""

    def test_stubbed_variants_compile_with_the_shim(self):
        inst = load_installer()
        source = kernel_parse.source()
        variants = [inst.stub_functions(source, ())]
        variants += [inst.stub_functions(source, (name,)) for name in ("process", "spectral", "lensGhost", "init")]
        with tempfile.TemporaryDirectory() as tmp:
            for i, text in enumerate(variants):
                path = os.path.join(tmp, "v%d.blink" % i)
                with open(path, "w") as f:
                    f.write(text)
                cmd = ["g++", "-std=c++17", "-fsyntax-only", "-Wall", "-Wdouble-promotion",
                       "-Wfloat-conversion", "-Werror", "-Wno-unused-parameter", "-Wno-unused-variable",
                       "-I", HARNESS, '-DKERNEL_SOURCE="%s"' % path, "-DKERNEL_CLASS=BlinkFlareKernel",
                       os.path.join(HARNESS, "kernel_tu.cpp")]
                res = subprocess.run(cmd, capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, res.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
