"""Consistency between the kernel, the knob spec and the presets."""
import re
import unittest

import kernel_parse
from blinkflare import presets, spec

KIND_FOR_TYPE = {"float": ("double", "int"), "int": ("int",), "bool": ("bool",),
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
        driven = set(linked) | set(spec.DERIVED_PARAMS)
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


class KnobSpec(unittest.TestCase):
    def test_names_unique_and_valid(self):
        names = [k.name for k in spec.KNOBS]
        self.assertEqual(len(names), len(set(names)))
        for n in names:
            self.assertRegex(n, r"^[a-z][a-z0-9_]*$")

    def test_first_knob_is_tab(self):
        self.assertEqual(spec.KNOBS[0].kind, "tab")

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
        self.assertEqual(set(params), set(kernel_parse.declared_params()))


class Presets(unittest.TestCase):
    def test_default_first(self):
        self.assertEqual(list(presets.PRESETS)[0], "Default")
        self.assertEqual(presets.PRESETS["Default"], {})

    def test_preset_values_valid(self):
        for name, values in presets.PRESETS.items():
            for knob_name, v in values.items():
                k = spec.knob(knob_name)
                self.assertTrue(k.is_value, (name, knob_name))
                self.assertNotIn(knob_name, spec.NON_LOOK_KNOBS, (name, knob_name))
                if k.kind == "color":
                    self.assertEqual(len(v), 3, (name, knob_name))
                elif k.kind == "bool":
                    self.assertIsInstance(v, bool, (name, knob_name))
                elif k.kind == "int":
                    self.assertIsInstance(v, int, (name, knob_name))
                elif k.kind == "double":
                    self.assertIsInstance(v, (int, float), (name, knob_name))
                    lo, hi = k.range
                    self.assertTrue(lo <= v <= hi, (name, knob_name, v))

    def test_preset_menu_lists_presets(self):
        self.assertEqual(spec.enum_items(spec.knob("preset")), list(presets.PRESETS))


if __name__ == "__main__":
    unittest.main()
