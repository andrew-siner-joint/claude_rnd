"""Element types, the table encoding and its Nuke expressions, and the lens model."""
import os
import random
import re
import tempfile
import unittest

import kernel_parse  # noqa: F401  (sets sys.path)
import nuke_expr
from blinkflare import elements, lenses


def random_element(rng, type_name):
    t = elements.TYPES[type_name]
    p = {i: rng.uniform(prm.lo, prm.hi) for i, prm in enumerate(t.params)}
    el = elements.element(type_name, p=p, intensity=rng.uniform(0, 2),
                          color=(rng.random(), rng.random(), rng.random()),
                          size=rng.uniform(0.01, 0.5), axis=rng.uniform(-1, 3),
                          rotation=rng.uniform(-180, 180), seed=rng.randint(0, 99),
                          dispersion=rng.uniform(0, 1), softness=rng.uniform(0, 1),
                          on=rng.random() > 0.2, layer=rng.randint(0, 6))
    if type_name == "Lens System":
        el.update(fstop=rng.choice([2.8, 4.0, 8.0]), coating=rng.choice(lenses.COATINGS),
                  max_ghosts=rng.randint(3, 10))
    return el


class Types(unittest.TestCase):
    def test_codes_follow_menu_order(self):
        for i, t in enumerate(elements.TYPES.values()):
            self.assertEqual(t.code, i + 1)  # the table uses type menu index + 1

    def test_params_and_defaults(self):
        for t in elements.TYPES.values():
            self.assertLessEqual(len(t.params), 8, t.name)
            self.assertIn(t.render_pass, elements.PASS_CODES, t.name)
            self.assertIsNotNone(t.generic.get("intensity"), t.name)
            for p in t.params:
                self.assertTrue(p.lo <= p.default <= p.hi, (t.name, p.label))
            labels = [p.label for p in t.params]
            self.assertEqual(len(labels), len(set(labels)), t.name)

    def test_pass_resolution(self):
        self.assertEqual(elements.resolve_pass("Streak", 0), elements.PASS_CODES["Streaks"])
        self.assertEqual(elements.resolve_pass("Streak", 4), 4)

    def test_element_fills_lens_fields_only_for_lens_system(self):
        self.assertIn("fstop", elements.element("Lens System"))
        self.assertNotIn("fstop", elements.element("Glow"))


class TableExpressions(unittest.TestCase):
    """Expression-node strings evaluate to exactly what encode() produces."""

    def knob_resolver(self, stack):
        values = {}
        for eid, el in enumerate(stack, 1):
            for field, v in elements.knob_values(el).items():
                values[elements.knob_name(eid, field)] = v
        return values

    def evaluate_table(self, stack, columns):
        knobs = self.knob_resolver(stack)
        grid = [[None] * len(columns) for _ in range(elements.ROWS)]
        for row in range(elements.ROWS):
            for x in range(len(columns)):
                px = []
                for c in range(4):
                    expr = elements.row_expression(columns, row, c)

                    def resolve(path):
                        if path in ("x", "y"):
                            return {"x": x + 0.5, "y": row + 0.5}[path]
                        if path in elements.CHANNEL_VARS:
                            return -999.0  # input passthrough must not leak into this row
                        name = path.split(".")
                        assert name[0] == "parent", path
                        v = knobs[name[1]]
                        if len(name) == 3:
                            v = v["rgb".index(name[2])]
                        return float(v)
                    px.append(nuke_expr.evaluate(expr, resolve))
                grid[row][x] = tuple(px)
        return [[grid[r][x] for r in range(elements.ROWS)] for x in range(len(columns))]

    def test_live_columns_match_encoder(self):
        rng = random.Random(3)
        names = [n for n in elements.TYPE_NAMES if n != "Lens System"]
        stack = [random_element(rng, rng.choice(names)) for _ in range(12)]
        columns = [elements.column_expressions(eid, el["type"]) for eid, el in enumerate(stack, 1)]
        got = self.evaluate_table(stack, columns)
        want = [elements.encode(el) for el in stack]
        for g, w in zip(got, want):
            for gr, wr in zip(g, w):
                for gv, wv in zip(gr, wr):
                    self.assertAlmostEqual(gv, wv, places=9)

    def test_lens_columns_match_encoder_and_scale_with_fstop(self):
        rng = random.Random(5)
        el = random_element(rng, "Lens System")
        ghosts = lenses.ghosts_for_element(el)
        self.assertTrue(ghosts)
        baked_at = el["fstop"]
        stack = [el]
        columns = [elements.lens_column_expressions(1, g, baked_at) for g in ghosts]
        for col in columns:
            for row in col:
                for expr in row:  # Nuke expressions: plain fixed-point literals only
                    self.assertIsNone(re.search(r"\d[eE][-+]?\d", expr), expr)
        got = self.evaluate_table(stack, columns)
        want = [elements.encode_lens_ghost(el, g) for g in ghosts]
        for g, w in zip(got, want):
            for gr, wr in zip(g, w):
                for gv, wv in zip(gr, wr):
                    self.assertAlmostEqual(gv, wv, places=9)
        # Changing the f-stop knob alone (no re-bake) halves sizes at 2x f-stop.
        el2 = dict(el, fstop=baked_at * 2)
        got2 = self.evaluate_table([el2], columns)
        self.assertAlmostEqual(got2[0][0][3], got[0][0][3] / 2, places=9)
        self.assertAlmostEqual(got2[0][5][1], got[0][5][1] / 2, places=9)

    def test_empty_table(self):
        self.assertEqual(nuke_expr.evaluate(elements.row_expression([], 0, 0),
                                            lambda p: {"x": 0.5, "y": 0.5, "r": 7.0}[p]), 0.0)

    def test_other_rows_pass_through(self):
        expr = elements.row_expression([elements.column_expressions(1, "Glow")], 2, 1)
        values = {"x": 0.5, "y": 0.5, "g": 7.0}
        self.assertEqual(nuke_expr.evaluate(expr, lambda p: values.get(p, 0.0)), 7.0)

    def test_knob_values_cover_value_fields(self):
        el = elements.element("Lens System")
        self.assertEqual(set(elements.knob_values(el)), set(elements.VALUE_FIELDS))
        plain = elements.knob_values(elements.element("Glow"))
        self.assertEqual(set(plain), set(elements.VALUE_FIELDS) - set(elements.LENS_FIELDS))


class LensOptics(unittest.TestCase):
    def test_builtin_focal_lengths(self):
        self.assertAlmostEqual(lenses.focal_length(lenses.load("Double Gauss 50mm")), 50.0, delta=0.5)
        self.assertAlmostEqual(lenses.focal_length(lenses.load("Cooke Triplet 50mm")), 50.0, delta=0.5)
        self.assertAlmostEqual(lenses.focal_length(lenses.load("Achromat 100mm")), 100.0, delta=0.5)

    def test_lensmaker(self):
        s = lenses.parse("0 0 0 20\n50 0.0001 1.5 20 1e9\n-50 0 1 20\n")
        self.assertAlmostEqual(lenses.focal_length(s, 0.5876), 50.0, places=2)

    def test_dispersion_shortens_blue_focus(self):
        s = lenses.parse("0 0 0 20\n50 3 1.5168 20 64.2\n-50 0 1 20\n")
        self.assertLess(lenses.focal_length(s, 0.465), lenses.focal_length(s, 0.610))

    def test_flat_plate_ghost_sits_on_the_light_in_focus(self):
        s = lenses.parse("0 2 1.5 20 64\n0 5 1 20\n0 1 0 18\n50 0.001 1.5 20 1e9\n-50 0 1 20\n")
        bfd = lenses.back_focus(s)
        m = lenses.ghost_matrix(s, 0, 1, 0.5876, bfd)
        main = lenses._mul(lenses._translate(bfd), lenses.system(s, 0.5876))
        self.assertAlmostEqual(m[0][0], 0.0, places=6)
        self.assertAlmostEqual(m[0][1] / main[0][1], 1.0, places=6)

    def test_quarter_wave_coating_minimum(self):
        nc, ns = lenses.COATING_INDEX, 1.52
        r = lenses.reflectance(1.0, ns, 0.55, "Single Coated", 0)
        self.assertAlmostEqual(r, ((ns - nc * nc) / (ns + nc * nc)) ** 2, places=9)
        bare = lenses.reflectance(1.0, ns, 0.55, "Uncoated", 0)
        self.assertAlmostEqual(bare, ((1 - ns) / (1 + ns)) ** 2, places=9)
        # Off the design wavelength a single layer reflects more: purple ghosts.
        self.assertGreater(lenses.reflectance(1.0, ns, 0.465, "Single Coated", 0), r)
        self.assertGreater(lenses.reflectance(1.0, ns, 0.610, "Single Coated", 0), r)

    def test_ghost_list_properties(self):
        g = lenses.ghosts(lenses.load("Double Gauss 50mm"), fstop=4.0, max_ghosts=12)
        self.assertEqual(len(g), 12)
        for ghost in g:
            self.assertTrue(all(s >= lenses.MIN_RADIUS for s in ghost.size))
            self.assertTrue(all(0.0 <= c <= lenses.SOFT_CEILING for c in ghost.color))
            self.assertLess(ghost.size[1], lenses.MAX_RADIUS)
        uncoated = lenses.ghosts(lenses.load("Double Gauss 50mm"), coating="Uncoated", max_ghosts=12)
        self.assertGreater(sum(sum(x.color) for x in uncoated), sum(sum(x.color) for x in g))

    def test_far_off_axis_ghosts_rank_lower(self):
        near = lenses.Ghost(0, 1, (1.2,) * 3, (0.1,) * 3, (0.1,) * 3)
        far = lenses.Ghost(0, 2, (-3.8,) * 3, (0.1,) * 3, (0.1,) * 3)
        self.assertGreater(lenses.visibility(near), 5 * lenses.visibility(far))
        kept = lenses.ghosts(lenses.load("Double Gauss 50mm"), fstop=11.0, max_ghosts=6)
        everything = lenses.ghosts(lenses.load("Double Gauss 50mm"), fstop=11.0, max_ghosts=200)
        ranked = sorted(everything, key=lambda g: -lenses.visibility(g))[:6]
        self.assertEqual([(g.i, g.j) for g in kept], [(g.i, g.j) for g in ranked])
        self.assertTrue(all(abs(1.0 - g.axis[1]) < 3.0 for g in kept[:3]))

    def test_stopping_down_shrinks_ghosts(self):
        a = dict(((x.i, x.j), x) for x in lenses.ghosts(lenses.load("Double Gauss 50mm"), 2.8, max_ghosts=60))
        b = dict(((x.i, x.j), x) for x in lenses.ghosts(lenses.load("Double Gauss 50mm"), 5.6, max_ghosts=60))
        shared = [k for k in a if k in b and a[k].size[1] > 0.02]
        self.assertTrue(shared)
        for k in shared:
            self.assertAlmostEqual(b[k].size[1], a[k].size[1] / 2, places=6)
            self.assertAlmostEqual(b[k].axis[1], a[k].axis[1], places=9)

    def test_sensor_reflections_included(self):
        g = lenses.ghosts(lenses.load("Double Gauss 50mm"), fstop=4.0, max_ghosts=60)
        sensor_index = len(lenses.with_sensor(lenses.load("Double Gauss 50mm"))) - 1
        self.assertTrue(any(x.j == sensor_index for x in g))

    def test_parse_errors(self):
        with self.assertRaises(ValueError):
            lenses.parse("50 3 1.5\n")
        with self.assertRaises(ValueError):
            lenses.parse("50 3 1.5 20\n-50 0 1 20\n")  # no stop

    def test_lens_folder_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "My Lens.dat")
            with open(path, "w") as f:
                f.write(lenses.BUILTIN["Cooke Triplet 50mm"])
            env = dict(os.environ)
            os.environ["BLINKFLARE_LENS_PATH"] = tmp
            try:
                self.assertIn("My Lens", lenses.lens_names())
                self.assertEqual(len(lenses.load("My Lens")), 7)
                self.assertEqual(len(lenses.load("From File", path)), 7)
            finally:
                os.environ.clear()
                os.environ.update(env)


if __name__ == "__main__":
    unittest.main()
