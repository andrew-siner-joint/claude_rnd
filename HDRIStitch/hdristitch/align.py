"""Geometry: find where each camera position points, using Hugin.

Two routes:
* first shoot (no rig template): estimate the lens, seed positions from the
  rig layout, find control points, optimise positions then lens, clean, level;
* every shoot after that: drop the new images into the saved rig template
  (lens + positions + masks), then optionally fine-tune positions only.
"""
import math

import numpy as np

from . import pto as ptolib

POSITIONS_ONLY = "y,p,r,!y0,!p0,!r0"
MIN_LINKS = 8  # control points an image needs before a large pose change is trusted
WITH_FOV = "y,p,r,v,b,!y0,!p0,!r0"
FULL_LENS = "y,p,r,v,a,b,c,d,e,!y0,!p0,!r0"


def detect_circle(proxy_paths, scale=4):
    """Image circle of a circular fisheye from the proxies: (cx, cy, r) in
    full-resolution pixels, or None if the image fills the frame."""
    import cv2

    acc = None
    for path in proxy_paths:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        small = cv2.resize(img, (img.shape[1] // scale, img.shape[0] // scale),
                           interpolation=cv2.INTER_AREA).astype(np.float32)
        top = max(np.percentile(small, 99), 1.0)
        small /= top
        acc = small if acc is None else np.maximum(acc, small)
    if acc is None:
        return None
    h, w = acc.shape
    mask = (acc > 0.03).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    if mask.mean() > 0.97:
        return None
    pts = []
    for row in range(h):
        cols = np.flatnonzero(mask[row])
        if cols.size < 4:
            continue
        for col in (cols[0], cols[-1]):
            if 1 < col < w - 2:
                pts.append((col + 0.5, row + 0.5))
    if len(pts) < 20:
        return None
    pts = np.array(pts, float)
    keep = np.ones(len(pts), bool)
    for _ in range(3):
        p = pts[keep]
        a = np.column_stack([p[:, 0], p[:, 1], np.ones(len(p))])
        rhs = -(p[:, 0] ** 2 + p[:, 1] ** 2)
        d, e, f = np.linalg.lstsq(a, rhs, rcond=None)[0]
        cx, cy = -d / 2, -e / 2
        r = math.sqrt(max(cx * cx + cy * cy - f, 1e-6))
        resid = np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r)
        keep = resid < max(2.0, 3 * np.median(resid[keep]))
    if r > 0.95 * math.hypot(w, h) / 2:
        return None
    return cx * scale, cy * scale, r * scale


def estimate_hfov(lens_cfg, width, circle):
    if lens_cfg.get("hfov"):
        return float(lens_cfg["hfov"])
    if circle is not None and lens_cfg["type"] == "circular":
        f_px = circle[2] / (math.pi / 2)          # assume the circle spans 180 degrees
        return math.degrees(width / f_px)
    return math.degrees(lens_cfg["sensor_width_mm"] / lens_cfg["focal_length_mm"])


def _crop_box(circle, margin):
    cx, cy, r = circle
    r *= (1.0 - margin)
    return [int(round(cx - r)), int(round(cx + r)), int(round(cy - r)), int(round(cy + r))]


def _optimise(hugin, pto, variables):
    hugin.run("pto_var", "--opt", variables, "-o", pto, pto)
    hugin.run("autooptimiser", "-n", "-o", pto, pto)


def _finish(hugin, pto, width, straighten=True):
    if straighten:
        hugin.run("pano_modify", "--straighten", "-o", pto, pto)
    hugin.run("pano_modify", "--projection=2", "--fov=360x180",
              "--canvas=%dx%d" % (width, width // 2),
              "--crop=0,%d,0,%d" % (width, width // 2), "-o", pto, pto)


def _level_with_lines(hugin, pto, cfg, log):
    """Level the horizon from vertical lines in the scene (opt-in). Returns
    True if it levelled, in which case the rig-axis straighten is skipped."""
    if not cfg["stitch"]["vertical_lines"]:
        return False
    hugin.run("linefind", "-l", "10", "-o", pto, pto)
    proj = ptolib.Project.load(hugin.workdir / pto)
    lines = sum(1 for ln in proj.lines if ln.startswith("c ") and ln.rstrip().endswith("t1"))
    if lines < 4:
        log("  vertical lines: only %d found; levelling to the rig axis instead" % lines)
        return False
    before = proj.pose(0)
    _optimise(hugin, pto, "y,p,r,!y0")
    after = ptolib.Project.load(hugin.workdir / pto).pose(0)
    log("  vertical lines: %d; horizon levelled (tilt %.2f deg pitch, %.2f deg roll)"
        % (lines, after[1] - before[1], after[2] - before[2]))
    return True


def directions(hugin, pto, image, points):
    """Unit vectors (Hugin's own maths, via pano_trafo) of image pixels."""
    import subprocess

    proj = ptolib.Project.load(hugin.workdir / pto)
    proj.set_line("p ", "p f2 w3600 h1800 v360 E0 R0")
    tmp = hugin.workdir / "_dirs.pto"
    proj.save(tmp)
    text = "\n".join("%f %f" % (x, y) for x, y in points) + "\n"
    out = subprocess.run([hugin.tools["pano_trafo"], str(tmp), str(image)], input=text,
                         capture_output=True, text=True, check=True).stdout
    tmp.unlink()
    vecs = []
    for line in out.strip().splitlines():
        px, py = (float(v) for v in line.split()[:2])
        lon = math.radians(px / 3600.0 * 360.0 - 180.0)
        lat = math.radians(90.0 - py / 1800.0 * 180.0)
        vecs.append((math.cos(lat) * math.sin(lon), math.sin(lat), math.cos(lat) * math.cos(lon)))
    return np.array(vecs)


def _frame(vecs):
    """Orthonormal camera frame from the directions of the image centre and a
    point to its right."""
    z = vecs[0] / np.linalg.norm(vecs[0])
    x = vecs[1] - np.dot(vecs[1], z) * z
    x /= np.linalg.norm(x)
    return np.column_stack([x, np.cross(z, x), z])


def moved_degrees(hugin, pto_a, pto_b, image, size):
    """Rotation angle between an image's orientation in two projects (any
    axis, including a turn about the lens axis, which barely moves the
    image centre)."""
    w, h = size
    pts = [(w / 2, h / 2), (w / 2 + w / 8, h / 2)]
    fa = _frame(directions(hugin, pto_a, image, pts))
    fb = _frame(directions(hugin, pto_b, image, pts))
    cos = (np.trace(fa.T @ fb) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def _points_per_image(project, count):
    links = [0] * count
    for line in project.lines:
        if line.startswith("c ") and not line.rstrip().endswith("t1"):
            parts = line.split()
            a, b = int(parts[1][1:]), int(parts[2][1:])
            if a != b:
                links[a] += 1
                links[b] += 1
    return links


def _find_points(hugin, src, dst, celeste, log):
    extra = ["--celeste"] if celeste else []
    hugin.run("cpfind", "--prealigned", *extra, "-o", dst, src)
    stats = hugin.stats(dst)
    if not stats["connected"]:
        log("  control points: some positions unmatched with the rig layout; retrying "
            "without it")
        hugin.run("cpfind", "--multirow", *extra, "-o", dst, src)
        stats = hugin.stats(dst)
    return stats


def align_auto(hugin, proxies, sizes, cfg, canvas_width, log=print, project="project.pto"):
    """First alignment of a rig, from scratch."""
    lens = cfg["lens"]
    width, height = sizes[0]
    circle = None
    crop = None
    if lens["type"] == "circular":
        if lens["crop"] == "auto":
            circle = detect_circle([hugin.workdir / p for p in proxies])
            if circle is None:
                log("  no image circle found; treating the lens as full-frame fisheye")
            else:
                crop = _crop_box(circle, lens["crop_margin"])
                log("  image circle: centre (%.0f, %.0f), radius %.0f px"
                    % (circle[0], circle[1], circle[2]))
        else:
            crop = [int(v) for v in lens["crop"]]
            circle = ((crop[0] + crop[1]) / 2, (crop[2] + crop[3]) / 2, (crop[1] - crop[0]) / 2)
    projection = 2 if crop is not None else 3
    hfov = estimate_hfov(lens, width, circle)
    log("  lens: %s fisheye, starting HFOV %.1f deg"
        % ("circular" if projection == 2 else "full-frame", hfov))
    args = ["-p", projection, "-f", "%.3f" % hfov]
    if crop is not None:
        args += ["-c", ",".join(str(v) for v in crop)]
    hugin.run("pto_gen", *args, "-o", project, *proxies)

    layout = cfg["rig"]["layout"]
    if len(layout) >= len(proxies):
        sets = []
        for k in range(1, len(proxies)):
            yaw, pitch = layout[k]
            sets += ["y%d=%g" % (k, yaw - layout[0][0]), "p%d=%g" % (k, pitch)]
        if layout[0][1]:
            sets.append("p0=%g" % layout[0][1])
        hugin.run("pto_var", "--set", ",".join(sets), "-o", project, project)
    else:
        log("  rig layout lists %d positions but there are %d; matching without it"
            % (len(layout), len(proxies)))

    work = "align_work.pto"
    stats = _find_points(hugin, project, work, cfg["stitch"]["celeste"], log)
    log("  control points: %d" % stats["points"])
    if stats["points"] < 3 * len(proxies):
        raise RuntimeError("Too few control points (%d) to align the rig. Is there enough "
                           "detail in the overlaps, and does the shooting order match "
                           "rig.layout? You can add points by hand: `hdri open <shoot>`, "
                           "add control points, optimise, save, then `hdri render <shoot>`."
                           % stats["points"])
    for variables in (POSITIONS_ONLY, WITH_FOV, FULL_LENS):
        _optimise(hugin, work, variables)
    hugin.run("cpclean", "-o", work, work)
    _optimise(hugin, work, FULL_LENS)
    levelled = _level_with_lines(hugin, work, cfg, log)
    _finish(hugin, work, canvas_width, straighten=not levelled)
    (hugin.workdir / work).replace(hugin.workdir / project)
    stats = hugin.stats(project)
    stats["route"] = "auto"
    return stats


def align_template(hugin, proxies, sizes, template_path, cfg, canvas_width, log=print,
                   project="project.pto"):
    """Align by reusing a saved rig template, optionally fine-tuning poses."""
    template = ptolib.Project.load(template_path)
    proj = ptolib.apply_template(template, proxies, sizes)
    proj.save(hugin.workdir / project)
    stats = {"route": "template", "points": 0, "mean": None, "max": None, "connected": True,
             "reverted": []}
    if not cfg["stitch"]["refine"]:
        _finish(hugin, project, canvas_width)
        return stats

    work = "align_work.pto"
    extra = ["--celeste"] if cfg["stitch"]["celeste"] else []
    hugin.run("cpfind", "--prealigned", *extra, "-o", work, project)
    found = hugin.stats(work)
    if found["points"] < 2 * len(proxies):
        log("  only %d control points; keeping the template positions" % found["points"])
        _finish(hugin, project, canvas_width)
        stats.update(found)
        return stats

    _optimise(hugin, work, POSITIONS_ONLY)
    hugin.run("cpclean", "-o", work, work)
    _optimise(hugin, work, POSITIONS_ONLY)

    limit = cfg["stitch"]["max_refine_deg"]
    links = _points_per_image(ptolib.Project.load(hugin.workdir / work), len(proxies))
    reverted = []
    for k in range(1, len(proxies)):
        # Plenty of control points: trust the new pose however far it moved
        # (a handheld nadir can turn a lot). Only a few: more likely a bad
        # match, so allow just a small correction.
        if links[k] < MIN_LINKS:
            moved = moved_degrees(hugin, project, work, k, sizes[k])
            if moved > limit:
                reverted.append((k, moved))
    if reverted:
        refined = ptolib.Project.load(hugin.workdir / work)
        images = refined.images
        for k, moved in reverted:
            for key, value in zip("ypr", template.pose(k)):
                images[k].set(key, "%.8f" % value)
            log("  position %d moved %.1f deg from the template with only %d control points;"
                " keeping the template pose" % (k + 1, moved, links[k]))
        refined.set_images(images)
        refined.save(hugin.workdir / work)
        frozen = {k for k, _ in reverted} | {0}
        free = ",".join("%s%d" % (v, k) for k in range(len(proxies)) if k not in frozen
                        for v in "ypr")
        if free:
            _optimise(hugin, work, free)
    levelled = _level_with_lines(hugin, work, cfg, log)
    _finish(hugin, work, canvas_width, straighten=not levelled)
    (hugin.workdir / work).replace(hugin.workdir / project)
    stats.update(hugin.stats(project))
    stats["reverted"] = [k + 1 for k, _ in reverted]
    return stats

