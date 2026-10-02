"""Runs the Nuke builder against the fake nuke module."""
import json
import os
import sys
import tempfile
import unittest

import kernel_parse  # noqa: F401  (sets sys.path)
import fake_nuke
import nuke_expr

sys.modules["nuke"] = fake_nuke

from blinkflare import builder, camera, presets, spec  # noqa: E402

CHANNEL_NAMES = {
    fake_nuke.XY_Knob: "xy", fake_nuke.XYZ_Knob: "xyz", fake_nuke.Color_Knob: "rgb",
    fake_nuke.AColor_Knob: "rgba",
}


class BuilderBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ)
        os.environ["BLINKFLARE_PRESET_SAVE_DIR"] = os.path.join(self.tmp.name, "personal")
        os.environ["BLINKFLARE_PRESET_PATH"] = ""
        fake_nuke.reset(fake_nuke.Format(2048, 858, 2.0))
        self.group = builder.create()
        self.blink = self.group.child(spec.KERNEL_NODE)
        self.proj = self.group.child(camera.PROJECTION_NODE)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def fire(self, knob_name, knob=None):
        """Run the group's knobChanged callback as Nuke would."""
        knob = knob or self.group.knob(knob_name) or fake_nuke.Knob(knob_name)
        fake_nuke.run_script(self.group["knobChanged"].value(), self.group, knob)


class GraphTest(BuilderBase):
    def test_group_named_and_selected(self):
        self.assertEqual(self.group.name(), "BlinkFlare1")
        self.assertEqual(fake_nuke.selectedNodes(), [self.group])
        self.assertEqual(builder.create().name(), "BlinkFlare2")

    def test_inputs(self):
        inputs = [c.name() for c in self.group.children() if c.Class() == "Input"]
        self.assertEqual(inputs, ["src", "occlusion", "dirt", "cam", "axis", "mask"])
        self.assertEqual(inputs.index("cam"), camera.CAMERA_INPUT)
        self.assertEqual(inputs.index("axis"), camera.AXIS_INPUT)
        self.assertEqual(inputs.index("mask"), camera.MASK_INPUT)

    def test_kernel_wired(self):
        self.assertEqual(self.blink.maxInputs(), 3)
        self.assertEqual([self.blink.input(i).name() for i in range(3)],
                         ["Canvas", "OcclusionDepth", "DirtFit"])
        occ = self.group.child("OcclusionDepth")
        self.assertEqual((occ["from0"].value(), occ["to0"].value()), ("depth.Z", "rgba.red"))
        mb = self.group.child(spec.MOTION_BLUR_NODE)
        self.assertIs(mb.input(0), self.blink)
        merge = self.group.child(spec.MERGE_NODE)
        self.assertEqual(merge.input(0).name(), "src")
        self.assertIs(merge.input(1), mb)
        self.assertIsNone(merge.input(2))
        self.assertEqual(merge["operation"].value(), "plus")
        switch = self.group.child(spec.SWITCH_NODE)
        self.assertEqual([switch.input(0), switch.input(1)], [merge, mb])

    def test_3d_pass_through_nodes(self):
        self.assertEqual(self.group.child(camera.CAMERA_XFORM).input(0).name(), "cam")
        self.assertEqual(self.group.child(camera.LIGHT_XFORM).input(0).name(), "axis")
        self.assertEqual(self.proj.input(0).name(), "Canvas")
        for name, _, _, default in camera.LENS_KNOBS:
            self.assertEqual(self.proj[name].value(), default)
            self.assertEqual(self.proj[name].expressions, {})
        self.assertEqual(self.proj["screen"].expressions[2], "0")

    def test_switches_driven_by_knobs(self):
        self.assertEqual(self.group.child("Canvas")["disable"].expressions[0], "parent.render_region")
        mb = self.group.child(spec.MOTION_BLUR_NODE)
        self.assertEqual(mb["disable"].expressions[0], "1 - parent.motion_blur")
        self.assertEqual(mb["shutteroffset"].value(), "centred")

    def test_all_kernel_params_have_expressions(self):
        for param in kernel_parse.declared_params():
            knob = builder.param_knob(self.blink, param)
            self.assertEqual(sorted(knob.expressions), list(range(knob.channels)), param)

    def test_every_expression_reference_resolves(self):
        check_references(self, self.group)
        builder.build_element_layers(self.group)
        check_references(self, self.group)

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
        links = [k.name for k in spec.KNOBS if k.kind == "link"]
        for name in links:
            node_name, knob_name = self.group[name].link.split(".")
            self.assertIsNotNone(self.group.child(node_name).knob(knob_name), name)

    def test_missing_link_target_is_skipped(self):
        fake_nuke.reset()
        del fake_nuke.CLASS_KNOBS["Merge2"]["invert_mask"]
        try:
            group = builder.create()
        finally:
            fake_nuke.CLASS_KNOBS["Merge2"]["invert_mask"] = fake_nuke.Boolean_Knob
        self.assertIsNone(group.knob("invert_mask"))

    def test_newline_flags(self):
        self.assertNotIn(fake_nuke.STARTLINE, self.group["center_light"].flags)
        self.assertIn(fake_nuke.STARTLINE, self.group["glow_enable"].flags)

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
        path = builder.save_toolset(os.path.join(self.tmp.name, "ToolSets", "BlinkFlare.nk"))
        self.assertTrue(os.path.exists(path))
        self.assertNotIn("BlinkFlare2", [n.name() for n in fake_nuke.root().children()])


class CameraLinkTest(BuilderBase):
    def connect_camera(self, cam, via_dot=True):
        src = cam
        if via_dot:
            src = fake_nuke.nodes.Dot(inputs=[cam])
        self.group.setInput(camera.CAMERA_INPUT, src)

    def test_input_change_links_lens_knobs_through_dots(self):
        cam = fake_nuke.nodes.Camera2(name="ShotCam")
        self.connect_camera(cam)
        self.fire("inputChange")
        for name, src, channel, _ in camera.LENS_KNOBS:
            expected = "root.ShotCam." + src + ("" if channel is None else ".%d" % channel)
            self.assertEqual(self.proj[name].expressions.get(0), expected, name)

    def test_repeat_events_change_nothing(self):
        self.connect_camera(fake_nuke.nodes.Camera2(name="ShotCam"))
        self.fire("inputChange")
        counts = [getattr(self.proj[name], "set_count", 0) for name, _, _, _ in camera.LENS_KNOBS]
        self.fire("showPanel")
        self.fire("inputChange")
        self.assertEqual(counts, [getattr(self.proj[name], "set_count", 0)
                                  for name, _, _, _ in camera.LENS_KNOBS])

    def test_camera_inside_another_group(self):
        outer = fake_nuke.nodes.Group(name="CamRig")
        with outer:
            cam = fake_nuke.nodes.Camera2(name="Cam")
        self.connect_camera(cam, via_dot=False)
        self.fire("inputChange")
        self.assertEqual(self.proj["focal"].expressions[0], "root.CamRig.Cam.focal")

    def test_disconnect_restores_defaults(self):
        self.connect_camera(fake_nuke.nodes.Camera2())
        self.fire("inputChange")
        self.group.setInput(camera.CAMERA_INPUT, None)
        self.fire("inputChange")
        for name, _, _, default in camera.LENS_KNOBS:
            self.assertEqual(self.proj[name].expressions, {})
            self.assertEqual(self.proj[name].value(), default)

    def test_non_camera_is_ignored(self):
        self.connect_camera(fake_nuke.nodes.Axis2())
        self.fire("inputChange")
        self.assertEqual(self.proj["focal"].expressions, {})

    def test_refresh_button(self):
        self.connect_camera(fake_nuke.nodes.Camera2(name="ShotCam"))
        fake_nuke.run_script(self.group["refresh_links"].script, self.group)
        self.assertEqual(self.proj["focal"].expressions[0], "root.ShotCam.focal")

    def test_other_knob_changes_do_nothing(self):
        self.connect_camera(fake_nuke.nodes.Camera2(name="ShotCam"))
        self.fire("glow_intensity")
        self.assertEqual(self.proj["focal"].expressions, {})

    def test_mask_connected_only_when_used(self):
        merge = self.group.child(spec.MERGE_NODE)
        self.group.setInput(camera.MASK_INPUT, fake_nuke.nodes.Dot())
        self.fire("inputChange")
        self.assertEqual(merge.input(2).name(), "mask")
        self.group.setInput(camera.MASK_INPUT, None)
        self.fire("inputChange")
        self.assertIsNone(merge.input(2))


class BakeTest(BuilderBase):
    def setUp(self):
        BuilderBase.setUp(self)
        # Stand-in for Nuke evaluating the projection expressions per frame.
        self.proj["screen"].getValueAt = lambda f, c=0: f * 10.0 + c
        self.proj["lens_center"].getValueAt = lambda f, c=0: 500.0 + f + c

    def test_bake_light_and_lens_center(self):
        self.group["light_source"].setValue(1)
        self.group["articulation_mode"].setValue(2)
        builder.bake_to_2d(self.group, range(1, 4))
        keys = self.group["light_pos"].keys
        self.assertEqual(keys[0], {1: 10.0, 2: 20.0, 3: 30.0})
        self.assertEqual(keys[1], {1: 11.0, 2: 21.0, 3: 31.0})
        self.assertEqual(self.group["axis_center"].keys[0], {1: 501.0, 2: 502.0, 3: 503.0})
        self.assertEqual(self.group["light_source"].getValue(), 0)
        self.assertEqual(self.group["articulation_mode"].getValue(), 0)

    def test_bake_prompts_for_range(self):
        self.group["light_source"].setValue(1)
        fake_nuke.inputs_queue.append("1001-1002")
        builder.bake_to_2d(self.group)
        self.assertEqual(sorted(self.group["light_pos"].keys[0]), [1001, 1002])
        self.assertEqual(self.group["axis_center"].keys, {})

    def test_bake_needs_3d(self):
        builder.bake_to_2d(self.group, range(1, 3))
        self.assertEqual(self.group["light_pos"].keys, {})
        self.assertTrue(fake_nuke.messages)


class ElementLayersTest(BuilderBase):
    def test_toggle_builds_solo_kernels_and_copies(self):
        self.group["element_layers"].setValue(True)
        self.fire("element_layers")
        mb = self.group.child(spec.MOTION_BLUR_NODE)
        switch = self.group.child(spec.SWITCH_NODE)
        prev_stream, prev_comp = self.blink, self.group.child(spec.MERGE_NODE)
        for index, name, layer in spec.ELEMENTS:
            inst = self.group.child("%s_%s" % (spec.KERNEL_NODE, name))
            solo = builder.param_knob(inst, "soloElement")
            self.assertEqual((solo.value(), solo.expressions), (index, {}))
            self.assertEqual(inst["disable"].expressions[0], "1 - parent.element_layers")
            self.assertEqual([inst.input(i).name() for i in range(3)],
                             ["Canvas", "OcclusionDepth", "DirtFit"])
            stream = self.group.child("Layer_" + name)
            comp = self.group.child("CompLayer_" + name)
            self.assertEqual([stream.input(0), stream.input(1)], [prev_stream, inst])
            self.assertEqual([comp.input(0), comp.input(1)], [prev_comp, mb])
            self.assertEqual(stream["to3"].value(), layer + ".alpha")
            self.assertEqual(comp["from0"].value(), layer + ".red")
            self.assertIn(layer, fake_nuke.layers())
            prev_stream, prev_comp = stream, comp
        self.assertIs(mb.input(0), prev_stream)
        self.assertIs(switch.input(0), prev_comp)

    def test_build_is_idempotent(self):
        builder.build_element_layers(self.group)
        count = len(self.group.children())
        builder.build_element_layers(self.group)
        self.assertEqual(len(self.group.children()), count)

    def test_switching_off_does_not_build(self):
        self.fire("element_layers")
        self.assertIsNone(self.group.child(spec.KERNEL_NODE + "_glow"))


class PresetTest(BuilderBase):
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
        self.assertEqual(self.group["glint_count"].value(),
                         presets.PRESETS["Sun Starburst"]["glint_count"])

    def test_save_then_apply(self):
        self.group["ghost_count"].setValue(23)
        self.group["glow_color"].setValue([0.1, 0.2, 0.3])
        self.group["light_source"].setValue(1)
        fake_nuke.inputs_queue.append("Show Look")
        path = builder.save_preset(self.group)
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["name"], "Show Look")
        self.assertEqual(data["knobs"]["ghost_count"], 23)
        self.assertNotIn("light_source", data["knobs"])
        self.assertIn("Show Look", self.group["preset"].items)
        self.assertEqual(self.group["preset"].value(), "Show Look")

        builder.apply_preset(self.group, "Default")
        builder.apply_preset(self.group, "Show Look")
        self.assertEqual(self.group["ghost_count"].value(), 23)
        self.assertEqual(self.group["glow_color"].values, [0.1, 0.2, 0.3])

    def test_cannot_overwrite_builtin(self):
        with self.assertRaises(ValueError):
            builder.save_preset(self.group, "Default")

    def test_show_panel_refreshes_menu(self):
        presets.write_preset("From Disk", {"ghost_count": 3})
        self.fire("showPanel")
        self.assertIn("From Disk", self.group["preset"].items)


def check_references(test, group):
    """Every knob reference in every expression in ``group`` must resolve."""
    nodes = dict((c.name(), c) for c in group.children())
    for node in [group] + group.children():
        for knob_name, knob in node.knobs().items():
            for expr in knob.expressions.values():
                for ref in nuke_expr.references(expr):
                    parts = ref.split(".")
                    where = "%s.%s: %s" % (node.name(), knob_name, ref)
                    if parts[0] == "input":
                        test.assertIn(parts[1:], (["width"], ["height"]), where)
                        continue
                    if parts[0] == "parent":
                        target, rest = group, parts[1:]
                    elif parts[0] in nodes and node is not group:
                        target, rest = nodes[parts[0]], parts[1:]
                    else:
                        target, rest = node, parts
                    if rest in (["width"], ["height"]):
                        continue
                    k = target.knob(rest[0])
                    test.assertIsNotNone(k, where)
                    if len(rest) > 1:
                        names = CHANNEL_NAMES.get(type(k), "")
                        ok = rest[1] in names or (rest[1].isdigit() and int(rest[1]) < k.channels)
                        test.assertTrue(ok, where)
                        test.assertEqual(len(rest), 2, where)


if __name__ == "__main__":
    unittest.main()
