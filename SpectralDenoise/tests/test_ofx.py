"""The built OFX plugin, driven through the HostSupport-based test host:
parameters, the Analyse button, profile storage, temporal frame requests,
mask / mix / output modes, render scale and the profile modes.

Rendered pixels are compared against the core (sdcore) run on the same data,
so these tests check the plugin's glue, and test_core.py checks the maths."""
import os

import numpy as np
import pytest

import ofxhost
import sdcore
import synth

W, H = 360, 240
FIRST, LAST = 1000, 1004


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """Five noisy frames on disk (OFX row order), with the panel box in
    canonical (bottom-up) coordinates."""
    d = tmp_path_factory.mktemp("clip")
    clean, noisy, rect, _ = synth.sequence(h=H, w=W, panel=100)
    for i, f in enumerate(noisy):
        ofxhost.write_raw(os.path.join(d, "src.%d.raw" % (FIRST + i)), f)
    x1, y1, x2, y2 = rect
    box = (x1, H - y2, x2, H - y1)
    return dict(dir=str(d), clean=clean, noisy=noisy, box=box,
                pattern=os.path.join(str(d), "src.%d.raw"))


def base(clip, extra=()):
    return ["clip Source %d %d %d %d %s" % (W, H, FIRST, LAST, clip["pattern"]), "create"] + list(extra)


def analysed(clip, t=1002):
    x1, y1, x2, y2 = clip["box"]
    return ["time %d" % t, "set analysisBottomLeft %g %g" % (x1, y1), "set analysisTopRight %g %g" % (x2, y2),
            "press analyseNoise"]


def flipped(frames):
    return [None if f is None else np.ascontiguousarray(f[::-1]) for f in frames]


def core_render(frames, ref, profile, **kw):
    """The core on OFX-ordered rows, returned top-down like read_raw."""
    return sdcore.denoise(flipped(frames), ref, profile, **kw)[::-1]


def render_path(clip, name):
    return os.path.join(clip["dir"], name)


def test_parameters_described(clip, tmp_path):
    r = ofxhost.run(["describe"], str(tmp_path))
    assert r.code == 0, r
    params = {l.split()[1]: l for l in r.startswith("PARAM")}
    for name in ["profileMode", "analysisBottomLeft", "analysisTopRight", "analyseNoise", "profileStatus",
                 "profileData", "inputColourspace", "brightnessAdaptive", "noiseLevel", "lumaLevel", "chromaLevel",
                 "lumaReduction", "chromaReduction", "fineLevel", "mediumLevel", "coarseLevel", "veryCoarseLevel",
                 "sharpen", "temporalRadius", "motionTolerance", "quality", "outputMode", "mix", "maskInvert"]:
        assert name in params, name
    assert "secret=1" in params["profileData"]
    assert "secret=0" in params["noiseLevel"]
    assert "default=1" in params["temporalRadius"]


def test_filter_context_has_no_mask(clip, tmp_path):
    r = ofxhost.run(["describe"], str(tmp_path), context="filter")
    assert r.code == 0, r
    assert not any(" maskInvert " in l for l in r.lines)


def test_analyse_stores_profile_and_status(clip, tmp_path):
    r = ofxhost.run(base(clip, analysed(clip) + ["get profileData", "get profileStatus", "get profileMode"]),
                    str(tmp_path))
    assert r.code == 0, r
    prof = r.values("profileData")
    assert prof.startswith("SDP1")
    assert r.values("profileStatus").startswith("Region, frame 1002")
    assert r.values("profileMode") == "0"
    # identical to the core analysing the same rows and box
    x1, y1, x2, y2 = clip["box"]
    ref = sdcore.analyse(flipped([clip["noisy"][2]])[0], region=(x1, y1, x2, y2))
    assert ref.split()[13:] == prof.split()[13:]


def test_analyse_switches_mode_to_analysed(clip, tmp_path):
    r = ofxhost.run(base(clip, ["set profileMode 2"] + analysed(clip) + ["get profileMode"]), str(tmp_path))
    assert r.values("profileMode") == "0"


def test_analyse_box_too_small(clip, tmp_path):
    r = ofxhost.run(base(clip, ["time 1002", "set analysisBottomLeft 10 10", "set analysisTopRight 20 20", "press analyseNoise",
                                "get profileData"]), str(tmp_path))
    assert any("24 x 24" in l for l in r.startswith("MESSAGE OfxMessageError")), str(r)
    assert r.values("profileData") == ""


def test_analyse_outside_clip(clip, tmp_path):
    r = ofxhost.run(base(clip, ["time 5", "press analyseNoise", "get profileData"]), str(tmp_path))
    assert any("no source frame" in l for l in r.startswith("MESSAGE OfxMessageError")), str(r)
    assert r.values("profileData") == ""


def test_no_profile_falls_back_to_auto(clip, tmp_path):
    out = render_path(clip, "noprof.raw")
    r = ofxhost.run(base(clip, ["get profileStatus", "set temporalRadius 0", "render 1002 %s" % out]), str(tmp_path))
    assert r.code == 0, r
    assert "No profile yet" in r.values("profileStatus")
    img = ofxhost.read_raw(out, W, H)
    auto = sdcore.analyse(flipped([clip["noisy"][2]])[0])
    # (the core's profile went through text: allow for its 6 digits)
    np.testing.assert_allclose(img[..., :3], core_render([clip["noisy"][2]], 0, auto)[..., :3], atol=1e-3)


@pytest.mark.parametrize("radius", [0, 1, 2])
def test_render_matches_core(clip, tmp_path, radius):
    out = render_path(clip, "r%d.raw" % radius)
    r = ofxhost.run(base(clip, analysed(clip) + ["set temporalRadius %d" % radius, "get profileData",
                                                  "render 1002 %s" % out]), str(tmp_path))
    assert r.code == 0, r
    prof = r.values("profileData")
    img = ofxhost.read_raw(out, W, H)
    frames = clip["noisy"][2 - radius:3 + radius]
    ref = core_render(frames, radius, prof, temporal_radius=radius)
    np.testing.assert_array_equal(img[..., :3], ref[..., :3])
    assert np.all(img[..., 3] == 1.0)
    fetched = r.startswith("RENDERED")[0].split("fetched:")[1].split()
    assert sorted(map(int, fetched)) == list(range(1002 - radius, 1003 + radius))
    assert synth.psnr(clip["clean"][2], img) > synth.psnr(clip["clean"][2], clip["noisy"][2]) + 8


def test_frames_needed_follow_radius(clip, tmp_path):
    r = ofxhost.run(base(clip, ["frames 1002", "set temporalRadius 3", "frames 1002", "set temporalRadius 0",
                                "frames 1002"]), str(tmp_path))
    src = [l for l in r.startswith("FRAMES Source")]
    assert src == ["FRAMES Source 1001:1003", "FRAMES Source 999:1005", "FRAMES Source 1002:1002"]


def test_clip_start_uses_available_frames(clip, tmp_path):
    out = render_path(clip, "start.raw")
    r = ofxhost.run(base(clip, analysed(clip) + ["set temporalRadius 2", "get profileData",
                                                  "render 1000 %s" % out]), str(tmp_path))
    assert r.code == 0, r
    fetched = sorted(map(int, r.startswith("RENDERED")[0].split("fetched:")[1].split()))
    assert fetched == [1000, 1001, 1002]
    img = ofxhost.read_raw(out, W, H)
    n = clip["noisy"]
    ref = core_render([None, None, n[0], n[1], n[2]], 2, r.values("profileData"), temporal_radius=2)
    np.testing.assert_array_equal(img[..., :3], ref[..., :3])


def test_identity(clip, tmp_path):
    r = ofxhost.run(base(clip, ["identity 1002", "set mix 0", "identity 1002", "set outputMode 1",
                                "identity 1002", "set outputMode 0", "set mix 1", "set lumaReduction 0",
                                "set chromaReduction 0", "identity 1002"]), str(tmp_path))
    assert [l.split()[1] for l in r.startswith("IDENTITY")] == ["no", "yes", "no", "yes"]


def test_mix_noise_output_and_mask(clip, tmp_path):
    d = clip["dir"]
    mask = np.zeros((H, W, 4), np.float32)
    mask[:, : W // 2, 3] = 1.0  # left half
    ofxhost.write_raw(os.path.join(d, "mask.%d.raw" % 1002), mask)
    outs = {k: render_path(clip, "m_%s.raw" % k) for k in ["full", "half", "noise", "mask", "inv"]}
    r = ofxhost.run(base(clip, analysed(clip) + [
        "set temporalRadius 0",
        "render 1002 %s" % outs["full"],
        "set mix 0.5", "render 1002 %s" % outs["half"], "set mix 1",
        "set outputMode 1", "render 1002 %s" % outs["noise"], "set outputMode 0",
        "clip Mask %d %d 1002 1002 %s" % (W, H, os.path.join(d, "mask.%d.raw")),
        "render 1002 %s" % outs["mask"],
        "set maskInvert 1", "render 1002 %s" % outs["inv"],
    ]), str(tmp_path))
    assert r.code == 0, r
    src = clip["noisy"][2]
    full, half, noise, masked, inv = (ofxhost.read_raw(outs[k], W, H)[..., :3]
                                      for k in ["full", "half", "noise", "mask", "inv"])
    np.testing.assert_allclose(half, 0.5 * (full + src), atol=1e-6)
    np.testing.assert_allclose(noise, src - full, atol=1e-6)
    np.testing.assert_allclose(masked[:, : W // 2], full[:, : W // 2], atol=1e-6)
    np.testing.assert_allclose(masked[:, W // 2:], src[:, W // 2:], atol=1e-6)
    np.testing.assert_allclose(inv[:, : W // 2], src[:, : W // 2], atol=1e-6)
    np.testing.assert_allclose(inv[:, W // 2:], full[:, W // 2:], atol=1e-6)


def test_colourspace_change_after_analysis(clip, tmp_path):
    """A profile measured in the other working space is not used: the status
    says so and the render falls back to Auto."""
    a, b = render_path(clip, "cs_stored.raw"), render_path(clip, "cs_auto.raw")
    r = ofxhost.run(base(clip, analysed(clip) + ["set temporalRadius 0", "set inputColourspace 1",
                                                  "get profileStatus", "render 1002 %s" % a,
                                                  "set profileMode 2", "render 1002 %s" % b]), str(tmp_path))
    assert "re-analyse" in r.values("profileStatus")
    np.testing.assert_array_equal(ofxhost.read_raw(a, W, H), ofxhost.read_raw(b, W, H))


def test_region_every_frame_matches_analysed(clip, tmp_path):
    a, b = render_path(clip, "live_a.raw"), render_path(clip, "live_b.raw")
    r = ofxhost.run(base(clip, analysed(clip) + ["set temporalRadius 0", "render 1002 %s" % a,
                                                  "set profileMode 1", "get profileStatus",
                                                  "render 1002 %s" % b]), str(tmp_path))
    assert r.values("profileStatus").startswith("Region: the box")
    # same measurement; the analysed one was stored as text (6 digits)
    np.testing.assert_allclose(ofxhost.read_raw(a, W, H), ofxhost.read_raw(b, W, H), atol=1e-4)


def test_half_render_scale(clip, tmp_path):
    out = render_path(clip, "half_scale.raw")
    r = ofxhost.run(base(clip, analysed(clip) + ["set temporalRadius 1", "get profileData",
                                                  "render 1002 %s 0.5" % out]), str(tmp_path))
    assert r.code == 0, r
    img = ofxhost.read_raw(out, W // 2, H // 2)
    n = [f.reshape(H // 2, 2, W // 2, 2, 3).mean((1, 3)) for f in clip["noisy"][1:4]]
    c = clip["clean"][2].reshape(H // 2, 2, W // 2, 2, 3).mean((1, 3))
    ref = core_render(n, 1, r.values("profileData"), temporal_radius=1, scale_shift=1)
    np.testing.assert_allclose(img[..., :3], ref[..., :3], atol=1e-5)
    assert synth.psnr(c, img) > synth.psnr(c, n[1]) + 6
