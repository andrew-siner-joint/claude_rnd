import numpy as np
import pytest

from hdristitch import color, exif, merge, rawdecode

import synth

H, W = 600, 900


def scene():
    yy, xx = np.mgrid[0:H, 0:W]
    stops = -14 + 20 * (xx / W) + 1.5 * np.sin(yy / 23.0) * np.cos(xx / 17.0)
    return stops, (2.0 ** stops)[..., None] * np.array([0.9, 1.0, 0.8])


def bracket(tmp_path, cam, errors=None, evs=(0, -3, 3, -6, 6), base=0.25):
    rng = np.random.default_rng(0)
    paths = []
    for k, ev in enumerate(evs):
        t = (1 / 125) * 2 ** ev
        actual = t * (errors or {}).get(ev, 1.0)
        p = tmp_path / ("f%d.dng" % k)
        synth.write_dng(str(p), cam, exposure=actual * 125 * base, shutter=t, noise=0.0005,
                        rng=rng)
        paths.append(p)
    return exif.read_shots(paths)


def test_decode_is_linear_and_normalised(tmp_path):
    cam = np.zeros((64, 96, 3))
    cam[:, :32], cam[:, 32:64], cam[:, 64:] = 0.1, 0.5, 2.0
    synth.write_dng(str(tmp_path / "a.dng"), cam, exposure=1.0)
    frame = rawdecode.decode(tmp_path / "a.dng")
    assert frame.rgb[32, 5] == pytest.approx([0.1] * 3, abs=2e-4)
    assert frame.rgb[32, 48] == pytest.approx([0.5] * 3, abs=2e-4)
    assert frame.rgb[32, 90] == pytest.approx([1.0] * 3, abs=1e-6)
    assert frame.saturated[32, 90] and not frame.saturated[32, 5]
    info = rawdecode.camera_info(tmp_path / "a.dng")
    assert np.allclose(info.cam_from_xyz, synth.CAM_FROM_XYZ, atol=1e-3)


def test_merge_recovers_radiance_and_fixes_shutter_error(tmp_path):
    stops, cam = scene()
    shots = bracket(tmp_path, cam, errors={3: 1.06})
    ref = 1 / 125 / 64
    for refine, worst in ((False, 0.04), (True, 0.004)):
        rgb, clipped, ratios, _ = merge.merge_bracket(shots, refine=refine)
        rel = rgb[..., 1] * ref / (cam[..., 1] * 0.25)
        errs = [abs(np.median(rel[(stops >= lo) & (stops < lo + 2)]) - 1)
                for lo in range(-6, 6, 2)]
        if refine:
            assert max(errs) < worst
            assert any(m for _, _, m in ratios)
        else:
            assert max(errs) > 0.03   # the 6% shutter error shows without refinement


def test_clipped_highlights_are_neutral_not_magenta(tmp_path):
    cam = np.full((60, 90, 3), 0.2)
    cam[20:40, 30:60] = synth.scene_to_camera(np.array([5000.0, 5000.0, 5000.0]))
    shots = bracket(tmp_path, cam, evs=(0, -2, 2))
    rgb, clipped, _, mask = merge.merge_bracket(shots)
    assert clipped > 0 and mask is not None
    gains = color.multipliers_for_xy(synth.CAM_FROM_XYZ, color.D65_XY)
    merge.neutralise_clipped(rgb, gains, mask)
    out = merge.to_output(rgb, gains, color.camera_to_output(synth.CAM_FROM_XYZ))
    core = out[30, 45]
    assert core.max() / core.min() == pytest.approx(1.0, abs=1e-3)


def test_default_jobs_is_sane():
    assert 1 <= merge.default_jobs(8, 33) <= 8
