# HDRIStitch

Turn a Nodal Ninja + fisheye bracketed shoot (Sony `.ARW` raws) into a
**scene-linear, colour-managed, stitched HDRI** for high-end CG lighting in
Blender. Runs on macOS. The only paid software it touches is what you
already have: Adobe CC (optional, for picking white balance), Nuke (clean-up)
and Resolve (alternative clean-up). Everything else is free: Hugin, exiftool
and Python.

```
 SHOOT              PROCESS (automatic)                         FINISH
 ─────              ───────────────────                         ──────
 8 positions   ┌─► group brackets ─► develop raws linearly ─┐
 × N brackets  │   (EXIF)            (your white balance)    │
 .ARW          │                                             ▼
               │   merge each position to HDR ─► align with Hugin ─► stitch in float
 [Lightroom:   │   (radiometric, exposure-        (rig template)      (no clipping,
  pick WB] ────┘    ratios measured)                                   no ringing)
                                                         │
                                                         ▼
                    name.exr  name_nosun.exr  name_sun.json  name_preview.jpg
                                                         │
                         Nuke: nadir patch, level, gray card ─► Blender add-on:
                                                                world + sun lamp
```

## What you get

- **One command per shoot**: `hdri process /path/to/shoot`, or drop the folder
  on the **HDRI Process** app. Multiple HDRIs on one card are split
  automatically.
- **True scene-linear radiance**: raws decoded with LibRaw, no tone curve, no
  gamma, no clipping. Brackets are merged with the real exposure ratios
  measured from the images (shutter speeds are never exact), then white
  balance and the camera matrix go on in floating point.
- **Raw white balance, your way**: pick it in Lightroom Classic or Camera Raw
  (it's read from the XMP sidecars), or pass kelvin/tint on the command line
  (`--wb 5600,+10`), using the same scale as Lightroom.
- **Repeatable alignment**: the first shoot is aligned from scratch with Hugin
  and saved as your **rig template**. After that every shoot reuses the exact
  lens calibration and positions and only fine-tunes them.
- **A clean HDR stitch**: Hugin supplies the geometry; the HDR pixels are
  resampled in floating point (Hugin's own remapper clamps HDR values), with
  centre-weighted feathered seams and no ringing around the sun.
- **Sun for CG**: the sun's direction and strength go to `_sun.json`, and an
  HDRI with the sun painted out is written for lighting. The Blender add-on
  builds the world plus a matching Sun lamp that stays aligned when you
  rotate the HDRI.
- **Clean-up tools for Nuke** (nadir/zenith patch, levelling, gray-card
  calibration) and a Blender add-on with gray and chrome reference balls.

## The workflow

| Step | What | Time | Guide |
|---|---|---|---|
| 0 | Install (once) | 10 min | [02 · Setup on macOS](docs/02-setup-mac.md) |
| 1 | Calibrate the rig, shoot | on set | [01 · Shooting](docs/01-shooting.md) |
| 2 | First shoot only: make the rig template | 15 min, once | [03 · Rig template](docs/03-rig-template.md) |
| 3 | Process each shoot | a few minutes, unattended | [04 · Processing](docs/04-processing.md) |
| 4 | Clean up: nadir, level, calibrate | 5–20 min | [05 · Clean-up in Nuke](docs/05-cleanup-nuke.md) |
| 5 | Light in Blender | 2 min | [06 · Blender](docs/06-blender.md) |

Problems: [07 · Troubleshooting](docs/07-troubleshooting.md). How the maths
works: [08 · How it works](docs/08-how-it-works.md).

## Quick start

```bash
# once
cd HDRIStitch
./install.sh                 # Python env, exiftool, `hdri`, droplet app, Nuke + Blender hooks
hdri doctor                  # everything green?

# every shoot
#   copy the card's .ARW files for ONE location into a folder, e.g. ~/HDRI/2026-10-06_rooftop
#   (optional) Lightroom Classic: set white balance on one frame, sync to all, Cmd+S
hdri process ~/HDRI/2026-10-06_rooftop          # or drag the folder onto "HDRI Process"

# first shoot only, once you're happy with the alignment
hdri template save ~/HDRI/2026-10-06_rooftop
```

Results land in `<shoot>/hdri/`:

| File | Use |
|---|---|
| `<shoot>.exr` | The HDRI. 32-bit float, scene linear, lat-long, 8192×4096 by default |
| `<shoot>_nosun.exr` | Same with the sun painted out: light with this plus a Sun lamp |
| `<shoot>_sun.json` | Sun direction, strength and colour, Blender rotation |
| `<shoot>_2k.exr` | Small copy for fast look-dev |
| `<shoot>_preview.jpg` | Tonemapped preview, for looking only |
| `<shoot>_holes.png` | Areas no photo covered (usually under the tripod), if any |
| `work/set01/` | Hugin project, merged positions, proxies, `log.txt`, `run.json` |

## Command reference

```
hdri process FOLDER [--wb auto|asshot|xmp|d65|5600|5600,+10] [--gamut rec709|acescg|rec2020]
                    [--ev reference|EV100] [--width 8192|auto] [--half] [--brackets N]
                    [--positions N] [--sets 1,2] [--no-template] [--no-refine]
                    [--stop-after merge|align] [--dry-run] [--card-ratio R]
hdri render FOLDER [--width N] [--sets 1]   re-stitch after editing the project in Hugin
hdri open FOLDER                            open the shoot's Hugin project in Hugin
hdri template save FOLDER [--name NAME]     save the alignment as your rig template
hdri template list | show [--name NAME]
hdri wb FILE.ARW                            as-shot white balance in kelvin/tint
hdri sun FILE.exr [--remove] [--card-ratio R]
hdri preview FILE.exr [--ev +1]
hdri doctor                                 check the installation
hdri config [--init]                        where the settings live
```

Defaults for all of these live in `~/HDRIStitch/config.toml`. The file is
commented; see [04 · Processing](docs/04-processing.md#settings).

## Layout of this folder

```
HDRIStitch/
  install.sh               macOS installer (re-run to update)
  hdristitch/              the Python package behind `hdri`
  nuke/                    Nuke menu + clean-up tools
  blender/                 Blender add-on (single file)
  docs/                    the workflow guides
  tests/                   synthetic-shoot test suite (pytest)
```

Tests: `pip install pytest && pytest tests` (the end-to-end ones need
Hugin's command-line tools; Blender ones need the `bpy` module).
