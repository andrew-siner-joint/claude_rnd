"""The whole run: raws -> brackets -> merged positions -> alignment -> EXR."""
import json
import math
import multiprocessing
import shutil
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from . import align, color, config, exif, grouping, imageio, merge, rawdecode, render, sun
from .hugin import Hugin


class Logger:
    def __init__(self, path=None, quiet=False):
        self.path = path
        self.quiet = quiet
        self.start = time.time()

    def __call__(self, msg=""):
        stamp = "[%5.0fs] " % (time.time() - self.start)
        if not self.quiet:
            print(stamp + msg if msg else "", flush=True)
        if self.path:
            with open(self.path, "a") as f:
                f.write(stamp + msg + "\n")


def find_raws(folder, extensions, recursive=False):
    folder = Path(folder)
    exts = {e.lower() for e in extensions}
    pattern = "**/*" if recursive else "*"
    ours = output_dir(folder).resolve()
    files = [p for p in folder.glob(pattern)
             if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("._")
             and ours not in p.resolve().parents]
    return sorted(files)


def output_dir(folder):
    return Path(folder) / "hdri"


def set_name(index, count):
    return "set%02d" % (index + 1)


def base_name(folder, index, count):
    stem = Path(folder).resolve().name.replace(" ", "_")
    return stem if count == 1 else "%s_set%02d" % (stem, index + 1)


# --------------------------------------------------------------- white balance

def resolve_white_balance(setting, brackets, info, log):
    """(gains, description) for the whole set: one white balance for every
    frame, so all positions match."""
    mode = color.parse_wb(setting)
    ref = sorted(brackets[0], key=lambda s: s.exposure)[(len(brackets[0]) - 1) // 2]
    if mode in ("auto", "xmp"):
        found = {}
        for bracket in brackets:
            for shot in bracket:
                value = color.read_xmp_white_balance(shot.path)
                if value is not None:
                    found[shot.path.name] = value
        if found:
            values = set(map(str, found.values()))
            pick = found.get(ref.path.name) or next(iter(found.values()))
            if len(values) > 1:
                log("  note: XMP sidecars disagree on white balance (%d different settings); "
                    "using %s. Sync white balance across all frames in Lightroom."
                    % (len(values), pick))
            if pick == "asshot":
                mode = "asshot"
            else:
                temp, tint = pick
                gains = color.multipliers_for_xy(info.cam_from_xyz,
                                                 color.kelvin_tint_to_xy(temp, tint))
                return gains, "XMP sidecar: %d K, tint %+d" % (round(temp), round(tint))
        elif mode == "xmp":
            raise RuntimeError("white_balance = xmp but no .xmp sidecars were found next to "
                               "the raws. In Lightroom Classic: select the photos, "
                               "Metadata > Save Metadata to File (Cmd+S).")
        else:
            mode = "asshot"
    if mode == "asshot":
        gains = info.as_shot
        return gains, "as shot (%s) from %s" % (
            color.describe_multipliers(info.cam_from_xyz, gains), ref.path.name)
    if mode == "d65":
        return color.multipliers_for_xy(info.cam_from_xyz, color.D65_XY), "D65"
    temp, tint = mode
    gains = color.multipliers_for_xy(info.cam_from_xyz, color.kelvin_tint_to_xy(temp, tint))
    return gains, "%d K, tint %+d" % (round(temp), round(tint))


# --------------------------------------------------------------------- stages

def merge_set(brackets, workdir, cfg, wb_gains, cam_to_out, anchor, proxy_exposure, log,
              jobs=0):
    mdir = workdir / "merged"
    pdir = workdir / "proxies"
    mdir.mkdir(parents=True, exist_ok=True)
    pdir.mkdir(parents=True, exist_ok=True)
    tasks = []
    for k, bracket in enumerate(brackets):
        tasks.append(dict(
            index=k + 1, shots=bracket, demosaic=cfg["merge"]["demosaic"],
            clip_start=cfg["merge"]["clip_start"], clip_end=cfg["merge"]["clip_end"],
            refine=cfg["merge"]["refine_exposure"], anchor=anchor, wb_mult=list(wb_gains),
            cam_to_out=np.asarray(cam_to_out).tolist(), gamut=cfg["color"]["gamut"],
            proxy_exposure=proxy_exposure,
            out=str(mdir / ("pos%02d.exr" % (k + 1))),
            proxy=str(pdir / ("pos%02d.jpg" % (k + 1)))))
    results = []
    if jobs <= 1:
        for task in tasks:
            log("  merging position %d/%d (%d frames)" % (task["index"], len(tasks),
                                                           len(task["shots"])))
            results.append(merge.merge_position(task))
    else:
        log("  merging %d positions, %d at a time" % (len(tasks), jobs))
        # spawn, not fork: LibRaw's OpenMP threads don't survive a fork
        with ProcessPoolExecutor(max_workers=jobs,
                                 mp_context=multiprocessing.get_context("spawn")) as pool:
            for k, res in enumerate(pool.map(merge.merge_position, tasks)):
                log("  merged position %d/%d" % (k + 1, len(tasks)))
                results.append(res)
    for k, res in enumerate(results):
        if res.ratios:
            gaps = ", ".join("%.2f%s" % (used, "" if measured else "*")
                             for _, used, measured in res.ratios)
            log("    position %d: bracket steps (stops) %s%s" % (
                k + 1, gaps, "   (* = from EXIF, too few pixels to measure)"
                if any(not m for _, _, m in res.ratios) else ""))
        if res.clipped_fraction > 0:
            log("    position %d: %.4f%% of pixels clipped even in the darkest frame"
                % (k + 1, 100 * res.clipped_fraction))
    return results


def resolve_template(cfg, name=None):
    name = name or cfg["rig"]["template"]
    if not name:
        return None
    path = Path(name).expanduser()
    if path.suffix != ".pto":
        path = config.templates_dir() / (name + ".pto")
    return path if path.exists() else None


def finish(folder, workdir, base, rgb, holes, meta, cfg, log, proxy_exposure, card_ratio=None,
           clip_level=None):
    """Write the EXR, previews, hole mask, sun data. Returns written paths."""
    out = output_dir(folder)
    gamut = cfg["color"]["gamut"]
    written = []
    # sidecars from an earlier render would no longer match this one
    for suffix in ("_holes.png", "_sun.json", "_nosun.exr"):
        stale = out / (base + suffix)
        if stale.exists():
            stale.unlink()
    for stale in out.iterdir() if out.exists() else []:
        tail = stale.name[len(base):]
        if stale.name.startswith(base) and tail.startswith("_") and tail.endswith("k.exr") \
                and tail[1:-5].isdigit():
            stale.unlink()
    if holes.any():
        log("  %.2f%% of the sphere had no photo (usually under the tripod)"
            % (100 * holes.mean()))
        imageio.write_png_mask(out / (base + "_holes.png"), holes)
        written.append(out / (base + "_holes.png"))
        if cfg["output"]["fill_holes"]:
            render.fill_holes(rgb, ~holes)
    meta = dict(meta, gamut=gamut)
    exr = out / (base + ".exr")
    peak = float(rgb.max())
    half = bool(cfg["output"]["half_float"])
    if half and peak > imageio.HALF_MAX:
        log("  note: peak %.0f is above half-float range; writing 32-bit float instead" % peak)
        half = False
    imageio.write_exr(exr, rgb, gamut=gamut, half=half, metadata=meta, latlong=True)
    written.append(exr)
    pw = int(cfg["output"]["preview_width"] or 0)
    if pw and pw < rgb.shape[1]:
        small = imageio.resize_area(rgb, pw)
        path = out / ("%s_%dk.exr" % (base, round(pw / 1024)))
        imageio.write_exr(path, small, gamut=gamut, half=float(small.max()) < 60000,
                          metadata=meta, latlong=True)
        written.append(path)
    jpg_src = imageio.resize_area(rgb, min(4096, rgb.shape[1]))
    rgb8, _ = imageio.tonemap(jpg_src, gamut, exposure=proxy_exposure)
    imageio.write_jpeg(out / (base + "_preview.jpg"), rgb8)
    written.append(out / (base + "_preview.jpg"))

    if cfg["output"]["sun"]:
        info = sun.find_sun(rgb, gamut, clip_level=clip_level)
        if info is None:
            log("  no distinct sun found (overcast or sun hidden)")
        else:
            sun.suggest(info, rgb, gamut, card_ratio=card_ratio)
            info["hdri"] = exr.name
            nosun = out / (base + "_nosun.exr")
            info["hdri_nosun"] = nosun.name
            imageio.write_exr(nosun, sun.remove_sun(rgb, info), gamut=gamut, half=False,
                              metadata=dict(meta, stage="sun removed"), latlong=True)
            sun.write_json(out / (base + "_sun.json"), info)
            written += [nosun, out / (base + "_sun.json")]
            log("  sun: azimuth %.1f, elevation %.1f deg; %s; suggested strength %.3g"
                % (info["lon_deg"], info["lat_deg"],
                   "CLIPPED (strength is an estimate)" if info["clipped"] else "not clipped",
                   info["strength"]))
    return written


def process_set(folder, index, count, brackets, cfg, opts, log):
    folder = Path(folder)
    name = set_name(index, count)
    base = base_name(folder, index, count)
    workdir = output_dir(folder) / "work" / name
    workdir.mkdir(parents=True, exist_ok=True)
    log.path = workdir / "log.txt"
    log("== %s: %d positions x %d brackets -> %s.exr" % (name, len(brackets), len(brackets[0]),
                                                         base))

    # colour + exposure, decided once for the whole set
    ref = sorted(brackets[0], key=lambda s: s.exposure)[(len(brackets[0]) - 1) // 2]
    info = rawdecode.camera_info(ref.path)
    gains, wb_text = resolve_white_balance(cfg["color"]["white_balance"], brackets, info, log)
    log("  white balance: %s" % wb_text)
    gamut = cfg["color"]["gamut"]
    cam_to_out = color.camera_to_output(info.cam_from_xyz, gamut)
    mids = [sorted(b, key=lambda s: s.exposure)[(len(b) - 1) // 2] for b in brackets]
    ref_exposure = 2 ** statistics.median(math.log2(s.exposure) for s in mids)
    if cfg["color"]["exposure"] == "reference":
        anchor, ev = ref_exposure, -math.log2(ref_exposure)
    else:
        ev = float(cfg["color"]["exposure"])
        anchor = 2.0 ** -ev
    proxy_exposure = 1.5 * ref_exposure / anchor
    log("  exposure anchor: EV100 %.2f (%s)" % (
        ev, "middle bracket" if cfg["color"]["exposure"] == "reference" else "fixed"))

    megapixels = info.width * info.height / 1e6
    jobs = int(cfg["merge"]["jobs"]) or merge.default_jobs(len(brackets), megapixels)
    results = merge_set(brackets, workdir, cfg, gains, cam_to_out, anchor, proxy_exposure, log,
                        jobs)
    run = {
        "name": base, "set": name, "folder": str(folder.resolve()),
        "positions": [[s.path.name for s in b] for b in brackets],
        "merged": [str(Path(r.path).relative_to(workdir)) for r in results],
        "proxies": [str(Path(r.proxy).relative_to(workdir)) for r in results],
        "white_balance": wb_text, "wb_gains": [float(g) for g in gains], "gamut": gamut,
        "ev100": ev, "proxy_exposure": proxy_exposure, "camera": ref.model, "lens": ref.lens,
        "clipped_fraction": max(r.clipped_fraction for r in results),
        # brightest value the merge can record: sensor clip in the darkest
        # frame, lifted to neutral by the largest white balance gain
        "clip_level": anchor * float(max(gains)) / min(min(s.exposure for s in b)
                                                     for b in brackets),
        "image_size": [info.width, info.height],
    }
    (workdir / "run.json").write_text(json.dumps(run, indent=2))
    if opts.get("stop_after") == "merge":
        log("Stopped after merging (--stop-after merge).")
        return run

    hugin = Hugin(workdir, cfg["tools"]["hugin_bin"], log)
    template = None if opts.get("no_template") else resolve_template(cfg, opts.get("template"))
    proxies = run["proxies"]
    sizes = [(info.width, info.height)] * len(proxies)
    width = cfg["output"]["width"]
    canvas = width if isinstance(width, int) else 8192
    if template is not None:
        log("aligning with rig template %s" % template)
        stats = align.align_template(hugin, proxies, sizes, template, cfg, canvas, log)
    else:
        log("aligning from scratch (no rig template yet)")
        stats = align.align_auto(hugin, proxies, sizes, cfg, canvas, log)
    _report_alignment(stats, log)
    run["alignment"] = stats
    (workdir / "run.json").write_text(json.dumps(run, indent=2))
    if opts.get("stop_after") == "align":
        log("Stopped after alignment (--stop-after align). Check %s in Hugin, then run "
            "`hdri render`." % (workdir / "project.pto"))
        return run
    return render_set(folder, workdir, run, cfg, log, opts)


def _report_alignment(stats, log):
    if stats.get("points"):
        log("  alignment: %d control points, mean error %s px, max %s px"
            % (stats["points"], stats.get("mean"), stats.get("max")))
    mean = stats.get("mean")
    if mean is not None and mean > 3.0:
        log("  WARNING: control point error is high. Inspect the project in Hugin "
            "(`hdri open`), fix or delete bad points, re-optimise, save, then `hdri render`.")
    if not stats.get("connected", True):
        log("  WARNING: not all positions are linked by control points.")


def render_set(folder, workdir, run, cfg, log, opts):
    hugin = Hugin(workdir, cfg["tools"]["hugin_bin"], log)
    project = workdir / "project.pto"
    if not project.exists():
        raise RuntimeError("No Hugin project at %s; run `hdri process` first." % project)
    width = cfg["output"]["width"]
    if width == "auto":
        width = render.native_width(project)
    width = int(width) // 2 * 2
    log("rendering %dx%d equirect" % (width, width // 2))
    merged = [workdir / m for m in run["merged"]]
    missing = [str(m) for m in merged if not m.exists()]
    if missing:
        raise RuntimeError("Merged positions are missing (deleted with clean_merged?): %s\n"
                           "Run `hdri process` again." % ", ".join(missing))
    rgb, holes = render.render(hugin, project, merged, width, cfg["stitch"], log)
    meta = {
        "white_balance": run["white_balance"], "ev100": float(run["ev100"]),
        "exposure_mode": str(cfg["color"]["exposure"]), "camera": run.get("camera", ""),
        "lens": run.get("lens", ""), "positions": len(merged),
        "brackets": len(run["positions"][0]), "software": "HDRIStitch",
        "clipped_fraction": float(run.get("clipped_fraction", 0.0)),
        "clip_level": float(run.get("clip_level", 0.0)),
    }
    if cfg["color"]["gamut"] != run["gamut"]:
        raise RuntimeError("The merged images are %s but the config now says %s; re-run "
                           "`hdri process`." % (run["gamut"], cfg["color"]["gamut"]))
    written = finish(folder, workdir, run["name"], rgb, holes, meta, cfg, log,
                     run["proxy_exposure"], card_ratio=opts.get("card_ratio"),
                     clip_level=run.get("clip_level"))
    for path in written:
        log("  wrote %s" % path)
    if cfg["output"]["clean_merged"]:
        shutil.rmtree(workdir / "merged", ignore_errors=True)
        log("  deleted merged positions (clean_merged = true)")
    run["outputs"] = [str(p) for p in written]
    (workdir / "run.json").write_text(json.dumps(run, indent=2, default=str))
    return run


def plan(folder, cfg, opts):
    raws = find_raws(folder, cfg["capture"]["extensions"], opts.get("recursive", False))
    if not raws:
        raise RuntimeError("No raw files (%s) in %s" % (", ".join(cfg["capture"]["extensions"]),
                                                         folder))
    shots = exif.read_shots(raws, cfg["tools"]["exiftool"])
    groups = grouping.group_brackets(shots, cfg["capture"]["brackets"],
                                     cfg["capture"]["gap_seconds"])
    return grouping.split_sets(groups, cfg["capture"]["positions"])


def process(folder, cfg, opts=None):
    opts = opts or {}
    log = Logger(quiet=opts.get("quiet", False))
    folder = Path(folder).expanduser()
    sets = plan(folder, cfg, opts)
    log(grouping.summary(sets))
    if opts.get("dry_run"):
        return []
    wanted = opts.get("sets")
    runs = []
    for k, brackets in enumerate(sets):
        if wanted and (k + 1) not in wanted:
            continue
        runs.append(process_set(folder, k, len(sets), brackets, cfg, opts, log))
    log("Done.")
    return runs


def rerender(folder, cfg, opts=None):
    opts = opts or {}
    log = Logger(quiet=opts.get("quiet", False))
    folder = Path(folder).expanduser()
    work = output_dir(folder) / "work"
    sets = sorted(p for p in work.glob("set*") if (p / "run.json").exists())
    if not sets:
        raise RuntimeError("Nothing to render in %s; run `hdri process` first." % work)
    runs = []
    for path in sets:
        k = int(path.name[3:])
        if opts.get("sets") and k not in opts["sets"]:
            continue
        log.path = path / "log.txt"
        run = json.loads((path / "run.json").read_text())
        log("== re-rendering %s from %s" % (run["name"], path / "project.pto"))
        runs.append(render_set(folder, path, run, cfg, log, opts))
    log("Done.")
    return runs
