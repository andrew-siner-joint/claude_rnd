import importlib
import os
import sys

import pytest

HERE = os.path.dirname(__file__)


@pytest.fixture
def nk(monkeypatch):
    import fake_nuke

    fake_nuke.reset()
    monkeypatch.setitem(sys.modules, "nuke", fake_nuke)
    monkeypatch.syspath_prepend(os.path.join(HERE, "..", "nuke"))
    mod = importlib.import_module("hdristitch_nuke")
    mod = importlib.reload(mod)
    return mod, fake_nuke


def _by_name(fake, name):
    return next(n for n in fake.created if n.name() == name)


def test_pole_patch_wiring(nk):
    mod, fake = nk
    read = fake.nodes.Read(file="/x/shot.exr")
    mix = mod.pole_patch("nadir", read)
    to_h = _by_name(fake, "Nadir_to_horizon")
    back = _by_name(fake, "Nadir_back")
    assert to_h["input"].value() == "Lat Long map" and to_h["output"].value() == "Lat Long map"
    assert back["output"].value() == "Lat Long map"
    assert to_h["format"].value() is read.format()
    assert to_h["rz"].value() == 90.0 and back["rz"].value() == -90.0
    assert to_h.input(0) is read and back.input(0).Class() == "RotoPaint"
    band = _by_name(fake, "Nadir_band")
    x, y, r, t = band["area"].value()
    assert y < 0 and t == pytest.approx(0.2 * 2048) and band["output"].value() == "alpha"
    assert mix.input(0) is read and mix.input(1) is back and mix.input(2) is band
    assert not fake.messages


def test_zenith_band_is_at_the_top(nk):
    mod, fake = nk
    read = fake.nodes.Read(file="/x/shot.exr")
    mod.pole_patch("zenith", read)
    x, y, r, t = _by_name(fake, "Zenith_band")["area"].value()
    assert y == pytest.approx(0.8 * 2048) and t > 2048


def test_write_is_raw_float_exr(nk):
    mod, fake = nk
    w = mod.write(fake.nodes.Read(file="a.exr"), "/x/out.exr")
    assert w["file_type"].value() == "exr" and w["raw"].value() is True
    assert w["datatype"].value() == "32 bit float"
    assert w["compression"].value() == "Zip (1 scanline)"
    assert w["metadata"].value().startswith("all metadata")


def test_gray_card_expressions(nk):
    mod, fake = nk
    group = mod.gray_card(fake.nodes.Read(file="a.exr"))
    mult = next(n for n in group.children if n.Class() == "Multiply")
    exprs = mult["value"].expressions
    assert set(exprs) == {0, 1, 2}
    assert "parent.sample.g" in exprs[1] and "parent.target" in exprs[0]


def test_cleanup_script_chain(nk, tmp_path):
    mod, fake = nk
    exr = tmp_path / "shoot.exr"
    exr.write_bytes(b"")
    (tmp_path / "shoot_holes.png").write_bytes(b"")
    fake.filename[0] = str(exr)
    out = mod.cleanup_script()
    assert out["file"].value() == str(tmp_path / "shoot_clean.exr")
    card = out.input(0)
    assert card.name() == "GrayCard_Calibrate" and card["disable"].value() is True
    names = [n.name() for n in fake.created]
    assert "Orient_HDRI" in names and "Nadir_patch" in names and "Nadir_no_photo" in names
