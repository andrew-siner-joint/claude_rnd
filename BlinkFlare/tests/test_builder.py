"""Runs the Nuke builder against the fake nuke module."""
import os
import sys
import tempfile
import unittest

import kernel_parse  # noqa: F401  (sets sys.path)
import fake_nuke

sys.modules["nuke"] = fake_nuke

from blinkflare import builder, presets, spec  # noqa: E402


class BuilderTest(unittest.TestCase):
    def setUp(self):
        fake_nuke.reset(fake_nuke.Format(2048, 858, 2.0))
        self.group = builder.create()
        self.blink = self.group.child(spec.KERNEL_NODE)

    def test_group_named_and_selected(self):
        self.assertEqual(self.group.name(), "BlinkFlare1")
        self.assertEqual(fake_nuke.selectedNodes(), [self.group])
        second = builder.create()
        self.assertEqual(second.name(), "BlinkFlare2")

    def test_inputs(self):
        inputs = [c.name() for c in self.group.children() if c.Class() == "Input"]
        self.assertEqual(inputs, ["src", "occlusion", "dirt"])
        self.assertEqual(self.group.maxInputs(), 3)

    def test_kernel_wired(self):
        self.assertEqual(self.blink.maxInputs(), 3)
        self.assertEqual(self.blink.input(0).name(), "Canvas")
        self.assertEqual(self.blink.input(1).name(), "OcclusionChannels")
        self.assertEqual(self.blink.input(2).name(), "DirtFit")
        merge = self.group.child(spec.MERGE_NODE)
        self.assertEqual(merge.input(0).name(), "src")
        self.assertIs(merge.input(1), self.blink)
        self.assertEqual(merge["operation"].value(), "plus")
        switch = self.group.child(spec.SWITCH_NODE)
        self.assertEqual(switch["which"].expressions[0], "parent.output_mode")

    def test_all_kernel_params_have_expressions(self):
        for param, ptype in kernel_parse.declared_params().items():
            knob = builder.param_knob(self.blink, param)
            self.assertEqual(sorted(knob.expressions), list(range(knob.channels)), param)

    def test_group_knobs_in_spec_order_with_defaults(self):
        expected = [k.name for k in spec.KNOBS]
        self.assertEqual(self.group.user_knob_order, expected)
        for k in spec.value_knobs():
            if k.kind in ("double", "int", "bool") and k.name != "pixel_aspect":
                self.assertEqual(self.group[k.name].value(), k.default, k.name)

    def test_format_dependent_defaults(self):
        self.assertEqual(self.group["light_pos"].values, [2048 * 0.7, 858 * 0.7])
        self.assertEqual(self.group["axis_center"].values, [1024.0, 429.0])
        self.assertEqual(self.group["pixel_aspect"].value(), 2.0)

    def test_link_knobs_point_at_real_knobs(self):
        for name in ("operation", "use_gpu", "vectorize"):
            target = self.group[name].link
            node_name, knob_name = target.split(".")
            self.assertIsNotNone(self.group.child(node_name).knob(knob_name), target)

    def test_newline_flags(self):
        self.assertNotIn(fake_nuke.STARTLINE, self.group["center_light"].flags)
        self.assertIn(fake_nuke.STARTLINE, self.group["glow_enable"].flags)

    def test_apply_preset_resets_look_only(self):
        self.group["light_pos"].setValue([10.0, 20.0])
        self.group["ghost_count"].setValue(40)
        self.group["streak_intensity"].animated = True
        builder.apply_preset(self.group, "Anamorphic Blue")
        p = presets.PRESETS["Anamorphic Blue"]
        self.assertEqual(self.group["light_pos"].values, [10.0, 20.0])
        self.assertEqual(self.group["ghost_count"].value(), p["ghost_count"])
        self.assertEqual(self.group["streak_intensity"].value(), p["streak_intensity"])
        self.assertFalse(self.group["streak_intensity"].isAnimated())
        self.assertEqual(self.group["streak_color"].values, list(p["streak_color"]))
        builder.apply_preset(self.group, "Default")
        self.assertEqual(self.group["ghost_count"].value(), spec.knob("ghost_count").default)
        self.assertEqual(fake_nuke.Undo.log[-1], ("end", ""))

    def test_apply_preset_from_menu(self):
        self.group["preset"].setValue("Sun Starburst")
        builder.apply_preset(self.group)
        self.assertEqual(self.group["glint_count"].value(), presets.PRESETS["Sun Starburst"]["glint_count"])

    def test_failed_compile_cleans_up(self):
        original = builder.compile_kernel

        def broken(blink):
            raise builder.BuildError("no NukeX")
        builder.compile_kernel = broken
        try:
            before = len(fake_nuke.root().children())
            with self.assertRaises(builder.BuildError):
                builder.create()
            self.assertEqual(len(fake_nuke.root().children()), before)
        finally:
            builder.compile_kernel = original

    def test_save_toolset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = builder.save_toolset(os.path.join(tmp, "ToolSets", "BlinkFlare.nk"))
            self.assertTrue(os.path.exists(path))
        self.assertNotIn("BlinkFlare2", [n.name() for n in fake_nuke.root().children()])


if __name__ == "__main__":
    unittest.main()
