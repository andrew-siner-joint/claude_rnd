
import numpy as np
import pytest

from hdristitch import sun


def sky(width=2048, lon=-70.0, lat=30.0, value=5000.0, radius_px=2.0):
    h = width // 2
    img = np.ones((h, width, 3), np.float32) * np.array([0.6, 0.8, 1.0], np.float32)
    img[h // 2:] = 0.2
    x = (lon + 180) / 360 * width - 0.5
    y = (90 - lat) / 180 * h - 0.5
    yy, xx = np.mgrid[0:h, 0:width]
    disc = np.hypot(xx - x, yy - y) <= radius_px
    img[disc] = value
    omega = (sun.pixel_solid_angle(np.arange(h), width, h)[:, None] * disc).sum()
    return img, omega


def test_direction_and_irradiance_of_unclipped_sun():
    img, omega = sky()
    info = sun.find_sun(img, clip_level=1e5)
    assert info["lon_deg"] == pytest.approx(-70.0, abs=0.1)
    assert info["lat_deg"] == pytest.approx(30.0, abs=0.1)
    assert not info["clipped"]
    expected = (5000.0 - np.array([0.6, 0.8, 1.0])) * omega
    assert info["measured_irradiance_rgb"] == pytest.approx(expected, rel=0.02)
    sun.suggest(info, img)
    assert info["strength_source"].startswith("measured")
    assert info["strength"] == pytest.approx(float(expected @ [0.2126, 0.7152, 0.0722])
                                             / float(np.array(info["color"]) @ [0.2126, 0.7152, 0.0722]),
                                             rel=0.03)
    rx, _, rz = info["blender_rotation_deg"]
    assert rx == pytest.approx(60.0, abs=0.1) and rz == pytest.approx(160.0, abs=0.1)


def test_sun_across_the_seam():
    img, _ = sky(lon=179.9, lat=10.0, radius_px=3.0)
    info = sun.find_sun(img)
    assert abs(abs(info["lon_deg"]) - 179.9) < 0.2


def test_clipped_sun_gets_an_estimate_and_neutral_colour():
    img, _ = sky(radius_px=4.0)
    img[img[..., 0] > 100] = 64.0   # flat plateau = clipped
    assert sun.find_sun(img, clip_level=64.0)["clipped"]
    assert not sun.find_sun(img, clip_level=1000.0)["clipped"]
    info = sun.find_sun(img)            # no clip level known: the flat top says clipped
    assert info["clipped"]
    sun.suggest(info, img, clear_sky_ratio=7.0)
    assert info["color"] == [1.0, 1.0, 1.0]
    e_sky = np.array(info["sky_horizontal_irradiance_rgb"])
    assert info["strength"] == pytest.approx(7.0 * float(e_sky @ [0.2126, 0.7152, 0.0722]),
                                             rel=1e-6)
    card = sun.suggest(dict(info), img, card_ratio=4.0)
    assert card["strength_source"].startswith("gray card")


def test_remove_sun_leaves_sky():
    img, _ = sky()
    info = sun.find_sun(img)
    clean = sun.remove_sun(img, info)
    assert clean.max() < 2.0
    assert np.allclose(clean[700:], img[700:])   # far away: untouched


def test_overcast_has_no_sun():
    img = np.ones((512, 1024, 3), np.float32)
    assert sun.find_sun(img) is None
