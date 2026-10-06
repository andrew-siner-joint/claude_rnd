import numpy as np
import pytest

from hdristitch import render

from conftest import needs_hugin


def test_fill_holes_is_smooth_and_keeps_valid_pixels():
    img = np.zeros((128, 256, 3), np.float32)
    img[..., 0] = np.linspace(0, 1, 256)[None, :]
    img[..., 1] = 0.5
    valid = np.ones((128, 256), bool)
    valid[100:] = False
    orig = img.copy()
    render.fill_holes(img, valid)
    assert np.array_equal(img[valid], orig[valid])
    assert np.all(np.isfinite(img)) and img[120, :, 1] == pytest.approx(0.5, abs=1e-3)
    assert np.all(np.diff(img[120, 10:-10, 0]) > -0.02)


def test_ramp_round_trip(tmp_path):
    render.write_ramp(tmp_path / "r.tif", 300, 200)
    import tifffile

    ramp = tifffile.imread(tmp_path / "r.tif")
    assert ramp[10, 20, 0] * 300 - 0.5 == pytest.approx(20, abs=1e-3)
    assert ramp[10, 20, 1] * 200 - 0.5 == pytest.approx(10, abs=1e-3)


@needs_hugin
def test_nona_coordinate_maps_are_subpixel_exact(tmp_path):
    from hdristitch import pto
    from hdristitch.hugin import Hugin

    text = ('p f2 w720 h360 v360 E0 R0 n"TIFF_m c:NONE r:CROP"\nm i0\n'
            'i w600 h400 f2 v250 Ra0 Rb0 Rc0 Rd0 Re0 Eev9 Er1.1 Eb0.9 r3 p10 y20 TrX0 TrY0 '
            'TrZ0 Tpy0 Tpp0 j0 a0 b0.01 c0 d0 e0 g0 t0 Va1 Vb-0.3 Vc0 Vd0 Vx0 Vy0 Vm5 '
            'S110,490,10,390 n"x.jpg"\n')
    (tmp_path / "p.pto").write_text(text)
    hugin = Hugin(tmp_path)
    (tmp_path / "render").mkdir()
    render.write_ramp(tmp_path / "render" / "ramp.tif", 600, 400)
    pto.coordinate_project(pto.Project(text), "ramp.tif", 720, 360).save(
        tmp_path / "render" / "c.pto")
    hugin.run("nona", "-m", "TIFF_m", "-r", "ldr", "--ignore-exposure", "-p", "FLOAT",
              "-z", "NONE", "-o", "render/out_", "render/c.pto")
    data, x0, y0 = render.read_coords(tmp_path / "render" / "out_0000.tif")
    alpha = data[..., 3] > 0.5
    # neutralised photometrics: the constant channel survives exactly
    assert data[..., 2][alpha] == pytest.approx(0.5, abs=1e-5)
    xs = data[..., 0][alpha] * 600 - 0.5
    ys = data[..., 1][alpha] * 400 - 0.5
    r = np.hypot(xs - 299.5, ys - 199.5)
    assert r.max() <= 191 and xs.min() >= 109 and ys.min() >= 9
    assert 0 <= x0 < 720 and 0 <= y0 < 360
