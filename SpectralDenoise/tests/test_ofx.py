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


def box_profile(clip, frame_index, scale=1, **kw):
    """What the plugin measures: the box on one frame, as the core does it."""
    f = clip["noisy"][frame_index]
    x1, y1, x2, y2 = clip["box"]
    if scale > 1:
        h, w = H // scale, W // scale
        f = f.reshape(h, scale, w, scale, 3).mean((1, 3))
        x1, y1, x2, y2 = x1 // scale, y1 // scale, -(-x2 // scale), -(-y2 // scale)
    return sdcore.analyse(flipped([f])[0], region=(x1, y1, x2, y2), **kw)


# The plugin keeps its profile in memory, the core's comes back as text that
# round-trips exactly: renders must match bit for bit.
ROUNDING = 0.0


def test_parameters_described(clip, tmp_path):
    r = ofxhost.run(["describe"], str(tmp_path))
    assert r.code == 0, r
    params = {l.split()[1]: l for l in r.startswith("PARAM")}
    for name in ["profileMode", "analysisBottomLeft", "analysisTopRight", "analyseNoise", "profileStatus",
                 "analysisFrame", "inputColourspace", "brightnessAdaptive", "noiseLevel", "lumaLevel", "chromaLevel",
                 "lumaReduction", "chromaReduction", "fineLevel", "mediumLevel", "coarseLevel", "veryCoarseLevel",
                 "sharpen", "temporalRadius", "motionTolerance", "quality", "outputMode", "mix", "maskInvert"]:
        assert name in params, name
    # every parameter is an ordinary visible one: nothing hidden for hosts to drop
    assert not [l for l in params.values() if "secret=1" in l]
    assert "default=1" in params["temporalRadius"]


def test_filter_context_has_no_mask(clip, tmp_path):
    r = ofxhost.run(["describe"], str(tmp_path), context="filter")
    assert r.code == 0, r
    assert not any(" maskInvert " in l for l in r.lines)


def test_analyse_records_frame_and_mode(clip, tmp_path):
    """The button reads no image: it records the frame and switches mode."""
    r = ofxhost.run(base(clip, analysed(clip) + ["get analysisFrame", "get profileStatus", "get profileMode"]),
                    str(tmp_path))
    assert r.code == 0, r
    assert r.values("analysisFrame") == "1002"
    assert r.values("profileMode") == "0"
    assert r.values("profileStatus").startswith("Analysed: the box on frame 1002")
    assert not r.startswith("MESSAGE")


def test_default_is_auto(clip, tmp_path):
    out = render_path(clip, "default.raw")
    r = ofxhost.run(base(clip, ["get profileMode", "get profileStatus", "set temporalRadius 0",
                                "render 1002 %s" % out]), str(tmp_path))
    assert r.code == 0, r
    assert r.values("profileMode") == "2"
    assert r.values("profileStatus").startswith("Auto")
    img = ofxhost.read_raw(out, W, H)
    auto = sdcore.analyse(flipped([clip["noisy"][2]])[0])
    np.testing.assert_array_equal(img[..., :3], core_render([clip["noisy"][2]], 0, auto)[..., :3])


def test_analysis_frame_is_measured_when_rendering_other_frames(clip, tmp_path):
    """Analyse on 1000, render 1003: the box on 1000 is the profile, frame
    1000 is requested and fetched."""
    out = render_path(clip, "other.raw")
    r = ofxhost.run(base(clip, analysed(clip, 1000) + ["set temporalRadius 0", "frames 1003",
                                                        "render 1003 %s" % out]), str(tmp_path))
    assert r.code == 0, r
    assert "FRAMES Source 1003:1003 1000:1000" in r.lines
    fetched = r.startswith("RENDERED")[0].split("fetched:")[1].split()
    assert sorted(map(int, fetched)) == [1000, 1003]
    img = ofxhost.read_raw(out, W, H)
    ref = core_render([clip["noisy"][3]], 0, box_profile(clip, 0))
    np.testing.assert_allclose(img[..., :3], ref[..., :3], atol=ROUNDING)


def test_box_too_small_falls_back_to_auto(clip, tmp_path):
    a, b = render_path(clip, "small_a.raw"), render_path(clip, "small_b.raw")
    r = ofxhost.run(base(clip, ["time 1002", "set analysisBottomLeft 10 10", "set analysisTopRight 20 20",
                                "press analyseNoise", "set temporalRadius 0", "render 1002 %s" % a,
                                "set profileMode 2", "render 1002 %s" % b]), str(tmp_path))
    assert r.code == 0, r
    np.testing.assert_array_equal(ofxhost.read_raw(a, W, H), ofxhost.read_raw(b, W, H))


def test_analysis_frame_outside_clip_falls_back_to_auto(clip, tmp_path):
    """The test host holds the clip's end frames, as Nuke does; a frame
    beyond the clip measures the held frame and must still render."""
    out = render_path(clip, "outside.raw")
    r = ofxhost.run(base(clip, ["time 5", "press analyseNoise", "set temporalRadius 0",
                                "render 1002 %s" % out]), str(tmp_path))
    assert r.code == 0, r
    assert np.all(np.isfinite(ofxhost.read_raw(out, W, H)))


@pytest.mark.parametrize("radius", [0, 1, 2])
def test_render_matches_core(clip, tmp_path, radius):
    out = render_path(clip, "r%d.raw" % radius)
    r = ofxhost.run(base(clip, analysed(clip) + ["set temporalRadius %d" % radius, "render 1002 %s" % out]),
                    str(tmp_path))
    assert r.code == 0, r
    img = ofxhost.read_raw(out, W, H)
    frames = clip["noisy"][2 - radius:3 + radius]
    ref = core_render(frames, radius, box_profile(clip, 2), temporal_radius=radius)
    np.testing.assert_allclose(img[..., :3], ref[..., :3], atol=ROUNDING)
    assert np.all(img[..., 3] == 1.0)
    fetched = r.startswith("RENDERED")[0].split("fetched:")[1].split()
    assert sorted(set(map(int, fetched))) == list(range(1002 - radius, 1003 + radius))
    assert synth.psnr(clip["clean"][2], img) > synth.psnr(clip["clean"][2], clip["noisy"][2]) + 8


def test_frames_needed_follow_radius(clip, tmp_path):
    r = ofxhost.run(base(clip, ["frames 1002", "set temporalRadius 3", "frames 1002", "set temporalRadius 0",
                                "frames 1002"]), str(tmp_path))
    src = [l for l in r.startswith("FRAMES Source")]
    assert src == ["FRAMES Source 1001:1003", "FRAMES Source 999:1005", "FRAMES Source 1002:1002"]


def test_clip_start_uses_available_frames(clip, tmp_path):
    out = render_path(clip, "start.raw")
    r = ofxhost.run(base(clip, analysed(clip, 1000) + ["set temporalRadius 2", "render 1000 %s" % out]),
                    str(tmp_path))
    assert r.code == 0, r
    fetched = sorted(set(map(int, r.startswith("RENDERED")[0].split("fetched:")[1].split())))
    assert fetched == [1000, 1001, 1002]
    img = ofxhost.read_raw(out, W, H)
    n = clip["noisy"]
    ref = core_render([None, None, n[0], n[1], n[2]], 2, box_profile(clip, 0), temporal_radius=2)
    np.testing.assert_allclose(img[..., :3], ref[..., :3], atol=ROUNDING)


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


def test_colourspace_change_remeasures(clip, tmp_path):
    """Changing Input Colourspace after analysing needs no re-analysis: the
    box is measured again in the new working space."""
    out = render_path(clip, "cs.raw")
    r = ofxhost.run(base(clip, analysed(clip) + ["set temporalRadius 0", "set inputColourspace 1",
                                                  "render 1002 %s" % out]), str(tmp_path))
    assert r.code == 0, r
    ref = core_render([clip["noisy"][2]], 0, box_profile(clip, 2, space=sdcore.VIDEO), space=sdcore.VIDEO)
    np.testing.assert_allclose(ofxhost.read_raw(out, W, H)[..., :3], ref[..., :3], atol=ROUNDING)


def test_region_every_frame_matches_analysed(clip, tmp_path):
    a, b = render_path(clip, "live_a.raw"), render_path(clip, "live_b.raw")
    r = ofxhost.run(base(clip, analysed(clip) + ["set temporalRadius 0", "render 1002 %s" % a,
                                                  "set profileMode 1", "get profileStatus",
                                                  "render 1002 %s" % b]), str(tmp_path))
    assert r.values("profileStatus").startswith("Region: the box")
    np.testing.assert_array_equal(ofxhost.read_raw(a, W, H), ofxhost.read_raw(b, W, H))


def test_half_render_scale(clip, tmp_path):
    """At half render scale the box is measured on the half-size image the
    host supplies, so the profile matches what is being denoised."""
    out = render_path(clip, "half_scale.raw")
    r = ofxhost.run(base(clip, analysed(clip) + ["set temporalRadius 1", "render 1002 %s 0.5" % out]),
                    str(tmp_path))
    assert r.code == 0, r
    img = ofxhost.read_raw(out, W // 2, H // 2)
    n = [f.reshape(H // 2, 2, W // 2, 2, 3).mean((1, 3)) for f in clip["noisy"][1:4]]
    c = clip["clean"][2].reshape(H // 2, 2, W // 2, 2, 3).mean((1, 3))
    ref = core_render(n, 1, box_profile(clip, 2, scale=2), temporal_radius=1)
    # (the host averages the half-size image in float32, numpy in float64)
    np.testing.assert_allclose(img[..., :3], ref[..., :3], atol=1e-5)
    assert synth.psnr(c, img) > synth.psnr(c, n[1]) + 6
