# 05 · Clean-up in Nuke

What usually needs doing:

- **Nadir**: the tripod and rig are in the shot. Always.
- **Level / orientation**: if the tripod wasn't quite level, or you want a
  different direction in the centre of the HDRI.
- **Calibration** (optional): exactly scale and neutralise the HDRI to a
  gray card that appears in it.
- Small fixes: a person who walked through, a lens flare.

The tools work in any Nuke edition (no NukeX or CaraVR needed). Pixel values
pass through as raw scene-linear data: the Read and Write nodes are set to
*raw data*, so no colour conversion happens anywhere.

## One-click setup

**Nodes › HDRIStitch › Cleanup Script from EXR…** and pick
`<shoot>/hdri/<shoot>.exr` (the main one, not `_nosun`). You get:

```
Read (raw) ─► Orient_HDRI ─► Nadir_to_horizon ─► Paint_Nadir ─► Nadir_back ─┐
                    │                                                      ▼
                    └──────────────────────────────────────────────► Nadir_patch ─► GrayCard_Calibrate ─► Write_HDRI
                                                       Nadir_band (mask) ──┘          (disabled)          <shoot>_clean.exr
```

(The other menu items build the same pieces one at a time on the selected node.)

## 1. Patch the nadir

The nadir is the bottom row of a lat-long: stretched into a smear across the
whole width and impossible to paint directly. `Nadir_to_horizon` rolls the
sphere 90° so the nadir sits **on the horizon, a quarter of the way in from
the left or right edge**, where it looks like an ordinary photo of the
ground. `Nadir_back` rolls it back, and `Nadir_patch` merges only a soft band
around the pole into the original, so the rest of the image is never
resampled.

1. View **Paint_Nadir**. Find the tripod (left or right quarter, on the horizon).
2. Paint it out with RotoPaint: **Clone** (C) ground texture over the legs
   and head, or **Reveal** from a frame held offset. Work in the middle of
   the view; the edges of the band blend away anyway.
3. View **Nadir_patch** to check the result in lat-long. If your paint goes
   further than the band, raise the top of `Nadir_band`'s *area*.
4. `<shoot>_holes.png` (loaded as `Nadir_no_photo`, if it exists) shows in
   white what no photo covered. HDRIStitch pre-filled it with a smooth blur;
   paint real texture over it.

If the nadir comes out split across the left and right edges instead (a
Nuke build with a different rotation convention), change `rz` to `rx` in both
`Nadir_to_horizon` and `Nadir_back`.

Zenith: **Nodes › HDRIStitch › Zenith Patch** does the same at the top
(rarely needed outdoors; useful for ceiling lights indoors).

## 2. Level and orient

`Orient_HDRI` is a lat-long-to-lat-long SphericalTransform:

- **ry** turns the HDRI (which direction is in the centre)
- **rx / rz** tilt it: level a horizon that's sloped or wavy

Level it before painting the nadir, since the patch sits downstream. If you
turn or level the HDRI here, re-run the sun step below so the sun data
matches.

## 3. Calibrate to a gray card (optional)

If a gray card (or ColorChecker gray patch) appears in the HDRI in the main
light:

1. Enable **GrayCard_Calibrate**.
2. Click the eyedropper on *card sample*, then **Ctrl+Shift+drag** (Cmd+Shift
   on a Mac) a box over the card in the Viewer to sample its average.
3. *card value*: the card's real reflectance (0.18 for an 18% card).
4. *also white-balance to the card*: on makes the card exactly neutral; off
   only fixes exposure.

This scales the whole HDRI. Only do it if the card sees the light your CG
object will see.

## 4. Write

Render **Write_HDRI** (select it, then *Render › Render Selected*). It writes
32-bit float, ZIP-compressed, RGB only, raw data, with the original EXR's
metadata passed through. The output is `<shoot>_clean.exr` next to the
original.

## 5. Update the sun data for the clean HDRI

The Blender add-on looks for `<name>_sun.json` next to the EXR you load.
Regenerate it for the cleaned file, which also writes the sun-removed copy:

```bash
hdri sun ~/HDRI/rooftop/hdri/rooftop_clean.exr --remove
```

If Nuke dropped the metadata, add `--gamut` (when it isn't rec709) and
`--clip-level` (from the original EXR's `hdristitch:clip_level`, or read it
from `hdri/work/set01/run.json`), so the sun is correctly flagged as clipped.

---

## Alternative: DaVinci Resolve (Fusion page)

Fusion ships a tool made for exactly this, **LatLong Patcher**:

1. Put the EXR on a timeline. On the Fusion page:
   `MediaIn ─► LatLongPatcher1 (Mode: Extract, X rotation −90) ─► Paint ─►
   LatLongPatcher2 (Mode: Apply, same rotation; background = MediaIn) ─► MediaOut`.
   If the extracted view shows the sky, use +90.
2. Paint the tripod out in the Paint node (Clone stroke).
3. Deliver page: format **EXR**, float, source resolution, single frame.

Keep the project unmanaged (DaVinci YRGB) with no grade on the clip, so the
linear values pass through untouched.

## Alternative: Photoshop

Photoshop opens the EXR in 32-bit mode. The Clone Stamp works there, so
small fixes **away from the poles** (a person on the horizon, a sensor spot)
are fine: *File › Save a Copy › OpenEXR*. Don't try the nadir in
Photoshop's flat lat-long view; use Nuke or Fusion for that.
