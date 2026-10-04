"""Consistency between the kernel, the knob spec and the presets."""
import json
import os
import re
import tempfile
import unittest

import kernel_parse
from blinkflare import elements, presets, spec

KIND_FOR_TYPE = {"float": ("double", "int"), "int": ("int", "enum"), "bool": ("bool",),
                 "float2": ("xy",), "float4": ("color",)}


class KernelParams(unittest.TestCase):
    def setUp(self):
        self.declared = kernel_parse.declared_params()
        self.calls = kernel_parse.define_calls()

    def test_every_param_defined_once_with_matching_label(self):
        vars_ = [c[0] for c in self.calls]
        self.assertEqual(sorted(vars_), sorted(self.declared))
        self.assertEqual(len(vars_), len(set(vars_)))
        for var, label, _ in self.calls:
            self.assertEqual(var, label, "label must match variable so knob names are predictable")

    def test_every_param_is_driven(self):
        linked = [k.blink for k in spec.value_knobs() if k.blink]
        self.assertEqual(len(linked), len(set(linked)), "param linked twice")
        driven = set(linked) | set(spec.DERIVED_PARAMS) | set(spec.BUILDER_PARAMS)
        self.assertEqual(driven, set(self.declared))

    def test_knob_kinds_match_param_types(self):
        for k in spec.value_knobs():
            if k.blink:
                self.assertIn(k.kind, KIND_FOR_TYPE[self.declared[k.blink]], k.name)
        for param, exprs in spec.DERIVED_PARAMS.items():
            width = {"float": 1, "float2": 2, "float4": 4}[self.declared[param]]
            self.assertEqual(len(exprs), width, param)

    def test_kernel_defaults_match_spec(self):
        defaults = dict((var, d) for var, _, d in self.calls)
        for k in spec.value_knobs():
            if not k.blink or k.default is None:
                continue
            kd = defaults[k.blink]
            if k.kind == "color":
                self.assertEqual(tuple(kd), tuple(k.default) + (1.0,), k.name)
            else:
                self.assertAlmostEqual(float(kd), float(k.default), msg=k.name)

    def test_kernel_type_codes_match_elements(self):
        src = kernel_parse.source()
        for t in elements.TYPES.values():
            if t.name != "Lens System":
                self.assertIn("code == %d)" % t.code, src, t.name)
        self.assertIn("code == %d)" % elements.LENS_GHOST_CODE, src)
        self.assertIn("lfClampi(elementCount, 0, %d)" % elements.MAX_COLUMNS, src)


class KnobSpec(unittest.TestCase):
    def test_names_unique_and_valid(self):
        names = [k.name for k in spec.KNOBS]
        self.assertEqual(len(names), len(set(names)))
        for n in names:
            self.assertRegex(n, r"^[a-z][a-z0-9_]*$")
            # Element knobs are e<id>_<field>; static names must never look like that.
            self.assertIsNone(re.match(r"e\d+_", n), n)

    def test_tabs_and_elements_last(self):
        tabs = [k for k in spec.KNOBS if k.kind == "tab"]
        self.assertIs(spec.KNOBS[0], tabs[0])
        self.assertEqual(tabs[-1].name, "tab_elements")

    def test_value_knobs_have_defaults(self):
        for k in spec.value_knobs():
            if k.kind != "xy":
                self.assertIsNotNone(k.default, k.name)
            if k.kind == "double":
                lo, hi = k.range
                self.assertTrue(lo <= k.default <= hi, k.name)

    def test_expressions_reference_real_knobs(self):
        kinds = dict((k.name, k.kind) for k in spec.KNOBS)
        exprs = [e for k in spec.value_knobs() if k.blink for e in spec.blink_expressions(k)]
        exprs += [e for es in spec.DERIVED_PARAMS.values() for e in es]
        for expr in exprs:
            for name, chan in re.findall(r"parent\.(\w+)(?:\.(\w))?", expr):
                self.assertIn(name, kinds, expr)
                if chan:
                    self.assertIn("." + chan, spec.CHANNELS[kinds[name]], expr)

    def test_resolve_params_covers_kernel(self):
        values = spec.defaults()
        values["light_pos"] = (100.0, 100.0)
        values["axis_center"] = (50.0, 50.0)
        params = spec.resolve_params(values, 200, 100)
        expected = set(kernel_parse.declared_params()) - set(spec.BUILDER_PARAMS)
        self.assertEqual(set(params), expected)

    def test_solo_menu_matches_passes(self):
        self.assertEqual(spec.enum_items(spec.knob("solo"))[1:], [p[1] for p in spec.PASSES])


class Presets(unittest.TestCase):
    def test_default_first(self):
        self.assertEqual(list(presets.PRESETS)[0], "Default")

    def test_preset_globals_valid(self):
        for name, preset in presets.PRESETS.items():
            for knob_name, v in preset.get("globals", {}).items():
                k = spec.knob(knob_name)
                self.assertTrue(k.is_value, (name, knob_name))
                self.assertNotIn(knob_name, spec.NON_LOOK_KNOBS, (name, knob_name))
                if k.kind == "color":
                    self.assertEqual(len(v), 3, (name, knob_name))
                elif k.kind == "int":
                    self.assertIsInstance(v, int, (name, knob_name))
                elif k.kind == "double":
                    lo, hi = k.range
                    self.assertTrue(lo <= v <= hi, (name, knob_name, v))

    def test_preset_elements_valid(self):
        for name, preset in presets.PRESETS.items():
            stack = presets.stack(preset)
            self.assertTrue(stack, name)
            for el in stack:
                t = elements.TYPES[el["type"]]
                self.assertEqual(len(el["p"]), 8, name)
                for i, p in enumerate(t.params):
                    self.assertTrue(p.lo <= el["p"][i] <= p.hi, (name, t.name, p.label, el["p"][i]))
                self.assertEqual(len(elements.encode(el)) if el["type"] != "Lens System" else 6,
                                 elements.ROWS)

    def test_overrides_by_label_land_on_the_right_param(self):
        el = presets.stack(presets.PRESETS["Classic Anamorphic"])[1]
        self.assertEqual(el["type"], "Streak")
        self.assertEqual(el["p"][elements.param_by_label("Streak", "Lines")], 2.0)

    def test_preset_menu_lists_presets(self):
        self.assertEqual(spec.enum_items(spec.knob("preset")), list(presets.all_presets()))


class SavedPresets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ)
        self.studio = os.path.join(self.tmp.name, "studio")
        self.show = os.path.join(self.tmp.name, "show")
        self.personal = os.path.join(self.tmp.name, "personal")
        os.environ["BLINKFLARE_PRESET_PATH"] = os.pathsep.join([self.show, self.studio])
        os.environ["BLINKFLARE_PRESET_SAVE_DIR"] = self.personal

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def write(self, folder, fname, data):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, fname), "w") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def look(self, count):
        return {"globals": {"dust": 0.1}, "elements": [{"type": "Ghost Set", "p": [count]}]}

    def test_earlier_folders_win_and_personal_is_last(self):
        self.write(self.studio, "a.json", dict(self.look(1), name="Look"))
        self.write(self.show, "b.json", dict(self.look(2), name="Look"))
        presets.write_preset("Look", {}, [elements.element("Glow")])
        found = presets.all_presets()["Look"]
        self.assertEqual(found["elements"][0]["p"][0], 2.0)
        self.assertEqual(presets.preset_dirs(), [self.show, self.studio, self.personal])

    def test_bad_files_and_fields_are_skipped(self):
        self.write(self.studio, "broken.json", "{not json")
        self.write(self.studio, "noname.json", {"elements": []})
        self.write(self.studio, "v2.json", {"name": "Old", "knobs": {"ghost_count": 3}})
        self.write(self.studio, "ok.json", {"name": "Ok", "globals": {
            "dust": 0.5, "coating_a": [1, 0, 0], "light_pos": [1, 2], "bogus": 1},
            "elements": [{"type": "Glow", "intensity": 2.0}, {"type": "Nope"}, "junk"]})
        found = presets.saved_presets()
        self.assertEqual(list(found), ["Ok"])
        self.assertEqual(found["Ok"]["globals"], {"dust": 0.5, "coating_a": (1.0, 0.0, 0.0)})
        self.assertEqual([e["type"] for e in found["Ok"]["elements"]], ["Glow"])
        self.assertEqual(found["Ok"]["elements"][0]["intensity"], 2.0)

    def test_builtin_names_cannot_be_shadowed(self):
        self.write(self.studio, "d.json", dict(self.look(1), name="Default"))
        self.assertEqual(presets.all_presets()["Default"], presets.PRESETS["Default"])
        with self.assertRaises(ValueError):
            presets.write_preset("Default", {}, [])

    def test_write_then_read_roundtrip(self):
        stack = [elements.element("Streak", color=(0.1, 0.2, 0.3), p={"Lines": 3}),
                 elements.element("Lens System", fstop=8.0, coating="Uncoated")]
        presets.write_preset("Mine", {"anamorphic": 0.5, "light_pos": (1, 1)}, stack)
        got = presets.all_presets()["Mine"]
        self.assertEqual(got["globals"], {"anamorphic": 0.5})
        self.assertEqual([e["type"] for e in got["elements"]], ["Streak", "Lens System"])
        self.assertEqual(tuple(got["elements"][0]["color"]), (0.1, 0.2, 0.3))
        self.assertEqual(got["elements"][1]["fstop"], 8.0)
        self.assertEqual(got["elements"][1]["coating"], "Uncoated")


if __name__ == "__main__":
    unittest.main()
