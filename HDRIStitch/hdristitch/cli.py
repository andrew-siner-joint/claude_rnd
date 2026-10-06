"""`hdri` command line."""
import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, config


def _sets(text):
    return [int(v) for v in text.split(",") if v.strip()] if text else None


def _cfg(args):
    cfg = config.load(args.config)
    for dotted, attr, conv in (
            ("color.white_balance", "wb", str), ("color.gamut", "gamut", str),
            ("color.exposure", "ev", lambda v: v if v == "reference" else float(v)),
            ("capture.brackets", "brackets", lambda v: v if v == "auto" else int(v)),
            ("capture.positions", "positions", int),
            ("output.width", "width", lambda v: v if v == "auto" else int(v)),
            ("stitch.blend_sharpness", "sharpness", float),
            ("merge.jobs", "jobs", int)):
        value = getattr(args, attr, None)
        if value is not None:
            config.override(cfg, dotted, conv(value))
    if getattr(args, "half", False):
        config.override(cfg, "output.half_float", True)
    if getattr(args, "no_refine", False):
        config.override(cfg, "stitch.refine", False)
    if getattr(args, "no_sun", False):
        config.override(cfg, "output.sun", False)
    return cfg


def _add_common(p, process=True):
    p.add_argument("folder", help="shoot folder containing the raw files")
    p.add_argument("--config", help="config file (default ~/HDRIStitch/config.toml)")
    p.add_argument("--sets", help="only these HDRI sets, e.g. 1 or 1,3")
    p.add_argument("--width", help="output width in pixels, or 'auto'")
    p.add_argument("--half", action="store_true", help="write 16-bit half float EXR")
    p.add_argument("--sharpness", help="seam blend sharpness (default 8)")
    p.add_argument("--no-sun", action="store_true", help="skip sun detection")
    p.add_argument("--card-ratio", type=float,
                   help="gray card sun/shade brightness ratio, to set the sun strength")
    p.add_argument("-q", "--quiet", action="store_true")
    if process:
        p.add_argument("--wb", help="white balance: auto, asshot, xmp, d65, 5600 or 5600,+10")
        p.add_argument("--gamut", choices=["rec709", "acescg", "rec2020"])
        p.add_argument("--ev", help="exposure anchor: 'reference' or an EV100 value")
        p.add_argument("--brackets", help="frames per bracket (default: auto)")
        p.add_argument("--positions", help="positions per HDRI (default 8)")
        p.add_argument("--jobs", help="parallel merges (default: automatic)")
        p.add_argument("--template", help="rig template name or .pto path")
        p.add_argument("--no-template", action="store_true",
                       help="align from scratch even if a rig template exists")
        p.add_argument("--no-refine", action="store_true",
                       help="use the template positions exactly, no new control points")
        p.add_argument("--stop-after", choices=["merge", "align"])
        p.add_argument("--recursive", action="store_true", help="also look in subfolders")
        p.add_argument("--dry-run", action="store_true",
                       help="only show how the frames would be grouped")


def cmd_process(args):
    from . import pipeline

    opts = dict(sets=_sets(args.sets), template=args.template, no_template=args.no_template,
                stop_after=args.stop_after, recursive=args.recursive, dry_run=args.dry_run,
                quiet=args.quiet, card_ratio=args.card_ratio)
    pipeline.process(args.folder, _cfg(args), opts)


def cmd_render(args):
    from . import pipeline

    pipeline.rerender(args.folder, _cfg(args), dict(sets=_sets(args.sets), quiet=args.quiet,
                                                    card_ratio=args.card_ratio))


def _project(folder, set_number=1):
    from .pipeline import output_dir

    path = Path(folder).expanduser()
    if path.suffix == ".pto":
        return path
    return output_dir(path) / "work" / ("set%02d" % set_number) / "project.pto"


def cmd_open(args):
    project = _project(args.folder, args.set)
    if not project.exists():
        sys.exit("No project at %s" % project)
    if platform.system() == "Darwin":
        apps = sorted(Path("/Applications").glob("Hugin*/Hugin.app")) + \
            [p for p in [Path("/Applications/Hugin.app")] if p.exists()]
        cmd = ["open", "-a", str(apps[-1])] if apps else ["open", "-a", "Hugin"]
        subprocess.run(cmd + [str(project)], check=False)
    else:
        subprocess.run(["hugin", str(project)], check=False)
    print("Opened %s\nWhen you're done: save in Hugin, then run  hdri render \"%s\""
          % (project, args.folder))


def cmd_template(args):
    from . import pto

    tdir = config.templates_dir()
    if args.action == "list":
        names = sorted(p.stem for p in tdir.glob("*.pto"))
        print("\n".join(names) if names else "No rig templates in %s yet." % tdir)
        return
    name = args.name or config.load(args.config)["rig"]["template"]
    if args.action == "save":
        if not args.source:
            sys.exit("Give the shoot folder (or a .pto) to save as the template.")
        project = _project(args.source, args.set)
        if not project.exists():
            sys.exit("No project at %s" % project)
        tdir.mkdir(parents=True, exist_ok=True)
        dest = tdir / (name + ".pto")
        if dest.exists():
            shutil.copy2(dest, dest.with_suffix(".pto.bak"))
        pto.as_template(pto.Project.load(project)).save(dest)
        print("Saved rig template '%s' -> %s" % (name, dest))
        print("Future `hdri process` runs will align with it automatically.")
    elif args.action == "show":
        path = tdir / (name + ".pto")
        if not path.exists():
            sys.exit("No template %s" % path)
        proj = pto.Project.load(path)
        print(path)
        for k in range(len(proj.images)):
            y, p, r = proj.pose(k)
            print("  position %d: yaw %7.2f  pitch %6.2f  roll %7.2f" % (k + 1, y, p, r))
        print("  lens: hfov %.2f, distortion a=%.4g b=%.4g c=%.4g, crop %s"
              % (proj.value(0, "v"), proj.value(0, "a"), proj.value(0, "b"),
                 proj.value(0, "c"), proj.crop(0)))
        print("  masks: %d" % proj.count("k "))


def cmd_wb(args):
    from . import color, rawdecode

    info = rawdecode.camera_info(args.raw)
    print(args.raw)
    print("  as shot : %s   gains R %.3f G 1 B %.3f" % (
        color.describe_multipliers(info.cam_from_xyz, info.as_shot), info.as_shot[0],
        info.as_shot[2]))
    xmp = color.read_xmp_white_balance(args.raw)
    if xmp is None:
        print("  XMP     : none")
    elif xmp == "asshot":
        print("  XMP     : As Shot")
    else:
        print("  XMP     : %d K, tint %+d" % xmp)
    for temp in (3200, 4300, 5000, 5600, 6500, 7500):
        g = color.multipliers_for_xy(info.cam_from_xyz, color.kelvin_tint_to_xy(temp, 0))
        print("  %5d K  : gains R %.3f G 1 B %.3f" % (temp, g[0], g[2]))


def cmd_sun(args):
    from . import imageio, sun

    rgb, header = imageio.read_exr(args.exr)
    gamut = args.gamut or imageio.gamut_of(header)
    clip = args.clip_level or imageio.exr_metadata(header).get("clip_level") or None
    info = sun.find_sun(rgb, gamut, radius_deg=args.radius, clip_level=clip)
    if info is None:
        sys.exit("No distinct sun found in %s" % args.exr)
    sun.suggest(info, rgb, gamut, card_ratio=args.card_ratio,
                clear_sky_ratio=args.clear_sky_ratio)
    exr = Path(args.exr)
    stem = exr.stem
    info["hdri"] = exr.name
    if args.remove:
        out = exr.with_name(stem + "_nosun.exr")
        imageio.write_exr(out, sun.remove_sun(rgb, info), gamut=gamut,
                          metadata=dict(imageio.exr_metadata(header), stage="sun removed"),
                          latlong=True)
        info["hdri_nosun"] = out.name
        print("wrote", out)
    path = exr.with_name(stem + "_sun.json")
    sun.write_json(path, info)
    print(json.dumps({k: info[k] for k in ("lon_deg", "lat_deg", "clipped", "strength",
                                            "strength_source", "blender_rotation_deg")},
                     indent=2))
    print("wrote", path)


def cmd_preview(args):
    from . import imageio

    rgb, header = imageio.read_exr(args.exr)
    gamut = imageio.gamut_of(header)
    small = imageio.resize_area(rgb, min(args.width, rgb.shape[1]))
    exposure = None if args.ev is None else 1.5 * 2 ** args.ev
    rgb8, _ = imageio.tonemap(small, gamut, exposure=exposure)
    out = Path(args.out or Path(args.exr).with_suffix(".jpg"))
    imageio.write_jpeg(out, rgb8)
    print("wrote", out)


def cmd_doctor(args):
    from . import exif, hugin

    ok = True
    print("HDRIStitch %s, Python %s (%s)" % (__version__, platform.python_version(),
                                            sys.executable))
    for mod in ("numpy", "rawpy", "cv2", "OpenEXR", "tifffile"):
        try:
            m = __import__(mod)
            print("  %-9s %s" % (mod, getattr(m, "__version__", "ok")))
        except ImportError as exc:
            ok = False
            print("  %-9s MISSING (%s)" % (mod, exc))
    cfg = config.load(args.config)
    print("config     %s" % cfg["_source"])
    tool = exif.find_exiftool(cfg["tools"]["exiftool"])
    print("exiftool   %s" % (tool or "not found (falls back to a built-in EXIF reader)"))
    try:
        tools = hugin.find_tools(cfg["tools"]["hugin_bin"])
        print("hugin      %s" % Path(tools["nona"]).parent)
        try:
            proc = subprocess.run([tools["checkpto"], "--help"], capture_output=True, timeout=20)
            runs = b"checkpto" in proc.stdout + proc.stderr
        except (OSError, subprocess.TimeoutExpired):
            runs = False
        if not runs:
            ok = False
            print("           ...but the tools won't run. On a Mac: clear the download "
                  "quarantine (xattr -dr com.apple.quarantine /Applications/Hugin*) and, on "
                  "Apple Silicon with an Intel build, install Rosetta "
                  "(softwareupdate --install-rosetta).")
    except hugin.HuginError as exc:
        ok = False
        print("hugin      %s" % exc)
    tpl = config.templates_dir() / (cfg["rig"]["template"] + ".pto")
    print("template   %s" % (tpl if tpl.exists() else
                             "%s (not created yet: the first shoot aligns from scratch)" % tpl))
    print("All good." if ok else "Some pieces are missing; see above.")
    return 0 if ok else 1


def cmd_config(args):
    dest = config.home() / "config.toml"
    if args.init:
        if dest.exists() and not args.force:
            sys.exit("%s already exists (use --force to overwrite)" % dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config.DEFAULTS_FILE, dest)
        (config.home() / "templates").mkdir(exist_ok=True)
        print("wrote", dest)
    else:
        print(dest if dest.exists() else "%s (not created; using built-in defaults)" % dest)


def build_parser():
    ap = argparse.ArgumentParser(prog="hdri", description="Bracketed fisheye raws -> HDRI EXR")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("process", help="merge, align and stitch a shoot folder")
    _add_common(p)
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("render", help="re-render after editing the project in Hugin")
    _add_common(p, process=False)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("open", help="open a shoot's Hugin project in the Hugin GUI")
    p.add_argument("folder")
    p.add_argument("--set", type=int, default=1)
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("template", help="save / list / show rig templates")
    p.add_argument("action", choices=["save", "list", "show"])
    p.add_argument("source", nargs="?", help="shoot folder or .pto (for save)")
    p.add_argument("--name", help="template name (default from config: rig.template)")
    p.add_argument("--set", type=int, default=1)
    p.add_argument("--config")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("wb", help="show a raw's as-shot white balance in kelvin/tint")
    p.add_argument("raw")
    p.set_defaults(func=cmd_wb)

    p = sub.add_parser("sun", help="find the sun in an HDRI; optionally remove it")
    p.add_argument("exr")
    p.add_argument("--remove", action="store_true", help="also write <name>_nosun.exr")
    p.add_argument("--card-ratio", type=float)
    p.add_argument("--clear-sky-ratio", type=float, default=7.0)
    p.add_argument("--radius", type=float, default=3.0, help="sun + glow radius, degrees")
    p.add_argument("--gamut", choices=["rec709", "acescg", "rec2020"],
                   help="the EXR's colour space, if its metadata was lost (e.g. re-saved)")
    p.add_argument("--clip-level", type=float,
                   help="brightest value the merge could record (from the original EXR)")
    p.set_defaults(func=cmd_sun)

    p = sub.add_parser("preview", help="tonemapped JPEG of an EXR")
    p.add_argument("exr")
    p.add_argument("--ev", type=float, help="exposure offset in stops")
    p.add_argument("--width", type=int, default=4096)
    p.add_argument("--out")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("doctor", help="check the installation")
    p.add_argument("--config")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("config", help="show or create ~/HDRIStitch/config.toml")
    p.add_argument("--init", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_config)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:  # friendly message, full traceback with HDRI_DEBUG=1
        import os

        if os.environ.get("HDRI_DEBUG"):
            raise
        print("\nError: %s" % exc, file=sys.stderr)
        return 1
