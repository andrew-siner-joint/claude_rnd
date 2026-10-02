"""install_blinkflare.py, run against the fake nuke module."""
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest

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


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ)
        os.environ["HOME"] = self.tmp.name
        os.environ["BLINKFLARE_PRESET_PATH"] = ""
        os.environ["BLINKFLARE_PRESET_SAVE_DIR"] = os.path.join(self.tmp.name, "presets")
        fake_nuke.reset()
        self.inst = load_installer()
        self.init_py = os.path.join(self.tmp.name, ".nuke", "init.py")

    def tearDown(self):
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
        self.assertIn("ok    basic kernel", report)
        self.assertIn("Compiled via 'recompile'", report)
        self.assertIn("Built BlinkFlare1", report)
        self.assertNotIn("PROBLEM", report)
        self.assertIn("installed and working", fake_nuke.messages[-1])
        self.assertEqual(self.root_nodes(), ["BlinkFlare1"])  # sandbox removed, node kept
        with open(self.init_py) as f:
            self.assertIn('nuke.pluginAddPath(r"%s")' % ROOT.replace("\\", "/"), f.read())
        self.assertIsNotNone(fake_nuke.menu("Nodes").findItem("Draw/BlinkFlare/BlinkFlare"))
        self.assertIsNotNone(fake_nuke.menu("Nodes").findItem("Draw/BlinkFlare/Check Install..."))

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

    def test_deferred_compile_is_reported(self):
        fake_nuke.BLINK_COMPILE_ON = {"validate"}
        report = self.run_main()
        self.assertIn("compiled via 'validate'", report)
        self.assertIn("Built BlinkFlare1", report)

    def test_unprefixed_param_knobs(self):
        fake_nuke.BLINK_PREFIX = ""
        report = self.run_main()
        self.assertIn("param knob named 'probeGain'", report)
        self.assertIn("Built BlinkFlare1", report)

    def test_rejected_function_is_found(self):
        fake_nuke.BLINK_REJECT.append("lfWrapPi(ang - a)")
        report = self.run_main()
        self.assertIn("rejects these kernel functions: spectralTerm", report)
        self.assertIn("spectralTerm starts at line", report)
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


class StubbedKernelsCompile(unittest.TestCase):
    """The emptied-out kernels the bisect compiles must themselves be valid."""

    def test_stubbed_variants_compile_with_the_shim(self):
        inst = load_installer()
        source = kernel_parse.source()
        variants = [inst.stub_functions(source, ())]
        variants += [inst.stub_functions(source, (name,)) for name in ("process", "spectralTerm", "init")]
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
