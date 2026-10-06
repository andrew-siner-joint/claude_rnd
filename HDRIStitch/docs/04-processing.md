# 04 · Processing a shoot

## 1. Offload

Copy each location's `.ARW` files into its own folder, e.g.
`~/HDRI/2026-10-06_rooftop/`. Several complete HDRIs in one folder are fine
(they're split into sets); stray test shots are not. See
[01 · Shooting](01-shooting.md#shooting-order).

Check the grouping before committing to a full run:

```bash
hdri process ~/HDRI/2026-10-06_rooftop --dry-run
```
```
HDRI set 1: 8 positions x 5 brackets (40 frames)
  bracket  1  10:02:11  DSC01001.ARW .. DSC01005.ARW  [1/125, 1/1000, 1/15, 1/8000, 0.5s]
  bracket  2  10:02:20  DSC01006.ARW .. DSC01010.ARW  [1/125, 1/1000, 1/15, 1/8000, 0.5s]
  ...
```

## 2. White balance (optional, in Adobe)

White balance is applied to the raw data before anything else, exactly like
in Lightroom, so changing it is lossless. Three ways to set it:

**a) Lightroom Classic** (visual, recommended):

1. *Library* › *Import*: **Add** the shoot folder (don't copy or convert to DNG).
2. Pick a frame where the light is typical (or a frame of your gray card or
   ColorChecker shot in the same light) and set **White Balance** in
   *Develop*: eyedropper on the gray card, or Temp/Tint by eye.
3. Select all the HDRI frames › *Settings* › **Sync Settings** › tick only
   *White Balance* › Synchronize.
4. Select all › *Metadata* › **Save Metadata to File** (Cmd+S). This writes the
   `.xmp` sidecars HDRIStitch reads. (Or turn on *Catalog Settings › Metadata ›
   Automatically write changes into XMP*.)

**b) Bridge + Camera Raw**: select all the frames › *Open in Camera Raw* › set
White Balance with everything selected › *Done*. Camera Raw writes the
sidecars itself.

**c) No Adobe**: pass it on the command line, on the same scale as Lightroom:
`--wb 5600` or `--wb 5600,+10` (kelvin, tint).

Only white balance is read from the sidecars. Exposure, tone, lens profiles
and the rest are deliberately ignored: an HDRI must stay scene-linear.

Which white balance? For lighting, either neutralise a gray card under the
main light (the sun), or match the white balance of your plates so CG and
photography agree. Never per-image auto WB: every frame has to use the same
one. HDRIStitch applies a single white balance to the whole set and tells you
if the sidecars disagree.

`hdri wb DSC01003.ARW` prints a frame's as-shot white balance in kelvin/tint.

## 3. Run it

Drag the folder onto **HDRI Process** in your Dock, or:

```bash
hdri process ~/HDRI/2026-10-06_rooftop
```

What you'll see:

```
== set01: 8 positions x 5 brackets -> 2026-10-06_rooftop.exr
  white balance: XMP sidecar: 5450 K, tint +7
  exposure anchor: EV100 12.97 (middle bracket)
  merging 8 positions, 3 at a time
    position 1: bracket steps (stops) 2.98, 3.02, 2.99, 3.01
    position 1: 0.0042% of pixels clipped even in the darkest frame
  ...
aligning with rig template ~/HDRIStitch/templates/default.pto
  alignment: 1380 control points, mean error 0.8 px, max 3.1 px
rendering 8192x4096 equirect
  ...
  sun: azimuth 41.3, elevation 35.2 deg; CLIPPED (strength is an estimate); suggested strength 8.2
  wrote .../hdri/2026-10-06_rooftop.exr
Done.
```

What the lines mean:

- **bracket steps**: the real exposure ratio between neighbouring frames,
  measured from the images. Sony's nominal 3.0 EV is never exactly 3.0. A `*`
  marks a step taken from EXIF instead, where too few pixels were well
  exposed in both frames to measure it.
- **clipped even in the darkest frame**: normally just the sun. If it's a
  large number, your darkest frame wasn't dark enough.
- **mean error**: alignment quality in pixels. Under ~2 is good. Over 3
  triggers a warning: see [Rig template](03-rig-template.md#2-if-something-is-off-fix-it-in-hugin).
- **sun**: where the sun is and whether it clipped. See [Blender](06-blender.md#calibrating-the-sun).

A shoot takes a few minutes, mostly decoding raws. The merge runs several
positions in parallel, as many as your Mac's memory allows (about 3 GB each
for 33 MP frames).

## 4. Check the result

Open `hdri/<shoot>_preview.jpg` (or the EXR in Nuke) and look at:

- **Seams**: nothing doubled, lines continuous
- **Horizon**: straight across the middle (if not, see [Troubleshooting](07-troubleshooting.md#the-horizon-is-tilted))
- **Nadir**: the tripod is visible, and patching it is the next step
- **Ghosts**: moving people or clouds at seams

## 5. Re-render after edits

Changed the project in Hugin (`hdri open`)? Re-render without re-merging:

```bash
hdri render ~/HDRI/2026-10-06_rooftop
```

The same goes for output settings (width, half float, sun detection, seam
sharpness). Colour settings (white balance, gamut, exposure anchor) are baked
into the merge, so those need `hdri process` again.

## Settings

All defaults live in `~/HDRIStitch/config.toml` (commented). The ones you're
most likely to change:

| Setting | Default | Notes |
|---|---|---|
| `color.white_balance` | `auto` | `auto` = XMP sidecars if present, else as shot. Also `asshot`, `xmp`, `d65`, `5600`, `"5600,+10"` |
| `color.gamut` | `rec709` | Blender's default working space (*Linear Rec.709*). `acescg` for ACES pipelines, or Blender 5's ACEScg working space |
| `color.exposure` | `reference` | See below |
| `output.width` | `8192` | About native for an 8 mm fisheye on the a7 IV. `auto` = the lens's native resolution. 16K is possible but needs ~32 GB of RAM |
| `output.half_float` | `false` | 16-bit half EXRs are half the size, but top out at 65504 |
| `stitch.blend_sharpness` | `8` | Higher = narrower seams (less ghosting); lower = softer transitions |
| `stitch.vertical_lines` | `false` | Level the horizon from vertical lines in the scene (architecture) |
| `capture.brackets` | `auto` | Set a number if auto-detection guesses wrong |
| `output.clean_merged` | `false` | Delete the per-position merges after a run, to save space |

Any of them can be overridden for one run, e.g. `--gamut acescg --width 4096`.

### Exposure anchoring

Pixel values are linear radiance. What changes is which number counts as "1.0":

- **`reference`** (default): values match the middle bracket. A pixel at
  1.0 is exactly as bright as the sensor's clip point at that exposure, so
  mid-grey lands around 0.1–0.2. Renders at Blender's default exposure look
  like the middle photo.
- **A fixed EV100**, e.g. `--ev 12`: all HDRIs share one absolute scale, so a
  brighter location really is brighter. Use it when several HDRIs from one
  place must match (a sequence of positions down a street, day-to-dusk), and
  set Blender's exposure once for all of them.

The EV100 of the anchor is stored in each EXR's metadata (`hdristitch:ev100`).

## Disk space

Per HDRI, at 8K: about 1 GB of working files (merged positions and proxies)
and 0.5 GB of results. Set `output.clean_merged = true` to delete the merged
positions after each run (then `hdri render` needs a fresh `hdri process`).

## Batch

```bash
for d in ~/HDRI/2026-10-*/; do hdri process "$d"; done
```
