"""Runs the Nuke builder against the fake nuke module."""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

import kernel_parse  # noqa: F401  (sets sys.path)
import fake_nuke
import nuke_expr

sys.modules["nuke"] = fake_nuke

from blinkflare import builder, camera, compiling, elements, presets, spec  # noqa: E402


class FakeClock(object):
    """Stands in for time: compiling.sleep advances it instantly."""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def use_fake_time():
    clock = FakeClock()
    compiling.clock = clock.time
    compiling.sleep = clock.sleep
    return clock

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
        os.environ["BLINKFLARE_CACHE_DIR"] = os.path.join(self.tmp.name, "cache")
        self.clock = use_fake_time()
        fake_nuke.reset(fake_nuke.Format(2048, 858, 2.0))
        self.errors = []
        self.group = builder.create(on_error=self.errors.append)
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
        self.assertEqual(inputs, ["src", "occlusion", "cam", "axis", "mask"])
        self.assertEqual(self.group.maxInputs(), 5)
        self.assertEqual(inputs.index("cam"), camera.CAMERA_INPUT)
        self.assertEqual(inputs.index("axis"), camera.AXIS_INPUT)
        self.assertEqual(inputs.index("mask"), camera.MASK_INPUT)

    def test_kernel_wired(self):
        self.assertEqual(self.blink.maxInputs(), 3)
        self.assertEqual([self.blink.input(i).name() for i in range(3)],
                         ["Canvas", "OcclusionDepth", "TableRow%d" % (elements.ROWS - 1)])
        chain = [self.group.child("TableRow%d" % r) for r in range(elements.ROWS)]
        self.assertEqual(chain[0].input(0).name(), "TableCrop")
        self.assertEqual(self.group.child("TableCrop").input(0).name(), "TableBase")
        for prev, row in zip(chain, chain[1:]):
            self.assertIs(row.input(0), prev)
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
            if param in spec.BUILDER_PARAMS:
                self.assertEqual(knob.expressions, {}, param)
            else:
                self.assertEqual(sorted(knob.expressions), list(range(knob.channels)), param)

    def test_every_expression_reference_resolves(self):
        check_references(self, self.group)
        builder.build_element_layers(self.group)
        check_references(self, self.group)

    def test_group_knobs_in_spec_order_with_defaults(self):
        static = [k.name for k in spec.KNOBS]
        end = static.index(spec.ELEMENTS_TAB_END) + 1
        before = static[:end] + ["element_ids", "blinkflare_version"]
        order = self.group.user_knob_order
        tabs = [n for n in order if n.startswith("tab_")]
        self.assertEqual(tabs, ["tab_flare", "tab_lens", "tab_elements", "tab_3d", "tab_output"])
        self.assertEqual(order[:len(before)], before)
        # Then the Default preset's elements, then the 3D and Output tabs.
        after = static[end:]
        self.assertEqual(after, spec.AFTER_ELEMENTS)
        self.assertEqual(order[-len(after):], after)
        middle = order[len(before):-len(after)]
        self.assertTrue(middle)
        self.assertTrue(all(n.startswith("e") and n[1].isdigit() for n in middle))
        self.assertFalse(self.group["element_ids"].visible)
        self.assertEqual(self.group["blinkflare_version"].value(), spec.VERSION)
        looks = presets.PRESETS["Default"]["globals"]
        for k in spec.value_knobs():
            if k.kind in ("double", "int", "bool") and k.name != "pixel_aspect":
                self.assertEqual(self.group[k.name].value(), looks.get(k.name, k.default), k.name)

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
        self.assertIn(fake_nuke.STARTLINE, self.group["light_pos"].flags)
        self.assertNotIn(fake_nuke.STARTLINE, self.group["e1_type"].flags)

    def fresh(self):
        """Start over with no cached kernel."""
        fake_nuke.reset()
        for name in os.listdir(compiling.cache_dir()):
            os.remove(os.path.join(compiling.cache_dir(), name))

    def groups(self):
        return [n.name() for n in fake_nuke.root().children() if n.Class() == "Group"]

    def test_kernel_compiles_once_then_is_pasted(self):
        self.assertEqual(fake_nuke.compile_count[0], 1)
        self.assertTrue(os.path.isfile(compiling.template_path(builder.kernel_source())))
        second = builder.create(on_error=self.errors.append)
        self.assertEqual(fake_nuke.compile_count[0], 1)
        self.assertIsNotNone(builder.param_knob_name(second.child(spec.KERNEL_NODE), "lightPos"))
        self.assertEqual(self.errors, [])

    def test_compile_holder_is_removed(self):
        self.assertNotIn("BlinkFlare_compiling", self.groups())

    def test_main_kernel_uses_gpu_after_cpu_only_compile(self):
        self.assertEqual(fake_nuke.compile_gpu, [False])
        self.assertTrue(self.blink["useGPUIfAvailable"].value())

    def test_deferred_compile_is_waited_for(self):
        self.fresh()
        fake_nuke.BLINK_ASYNC_POLLS = 20
        done = []
        group = builder.create(on_done=done.append, on_error=self.errors.append)
        self.assertEqual(done, [group])
        self.assertIsNotNone(group)
        self.assertGreater(self.clock.now, 1.0)  # waited on the timer, not by blocking
        self.assertEqual(self.errors, [])

    def test_load_from_file_fallback(self):
        self.fresh()
        fake_nuke.BLINK_COMPILE_ON = {"file"}
        group = builder.create(on_error=self.errors.append)
        self.assertIsNotNone(group, self.errors)
        self.assertGreaterEqual(self.clock.now, compiling.FILE_FALLBACK_AFTER)

    def test_unprefixed_param_knobs_are_found(self):
        self.fresh()
        fake_nuke.BLINK_PREFIX = ""
        kernel = builder.create(on_error=self.errors.append).child(spec.KERNEL_NODE)
        self.assertEqual(builder.param_knob_name(kernel, "lightPos"), "lightPos")

    def test_rejected_kernel_reports_and_cleans_up(self):
        self.fresh()
        fake_nuke.BLINK_REJECT.append("lfWrapPi")
        self.assertIsNone(builder.create(on_error=self.errors.append))
        msg = self.errors[-1]
        self.assertIn("did not compile: Nuke rejected the kernel", msg)
        self.assertIn("node in error: True", msg)
        self.assertLess(self.clock.now, compiling.ERROR_GRACE + 5)
        self.assertIn("Check Install", msg)
        self.assertIn("Build From Compiled BlinkScript", msg)
        self.assertEqual(self.groups(), [])
        self.assertFalse(os.path.exists(compiling.template_path(builder.kernel_source())))

    def test_compile_failure_mentions_licence(self):
        self.fresh()
        fake_nuke.env["nukex"] = False
        fake_nuke.BLINK_COMPILE_ON = set()
        builder.create(on_error=self.errors.append)
        self.assertIn("needs NukeX", self.errors[-1])

    def test_holder_deleted_by_user_while_compiling(self):
        self.fresh()
        fake_nuke.BLINK_COMPILE_ON = set()
        original = compiling.later

        def delete_then_wait(ms, fn):
            holder = fake_nuke.toNode("root.BlinkFlare_compiling")
            if holder is not None:
                fake_nuke.delete(holder)
            compiling.later = original
            original(ms, fn)
        compiling.later = delete_then_wait
        try:
            builder.create(on_error=self.errors.append)
        finally:
            compiling.later = original
        self.assertIn("did not compile", self.errors[-1])

    def test_stale_cached_kernel_is_recompiled(self):
        path = compiling.template_path(builder.kernel_source())
        with open(path, "w") as f:
            f.write('[{"class": "BlinkScript", "name": "Old", "knobs": {}, "params": []}]')
        group = builder.create(on_error=self.errors.append)
        self.assertIsNotNone(group, self.errors)
        self.assertEqual(fake_nuke.compile_count[0], 2)

    def test_build_from_hand_compiled_node(self):
        self.fresh()
        node = fake_nuke.nodes.BlinkScript(name="MyKernel")
        node["kernelSource"].setValue(builder.kernel_source())
        node["recompile"].execute()
        group = builder.create_from_kernel(node, on_error=self.errors.append)
        self.assertIsNotNone(group, self.errors)
        self.assertEqual(fake_nuke.compile_count[0], 1)  # only the hand compile
        self.assertTrue(os.path.isfile(compiling.template_path(builder.kernel_source())))

    def test_build_from_wrong_node_explains(self):
        for node in (None, fake_nuke.nodes.Dot(), fake_nuke.nodes.BlinkScript()):
            with self.assertRaises(builder.BuildError):
                builder.create_from_kernel(node)

    def test_build_errors_are_reported_not_raised(self):
        fake_nuke.reset()
        original = builder.add_group_knobs

        def broken(group, fmt):
            raise RuntimeError("boom")
        builder.add_group_knobs = broken
        try:
            self.assertIsNone(builder.create(on_error=self.errors.append))
        finally:
            builder.add_group_knobs = original
        self.assertIn("boom", self.errors[-1])
        self.assertEqual(self.groups(), [])

    def test_save_toolset(self):
        path = builder.save_toolset(os.path.join(self.tmp.name, "ToolSets", "BlinkFlare.nk"))
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            self.assertIn('"class": "Group"', f.read())
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
        for index, name, layer in spec.PASSES:
            name = name.lower()
            inst = self.group.child("%s_%s" % (spec.KERNEL_NODE, name))
            solo = builder.param_knob(inst, "soloPass")
            self.assertEqual((solo.value(), solo.expressions), (index, {}))
            self.assertEqual(inst["disable"].expressions[0], "1 - parent.element_layers")
            self.assertEqual([inst.input(i) for i in range(3)],
                             [self.blink.input(i) for i in range(3)])
            self.assertIsNotNone(inst.input(2))
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

    def test_layer_kernels_follow_the_stack(self):
        builder.build_element_layers(self.group)
        builder.add_element(self.group, "Streak")
        count = builder.param_knob(self.blink, "elementCount").value()
        for _, name, _ in spec.PASSES:
            inst = self.group.child("%s_%s" % (spec.KERNEL_NODE, name.lower()))
            self.assertEqual(builder.param_knob(inst, "elementCount").value(), count, name)


class PresetTest(BuilderBase):
    def types(self):
        return [el["type"] for el in builder.stack(self.group)]

    def test_default_preset_applied_on_create(self):
        want = presets.stack(presets.PRESETS["Default"])
        self.assertEqual(self.types(), [el["type"] for el in want])
        for got, el in zip(builder.stack(self.group), want):
            self.assertEqual(elements.knob_values(got), elements.knob_values(el))

    def test_apply_preset_replaces_look_and_stack(self):
        self.group["light_pos"].setValue([10.0, 20.0])
        self.group["anamorphic"].animated = True
        builder.apply_preset(self.group, "Classic Anamorphic")
        p = presets.PRESETS["Classic Anamorphic"]
        self.assertEqual(self.group["light_pos"].values, [10.0, 20.0])
        self.assertEqual(self.types(), [el["type"] for el in presets.stack(p)])
        self.assertEqual(self.group["anamorphic"].value(), p["globals"]["anamorphic"])
        self.assertFalse(self.group["anamorphic"].isAnimated())
        builder.apply_preset(self.group, "Default")
        self.assertEqual(self.group["anamorphic"].value(), spec.knob("anamorphic").default)
        self.assertEqual(self.types(), [el["type"] for el in presets.stack(presets.PRESETS["Default"])])
        # Element edits stay out of the undo history (Nuke can't undo knob
        # additions, so undoing just the values would break the stack).
        self.assertEqual(fake_nuke.Undo.log[-1], ("enable", ""))
        self.assertFalse(fake_nuke.Undo._disabled[0])
        # Knobs of replaced elements are gone; ids restart.
        n = len(presets.stack(presets.PRESETS["Default"]))
        self.assertEqual(builder.element_ids(self.group), list(range(1, n + 1)))
        self.assertIsNone(self.group.knob("e%d_on" % (n + 1)))

    def test_apply_preset_from_menu(self):
        self.group["preset"].setValue("Golden Hour Sun")
        builder.apply_preset(self.group)
        self.assertEqual(self.types(),
                         [el["type"] for el in presets.stack(presets.PRESETS["Golden Hour Sun"])])

    def test_save_then_apply(self):
        self.group["dust"].setValue(0.77)
        self.group["light_source"].setValue(1)
        builder.clear_elements(self.group)
        builder.add_element(self.group, "Streak", elements.element("Streak", color=(0.1, 0.2, 0.3)))
        builder.add_element(self.group, "Lens System",
                            elements.element("Lens System", fstop=8.0, coating="Uncoated"))
        fake_nuke.inputs_queue.append("Show Look")
        path = builder.save_preset(self.group)
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["name"], "Show Look")
        self.assertEqual(data["globals"]["dust"], 0.77)
        self.assertNotIn("light_source", data["globals"])
        self.assertEqual([e["type"] for e in data["elements"]], ["Streak", "Lens System"])
        self.assertIn("Show Look", self.group["preset"].items)
        self.assertEqual(self.group["preset"].value(), "Show Look")

        builder.apply_preset(self.group, "Default")
        builder.apply_preset(self.group, "Show Look")
        self.assertEqual(self.group["dust"].value(), 0.77)
        self.assertEqual(self.types(), ["Streak", "Lens System"])
        self.assertEqual(self.group["e1_color"].values, [0.1, 0.2, 0.3])
        self.assertEqual(self.group["e2_fstop"].value(), 8.0)
        self.assertEqual(self.group["e2_coating"].value(), "Uncoated")

    def test_cannot_overwrite_builtin(self):
        with self.assertRaises(ValueError):
            builder.save_preset(self.group, "Default")

    def test_show_panel_refreshes_menu(self):
        presets.write_preset("From Disk", {"dust": 0.2}, [elements.element("Glow")])
        self.fire("showPanel")
        self.assertIn("From Disk", self.group["preset"].items)

    def test_show_panel_restores_element_ui(self):
        eid = builder.element_ids(self.group)[0]
        for name, knob in self.group.knobs().items():
            if name.startswith("e%d_" % eid):
                knob.setVisible(True)
                knob.setLabel(name)
        self.fire("showPanel")
        t = elements.TYPES[self.group["e%d_type" % eid].value()]
        self.assertEqual(self.group["e%d_begin" % eid].label, "1. " + t.name)
        self.assertEqual(self.group["e%d_p0" % eid].label, t.params[0].label)
        self.assertFalse(self.group["e%d_p7" % eid].visible)
        self.assertFalse(self.group["e%d_fstop" % eid].visible)

    def test_ids_survive_an_out_of_sync_stack(self):
        # e.g. after an undo restored element_ids but not the removed knobs.
        ids = builder.element_ids(self.group)
        builder.remove_element(self.group, ids[0])
        self.group["element_ids"].setValue(",".join(str(i) for i in ids) + ",x,%d" % ids[1])
        self.assertEqual(builder.element_ids(self.group), ids[1:])
        # Orphaned knobs (id missing from the list) never collide with new ones.
        self.group["element_ids"].setValue(str(ids[1]))
        eid = builder.add_element(self.group, "Glow")
        self.assertGreater(eid, max(ids))
        builder.rebuild_table(self.group)

    def test_older_nodes_are_refused(self):
        self.group.removeKnob(self.group["element_ids"])
        for call in (lambda: builder.apply_preset(self.group, "Default"),
                     lambda: builder.add_element(self.group, "Glow"),
                     lambda: builder.save_preset(self.group, "x")):
            with self.assertRaises(builder.BuildError):
                call()


def evaluate_table(group):
    """Run the TableRow Expression chain over the table's pixels."""
    width = int(group.child("TableCrop")["box"].value(2))
    rows = [group.child("TableRow%d" % r) for r in range(elements.ROWS)]
    columns = []
    for x in range(width):
        column = []
        for y in range(elements.ROWS):
            pixel = [0.0, 0.0, 0.0, 0.0]  # TableBase is black
            for row in rows:
                env = {"x": float(x), "y": float(y)}
                env.update(zip(elements.CHANNEL_VARS, pixel))

                def resolve(path):
                    if path in env:
                        return env[path]
                    parts = path.split(".")
                    assert parts[0] == "parent", path
                    return fake_nuke._knob_number(group[parts[1]], parts[2] if len(parts) > 2 else None)
                pixel = [nuke_expr.evaluate(row["expr%d" % c].value(), resolve) for c in range(4)]
            column.append(tuple(pixel))
        columns.append(column)
    return columns


class ElementStackTest(BuilderBase):
    def assertTableMatches(self, places=9):
        got = evaluate_table(self.group)
        want = elements.table(builder.stack(self.group))
        self.assertEqual(len(got), max(len(want), 1))
        self.assertEqual(builder.param_knob(self.blink, "elementCount").value(), len(want))
        for x, (g, w) in enumerate(zip(got, want)):
            for r in range(elements.ROWS):
                for c in range(4):
                    self.assertAlmostEqual(g[r][c], w[r][c], places=places, msg=(x, r, c))

    def assertPanelOrder(self):
        """Element knobs sit between the Elements tab and the 3D tab."""
        order = self.group.user_knob_order
        tail = [n for n in spec.AFTER_ELEMENTS if self.group.knob(n) is not None]
        self.assertEqual(order[-len(tail):], tail)
        first_after = order.index(tail[0])
        for eid in builder.element_ids(self.group):
            for field, _ in builder._element_knobs(eid):
                name = elements.knob_name(eid, field)
                self.assertGreater(order.index(name), order.index("elements_info"), name)
                self.assertLess(order.index(name), first_after, name)

    def tweak_later_tabs(self):
        """Values, animation and links on the 3D and Output tabs."""
        self.group["output_mode"].setValue(1)
        self.group["solo"].setValue(3)
        self.group["motion_blur"].setValueAt(True, 1001)
        self.group["render_region"].setExpression("frame > 1005")
        return {name: self.group[name].toScript()
                for name in ("output_mode", "solo", "motion_blur", "render_region")}

    def assertLaterTabsKept(self, saved):
        for name, script in saved.items():
            self.assertEqual(self.group[name].toScript(), script, name)
        for k in spec.KNOBS:
            if k.kind == "link" and k.name in spec.AFTER_ELEMENTS:
                knob = self.group.knob(k.name)
                if knob is not None:
                    node_name, knob_name = knob.getLink().split(".")
                    self.assertIsNotNone(self.group.child(node_name).knob(knob_name), k.name)

    def test_new_elements_go_before_the_3d_and_output_tabs(self):
        self.assertPanelOrder()
        saved = self.tweak_later_tabs()
        builder.add_element(self.group, "Streak")
        builder.duplicate_element(self.group, builder.element_ids(self.group)[0])
        self.assertPanelOrder()
        self.assertLaterTabsKept(saved)
        builder.apply_preset(self.group, "Classic Anamorphic")
        self.assertPanelOrder()
        self.assertLaterTabsKept(saved)

    def test_later_tabs_restored_even_if_removing_knobs_loses_state(self):
        saved = self.tweak_later_tabs()
        fake_nuke.REMOVE_KNOB_RESETS[0] = True
        builder.add_element(self.group, "Ring")
        builder.apply_preset(self.group, "Physical 50mm")
        self.assertPanelOrder()
        self.assertLaterTabsKept(saved)

    def test_lift_puts_knobs_back_after_an_error(self):
        before = list(self.group.user_knob_order)
        with self.assertRaises(KeyError):
            with builder._AfterElementsLifted(self.group):
                self.assertIsNone(self.group.knob("tab_3d"))
                raise KeyError("boom")
        self.assertEqual(self.group.user_knob_order, before)

    def test_failed_lift_puts_back_what_came_off(self):
        before = list(self.group.user_knob_order)
        remove = self.group.removeKnob

        def flaky(knob):
            if knob.name() == "solo":
                raise RuntimeError("Nuke said no")
            remove(knob)
        self.group.removeKnob = flaky
        with self.assertRaises(RuntimeError):
            with builder._AfterElementsLifted(self.group):
                self.fail("should not get here")
        self.assertEqual(self.group.user_knob_order, before)

    def test_older_layout_is_reordered_on_first_add(self):
        # 3.0 nodes had Elements as the last tab.
        order = self.group._user_order
        after = [n for n in order if n in spec.AFTER_ELEMENTS]
        rest = [n for n in order if n not in spec.AFTER_ELEMENTS]
        cut = rest.index("tab_elements")
        order[:] = rest[:cut] + after + rest[cut:]
        builder.add_element(self.group, "Glow")
        tabs = [n for n in self.group.user_knob_order if n.startswith("tab_")]
        self.assertEqual(tabs, ["tab_flare", "tab_lens", "tab_elements", "tab_3d", "tab_output"])
        self.assertPanelOrder()

    def test_table_matches_the_stack(self):
        self.assertTableMatches()

    def test_table_follows_knob_edits_without_rebuilding(self):
        self.group["e1_intensity"].setValue(1.75)
        self.group["e2_color"].setValue([0.2, 0.4, 0.6])
        self.group["e3_p2"].setValue(4.5)
        self.group["e4_on"].setValue(False)
        self.group["e5_layer"].setValue(elements.PASS_CODES["Other"])
        self.assertTableMatches()

    def test_lens_system_table(self):
        builder.add_element(self.group, "Lens System",
                            elements.element("Lens System", fstop=4.0, max_ghosts=6))
        self.assertTableMatches()
        eid = builder.element_ids(self.group)[-1]
        # Live: size and brightness knobs drive the baked ghosts directly.
        self.group["e%d_size" % eid].setValue(1.3)
        self.group["e%d_dispersion" % eid].setValue(0.25)
        self.group["e%d_color" % eid].setValue([1.0, 0.5, 0.2])
        self.assertTableMatches()

    def test_empty_stack(self):
        builder.clear_elements(self.group)
        self.assertEqual(builder.element_ids(self.group), [])
        self.assertEqual(builder.param_knob(self.blink, "elementCount").value(), 0)
        self.assertTableMatches()
        self.assertEqual([k for k in self.group.knobs() if k[:1] == "e" and k[1:2].isdigit()], [])

    def test_add_element(self):
        before = builder.element_ids(self.group)
        eid = builder.add_element(self.group, "Streak")
        self.assertEqual(builder.element_ids(self.group), before + [eid])
        self.assertEqual(eid, max(before) + 1)
        t = elements.TYPES["Streak"]
        self.assertEqual(self.group["e%d_begin" % eid].label, "%d. Streak" % len(before + [eid]))
        self.assertEqual(self.group["e%d_begin" % eid].flag, fake_nuke.TABBEGINCLOSEDGROUP)
        self.assertEqual(self.group["e%d_end" % eid].flag, fake_nuke.TABENDGROUP)
        self.assertEqual(self.group["e%d_size" % eid].label, "Length")
        for i in range(8):
            knob = self.group["e%d_p%d" % (eid, i)]
            self.assertEqual(knob.visible, i < len(t.params), i)
            if i < len(t.params):
                self.assertEqual(knob.label, t.params[i].label)
                self.assertEqual(knob.value(), t.params[i].default)
                self.assertEqual(knob.range, (t.params[i].lo, t.params[i].hi))
        self.assertFalse(self.group["e%d_softness" % eid].visible)
        for field in elements.LENS_FIELDS:
            self.assertFalse(self.group["e%d_%s" % (eid, field)].visible, field)
        self.assertEqual(elements.knob_values(builder.element_values(self.group, eid)),
                         elements.knob_values(elements.element("Streak")))
        self.assertTableMatches()

    def test_add_from_the_panel(self):
        self.group["add_type"].setValue("Caustic")
        fake_nuke.run_script(self.group["add_element"].script, self.group)
        self.assertEqual(builder.stack(self.group)[-1]["type"], "Caustic")

    def test_remove_element_renumbers_headers(self):
        ids = builder.element_ids(self.group)
        builder.remove_element(self.group, ids[1])
        self.assertEqual(builder.element_ids(self.group), [ids[0]] + ids[2:])
        self.assertIsNone(self.group.knob("e%d_on" % ids[1]))
        third = self.group["e%d_begin" % ids[2]].label
        self.assertTrue(third.startswith("2. "), third)
        self.assertTableMatches()

    def test_delete_button(self):
        eid = builder.element_ids(self.group)[0]
        fake_nuke.run_script(self.group["e%d_del" % eid].script, self.group)
        self.assertNotIn(eid, builder.element_ids(self.group))

    def test_duplicate_copies_values_and_animation(self):
        eid = builder.element_ids(self.group)[2]
        self.group["e%d_intensity" % eid].setValueAt(0.5, 1)
        self.group["e%d_intensity" % eid].setValueAt(2.0, 10)
        self.group["e%d_p1" % eid].setExpression("frame / 10")
        new = builder.duplicate_element(self.group, eid)
        self.assertEqual(builder.element_ids(self.group)[-1], new)
        self.assertEqual(elements.knob_values(builder.element_values(self.group, new)),
                         elements.knob_values(builder.element_values(self.group, eid)))
        self.assertEqual(self.group["e%d_intensity" % new].keys, {0: {1.0: 0.5, 10.0: 2.0}})
        self.assertEqual(self.group["e%d_p1" % new].expressions, {0: "frame / 10"})
        self.assertTableMatches()

    def test_duplicate_button(self):
        eid = builder.element_ids(self.group)[0]
        count = len(builder.element_ids(self.group))
        fake_nuke.run_script(self.group["e%d_dup" % eid].script, self.group)
        self.assertEqual(len(builder.element_ids(self.group)), count + 1)

    def test_type_change_resets_values_and_relabels(self):
        eid = builder.element_ids(self.group)[0]
        self.group["e%d_p0" % eid].setValueAt(3.0, 5)
        self.group["e%d_layer" % eid].setValue(3)
        self.group["e%d_type" % eid].setValue("Ring")
        self.fire("e%d_type" % eid)
        ring = elements.element("Ring", layer=3)
        self.assertEqual(elements.knob_values(builder.element_values(self.group, eid)),
                         elements.knob_values(ring))
        self.assertFalse(self.group["e%d_p0" % eid].isAnimated())
        self.assertEqual(self.group["e%d_p0" % eid].label, "Thickness")
        self.assertEqual(self.group["e%d_size" % eid].label, "Radius")
        self.assertFalse(self.group["e%d_p2" % eid].visible)
        self.assertTrue(self.group["e%d_begin" % eid].label.endswith("Ring"))
        self.assertTableMatches()

    def test_change_to_and_from_lens_system(self):
        eid = builder.element_ids(self.group)[0]
        self.group["e%d_type" % eid].setValue("Lens System")
        self.fire("e%d_type" % eid)
        self.assertTrue(self.group["e%d_fstop" % eid].visible)
        self.assertGreater(builder.param_knob(self.blink, "elementCount").value(),
                           len(builder.element_ids(self.group)))
        self.assertTableMatches()
        self.group["e%d_type" % eid].setValue("Glow")
        self.fire("e%d_type" % eid)
        self.assertFalse(self.group["e%d_fstop" % eid].visible)
        self.assertTableMatches()

    def test_lens_changes_rebake(self):
        builder.clear_elements(self.group)
        eid = builder.add_element(self.group, "Lens System",
                                  elements.element("Lens System", max_ghosts=10))
        count = builder.param_knob(self.blink, "elementCount")
        self.assertEqual(count.value(), 10)
        self.group["e%d_max_ghosts" % eid].setValue(4)
        self.fire("e%d_max_ghosts" % eid)
        self.assertEqual(count.value(), 4)
        before = self.group.child("TableRow0")["expr2"].value()
        self.group["e%d_lens" % eid].setValue("Cooke Triplet 50mm")
        self.fire("e%d_lens" % eid)
        self.assertNotEqual(self.group.child("TableRow0")["expr2"].value(), before)
        self.assertTableMatches()
        # Other knobs are read live by the expressions: no rebuild needed.
        before = self.group.child("TableRow0")["expr2"].value()
        self.group["e%d_intensity" % eid].setValue(0.3)
        self.fire("e%d_intensity" % eid)
        self.assertEqual(self.group.child("TableRow0")["expr2"].value(), before)

    def test_bad_lens_file_gives_no_ghosts(self):
        builder.clear_elements(self.group)
        el = elements.element("Lens System", lens="From File",
                              lens_file=os.path.join(self.tmp.name, "missing.dat"))
        builder.add_element(self.group, "Lens System", el)
        self.assertEqual(builder.param_knob(self.blink, "elementCount").value(), 0)

    def test_table_is_capped(self):
        builder.clear_elements(self.group)
        for _ in range(elements.MAX_COLUMNS + 5):
            builder.add_element(self.group, "Glow", rebuild=False)
        builder.rebuild_table(self.group)
        self.assertEqual(builder.param_knob(self.blink, "elementCount").value(), elements.MAX_COLUMNS)
        self.assertEqual(self.group.child("TableCrop")["box"].value(2), elements.MAX_COLUMNS)

    def test_clear_button_asks_first(self):
        fake_nuke.asks_queue.append(False)
        fake_nuke.run_script(self.group["clear_elements"].script, self.group)
        self.assertTrue(builder.element_ids(self.group))
        fake_nuke.asks_queue.append(True)
        fake_nuke.run_script(self.group["clear_elements"].script, self.group)
        self.assertEqual(builder.element_ids(self.group), [])


LEGACY_COMMIT = "1ef3234"  # BlinkFlare 3.0, with the dirt input


@contextlib.contextmanager
def legacy_blinkflare(folder):
    """Import the blinkflare package from ``folder`` instead of this one."""
    def ours(name):
        return name == "blinkflare" or name.startswith("blinkflare.")
    saved = dict((k, v) for k, v in sys.modules.items() if ours(k))
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, folder)
    try:
        import blinkflare.builder as legacy  # noqa: F401
        yield sys.modules["blinkflare.builder"]
    finally:
        sys.path.remove(folder)
        for k in [k for k in sys.modules if ours(k)]:
            del sys.modules[k]
        sys.modules.update(saved)


class UpgradeTest(BuilderBase):
    def scene(self, group, inputs):
        """Plate, camera, axis and dirt texture feeding ``group``; a node after it."""
        with fake_nuke.root():
            nodes = dict(plate=fake_nuke.nodes.Constant(name="Plate"),
                         cam=fake_nuke.nodes.Camera2(name="Camera1"),
                         axis=fake_nuke.nodes.Axis2(name="Axis1"),
                         dirt=fake_nuke.nodes.Constant(name="DirtTex"),
                         after=fake_nuke.nodes.NoOp(name="After"))
        for name, index in inputs.items():
            group.setInput(index, nodes["plate" if name == "src" else name])
        nodes["after"].setInput(0, group)
        return nodes

    def make_30(self, group):
        """Make ``group`` look like what 3.0 built: a dirt input between
        occlusion and cam, dirt knobs, Elements as the last tab."""
        with group:
            fake_nuke.nodes.Input(name="dirt")
        numbers = {"src": 0, "occlusion": 1, "dirt": 2, "cam": 3, "axis": 4, "mask": 5}
        for n in group.nodes():
            if n.Class() == "Input":
                n["number"].setValue(numbers[n.name()])
        dirt = fake_nuke.Boolean_Knob("dirt_enable", "Enable Dirt")
        group.addKnob(dirt)
        dirt.setValue(True)
        group["blinkflare_version"].setValue("3.0.0")
        group["solo"].setValues(group["solo"].items + ["Dirt"])
        group["solo"].setValue("Dirt")
        order = group._user_order
        after = [n for n in order if n in spec.AFTER_ELEMENTS]
        rest = [n for n in order if n not in spec.AFTER_ELEMENTS]
        cut = rest.index("tab_elements")
        order[:] = rest[:cut] + after + rest[cut:]
        return {"src": 0, "dirt": 2, "cam": 3, "axis": 4}

    def style(self, group):
        """Settings, keyframes and elements worth carrying across."""
        group["anamorphic"].setValue(0.6)
        group["light_pos"].setValueAt(100.0, 1001, 0)
        group["light_pos"].setValueAt(400.0, 1010, 0)
        group["output_mode"].setValue(1)
        group.node(spec.MERGE_NODE)["mix"].setValue(0.4)
        builder.clear_elements(group)
        eid = builder.add_element(group, "Streak", elements.element("Streak", color=(0.2, 0.4, 1.0)))
        group[elements.knob_name(eid, "intensity")].setValueAt(0.5, 1001)
        group[elements.knob_name(eid, "intensity")].setValueAt(2.0, 1010)
        builder.add_element(group, "Lens System", elements.element("Lens System", fstop=8.0))
        group.setXYpos(300, 400)

    def assertUpgraded(self, new, nodes):
        self.assertNotIn(self.group, fake_nuke.root().children())
        self.assertEqual(new.name(), "BlinkFlare1")
        self.assertEqual((new.xpos(), new.ypos()), (300, 400))
        self.assertEqual(builder.node_version(new), spec.VERSION)
        self.assertEqual(sorted(builder._input_indices(new)), ["axis", "cam", "mask", "occlusion", "src"])
        self.assertIs(new.input(0), nodes["plate"])
        self.assertIs(new.input(camera.CAMERA_INPUT), nodes["cam"])
        self.assertIs(new.input(camera.AXIS_INPUT), nodes["axis"])
        self.assertIs(nodes["after"].input(0), new)
        self.assertEqual(new["anamorphic"].value(), 0.6)
        self.assertEqual(new["light_pos"].keys, {0: {1001.0: 100.0, 1010.0: 400.0}})
        self.assertEqual(new["output_mode"].value(), "Flare Only")
        self.assertEqual(new.node(spec.MERGE_NODE)["mix"].value(), 0.4)
        stack = builder.stack(new)
        self.assertEqual([el["type"] for el in stack], ["Streak", "Lens System"])
        self.assertEqual(stack[0]["color"], (0.2, 0.4, 1.0))
        self.assertEqual(stack[1]["fstop"], 8.0)
        self.assertEqual(new["e1_intensity"].keys, {0: {1001.0: 0.5, 1010.0: 2.0}})
        tabs = [n for n in new.user_knob_order if n.startswith("tab_")]
        self.assertEqual(tabs, ["tab_flare", "tab_lens", "tab_elements", "tab_3d", "tab_output"])
        proj = new.node(camera.PROJECTION_NODE)
        self.assertEqual(proj["focal"].expressions, {0: "root.Camera1.focal"})

    def test_upgrade_a_30_node(self):
        nodes = self.scene(self.group, self.make_30(self.group))
        self.style(self.group)
        new, notes = builder.upgrade(self.group)
        self.assertUpgraded(new, nodes)
        self.assertIsNone(new.node("dirt"))
        self.assertIsNone(new.knob("dirt_enable"))
        self.assertEqual(new["solo"].value(), "All")
        self.assertIn("its 'dirt' input (DirtTex) was disconnected", notes)
        self.assertIn("lens dirt was on; BlinkFlare no longer has it", notes)
        self.assertTrue(any(n.startswith("Solo was reset") for n in notes), notes)

    def test_upgrade_a_node_from_before_element_stacks(self):
        nodes = self.scene(self.group, {"src": 0, "cam": 2, "axis": 3})
        for name in [n for n in self.group.knobs() if n[:1] == "e" and n[1:2].isdigit()]:
            self.group.removeKnob(self.group[name])
        for name in ("element_ids", "blinkflare_version"):
            self.group.removeKnob(self.group[name])
        self.assertEqual(builder.node_version(self.group), "2")
        new, notes = builder.upgrade(self.group)
        self.assertIs(new.input(camera.CAMERA_INPUT), nodes["cam"])
        self.assertEqual([el["type"] for el in builder.stack(new)],
                         [el["type"] for el in presets.stack(presets.PRESETS["Default"])])
        self.assertIn("it predates element stacks, so its look starts from the Default preset", notes)

    def test_upgrade_inside_another_group(self):
        with fake_nuke.root():
            outer = fake_nuke.nodes.Group(name="Shot")
        with outer:
            inner = builder.create()
        self.make_30(inner)
        new, notes = builder.upgrade(inner)
        self.assertIn(new, outer.children())
        self.assertNotIn(inner, outer.children())
        self.assertEqual(new.name(), inner.name())

    def test_element_layers_rebuilt(self):
        self.group["element_layers"].setValue(True)
        builder.build_element_layers(self.group)
        new, _ = builder.upgrade(self.group)
        self.assertIsNotNone(new.node("%s_glow" % spec.KERNEL_NODE))

    def test_not_a_blinkflare_node(self):
        with fake_nuke.root():
            other = fake_nuke.nodes.Group(name="Other")
        with self.assertRaises(builder.BuildError):
            builder.upgrade(other)

    def test_failed_transfer_leaves_the_old_node(self):
        self.make_30(self.group)
        before = list(fake_nuke.root().children())

        def broken(old, new):
            raise RuntimeError("boom")
        real, builder._transfer = builder._transfer, broken
        try:
            self.assertEqual(builder.upgrade_selected(), [])  # nothing selected: upgrade all
        finally:
            builder._transfer = real
        self.assertEqual(fake_nuke.root().children(), before)
        self.assertIn("BlinkFlare1 failed: Building BlinkFlare failed", fake_nuke.messages[-1])
        self.assertIn("boom", fake_nuke.messages[-1])

    def test_upgrade_selected_offers_every_older_node(self):
        self.make_30(self.group)
        current = builder.create()
        for n in fake_nuke.selectedNodes():
            n.setSelected(False)
        fake_nuke.asks_queue.append(False)
        self.assertEqual(builder.upgrade_selected(), [])
        self.assertIn(self.group, fake_nuke.root().children())
        done = builder.upgrade_selected()
        self.assertEqual(len(done), 1)
        self.assertIn(current, fake_nuke.root().children())  # current nodes are left alone
        self.assertNotIn(self.group, fake_nuke.root().children())
        self.assertIn("Upgraded 1 BlinkFlare node to v%s." % spec.VERSION, fake_nuke.messages[-1])
        self.assertEqual(fake_nuke.selectedNodes(), [done[0][0]])
        done[0][0].setSelected(False)
        self.assertEqual(builder.upgrade_selected(), [])
        self.assertIn("No BlinkFlare nodes need upgrading", fake_nuke.messages[-1])

    def test_upgrade_selected_takes_the_selection(self):
        current = builder.create()  # selected after creation
        done = builder.upgrade_selected()
        self.assertEqual(len(done), 1)
        self.assertNotIn(current, fake_nuke.root().children())
        self.assertIn(self.group, fake_nuke.root().children())

    def test_upgrade_a_real_30_node(self):
        top = subprocess.run(["git", "-C", kernel_parse.ROOT, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
        if top.returncode != 0:
            self.skipTest("no git history")
        folder = os.path.join(self.tmp.name, "legacy")
        os.makedirs(folder)
        archive = subprocess.run(["git", "-C", top.stdout.strip(), "archive", LEGACY_COMMIT,
                                  "BlinkFlare/blinkflare"], capture_output=True)
        if archive.returncode != 0:
            self.skipTest("BlinkFlare 3.0 isn't in this checkout's history")
        subprocess.run(["tar", "-x", "-C", folder], input=archive.stdout, check=True)
        nuke_dir = os.path.join(folder, "BlinkFlare")
        with legacy_blinkflare(nuke_dir) as legacy:
            old = legacy.create()
            self.assertEqual(old["blinkflare_version"].value(), "3.0.0")
            self.assertEqual(old.node(spec.KERNEL_NODE).maxInputs(), 4)  # src, occlusion, dirt, elements
        nodes = self.scene(old, {"src": 0, "dirt": 2, "cam": 3, "axis": 4})
        self.style(old)
        old.setName("Flare30")
        new, notes = builder.upgrade(old)
        self.assertEqual(new.name(), "Flare30")
        self.assertEqual(new.node(spec.KERNEL_NODE).maxInputs(), 3)
        self.assertIs(new.input(camera.AXIS_INPUT), nodes["axis"])
        self.assertIs(nodes["after"].input(0), new)
        self.assertEqual([el["type"] for el in builder.stack(new)], ["Streak", "Lens System"])
        self.assertEqual(new["e1_intensity"].keys, {0: {1001.0: 0.5, 1010.0: 2.0}})
        self.assertIn("its 'dirt' input (DirtTex) was disconnected", notes)
        check_references(self, new)


def check_references(test, group):
    """Every knob reference in every expression in ``group`` must resolve."""
    nodes = dict((c.name(), c) for c in group.children())
    for node in [group] + group.children():
        exprs = [(name, e) for name, knob in node.knobs().items() for e in knob.expressions.values()]
        if node.Class() == "Expression":  # per-pixel expressions live in the expr knobs
            exprs += [("expr%d" % c, node["expr%d" % c].value()) for c in range(4)]
        for knob_name, expr in exprs:
            if True:
                for ref in nuke_expr.references(expr):
                    if node.Class() == "Expression" and ref in ("x", "y") + elements.CHANNEL_VARS:
                        continue
                    parts = ref.split(".")
                    where = "%s.%s: %s" % (node.name(), knob_name, ref)
                    if parts[0] == "input":
                        test.assertIn(parts[1:], (["width"], ["height"]), where)
                        continue
                    if parts[0] == "root":  # absolute path to a node outside the group
                        target, i = fake_nuke.root(), 1
                        while i < len(parts) and target.child(parts[i]) is not None:
                            target, i = target.child(parts[i]), i + 1
                        rest = parts[i:]
                    elif parts[0] == "parent":
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
