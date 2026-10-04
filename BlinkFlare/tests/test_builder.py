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
        self.assertEqual(inputs, ["src", "occlusion", "dirt", "cam", "axis", "mask"])
        self.assertEqual(inputs.index("cam"), camera.CAMERA_INPUT)
        self.assertEqual(inputs.index("axis"), camera.AXIS_INPUT)
        self.assertEqual(inputs.index("mask"), camera.MASK_INPUT)

    def test_kernel_wired(self):
        self.assertEqual(self.blink.maxInputs(), 4)
        self.assertEqual([self.blink.input(i).name() for i in range(4)],
                         ["Canvas", "OcclusionDepth", "DirtFit", "TableRow%d" % (elements.ROWS - 1)])
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
        expected = [k.name for k in spec.KNOBS] + ["element_ids", "blinkflare_version"]
        order = self.group.user_knob_order
        self.assertEqual(order[:len(expected)], expected)
        # Then the Default preset's elements, appended to the Elements tab.
        self.assertTrue(all(n.startswith("e") and n[1].isdigit() for n in order[len(expected):]))
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
            self.assertEqual([inst.input(i) for i in range(4)],
                             [self.blink.input(i) for i in range(4)])
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
