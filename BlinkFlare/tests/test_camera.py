"""3D placement: the projection math and the Nuke expressions generated from it."""
import random
import unittest

import numpy as np

import kernel_parse  # noqa: F401  (sets sys.path)
import nuke_expr
from blinkflare import camera, spec

W, H = 2048, 1080
HALF_FOV = 24.576 / 2 / 50.0  # tan of the half horizontal FOV for the defaults


def proj(light, cam=None, **lens):
    return camera.project(cam or camera.matrix_trs(), light, W, H, **lens)


class ProjectReference(unittest.TestCase):
    """camera.project against hand-derived answers."""

    def assertPoint(self, got, expected, places=6):
        self.assertAlmostEqual(got[0], expected[0], places)
        self.assertAlmostEqual(got[1], expected[1], places)

    def test_on_axis_is_frame_center(self):
        p = proj((0, 0, -10))
        self.assertPoint(p["screen"], (W / 2, H / 2))
        self.assertAlmostEqual(p["depth"], 10)
        self.assertEqual(p["visible"], 1.0)

    def test_horizontal_aperture_spans_width(self):
        self.assertPoint(proj((10 * HALF_FOV, 0, -10))["screen"], (W, H / 2))
        self.assertPoint(proj((-10 * HALF_FOV, 0, -10))["screen"], (0, H / 2))

    def test_vertical_uses_format_aspect(self):
        top = 10 * HALF_FOV * H / W
        self.assertPoint(proj((0, top, -10))["screen"], (W / 2, H))
        squeezed = camera.project(camera.matrix_trs(), (0, top / 2, -10), W, H, pixel_aspect=2.0)
        self.assertPoint(squeezed["screen"], (W / 2, H))

    def test_behind_camera_is_hidden(self):
        self.assertEqual(proj((0, 0, 10))["visible"], 0.0)
        self.assertEqual(proj((0, 0, 0))["visible"], 0.0)

    def test_camera_transform(self):
        # Camera at (5, 2, 3) turned 90 degrees about Y looks down world -X.
        cam = camera.matrix_trs((5, 2, 3), (0, 90, 0))
        p = proj((-5, 2, 3), cam)
        self.assertPoint(p["screen"], (W / 2, H / 2))
        self.assertAlmostEqual(p["depth"], 10)
        # World -Z is now camera +X, i.e. screen right.
        self.assertGreater(proj((-5, 2, 2), cam)["screen"][0], W / 2)

    def test_uniform_scale_keeps_screen_position(self):
        light = (1.0, -0.5, -7.0)
        plain = proj(light, camera.matrix_trs((1, 2, 3), (10, 20, 30)))
        scaled = proj(light, camera.matrix_trs((1, 2, 3), (10, 20, 30), (2, 2, 2)))
        self.assertPoint(scaled["screen"], plain["screen"])

    def test_matches_full_matrix_inverse(self):
        # Non-uniform scale changes the view; camera space must be exactly
        # inverse(world_matrix) * point, as a renderer would compute it.
        cam = camera.matrix_trs((1, 2, 3), (10, 20, 30), (2, 3, 0.5))
        light = (1.0, -0.5, -7.0)
        q = np.linalg.inv(np.array(cam).reshape(4, 4)) @ np.array(list(light) + [1.0])
        k = 2 * 50.0 / 24.576 / -q[2]
        expected = ((q[0] * k + 1) * W / 2, H / 2 + q[1] * k * W / 2)
        self.assertPoint(proj(light, cam)["screen"], expected)

    def test_window_translate_moves_lens_center(self):
        p = proj((0, 0, -10), win_translate=(0.5, 0.25))
        expected = (W * 0.25, H / 2 - 0.25 * W / 2)
        self.assertPoint(p["lens_center"], expected)
        self.assertPoint(p["screen"], expected)

    def test_window_scale_and_roll(self):
        edge = (10 * HALF_FOV / 2, 0, -10)  # u = 0.5
        self.assertPoint(proj(edge, win_scale=(0.5, 1))["screen"], (W, H / 2))
        self.assertPoint(proj(edge, winroll=90)["screen"], (W / 2, H / 2 + 0.5 * W / 2))


class GeneratedExpressions(unittest.TestCase):
    """The Projection NoOp's expressions evaluate to camera.project."""

    def evaluate_projection(self, cam, light_world, lens, pixel_aspect):
        light_m = camera.matrix_trs(light_world)
        knobs = {}
        lens_values = dict((name, lens.get(name, default)) for name, _, _, default in camera.LENS_KNOBS)

        def resolve(path):
            parts = path.split(".")
            if parts[0] == camera.CAMERA_XFORM:
                return cam[int(parts[2])]
            if parts[0] == camera.LIGHT_XFORM:
                return light_m[int(parts[2])]
            if path == "input.width":
                return W
            if path == "input.height":
                return H
            if path == "parent.pixel_aspect":
                return pixel_aspect
            if path in lens_values:
                return lens_values[path]
            return knobs[path]

        for name, kind, exprs in camera.PROJECTION_KNOBS:
            if kind == "lens":
                continue
            if kind == "double":
                knobs[name] = nuke_expr.evaluate(exprs[0], resolve)
            else:
                for c, expr in zip("xyz", exprs):
                    knobs["%s.%s" % (name, c)] = nuke_expr.evaluate(expr, resolve)
        return knobs

    def test_matches_reference_for_random_scenes(self):
        rng = random.Random(7)
        for _ in range(200):
            cam = camera.matrix_trs(
                [rng.uniform(-50, 50) for _ in range(3)],
                [rng.uniform(-180, 180) for _ in range(3)],
                [rng.uniform(0.5, 2) for _ in range(3)])
            light = [rng.uniform(-100, 100) for _ in range(3)]
            lens = {"focal": rng.uniform(12, 200), "haperture": rng.uniform(10, 40),
                    "win_tx": rng.uniform(-0.5, 0.5), "win_ty": rng.uniform(-0.5, 0.5),
                    "win_sx": rng.uniform(0.5, 2), "win_sy": rng.uniform(0.5, 2),
                    "winroll": rng.uniform(-45, 45)}
            pa = rng.choice([1.0, 2.0])
            got = self.evaluate_projection(cam, light, lens, pa)
            ref = camera.project(
                cam, light, W, H, pixel_aspect=pa, focal=lens["focal"], haperture=lens["haperture"],
                win_translate=(lens["win_tx"], lens["win_ty"]),
                win_scale=(lens["win_sx"], lens["win_sy"]), winroll=lens["winroll"])
            for key, ref_v in (("screen", ref["screen"]), ("lens_center", ref["lens_center"])):
                for c, v in zip("xy", ref_v):
                    self.assertAlmostEqual(got["%s.%s" % (key, c)], v, delta=1e-6 * max(1.0, abs(v)))
            self.assertAlmostEqual(got["visible"], ref["visible"])
            self.assertAlmostEqual(got["depth"], ref["depth"], places=6)

    def test_lens_knob_defaults_frame_center(self):
        got = self.evaluate_projection(camera.matrix_trs(), (0, 0, -5), {}, 1.0)
        self.assertAlmostEqual(got["lens_center.x"], W / 2)
        self.assertAlmostEqual(got["lens_center.y"], H / 2)

    def test_derived_params_match_resolve_params(self):
        """Kernel param expressions blend 2D/3D the way resolve_params does."""
        scene = {"world_matrix": camera.matrix_trs((0, 0, 5), (0, 10, 0)),
                 "light_world": (3.0, 1.0, -20.0), "focal": 35.0}
        p = camera.project(width=W, height=H, **scene)
        for light_source in (0, 1):
            for mode in (0, 1, 2):
                values = spec.defaults()
                values.update(light_source=light_source, articulation_mode=mode,
                              light_pos=(100.0, 200.0), axis_center=(300.0, 400.0),
                              light_depth=55.0)
                expected = spec.resolve_params(values, W, H, scene)

                def resolve(path):
                    parts = path.split(".")
                    if parts[0] == "parent":
                        v = values[parts[1]]
                        return v["xy".index(parts[2])] if len(parts) > 2 else v
                    if parts[0] == camera.PROJECTION_NODE:
                        v = p[parts[1]]
                        return v["xy".index(parts[2])] if len(parts) > 2 else v
                    return {"input.width": W, "input.height": H}[path]

                for param in ("lightPos", "axisCenter", "lightDepth", "masterIntensity"):
                    exprs = spec.DERIVED_PARAMS[param]
                    got = [nuke_expr.evaluate(e, resolve) for e in exprs]
                    want = expected[param]
                    want = list(want) if isinstance(want, tuple) else [want]
                    for g, w in zip(got, want):
                        self.assertAlmostEqual(g, w, places=6, msg=(param, light_source, mode))


if __name__ == "__main__":
    unittest.main()
