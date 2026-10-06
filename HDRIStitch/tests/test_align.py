import numpy as np
import pytest

from hdristitch import align
from hdristitch.hugin import Hugin

from conftest import needs_hugin

pytestmark = needs_hugin

LINE = ('i w600 h400 f2 v250 Ra0 Rb0 Rc0 Rd0 Re0 Eev0 Er1 Eb1 r{r} p{p} y{y} TrX0 TrY0 TrZ0 '
        'Tpy0 Tpp0 j0 a0 b0 c0 d0 e0 g0 t0 Va1 Vb0 Vc0 Vd0 Vx0 Vy0 Vm5 n"x.jpg"')


def project(path, poses):
    lines = ['p f2 w720 h360 v360 E0 R0 n"TIFF_m"', "m i0"]
    lines += [LINE.format(y=y, p=p, r=r) for y, p, r in poses]
    path.write_text("\n".join(lines) + "\n")
    return path.name


def test_moved_degrees_uses_hugins_own_geometry(tmp_path):
    hugin = Hugin(tmp_path)
    a = project(tmp_path / "a.pto", [(0, 0, 0), (60, 0, 0), (0, 90, 0)])
    b = project(tmp_path / "b.pto", [(0, 0, 0), (65, 0, 0), (0, 90, 0)])
    assert align.moved_degrees(hugin, a, b, 0, (600, 400)) == pytest.approx(0, abs=0.05)
    assert align.moved_degrees(hugin, a, b, 1, (600, 400)) == pytest.approx(5, abs=0.1)
    # straight up, yaw and roll trade off (gimbal lock): a 3 deg turn of the
    # zenith shot is 3 deg however it's written
    c = project(tmp_path / "c.pto", [(0, 0, 0), (60, 0, 0), (3, 90, 0)])
    assert align.moved_degrees(hugin, a, c, 2, (600, 400)) == pytest.approx(3, abs=0.2)


def test_circle_detection(tmp_path):
    import cv2

    img = np.zeros((800, 1200, 3), np.uint8)
    cv2.circle(img, (610, 395), 372, (180, 160, 140), -1)
    paths = []
    for k in range(3):
        p = tmp_path / ("p%d.jpg" % k)
        cv2.imwrite(str(p), img)
        paths.append(p)
    cx, cy, r = align.detect_circle(paths)
    assert (cx, cy, r) == (pytest.approx(610.5, abs=2), pytest.approx(395.5, abs=2),
                           pytest.approx(372, abs=3))
    full = np.full((800, 1200, 3), 150, np.uint8)
    cv2.imwrite(str(paths[0]), full)
    assert align.detect_circle(paths[:1]) is None
