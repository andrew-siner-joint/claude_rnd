import numpy as np
import pytest

from hdristitch import color

import synth


@pytest.mark.parametrize("temp,tint", [(2850, 0), (3200, 5), (4300, -12), (5600, 10),
                                       (6500, 0), (9000, 30), (20000, -20)])
def test_kelvin_tint_round_trip(temp, tint):
    t, n = color.xy_to_kelvin_tint(*color.kelvin_tint_to_xy(temp, tint))
    assert t == pytest.approx(temp, rel=1e-4)
    assert n == pytest.approx(tint, abs=1e-3)


def test_standard_illuminants_on_adobe_scale():
    # Lightroom/Camera Raw read D65 as ~6500 K, +10 and Illuminant A as ~2856 K, 0
    t, n = color.xy_to_kelvin_tint(*color.D65_XY)
    assert t == pytest.approx(6504, abs=5) and n == pytest.approx(10, abs=0.5)
    t, n = color.xy_to_kelvin_tint(0.44757, 0.40745)
    assert t == pytest.approx(2856, abs=5) and n == pytest.approx(0, abs=0.5)


def test_multipliers_neutralise_their_illuminant():
    cam = synth.CAM_FROM_XYZ
    for temp in (3000, 5600, 8000):
        xy = color.kelvin_tint_to_xy(temp, 0)
        gains = color.multipliers_for_xy(cam, xy)
        assert gains[1] == 1.0
        neutral = cam @ color.xy_to_xyz(*xy)
        balanced = neutral * gains
        assert np.allclose(balanced / balanced[1], 1.0)
        assert color.xy_for_multipliers(cam, gains) == pytest.approx(xy, abs=1e-9)


def test_camera_to_output_maps_balanced_white_to_white():
    for gamut in ("rec709", "acescg", "rec2020"):
        m = color.camera_to_output(synth.CAM_FROM_XYZ, gamut)
        assert np.allclose(m @ np.ones(3), 1.0, atol=1e-6)


def test_camera_to_output_recovers_scene_colours():
    # scene Rec.709 -> camera -> D65 white balance -> matrix == scene (x green gain)
    rng = np.random.default_rng(1)
    scene = rng.uniform(0, 1, (50, 3))
    cam = synth.scene_to_camera(scene)
    gains = color.multipliers_for_xy(synth.CAM_FROM_XYZ, color.D65_XY)
    out = (cam * gains) @ color.camera_to_output(synth.CAM_FROM_XYZ).T
    green = (synth.CAM_FROM_XYZ @ synth.XYZ_FROM_SRGB @ np.ones(3))[1]
    assert np.allclose(out, scene * green, rtol=2e-3, atol=1e-6)


def test_parse_wb():
    assert color.parse_wb("auto") == "auto"
    assert color.parse_wb("AsShot") == "asshot"
    assert color.parse_wb("5600") == (5600.0, 0.0)
    assert color.parse_wb("5600K, +10") == (5600.0, 10.0)
    assert color.parse_wb("3200,-4") == (3200.0, -4.0)
    with pytest.raises(ValueError):
        color.parse_wb("warm")
    with pytest.raises(ValueError):
        color.parse_wb("100")


XMP_ATTR = '''<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description
 crs:WhiteBalance="Custom" crs:Temperature="5450" crs:Tint="+7" crs:Exposure2012="0"/>
</rdf:RDF></x:xmpmeta>'''
XMP_ELEM = '''<rdf:Description><crs:Temperature>4100</crs:Temperature>
<crs:Tint>-3</crs:Tint></rdf:Description>'''
XMP_ASSHOT = '''<rdf:Description crs:WhiteBalance="As Shot"/>'''


def test_xmp_sidecars(tmp_path):
    raw = tmp_path / "DSC00001.ARW"
    raw.write_bytes(b"")
    assert color.read_xmp_white_balance(raw) is None
    (tmp_path / "DSC00001.xmp").write_text(XMP_ATTR)
    assert color.read_xmp_white_balance(raw) == (5450.0, 7.0)
    (tmp_path / "DSC00001.xmp").write_text(XMP_ELEM)
    assert color.read_xmp_white_balance(raw) == (4100.0, -3.0)
    (tmp_path / "DSC00001.xmp").write_text(XMP_ASSHOT)
    assert color.read_xmp_white_balance(raw) == "asshot"
