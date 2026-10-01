"""Behavior of the real kernel source, compiled and run with the C++ harness."""
import os
import subprocess
import sys
import unittest

import numpy as np

import kernel_parse

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness")
sys.path.insert(0, HARNESS)
import preview  # noqa: E402

W, H = 320, 180
ALL_OFF = dict(glow_enable=False, glint_enable=False, streak_enable=False, ring_enable=False,
               ghost_enable=False, spectral_enable=False)


def render(overrides=None, **inputs):
    values = preview.look_values("Default", overrides, W, H)
    return preview.render_flare(values, W, H, **inputs)


class KernelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preview.build()
        cls.default = render()

    def test_harness_registers_every_param(self):
        out = subprocess.check_output([preview.BINARY, "--list-params"], text=True)
        registered = dict(line.split() for line in out.strip().splitlines())
        declared = kernel_parse.declared_params()
        widths = {"float": "1", "float2": "2", "float4": "4", "int": "1", "bool": "1"}
        self.assertEqual(registered, dict((k, widths[t]) for k, t in declared.items()))

    def test_output_finite_and_non_negative(self):
        self.assertTrue(np.isfinite(self.default).all())
        self.assertGreaterEqual(self.default.min(), 0.0)

    def test_brightest_at_light(self):
        lum = self.default[..., :3].sum(-1)
        y, x = np.unravel_index(np.argmax(lum), lum.shape)
        self.assertLess(abs(x - W * 0.7), 2)
        self.assertLess(abs(y - H * 0.7), 2)

    def test_alpha_is_clamped_max_rgb(self):
        expected = np.clip(self.default[..., :3].max(-1), 0.0, 1.0)
        np.testing.assert_allclose(self.default[..., 3], expected, atol=1e-6)

    def test_deterministic(self):
        np.testing.assert_array_equal(render(), self.default)

    def test_disabling_everything_is_black(self):
        self.assertEqual(np.abs(render(ALL_OFF)).max(), 0.0)

    def test_each_element_contributes(self):
        for name in ("glow", "glint", "streak", "ring", "ghost", "spectral"):
            ov = dict(ALL_OFF)
            ov[name + "_enable"] = True
            self.assertGreater(render(ov)[..., :3].sum(), 1.0, name)

    def test_master_intensity_scales_linearly(self):
        np.testing.assert_allclose(render({"master_intensity": 2.0})[..., :3],
                                   self.default[..., :3] * 2.0, rtol=1e-5, atol=1e-6)

    def test_offscreen_fade(self):
        far = render({"light_pos": (W + H * 0.6, H * 0.5), "offscreen_fade": 0.5})
        self.assertEqual(far.max(), 0.0)
        near = render({"light_pos": (W + H * 0.1, H * 0.5), "offscreen_fade": 0.5})
        self.assertGreater(near.max(), 0.0)

    def test_occlusion(self):
        occ = np.zeros((H, W, 4), np.float32)
        occ[..., 3] = 1.0
        self.assertEqual(render({"occlusion_enable": True}, occlusion=occ).max(), 0.0)
        half = np.zeros((H, W, 4), np.float32)
        half[:, : int(W * 0.7), 3] = 1.0
        dim = render({"occlusion_enable": True, "occlusion_radius": 10.0}, occlusion=half)
        ratio = dim[..., :3].sum() / self.default[..., :3].sum()
        self.assertTrue(0.3 < ratio < 0.7, ratio)
        inv = render({"occlusion_enable": True, "occlusion_invert": True}, occlusion=occ)
        np.testing.assert_allclose(inv, self.default, rtol=1e-5, atol=1e-6)

    def test_dirt_only_where_texture(self):
        dirt = np.zeros((H, W, 4), np.float32)
        dirt[:, : W // 2] = 1.0
        on = render(dict(ALL_OFF, dirt_enable=True), dirt=dirt)
        self.assertGreater(on[:, : W // 2].max(), 0.0)
        self.assertEqual(on[:, W // 2:].max(), 0.0)

    def test_ghosts_follow_articulation_point(self):
        ghosts = dict(ALL_OFF, ghost_enable=True, ghost_spread_min=2.0, ghost_spread_max=2.0,
                      ghost_count=1, ghost_size=0.05, ghost_size_random=0.0)
        light = (W * 0.75, H * 0.5)
        for axis in ((W * 0.5, H * 0.5), (W * 0.5, H * 0.3)):
            img = render(dict(ghosts, light_pos=light, axis_center=axis))
            lum = img[..., :3].sum(-1)
            ys, xs = np.nonzero(lum > lum.max() * 0.5)
            mirror = (2 * axis[0] - light[0], 2 * axis[1] - light[1])
            self.assertLess(abs(xs.mean() + 0.5 - mirror[0]), 1.5, axis)
            self.assertLess(abs(ys.mean() + 0.5 - mirror[1]), 1.5, axis)

    def test_rotate_with_light_is_continuous_across_branch_cut(self):
        # Light just above vs just below the line left of the articulation point,
        # where atan2 jumps from +pi to -pi.
        ov = dict(ALL_OFF, glint_enable=True, glint_fine=0.0, spin_with_light=True,
                  axis_center=(W * 0.5, H * 0.5))
        above = render(dict(ov, light_pos=(W * 0.2, H * 0.5 + 0.01)))
        below = render(dict(ov, light_pos=(W * 0.2, H * 0.5 - 0.01)))
        diff = np.abs(above - below)[..., :3].sum() / above[..., :3].sum()
        self.assertLess(diff, 0.02)

    def test_pixel_aspect_keeps_glow_round(self):
        ov = dict(ALL_OFF, glow_enable=True, core_intensity=0.0, pixel_aspect=2.0,
                  light_pos=(W * 0.5 / 2.0, H * 0.5))
        lum = render(ov)[..., :3].sum(-1)
        cy, cx = int(H * 0.5), int(W * 0.25)
        # 10 square pixels away = 5 image pixels horizontally, 10 vertically.
        self.assertAlmostEqual(lum[cy, cx + 5] / lum[cy + 10, cx], 1.0, delta=0.08)


if __name__ == "__main__":
    unittest.main()
