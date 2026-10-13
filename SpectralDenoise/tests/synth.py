"""Deterministic synthetic test material: a scene with edges, textures and
smooth shading, a flat panel to profile, several kinds of noise, and moving
sequences. No downloads or image libraries needed."""
import numpy as np
from scipy.ndimage import gaussian_filter, shift as ndshift


def scene(h=480, w=720, panel=160, seed=0):
    """Linear-light RGB float32 (top row first) and the flat panel's rect
    (x1, y1, x2, y2 in array coordinates)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32)
    # smooth shading
    img[...] = (0.05 + 0.25 * xx / w)[..., None] * np.array([1.0, 0.9, 0.8], np.float32)
    # textured band (fine sinusoids, like fabric)
    band = (yy > 0.15 * h) & (yy < 0.4 * h) & (xx < w - panel)
    tex = 0.2 + 0.12 * np.sin(xx / 2.3) * np.cos(yy / 3.1) + 0.06 * np.sin((xx + yy) / 7.0)
    img[band] = (tex[band][:, None] * np.array([0.9, 0.6, 0.4], np.float32))
    # hard-edged discs (edges) in several colours and brightnesses
    for i in range(6):
        cx, cy, r = rng.uniform(40, w - panel - 40), rng.uniform(0.45 * h, h - 40), rng.uniform(20, 50)
        m = (xx - cx) ** 2 + (yy - cy) ** 2 < r * r
        img[m] = rng.uniform(0.02, 0.9, 3).astype(np.float32)
    # fine random texture (foliage-like), blurred a little
    t = gaussian_filter(rng.standard_normal((h, w)), 1.0).astype(np.float32)
    region = (yy > 0.05 * h) & (yy < 0.12 * h) & (xx < w - panel)
    img[region] = (0.3 + 0.1 * t[region])[:, None] * np.array([0.5, 0.8, 0.4], np.float32)
    # flat panel: a sky-like gentle gradient
    py, px = np.mgrid[0:h, 0:panel].astype(np.float32)
    img[:, w - panel:] = np.stack([0.18 + 0.05 * py / h, 0.22 + 0.06 * py / h, 0.32 + 0.04 * px / panel], -1)
    return np.clip(img, 0, None), (w - panel + 12, 12, w - 12, h - 12)


def noise_white(clean, rng, a=0.002, b=0.00002):
    """Signal-dependent (photon + read) noise, independent per channel."""
    sd = np.sqrt(a * np.clip(clean, 0, None) + b)
    return (clean + sd * rng.standard_normal(clean.shape)).astype(np.float32)


def noise_grain(clean, rng, a=0.004, b=0.00004, blur=0.8, chroma_blotch=0.0):
    """Correlated grain, optionally with low-frequency colour blotches."""
    n = rng.standard_normal(clean.shape)
    n = np.stack([gaussian_filter(n[..., c], blur) for c in range(3)], -1)
    n /= n.std()
    if chroma_blotch > 0:
        m = rng.standard_normal(clean.shape)
        m = np.stack([gaussian_filter(m[..., c], 4) for c in range(3)], -1)
        m /= m.std()
        n = n + chroma_blotch * (m - m.mean(-1, keepdims=True))
    sd = np.sqrt(a * np.clip(clean, 0, None) + b)
    return (clean + sd * n).astype(np.float32)


def stationary(clean, rng, sigma):
    return (clean + sigma * rng.standard_normal(clean.shape)).astype(np.float32)


def sequence(n=5, pan=(1.6, -0.7), kind="white", seed=0, **kw):
    """Camera pan plus a square object moving 10 px/frame. Returns clean
    frames, noisy frames, panel rect, object box (y0, y1, x0, x1) at the
    middle frame."""
    rng = np.random.default_rng(seed)
    base, rect = scene(**kw)
    clean, noisy = [], []
    mid = n // 2
    for f in range(n):
        t = f - mid
        fr = np.stack([ndshift(base[..., c], (pan[1] * t, pan[0] * t), order=3, mode="nearest") for c in range(3)], -1)
        oy, ox = 60, 200 + 10 * t
        yy, xx = np.mgrid[0:80, 0:80]
        tex = 0.3 + 0.25 * np.sin(xx / 3.0) * np.cos(yy / 5.0)
        fr[oy:oy + 80, ox:ox + 80] = np.stack([tex, tex * 0.7, tex * 0.4], -1)
        fr = np.clip(fr, 0, None).astype(np.float32)
        clean.append(fr)
        noisy.append(noise_white(fr, rng) if kind == "white" else noise_grain(fr, rng))
    return clean, noisy, rect, (60, 140, 200, 280)


def srgb(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)


def psnr(clean, test, box=None):
    """PSNR in display (sRGB) space, RGB only."""
    a, b = srgb(clean[..., :3]), srgb(test[..., :3])
    if box is not None:
        y0, y1, x0, x1 = box
        a, b = a[y0:y1, x0:x1], b[y0:y1, x0:x1]
    a, b = np.clip(a, 0, 1), np.clip(b, 0, 1)
    return float(10 * np.log10(1.0 / np.mean((a - b) ** 2)))
