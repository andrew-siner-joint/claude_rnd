"""Behavior of the real kernel source, compiled and run with the C++ harness."""
import os
import subprocess
import sys
import unittest

import numpy as np

import kernel_parse
from blinkflare import elements, lenses

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness")
sys.path.insert(0, HARNESS)
import preview  # noqa: E402

W, H = 320, 180
LIGHT = (W * 0.7, H * 0.7)
CENTER = (W * 0.5, H * 0.5)
DEFAULT_STACK = preview.look("Default", None, W, H)[1]
ALL_TYPES = [elements.element(name) for name in elements.TYPE_NAMES]


def render(stack=None, overrides=None, w=W, h=H, **kw):
    values, default_stack = preview.look("Default", overrides, w, h)
    return preview.render_flare(values, w, h, default_stack if stack is None else stack, **kw)


def along_axis(t, light=LIGHT, center=CENTER):
    return (light[0] + (center[0] - light[0]) * t, light[1] + (center[1] - light[1]) * t)


def centroid(channel_image, threshold=0.5):
    ys, xs = np.nonzero(channel_image > channel_image.max() * threshold)
    return xs.mean() + 0.5, ys.mean() + 0.5


def plain_ghost(**kw):
    """A hard-edged round Ghost Set ghost (no dust, clip, rim or fringe)."""
    p = {"Count": 1, "Size Random": 0.0, "Hollow": 0.0, "Rim": 0.0, "Barrel Clip": 0.0,
         "Intensity Random": 0.0, "Coating Mix": 0.0}
    p.update(kw.pop("p", {}))
    return elements.element("Ghost Set", p=p, dispersion=0.0, softness=0.0, **kw)


ROUND = {"aperture_blades": 0, "dust": 0.0}


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
        self.assertLess(abs(x + 0.5 - LIGHT[0]), 2)
        self.assertLess(abs(y + 0.5 - LIGHT[1]), 2)

    def test_alpha_is_clamped_max_rgb(self):
        expected = np.clip(self.default[..., :3].max(-1), 0.0, 1.0)
        np.testing.assert_allclose(self.default[..., 3], expected, atol=1e-6)

    def test_deterministic(self):
        np.testing.assert_array_equal(render(), self.default)

    def test_output_is_flare_only(self):
        plate = np.full((H, W, 4), 0.3, np.float32)
        np.testing.assert_array_equal(render(src=plate), self.default)


class ElementStack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preview.build()

    def test_empty_stack_is_black(self):
        self.assertEqual(np.abs(render([])).max(), 0.0)

    def test_off_or_zero_intensity_renders_nothing(self):
        off = [dict(el, on=False) for el in ALL_TYPES]
        self.assertEqual(np.abs(render(off)).max(), 0.0)
        zero = [dict(el, intensity=0.0) for el in ALL_TYPES]
        self.assertEqual(np.abs(render(zero)).max(), 0.0)

    def test_every_type_renders_alone(self):
        for el in ALL_TYPES:
            img = render([el])
            self.assertTrue(np.isfinite(img).all(), el["type"])
            self.assertGreaterEqual(img.min(), 0.0, el["type"])
            self.assertGreater(img[..., :3].max(), 1e-4, el["type"])

    def test_stack_is_additive(self):
        a, b = ALL_TYPES[::2], ALL_TYPES[1::2]
        np.testing.assert_allclose(render(a + b)[..., :3],
                                   render(a)[..., :3] + render(b)[..., :3], rtol=1e-5, atol=1e-6)

    def test_element_intensity_scales_linearly(self):
        for el in ALL_TYPES:
            one = render([el])
            two = render([dict(el, intensity=el["intensity"] * 2.0)])
            np.testing.assert_allclose(two[..., :3], one[..., :3] * 2.0, rtol=1e-5, atol=1e-7,
                                       err_msg=el["type"])

    def test_element_color_tints(self):
        for name in ("Glow", "Iris", "Ghost Set", "Starburst", "Caustic"):
            el = elements.element(name)
            if name == "Ghost Set":  # coatings tint ghosts on their own
                el["p"][elements.param_by_label(name, "Coating Mix")] = 0.0
            white = render([dict(el, color=(1.0, 1.0, 1.0))])
            red = render([dict(el, color=(1.0, 0.0, 0.0))])
            self.assertGreater(red[..., 0].sum(), 0.0, name)
            if name != "Glow":  # the glow's white-hot core ignores the colour
                self.assertEqual(red[..., 1:3].max(), 0.0, name)
            self.assertLessEqual(red[..., 0].sum(), white[..., 0].sum() * (1 + 1e-6), name)

    def test_element_count_limits_the_loop(self):
        stack = ALL_TYPES[:4]
        np.testing.assert_array_equal(render(stack, element_count=2), render(stack[:2]))
        # Reading past the table's edge finds no elements.
        np.testing.assert_array_equal(render(stack, element_count=9), render(stack))

    def test_master_intensity_and_tint(self):
        base = render(DEFAULT_STACK)
        np.testing.assert_allclose(render(DEFAULT_STACK, {"master_intensity": 2.0})[..., :3],
                                   base[..., :3] * 2.0, rtol=1e-5, atol=1e-6)
        tinted = render(DEFAULT_STACK, {"master_tint": (1.0, 0.5, 0.25)})
        np.testing.assert_allclose(tinted[..., :3], base[..., :3] * (1.0, 0.5, 0.25),
                                   rtol=1e-5, atol=1e-6)


class Passes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preview.build()

    def test_solo_passes_sum_to_full_flare(self):
        full = render(ALL_TYPES)
        parts = sum(render(ALL_TYPES, {"solo": code})[..., :3] for code, _, _ in elements.PASSES)
        np.testing.assert_allclose(parts, full[..., :3], rtol=1e-4, atol=1e-5)

    def test_each_solo_pass_is_its_elements(self):
        for code, name, _ in elements.PASSES:
            members = [el for el in ALL_TYPES if elements.resolve_pass(el["type"], 0) == code]
            np.testing.assert_allclose(render(ALL_TYPES, {"solo": code}), render(members),
                                       rtol=1e-6, atol=1e-7, err_msg=name)

    def test_out_of_range_solo_shows_everything(self):
        np.testing.assert_array_equal(render(ALL_TYPES, {"solo": len(elements.PASSES) + 1}),
                                      render(ALL_TYPES))

    def test_layer_override_moves_an_element(self):
        glow = elements.element("Glow", layer=elements.PASS_CODES["Other"])
        self.assertEqual(render([glow], {"solo": elements.PASS_CODES["Glow"]}).max(), 0.0)
        np.testing.assert_array_equal(render([glow], {"solo": elements.PASS_CODES["Other"]}),
                                      render([glow]))


class Geometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preview.build()

    def test_axis_position_places_elements_on_the_flare_axis(self):
        for t in (0.0, 0.5, 1.0, 1.5):
            glow = elements.element("Glow", axis=t, size=0.02, dispersion=0.0)
            lum = render([glow], ROUND)[..., :3].sum(-1)
            y, x = np.unravel_index(np.argmax(lum), lum.shape)
            want = along_axis(t)
            self.assertLess(abs(x + 0.5 - want[0]), 1.0, t)
            self.assertLess(abs(y + 0.5 - want[1]), 1.0, t)

    def test_ghosts_follow_articulation_point(self):
        ghost = plain_ghost(axis=2.0, size=0.05, p={"Axis End": 2.0})
        for axis in (CENTER, (W * 0.55, H * 0.42)):
            img = render([ghost], dict(ROUND, axis_center=axis))
            got = centroid(img[..., :3].sum(-1))
            want = along_axis(2.0, center=axis)  # the light mirrored through the axis point
            self.assertLess(abs(got[0] - want[0]), 1.0, axis)
            self.assertLess(abs(got[1] - want[1]), 1.0, axis)

    def test_ghost_set_spans_axis_start_to_end(self):
        ghosts = plain_ghost(axis=0.4, size=0.01, p={"Count": 24, "Axis End": 1.6})
        lum = render([ghosts], ROUND)[..., :3].sum(-1)
        ys, xs = np.nonzero(lum > 0)
        lo, hi = along_axis(0.4), along_axis(1.6)
        margin = 0.01 * H + 2
        self.assertGreaterEqual(xs.min() + 0.5, hi[0] - margin)
        self.assertLessEqual(xs.max() + 0.5, lo[0] + margin)
        # Spread out along the segment, not bunched together.
        self.assertGreater(xs.max() - xs.min(), (lo[0] - hi[0]) * 0.6)

    def test_lens_ghost_channels_sit_at_their_own_positions(self):
        ghost = lenses.Ghost(0, 1, axis=(0.8, 1.2, 1.6), size=(0.04, 0.05, 0.06), color=(1, 1, 1))
        el = elements.element("Lens System", dispersion=1.0, softness=0.0,
                              p={"Barrel Clip": 0.0, "Dust": 0.0, "Hollow": 0.0, "Rim": 0.0})
        img = render([el], ROUND, ghosts_for=lambda _: [ghost])
        for channel, t, size in zip(range(3), ghost.axis, ghost.size):
            got = centroid(img[..., channel])
            want = along_axis(t)
            self.assertLess(abs(got[0] - want[0]), 1.0, channel)
            self.assertLess(abs(got[1] - want[1]), 1.0, channel)
            area = (img[..., channel] > 0).sum()
            self.assertAlmostEqual(area / (np.pi * (size * H) ** 2), 1.0, delta=0.08)

    def test_lens_system_dispersion_scale_merges_channels(self):
        ghost = lenses.Ghost(0, 1, axis=(0.8, 1.2, 1.6), size=(0.03, 0.04, 0.05), color=(1, 1, 1))
        el = elements.element("Lens System", dispersion=0.0, softness=0.0,
                              p={"Barrel Clip": 0.0, "Dust": 0.0, "Hollow": 0.0, "Rim": 0.0})
        img = render([el], ROUND, ghosts_for=lambda _: [ghost])
        np.testing.assert_array_equal(img[..., 0], img[..., 1])
        np.testing.assert_array_equal(img[..., 2], img[..., 1])

    def test_anamorphic_squeezes_ghosts_into_ovals(self):
        iris = elements.element("Iris", axis=1.0, size=0.1, dispersion=0.0, softness=0.0,
                                p={"Hollow": 0.0, "Rim": 0.0, "Barrel Clip": 0.0, "Dust": 0.0})
        for squeeze in (1.0, 0.5):
            mask = render([iris], dict(ROUND, anamorphic=squeeze))[..., 1] > 0
            ys, xs = np.nonzero(mask)
            ratio = float(xs.max() - xs.min() + 1) / float(ys.max() - ys.min() + 1)
            self.assertAlmostEqual(ratio, squeeze, delta=0.06, msg=squeeze)

    def test_aperture_blades_shape_ghosts(self):
        iris = elements.element("Iris", axis=1.0, size=0.15, dispersion=0.0, softness=0.0,
                                p={"Hollow": 0.0, "Rim": 0.0, "Barrel Clip": 0.0, "Dust": 0.0})
        circle = (render([iris], ROUND)[..., 1] > 0).sum()
        hexagon = (render([iris], dict(ROUND, aperture_blades=6, aperture_roundness=0.0))[..., 1] > 0).sum()
        # A regular hexagon has 3*sqrt(3)/(2*pi) of its circumcircle's area.
        self.assertAlmostEqual(hexagon / circle, 3 * np.sqrt(3) / (2 * np.pi), delta=0.02)

    def test_barrel_clip_cuts_ghosts_on_the_outside(self):
        light = (W * 0.9, H * 0.5)  # light right of centre: the clip pushes in from the right
        ghost = plain_ghost(axis=0.3, size=0.12, p={"Axis End": 0.3, "Barrel Clip": 1.0})
        clean = render([plain_ghost(axis=0.3, size=0.12, p={"Axis End": 0.3})], dict(ROUND, light_pos=light))
        cut = render([ghost], dict(ROUND, light_pos=light, barrel_clip=0.5))
        lost = (clean[..., 1] > 0) & ~(cut[..., 1] > 0)
        self.assertGreater(lost.sum(), 20)
        ys, xs = np.nonzero(lost)
        cx = along_axis(0.3, light)[0]
        self.assertLess(xs.mean() + 0.5, cx, "clipped on the side away from the light")
        self.assertEqual(render([ghost], dict(ROUND, light_pos=light, barrel_clip=0.0)).tolist(),
                         clean.tolist())

    def test_footprint_rejection_never_cuts_visible_light(self):
        # Iris, Caustic and lens ghosts skip pixels outside a bound before
        # evaluating; just inside that bound they must already be black.
        ghost = lenses.Ghost(0, 1, axis=(1.0, 1.0, 1.0), size=(0.1, 0.1, 0.1), color=(1, 1, 1))
        yy, xx = np.mgrid[0:H, 0:W]
        cx, cy = along_axis(1.0)
        dist = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy) / H
        for soft in (0.0, 1.0):
            cases = [
                elements.element("Iris", axis=1.0, size=0.1, p={"Rim": 2.0}, dispersion=1.0, softness=soft),
                elements.element("Caustic", axis=1.0, size=0.1, dispersion=1.0, softness=soft),
                elements.element("Lens System", softness=soft, p={"Rim": 2.0}),
            ]
            bound = 0.1 * (1.6 + 1.2 * soft) + 4.0 / H
            band = (dist > bound - 3.0 / H) & (dist < bound)
            for el in cases:
                img = render([el], dict(ROUND, dispersion=1.0), ghosts_for=lambda _: [ghost])
                self.assertGreater(img.max(), 0.0, el["type"])
                self.assertEqual(img[band].max(), 0.0, (el["type"], soft))
                # The widest channel (Iris red: 1.5x) does reach close to the bound.
                if el["type"] == "Iris" and soft == 0.0:
                    lit = dist[img[..., 0] > 0]
                    self.assertGreater(lit.max(), 0.1 * 1.45)

    def test_pixel_aspect_keeps_glow_round(self):
        glow = elements.element("Glow", dispersion=0.0, p={"Core Intensity": 0.0, "White Core": 0.0})
        lum = render([glow], {"pixel_aspect": 2.0, "light_pos": (W * 0.5 / 2.0, H * 0.5)})[..., :3].sum(-1)
        cy, cx = int(H * 0.5), int(W * 0.25)
        # 10 square pixels away = 5 image pixels horizontally, 10 vertically.
        self.assertAlmostEqual(lum[cy, cx + 5] / lum[cy + 10, cx], 1.0, delta=0.08)

    def test_rotate_with_light_is_continuous_across_branch_cut(self):
        # Light just above vs just below the line left of the articulation point,
        # where atan2 jumps from +pi to -pi.
        star = elements.element("Starburst", p={"Fine Rays": 0.0})
        ov = {"spin_with_light": True, "axis_center": (W * 0.5, H * 0.5)}
        above = render([star], dict(ov, light_pos=(W * 0.2, H * 0.5 + 0.01)))
        below = render([star], dict(ov, light_pos=(W * 0.2, H * 0.5 - 0.01)))
        diff = np.abs(above - below)[..., :3].sum() / above[..., :3].sum()
        self.assertLess(diff, 0.02)

    def test_starburst_spike_count_follows_blades(self):
        # Even blade counts give that many spikes; odd counts double.
        star = elements.element("Starburst", size=0.45, dispersion=0.0,
                                p={"Fine Rays": 0.0, "Length Random": 0.0, "Breakup": 0.0})
        r = 0.2 * H
        angles = np.linspace(0, 2 * np.pi, 720, endpoint=False)
        xs = (LIGHT[0] + r * np.cos(angles)).astype(int)
        ys = (LIGHT[1] + r * np.sin(angles)).astype(int)
        for blades, spikes in ((6, 6), (5, 10), (8, 8)):
            lum = render([star], {"aperture_blades": blades})[..., 1]
            ring = lum[ys, xs]
            peaks = ring > ring.max() * 0.3
            rising = np.count_nonzero(peaks & ~np.roll(peaks, 1))
            self.assertEqual(rising, spikes, blades)


class LightAndOcclusion(unittest.TestCase):
    """Global visibility controls scale the whole stack."""

    @classmethod
    def setUpClass(cls):
        preview.build()
        cls.default = render()

    def test_offscreen_fade(self):
        far = render(None, {"light_pos": (W + H * 0.6, H * 0.5), "offscreen_fade": 0.5})
        self.assertEqual(far.max(), 0.0)
        near = render(None, {"light_pos": (W + H * 0.1, H * 0.5), "offscreen_fade": 0.5})
        self.assertGreater(near.max(), 0.0)

    def test_occlusion(self):
        occ = np.zeros((H, W, 4), np.float32)
        occ[..., 3] = 1.0
        self.assertEqual(render(None, {"occlusion_enable": True}, occlusion=occ).max(), 0.0)
        half = np.zeros((H, W, 4), np.float32)
        half[:, : int(W * 0.7), 3] = 1.0
        dim = render(None, {"occlusion_enable": True, "occlusion_radius": 10.0}, occlusion=half)
        ratio = dim[..., :3].sum() / self.default[..., :3].sum()
        self.assertTrue(0.3 < ratio < 0.7, ratio)
        inv = render(None, {"occlusion_enable": True, "occlusion_invert": True}, occlusion=occ)
        np.testing.assert_allclose(inv, self.default, rtol=1e-5, atol=1e-6)

    def depth_plate(self, value):
        occ = np.zeros((H, W, 4), np.float32)
        occ[..., 0] = value  # the group copies depth.Z into red
        return occ

    def test_depth_occlusion_inverse_z(self):
        ov = {"occlusion_enable": True, "occlusion_mode": 1}
        near_wall = self.depth_plate(1.0 / 10.0)  # geometry 10 units away
        self.assertEqual(render(None, dict(ov, light_depth=100.0), occlusion=near_wall).max(), 0.0)
        np.testing.assert_allclose(render(None, dict(ov, light_depth=5.0), occlusion=near_wall),
                                   self.default, rtol=1e-6)
        np.testing.assert_allclose(render(None, dict(ov, light_depth=100.0), occlusion=self.depth_plate(0.0)),
                                   self.default, rtol=1e-6)

    def test_depth_occlusion_linear_z(self):
        ov = {"occlusion_enable": True, "occlusion_mode": 2}
        wall = self.depth_plate(10.0)
        self.assertEqual(render(None, dict(ov, light_depth=100.0), occlusion=wall).max(), 0.0)
        np.testing.assert_allclose(render(None, dict(ov, light_depth=5.0), occlusion=wall),
                                   self.default, rtol=1e-6)
        np.testing.assert_allclose(render(None, dict(ov, light_depth=100.0), occlusion=self.depth_plate(0.0)),
                                   self.default, rtol=1e-6)

    def test_source_brightness(self):
        for level, amount, expected in ((0.5, 1.0, 0.5), (2.0, 1.0, 2.0), (0.5, 0.5, 0.75),
                                        (40.0, 1.0, 10.0)):
            plate = np.full((H, W, 4), level, np.float32)
            got = render(None, {"source_intensity": amount}, src=plate)
            np.testing.assert_allclose(got[..., :3], self.default[..., :3] * expected,
                                       rtol=1e-4, atol=1e-6, err_msg=str(level))

    def test_source_black_and_white_points(self):
        plate = np.full((H, W, 4), 3.0, np.float32)
        got = render(None, {"source_intensity": 1.0, "source_black": 1.0, "source_white": 5.0}, src=plate)
        np.testing.assert_allclose(got[..., :3], self.default[..., :3] * 0.5, rtol=1e-4, atol=1e-6)

    def test_source_color(self):
        plate = np.zeros((H, W, 4), np.float32)
        plate[..., :3] = (1.0, 0.5, 0.25)
        lum = 0.2126 * 1.0 + 0.7152 * 0.5 + 0.0722 * 0.25
        got = render(None, {"source_color": 1.0}, src=plate)
        tint = np.array([1.0, 0.5, 0.25]) / lum
        np.testing.assert_allclose(got[..., :3], self.default[..., :3] * tint, rtol=1e-4, atol=1e-6)

    def test_sample_radius_is_relative_to_frame_height(self):
        # Radius 40 = 4% of the frame height at any resolution: a matte that
        # starts 4.5% away is never sampled, one at 3% is.
        glow = [elements.element("Glow")]
        for w, h in ((320, 180), (640, 360)):
            yy, xx = np.mgrid[0:h, 0:w]
            dist = np.hypot(xx + 0.5 - w * 0.7, yy + 0.5 - h * 0.7) / h
            base = render(glow, None, w, h)
            for edge, dims in ((0.045, False), (0.03, True)):
                occ = np.repeat((dist > edge)[..., None], 4, axis=-1).astype(np.float32)
                for key, amount in (("occlusion", {"occlusion_enable": True, "occlusion_radius": 40.0}),
                                    ("source", {"source_intensity": 1.0, "source_radius": 40.0})):
                    if key == "occlusion":
                        out = render(glow, amount, w, h, occlusion=occ)
                    else:  # bright plate only beyond the edge
                        out = render(glow, amount, w, h, src=np.ascontiguousarray(1.0 - occ))
                    ratio = out[..., :3].sum() / base[..., :3].sum()
                    if dims:
                        self.assertLess(ratio, 0.95, (w, edge, key))
                    else:
                        self.assertAlmostEqual(ratio, 1.0, places=5, msg=(w, edge, key))


if __name__ == "__main__":
    unittest.main()
