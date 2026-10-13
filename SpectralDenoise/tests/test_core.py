"""Behaviour of the core (src/core) through its C API: profiling accuracy,
noise reduction, detail, temporal filtering and robustness."""
import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

import sdcore
import synth


@pytest.fixture(scope="module")
def scene():
    return synth.scene()


@pytest.fixture(scope="module")
def white(scene):
    clean, rect = scene
    return clean, synth.noise_white(clean, np.random.default_rng(1)), rect


# --- pass-through ------------------------------------------------------------


def test_zero_level_returns_input(white):
    _, noisy, _ = white
    prof = sdcore.analyse(noisy)
    for q in (0, 1, 2):
        out = sdcore.denoise([noisy], 0, prof, quality=q, level=0.0)
        assert np.abs(out - noisy).max() < 1e-4


def test_zero_amounts_return_input(white):
    _, noisy, _ = white
    prof = sdcore.analyse(noisy)
    out = sdcore.denoise([noisy], 0, prof, luma_amount=0.0, chroma_amount=0.0)
    assert np.abs(out - noisy).max() < 1e-4


@pytest.mark.parametrize("space", [sdcore.LINEAR, sdcore.VIDEO])
def test_colour_roundtrip_with_negatives_and_hdr(space):
    rng = np.random.default_rng(0)
    img = rng.uniform(-0.2, 40.0, (64, 96, 4)).astype(np.float32)
    prof = sdcore.analyse(img, space=space)
    out = sdcore.denoise([img], 0, prof, level=0.0, space=space)
    np.testing.assert_allclose(out, img, rtol=2e-4, atol=2e-5)


def test_alpha_is_untouched(white):
    _, noisy, _ = white
    rgba = np.concatenate([noisy, np.random.default_rng(3).random(noisy.shape[:2] + (1,), np.float32)], -1)
    out = sdcore.denoise([rgba], 0, sdcore.analyse(rgba))
    assert np.array_equal(out[..., 3], rgba[..., 3])


# --- profiling ---------------------------------------------------------------


def test_region_profile_matches_known_noise():
    """Pure stationary noise: every coefficient at every level is measured
    within 12% of the truth, which is measured on the noise directly."""
    rng = np.random.default_rng(4)
    sigma = 0.03
    flat = (0.5 + sigma * rng.standard_normal((512, 512, 3))).astype(np.float32)
    region = sdcore.parse_profile(sdcore.analyse(flat, region=(0, 0, 512, 512), space=sdcore.VIDEO))
    noise = (flat - 0.5).astype(np.float32)
    truth = sdcore.parse_profile(sdcore.analyse(noise + 0.5, region=(0, 0, 512, 512), space=sdcore.VIDEO))
    # level 0, white noise in an orthonormal transform: sigma everywhere
    s0 = region["sigma"][0]
    assert np.all(np.abs(s0[:, 1:, :] / sigma - 1) < 0.12) and np.all(np.abs(s0[:, 0, 1:] / sigma - 1) < 0.12)
    np.testing.assert_allclose(region["sigma"], truth["sigma"], rtol=1e-5)


def test_region_profile_ignores_gradients():
    rng = np.random.default_rng(5)
    yy, xx = np.mgrid[0:300, 0:300]
    ramp = (0.2 + 0.3 * xx / 300 + 0.2 * yy / 300)[..., None].astype(np.float32) * np.ones(3, np.float32)
    noise = 0.02 * rng.standard_normal(ramp.shape).astype(np.float32)
    a = sdcore.parse_profile(sdcore.analyse(ramp + noise, region=(0, 0, 300, 300), space=sdcore.VIDEO))
    b = sdcore.parse_profile(sdcore.analyse(0.4 + noise, region=(0, 0, 300, 300), space=sdcore.VIDEO))
    assert np.abs(a["sigma"][0] / b["sigma"][0] - 1).max() < 0.15


def test_region_too_small_is_reported(white):
    _, noisy, _ = white
    with pytest.raises(ValueError, match="24 x 24"):
        sdcore.analyse(noisy, region=(10, 10, 30, 30))


def test_auto_profile_never_grossly_overestimates(scene):
    """Overestimating noise destroys detail, so the automatic profile must
    stay close to, or below, the truth at every level and coefficient."""
    clean, _ = scene
    rng = np.random.default_rng(6)
    noisy = synth.stationary(clean, rng, 0.03)
    flat = synth.stationary(np.full_like(clean, 0.3), rng, 0.03)
    auto = sdcore.parse_profile(sdcore.analyse(noisy, space=sdcore.VIDEO))["sigma"]
    truth = sdcore.parse_profile(sdcore.analyse(flat, region=(0, 0, clean.shape[1], clean.shape[0]),
                                                space=sdcore.VIDEO))["sigma"]
    # total noise per level and channel
    pix = lambda s: np.sqrt((s ** 2).mean((-1, -2)))
    assert (pix(auto) / pix(truth)).max() < 1.6
    # coefficient by coefficient on the levels that carry the noise (the
    # coarsest level of this small image holds almost none)
    assert (auto[:3] / truth[:3]).max() < 2.0
    assert np.median(auto[0] / truth[0]) == pytest.approx(1.0, abs=0.15)


def test_auto_profile_on_busy_texture_and_blotches():
    """Auto mode on a frame with no flat area at all, and on blotchy colour
    noise: still a solid improvement (coarse levels neither run away on the
    texture nor ignore the blotches)."""
    rng = np.random.default_rng(3)
    t = np.stack([gaussian_filter(rng.standard_normal((480, 720)), s) for s in (1.5, 3, 6)], -1)
    tex = np.clip(0.25 + 0.08 * t, 0.01, None).astype(np.float32)
    stat = synth.stationary(tex, np.random.default_rng(1), 0.02)
    assert synth.psnr(tex, sdcore.denoise([stat], 0, sdcore.analyse(stat))) > synth.psnr(tex, stat) + 6.5
    blotch = synth.noise_grain(tex, np.random.default_rng(1), chroma_blotch=0.7)
    assert synth.psnr(tex, sdcore.denoise([blotch], 0, sdcore.analyse(blotch))) > synth.psnr(tex, blotch) + 9


def test_luma_curve_follows_signal_dependent_noise(white):
    """With photon noise, dark areas are noisier in the working space; the
    curve must pick that up."""
    _, noisy, rect = white
    p = sdcore.parse_profile(sdcore.analyse(noisy, region=rect))
    assert p["curve"][0].max() / p["curve"][0].min() > 1.4


def test_profile_text_roundtrip_and_rejection(white):
    _, noisy, _ = white
    prof = sdcore.analyse(noisy)
    assert sdcore.summary(prof).startswith("Auto")
    assert sdcore.summary("garbage") == "No profile"
    p = sdcore.parse_profile(prof)
    assert sdcore.format_profile(p, prof).split() == prof.split() or np.allclose(
        [float(x) for x in sdcore.format_profile(p, prof).split()[1:]], [float(x) for x in prof.split()[1:]], rtol=1e-5)
    with pytest.raises(RuntimeError):
        sdcore.denoise([noisy], 0, prof.replace("SDP1", "XXXX"))
    with pytest.raises(RuntimeError):
        sdcore.denoise([noisy], 0, prof.rsplit(" ", 5)[0])  # truncated


# --- noise reduction ------------------------------------------------------------


@pytest.mark.parametrize("kind,min_gain", [("white", 11.0), ("grain", 8.5)])
def test_noise_reduction_gain(scene, kind, min_gain):
    clean, rect = scene
    rng = np.random.default_rng(7)
    noisy = synth.noise_white(clean, rng) if kind == "white" else synth.noise_grain(clean, rng)
    prof = sdcore.analyse(noisy, region=rect)
    out = sdcore.denoise([noisy], 0, prof)
    assert synth.psnr(clean, out) - synth.psnr(clean, noisy) > min_gain


def test_quality_ordering(white):
    clean, noisy, rect = white
    prof = sdcore.analyse(noisy, region=rect)
    p = [synth.psnr(clean, sdcore.denoise([noisy], 0, prof, quality=q)) for q in (0, 1, 2)]
    assert p[0] < p[1] <= p[2] + 0.05


def test_region_profile_handles_chroma_blotches(scene):
    """Low-frequency colour noise is what a region profile is for: it must
    clearly beat the conservative automatic profile there."""
    clean, rect = scene
    noisy = synth.noise_grain(clean, np.random.default_rng(8), chroma_blotch=0.7)
    region = synth.psnr(clean, sdcore.denoise([noisy], 0, sdcore.analyse(noisy, region=rect)))
    auto = synth.psnr(clean, sdcore.denoise([noisy], 0, sdcore.analyse(noisy)))
    assert region > auto + 0.5
    assert region - synth.psnr(clean, noisy) > 8.0


def test_brightness_adaptive_helps(white):
    clean, noisy, rect = white
    prof = sdcore.analyse(noisy, region=rect)
    on = synth.psnr(clean, sdcore.denoise([noisy], 0, prof, luma_adaptive=True))
    off = synth.psnr(clean, sdcore.denoise([noisy], 0, prof, luma_adaptive=False))
    assert on > off + 0.5


def test_edges_stay_sharp():
    """A hard edge under noise keeps its 10-90% rise width."""
    rng = np.random.default_rng(9)
    img = np.full((200, 200, 3), 0.1, np.float32)
    img[:, 100:] = 0.6
    noisy = synth.stationary(img, rng, 0.02)
    prof = sdcore.analyse(noisy, space=sdcore.VIDEO)
    out = sdcore.denoise([noisy], 0, prof, space=sdcore.VIDEO)
    row = out[20:180, 90:110, 1].mean(0)
    rise = np.sum((row > 0.15) & (row < 0.55))
    assert rise <= 2
    # flat areas are clean
    assert out[20:180, 10:80].std() < 0.25 * noisy[20:180, 10:80].std()


def test_level_and_amount_controls_are_monotonic(white):
    clean, noisy, rect = white
    prof = sdcore.analyse(noisy, region=rect)
    removed = []
    for lv in (0.5, 1.0, 2.0):
        out = sdcore.denoise([noisy], 0, prof, level=lv)
        removed.append(np.abs(noisy - out).mean())
    assert removed[0] < removed[1] < removed[2]
    # amounts blend in the working space, which is linear for Video
    vprof = sdcore.analyse(noisy, region=rect, space=sdcore.VIDEO)
    half = sdcore.denoise([noisy], 0, vprof, luma_amount=0.5, chroma_amount=0.5, space=sdcore.VIDEO)
    full = sdcore.denoise([noisy], 0, vprof, space=sdcore.VIDEO)
    np.testing.assert_allclose(half[..., :3], 0.5 * (full[..., :3] + noisy[..., :3]), atol=2e-3)


def test_chroma_only(white):
    clean, noisy, rect = white
    prof = sdcore.analyse(noisy, region=rect)
    out = sdcore.denoise([noisy], 0, prof, luma_level=0.0, space=sdcore.VIDEO)
    # Video space: luma (R+G+B) is untouched when only chroma is filtered
    np.testing.assert_allclose(out[..., :3].sum(-1), noisy[..., :3].sum(-1), atol=1e-3)
    assert np.abs(out - noisy).max() > 0.01


def test_scale_controls_target_their_band(white):
    clean, noisy, rect = white
    prof = sdcore.analyse(noisy, region=rect)
    fine_only = sdcore.denoise([noisy], 0, prof, scale_level=(1, 0, 0, 0))
    coarse_only = sdcore.denoise([noisy], 0, prof, scale_level=(0, 0, 1, 1))
    hp = lambda x: x - gaussian_filter(x, (2, 2, 0))
    # fine level removes high frequencies, coarse levels leave them alone
    assert np.abs(hp(noisy - fine_only)).mean() > 4 * np.abs(hp(noisy - coarse_only)).mean()


def test_sharpen_adds_high_frequencies(white):
    _, noisy, rect = white
    prof = sdcore.analyse(noisy, region=rect)
    a = sdcore.denoise([noisy], 0, prof)
    b = sdcore.denoise([noisy], 0, prof, sharpen=0.5)
    hp = lambda x: (x - gaussian_filter(x, (1, 1, 0))).std()
    assert hp(b) > 1.05 * hp(a)


def test_reduced_render_scale_uses_coarser_levels(white):
    """At half resolution (box-filtered, like a host's proxy) the converted
    profile does nearly as well as one measured at that resolution."""
    clean, noisy, rect = white
    prof = sdcore.analyse(noisy, region=rect)
    half_noisy = noisy.reshape(noisy.shape[0] // 2, 2, noisy.shape[1] // 2, 2, 3).mean((1, 3))
    half_clean = clean.reshape(clean.shape[0] // 2, 2, clean.shape[1] // 2, 2, 3).mean((1, 3))
    shifted = sdcore.denoise([half_noisy], 0, prof, scale_shift=1)
    unshifted = sdcore.denoise([half_noisy], 0, prof, scale_shift=0)
    native = sdcore.denoise([half_noisy], 0, sdcore.analyse(half_noisy, region=tuple(v // 2 for v in rect)))
    assert synth.psnr(half_clean, shifted) > synth.psnr(half_clean, native) - 0.5
    # unshifted would use full-res noise levels on half-res noise: too strong
    assert np.abs(half_noisy - unshifted).mean() > np.abs(half_noisy - shifted).mean()


# --- temporal ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def seq():
    return synth.sequence()


def test_temporal_improves_and_does_not_ghost(seq):
    clean, noisy, rect, box = seq
    prof = sdcore.analyse(noisy[2], region=rect)
    res = {}
    for r in (0, 1, 2):
        out = sdcore.denoise(noisy[2 - r:3 + r], r, prof, temporal_radius=r)
        res[r] = (synth.psnr(clean[2], out), synth.psnr(clean[2], out, box))
    assert res[1][0] > res[0][0] + 0.6
    assert res[2][0] > res[1][0] + 0.2
    # the fast-moving object must not get worse (no ghosts of other frames)
    assert res[2][1] > res[0][1]


def test_temporal_rejects_unrelated_frames(seq):
    """Neighbours from a different shot are rejected, not blended in."""
    clean, noisy, rect, _ = seq
    prof = sdcore.analyse(noisy[2], region=rect)
    other = synth.noise_white(np.ascontiguousarray(clean[0][::-1, ::-1]), np.random.default_rng(10))
    spatial = sdcore.denoise([noisy[2]], 0, prof)
    mixed = sdcore.denoise([other, noisy[2], other], 1, prof, temporal_radius=1)
    assert synth.psnr(clean[2], mixed) > synth.psnr(clean[2], spatial) - 0.3


def test_missing_and_mismatched_neighbours(seq):
    clean, noisy, rect, _ = seq
    prof = sdcore.analyse(noisy[2], region=rect)
    spatial = sdcore.denoise([noisy[2]], 0, prof)
    missing = sdcore.denoise([None, noisy[2], None], 1, prof, temporal_radius=1)
    assert np.array_equal(missing, spatial)
    one_side = sdcore.denoise([None, noisy[2], noisy[3]], 1, prof, temporal_radius=1)
    assert synth.psnr(clean[2], one_side) > synth.psnr(clean[2], spatial)


def test_static_shot_temporal_gain():
    clean, rect = synth.scene()
    rng = np.random.default_rng(11)
    frames = [synth.noise_white(clean, rng) for _ in range(5)]
    prof = sdcore.analyse(frames[2], region=rect)
    r0 = synth.psnr(clean, sdcore.denoise([frames[2]], 0, prof))
    r2 = synth.psnr(clean, sdcore.denoise(frames, 2, prof, temporal_radius=2))
    assert r2 > r0 + 2.0


# --- robustness ----------------------------------------------------------------------


@pytest.mark.parametrize("shape", [(5, 5), (7, 30), (20, 9), (17, 17), (33, 260), (1, 64)])
def test_small_and_odd_sizes(shape):
    rng = np.random.default_rng(12)
    img = (0.3 + 0.05 * rng.standard_normal(shape + (3,))).astype(np.float32)
    try:
        prof = sdcore.analyse(img)
    except ValueError:
        big = (0.3 + 0.05 * rng.standard_normal((64, 64, 3))).astype(np.float32)
        prof = sdcore.analyse(big)
    out = sdcore.denoise([img, img, img], 1, prof, temporal_radius=1)
    assert out.shape == img.shape and np.all(np.isfinite(out))


def test_non_finite_input(white):
    _, noisy, _ = white
    bad = noisy.copy()
    bad[10, 10] = np.nan
    bad[20, 30, 1] = np.inf
    bad[40, 50, 2] = -np.inf
    prof = sdcore.analyse(bad)
    out = sdcore.denoise([bad], 0, prof)
    assert np.all(np.isfinite(out))


def test_deterministic_across_thread_counts(seq):
    _, noisy, rect, _ = seq
    prof = sdcore.analyse(noisy[2], region=rect)
    a = sdcore.denoise(noisy[1:4], 1, prof, temporal_radius=1, threads=1)
    b = sdcore.denoise(noisy[1:4], 1, prof, temporal_radius=1, threads=7)
    assert np.array_equal(a, b)
