"""Render BlinkFlare previews outside Nuke with the C++ harness.

    python3 preview.py --preset Default --out flare.png [--width 960 --height 540]
    python3 preview.py --docs ../../docs/previews   # regenerate README images
"""
import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from blinkflare import camera, spec, presets  # noqa: E402

BINARY = os.path.join(HERE, "build", "blinkflare_render")


def build():
    subprocess.check_call(["make", "-s", "-C", HERE])


def plate(w, h):
    """Dusk sky gradient in scene-linear values, row 0 at the bottom."""
    y = np.linspace(0.0, 1.0, h)[:, None]
    x = np.linspace(0.0, 1.0, w)[None, :]
    top = np.array([0.010, 0.016, 0.040])
    horizon = np.array([0.090, 0.045, 0.030])
    t = np.clip(y * 1.4, 0.0, 1.0)[..., None]
    sky = horizon * (1.0 - t) + top * t
    sky = sky * (1.0 - 0.25 * (x[..., None] - 0.5) ** 2)
    ground = (y < 0.18)[..., None]
    rgb = np.where(ground, np.array([0.006, 0.006, 0.008]), sky)
    rgba = np.concatenate([rgb, np.ones((h, w, 1))], axis=-1)
    return rgba.astype(np.float32)


def dirt_texture(w, h, seed=3):
    """Procedural lens dirt: soft blobs, rings and a couple of smears."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    acc = np.zeros((h, w), np.float32)
    for _ in range(140):
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(0.004, 0.035) * h
        d2 = ((xx - cx) ** 2 + (yy - cy) ** 2) / (r * r)
        blob = np.exp(-d2 * 2.0)
        if rng.uniform() < 0.3:
            blob = blob * np.clip(d2 * 1.5, 0.0, 1.0)  # dried droplet ring
        acc += blob * rng.uniform(0.15, 0.6)
    for _ in range(4):
        cx, cy = rng.uniform(0, w), rng.uniform(0, h)
        ang = rng.uniform(0, np.pi)
        u = (xx - cx) * np.cos(ang) + (yy - cy) * np.sin(ang)
        v = -(xx - cx) * np.sin(ang) + (yy - cy) * np.cos(ang)
        acc += 0.25 * np.exp(-(u / (0.25 * h)) ** 2 - (v / (0.02 * h)) ** 2)
    acc = np.clip(acc, 0.0, 1.0)
    rgba = np.stack([acc, acc, acc, acc], axis=-1)
    return rgba.astype(np.float32)


def look_values(preset="Default", overrides=None, w=960, h=540):
    values = spec.defaults()
    values.update(presets.PRESETS[preset])
    values["light_pos"] = (w * 0.70, h * 0.70)
    values["axis_center"] = (w * 0.5, h * 0.5)
    if overrides:
        values.update(overrides)
    return values


def render_flare(values, w, h, src=None, occlusion=None, dirt=None, scene=None, verbose=False):
    """Return the kernel output (flare only) as float32 HxWx4, row 0 bottom."""
    params = spec.resolve_params(values, w, h, scene)
    with tempfile.TemporaryDirectory() as tmp:
        pfile = os.path.join(tmp, "params.txt")
        with open(pfile, "w") as f:
            for name, v in sorted(params.items()):
                vals = v if isinstance(v, (tuple, list)) else (v,)
                f.write("%s %s\n" % (name, " ".join(repr(float(x)) for x in vals)))
        out = os.path.join(tmp, "out.raw")
        cmd = [BINARY, str(w), str(h), pfile, out]
        for flag, img in (("--src", src), ("--occlusion", occlusion), ("--dirt", dirt)):
            if img is not None:
                path = os.path.join(tmp, flag.strip("-") + ".raw")
                np.ascontiguousarray(img, np.float32).tofile(path)
                cmd += [flag, path]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if verbose:
            sys.stderr.write(res.stderr)
        return np.fromfile(out, np.float32).reshape(h, w, 4)


def to_display(rgba):
    """Soft-clip scene-linear RGB and encode sRGB, flipping to top-down rows."""
    rgb = rgba[..., :3]
    rgb = 1.0 - np.exp(-np.maximum(rgb, 0.0))
    srgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    img = (np.clip(srgb, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return Image.fromarray(img[::-1])


def render_png(values, w, h, path=None, background=True, **inputs):
    bg = plate(w, h) if background else None
    inputs.setdefault("src", bg)
    flare = render_flare(values, w, h, verbose=True, **inputs)
    comp = flare + inputs["src"] if inputs["src"] is not None else flare
    img = to_display(comp)
    if path:
        img.save(path)
    return img


def contact_sheet(path, w=640, h=360, cols=2):
    names = list(presets.PRESETS.keys())
    rows = (len(names) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), (0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    for i, name in enumerate(names):
        img = render_png(look_values(name, w=w, h=h), w, h)
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(img, (x, y))
        _label(draw, x, y, name)
    sheet.save(path)


ELEMENTS_OFF = dict(glow_enable=False, glint_enable=False, streak_enable=False, ring_enable=False,
                    ghost_enable=False, spectral_enable=False)
ELEMENTS = [
    ("Glow + Core", dict(glow_enable=True)),
    ("Glints", dict(glint_enable=True, glint_intensity=1.2)),
    ("Streaks", dict(streak_enable=True, streak_intensity=1.2)),
    ("Ring (spectral)", dict(ring_enable=True, ring_intensity=0.15)),
    ("Ghosts", dict(ghost_enable=True, ghost_intensity=0.3)),
    ("Spectral Streaks", dict(spectral_enable=True, spectral_intensity=1.0, spectral_length=0.06)),
]


def _label(draw, x, y, text):
    draw.rectangle((x + 4, y + 4, x + 12 + 6 * len(text), y + 20), fill=(0, 0, 0))
    draw.text((x + 8, y + 6), text, fill=(235, 235, 235))


def element_sheet(path, w=480, h=270, cols=3):
    rows = (len(ELEMENTS) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h))
    draw = ImageDraw.Draw(sheet)
    for i, (name, ov) in enumerate(ELEMENTS):
        values = look_values("Default", dict(ELEMENTS_OFF, **ov), w, h)
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(render_png(values, w, h), (x, y))
        _label(draw, x, y, name)
    sheet.save(path)


def _cross(draw, x, y, color, size=6):
    draw.line((x - size, y, x + size, y), fill=color)
    draw.line((x, y - size, x, y + size), fill=color)


def articulation_gif(path, w=480, h=270, frames=36, preset="Default"):
    """Light orbiting the articulation point; markers show both handles."""
    images = []
    cx, cy = w * 0.5, h * 0.5
    for f in range(frames):
        a = 2.0 * np.pi * f / frames
        light = (cx + np.cos(a) * w * 0.32, cy + np.sin(a) * h * 0.30)
        values = look_values(preset, {"light_pos": light, "spin_with_light": True}, w, h)
        img = render_png(values, w, h).convert("RGB")
        draw = ImageDraw.Draw(img)
        _cross(draw, cx, h - cy, (80, 200, 255))
        _cross(draw, light[0], h - light[1], (255, 200, 60))
        images.append(img)
    images[0].save(path, save_all=True, append_images=images[1:], duration=60, loop=0)


def camera_gif(path, w=480, h=270, frames=40):
    """A camera panning past a light fixed in world space (3D mode)."""
    images = []
    light = (-30.0, 12.0, -100.0)
    for f in range(frames):
        t = f / (frames - 1.0)
        scene = {
            "world_matrix": camera.matrix_trs((0, 2, 0), (4 - 6 * t, 40 - 55 * t, 0)),
            "light_world": light, "focal": 35.0, "haperture": 36.0,
        }
        values = look_values("Default", {"light_source": 1, "articulation_mode": 2}, w, h)
        img = render_png(values, w, h, scene=scene).convert("RGB")
        _label(ImageDraw.Draw(img), 0, 0, "3D: camera pan, light at a fixed world position")
        images.append(img)
    images[0].save(path, save_all=True, append_images=images[1:], duration=70, loop=0)


def docs(folder):
    os.makedirs(folder, exist_ok=True)
    contact_sheet(os.path.join(folder, "presets.png"))
    element_sheet(os.path.join(folder, "elements.png"))
    articulation_gif(os.path.join(folder, "articulation.gif"))
    camera_gif(os.path.join(folder, "camera.gif"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="Default")
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--height", type=int, default=540)
    ap.add_argument("--out")
    ap.add_argument("--contact-sheet")
    ap.add_argument("--docs", help="render all README images into this folder")
    ap.add_argument("--set", action="append", default=[],
                    help="override a knob, e.g. --set ghost_count=20")
    args = ap.parse_args()
    build()
    if args.docs:
        docs(args.docs)
        return
    if args.contact_sheet:
        contact_sheet(args.contact_sheet)
        return
    overrides = {}
    for item in args.set:
        name, raw = item.split("=", 1)
        overrides[name] = eval(raw)  # trusted local CLI input
    values = look_values(args.preset, overrides, args.width, args.height)
    render_png(values, args.width, args.height, args.out or "flare.png")


if __name__ == "__main__":
    main()
