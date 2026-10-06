"""Full runs of `hdri` on synthetic shoots (8 positions x 3 brackets of DNG
raws rendered from a known HDR scene), checked against that scene."""
import json

import numpy as np
import pytest

from hdristitch import cli, imageio

import make_shoot
import synth
from conftest import needs_hugin

pytestmark = [pytest.mark.slow, needs_hugin]
WIDTH = 1536
BRACKETS = (0, -4, 4)


@pytest.fixture(scope="module")
def scene():
    return synth.make_scene(WIDTH, seed=7)


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    h = tmp_path_factory.mktemp("home")
    (h / "templates").mkdir()
    return h


def run(home, monkeypatch, *argv):
    monkeypatch.setenv("HDRISTITCH_HOME", str(home))
    monkeypatch.setenv("HDRI_DEBUG", "1")
    assert cli.main(list(argv)) == 0


def truth(scene):
    green = (synth.CAM_FROM_XYZ @ synth.XYZ_FROM_SRGB @ np.ones(3))[1]
    return scene * 0.25 * green   # reference exposure; 1.0 = green clip


def compare(out, ref):
    import cv2

    blur = lambda a: cv2.GaussianBlur(a, (0, 0), 1.5)
    o, r = blur(out), blur(ref)
    keep = ref.max(axis=2) < 10   # skip the sun
    rel = (o[keep] - r[keep]) / (r[keep] + 0.005)
    return np.median(rel, axis=0), np.median(np.abs(rel))


def test_first_shoot_then_template(scene, home, tmp_path, monkeypatch):
    shoot1 = tmp_path / "Location A"
    make_shoot.make_shoot(str(shoot1), scene, brackets=BRACKETS, seed=1)
    run(home, monkeypatch, "process", str(shoot1), "--width", str(WIDTH), "--wb", "d65")
    out_dir = shoot1 / "hdri"
    rgb, header = imageio.read_exr(out_dir / "Location_A.exr")
    meta = imageio.exr_metadata(header)
    assert rgb.shape == (WIDTH // 2, WIDTH, 3)
    assert meta["gamut"] == "rec709" and meta["positions"] == 8 and meta["brackets"] == 3
    bias, err = compare(rgb, truth(scene))
    assert np.all(np.abs(bias) < 0.01), bias     # colour and exposure
    assert err < 0.05
    run_json = json.loads((out_dir / "work" / "set01" / "run.json").read_text())
    assert run_json["alignment"]["route"] == "auto"
    assert run_json["alignment"]["mean"] < 1.5
    sun_info = json.loads((out_dir / "Location_A_sun.json").read_text())
    assert sun_info["lon_deg"] == pytest.approx(40.0, abs=0.5)
    assert sun_info["lat_deg"] == pytest.approx(35.0, abs=0.5)
    assert sun_info["clipped"]
    assert (out_dir / "Location_A_nosun.exr").exists()
    assert (out_dir / "Location_A_preview.jpg").exists()
    assert (out_dir / "Location_A_1k.exr").exists() or WIDTH <= 2048

    run(home, monkeypatch, "template", "save", str(shoot1))
    assert (home / "templates" / "default.pto").exists()

    # second shoot: same rig with a little play in every position, white
    # balance picked in "Lightroom" (XMP sidecars)
    shoot2 = tmp_path / "LocationB"
    make_shoot.make_shoot(str(shoot2), scene, brackets=BRACKETS, seed=2, jitter=0.25,
                          start="2026:10:06 12:00:00")
    for raw in shoot2.glob("*.dng"):
        raw.with_suffix(".xmp").write_text(
            '<rdf:Description crs:WhiteBalance="Custom" crs:Temperature="6504" '
            'crs:Tint="+10"/>')
    run(home, monkeypatch, "process", str(shoot2), "--width", str(WIDTH))
    run2 = json.loads((shoot2 / "hdri" / "work" / "set01" / "run.json").read_text())
    assert run2["alignment"]["route"] == "template"
    assert run2["alignment"]["mean"] < 1.5
    assert run2["white_balance"].startswith("XMP sidecar: 6504 K")
    rgb2, _ = imageio.read_exr(shoot2 / "hdri" / "LocationB.exr")
    bias2, _ = compare(rgb2, truth(scene))
    assert np.all(np.abs(bias2) < 0.02), bias2

    # editing the project in Hugin, then re-rendering only
    before = (shoot2 / "hdri" / "LocationB.exr").stat().st_mtime_ns
    run(home, monkeypatch, "render", str(shoot2), "--width", "1024", "--no-sun")
    assert (shoot2 / "hdri" / "LocationB.exr").stat().st_mtime_ns != before
    assert imageio.read_exr(shoot2 / "hdri" / "LocationB.exr")[0].shape == (512, 1024, 3)
    assert not (shoot2 / "hdri" / "LocationB_nosun.exr").exists()   # stale, removed


def test_grouping_error_is_friendly(scene, home, tmp_path, monkeypatch, capsys):
    shoot = tmp_path / "broken"
    make_shoot.make_shoot(str(shoot), scene, brackets=BRACKETS, rig=synth.RIG[:7], seed=3,
                          size=(300, 200), circle=190)
    monkeypatch.setenv("HDRISTITCH_HOME", str(home))
    monkeypatch.delenv("HDRI_DEBUG", raising=False)
    assert cli.main(["process", str(shoot)]) == 1
    assert "not a multiple of 8 positions" in capsys.readouterr().err
    assert cli.main(["process", str(shoot), "--positions", "7", "--dry-run"]) == 0
