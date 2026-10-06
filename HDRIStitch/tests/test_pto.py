import pytest

from hdristitch import pto

PROJECT = '''# hugin project file
#hugin_ptoversion 2
p f2 w8192 h4096 v360  k0 E12.5 R0 n"TIFF_m c:LZW r:CROP"
m i0

#-hugin  cropFactor=1
i w1200 h800 f2 v294.5 Ra0 Rb0 Rc0 Rd0 Re0 Eev12.3 Er1.02 Eb0.98 r0 p0 y0 TrX0 TrY0 TrZ0 Tpy0 Tpp0 j0 a0.01 b-0.02 c0.003 d1.5 e-2 g0 t0 Va1 Vb-0.2 Vc0.1 Vd0 Vx0 Vy0  Vm5 S240,960,40,760 n"proxies/pos01.jpg"
#-hugin  cropFactor=1
i w1200 h800 f2 v=0 Ra=0 Rb=0 Rc=0 Rd=0 Re=0 Eev12.3 Er1 Eb1 r1.5 p-0.5 y60.2 TrX0 TrY0 TrZ0 Tpy0 Tpp0 j0 a=0 b=0 c=0 d=0 e=0 g=0 t=0 Va=0 Vb=0 Vc=0 Vd=0 Vx=0 Vy=0  Vm5 S240,960,40,760 n"proxies/pos 02.jpg"

v y1
v p1
c n0 N1 x10 y20 X30 Y40 t0
c n0 N0 x10 y20 X30 Y45 t1
k i1 t0 p"0 0 100 0 100 100"
'''


def test_parse_and_links():
    proj = pto.Project(PROJECT)
    images = proj.images
    assert len(images) == 2
    assert images[1].filename == "proxies/pos 02.jpg"
    assert proj.value(1, "v") == pytest.approx(294.5)       # linked to image 0
    assert proj.value(1, "b") == pytest.approx(-0.02)
    assert proj.pose(1) == (60.2, -0.5, 1.5)
    assert proj.crop(1) == [240, 960, 40, 760]


def test_round_trip_preserves_text():
    proj = pto.Project(PROJECT)
    proj.set_images(proj.images)
    assert [ln.split() for ln in proj.lines] == [ln.split() for ln in PROJECT.rstrip("\n").splitlines()]


def test_apply_template_swaps_images_and_drops_points():
    tpl = pto.Project(PROJECT)
    new = pto.apply_template(tpl, ["a.jpg", "b.jpg"], [(1200, 800)] * 2)
    assert [im.filename for im in new.images] == ["a.jpg", "b.jpg"]
    assert new.count("c ") == 0 and new.count("k ") == 1
    assert new.pose(1) == tpl.pose(1)
    with pytest.raises(ValueError, match="1200x800"):
        pto.apply_template(tpl, ["a.jpg", "b.jpg"], [(1000, 800)] * 2)
    with pytest.raises(ValueError, match="2 images"):
        pto.apply_template(tpl, ["a.jpg"])


def test_coordinate_project_neutralises_photometrics():
    proj = pto.coordinate_project(pto.Project(PROJECT), "ramp.tif", 2048, 1024)
    assert 'p f2 w2048 h1024 v360 k0 E0 R0 n"TIFF_m c:NONE r:CROP"' in proj.lines
    assert "m i5" in proj.lines
    for k, im in enumerate(proj.images):
        assert im.filename == "ramp.tif"
        assert im.raw("Eev") == "0" and im.raw("Er") == "1" and im.raw("Vb") == "0"
    # geometry and masks untouched
    assert proj.value(0, "b") == pytest.approx(-0.02)
    assert proj.pose(1) == (60.2, -0.5, 1.5)
    assert proj.count("k ") == 1 and proj.count("c ") == 0


def test_template_strips_control_points():
    t = pto.as_template(pto.Project(PROJECT))
    assert t.count("c ") == 0 and t.count("k ") == 1 and t.lines[0].startswith("# HDRIStitch")
