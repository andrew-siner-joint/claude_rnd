# 08 · How it works

For when you want to know what happens to your pixels, or need to explain
the HDRI's provenance to a lighting supervisor.

## 1. Grouping

EXIF (exiftool, or a built-in reader) gives each frame's time and exposure
`H = t × ISO/100 / N²`. Frames are sorted by capture time. The bracket size
is the smallest N for which every run of N frames contains N distinct
exposures, all runs sharing the same set (manual-mode bracketing). If the
exposures vary between positions, it falls back to the time gaps between
bursts. Brackets are then grouped into sets of 8 positions.

## 2. Linear raw decoding

LibRaw (via rawpy) develops every frame identically:

- camera-native RGB, **white balance gains 1, 1, 1** (so no channel clips
  before the others), no colour matrix yet
- linear: gamma 1, no auto-brightening, no tone curve
- LibRaw's per-image white-level adjustment **off**, so every frame has the
  same scale; values are normalised so 1.0 = the sensor's white level
- no rotation (every position has the same pixel geometry, which the rig
  template relies on)

Each frame also gets a saturation mask, measured on the raw Bayer data
before demosaicing and slightly dilated. The true clip point is detected
from the pile-up of values at the top of the histogram, since it often sits a
little below the nominal white level.

## 3. Merging a bracket

For each pixel, each frame's estimate of radiance is `value / H`. These are
averaged with weights:

- **∝ exposure H**: brighter frames have less noise relative to signal (and
  that's close to optimal for photon noise),
- **× a smooth roll-off** from 80% to 95% of the clip level, and zero inside
  the (feathered) saturation mask.

Pixels clipped in every frame (the sun's core) keep the darkest frame's
value: a lower bound. Before white balance they're lifted to neutral, since
equal clipped channels would otherwise turn magenta once white balance gains
were applied.

**Exposure ratios are measured, not assumed.** For each pair of neighbouring
frames, the median ratio over pixels well exposed in both (3–70% of clip)
replaces the EXIF ratio, if it's within ⅓ stop of it. Real shutters are off
by a few percent, which would otherwise show up as faint bands where the
merge hands over from one frame to the next. The chain is re-anchored so the
middle frame keeps its nominal exposure.

Finally the values are scaled to the exposure anchor: the median middle
bracket across positions (`reference`), or a fixed EV100.

## 4. White balance and colour

White balance is a per-channel gain on camera RGB, the same operation as
Lightroom's on raw data. Kelvin/tint are converted with the DNG SDK's
algorithm (Robertson isotemperature lines in CIE 1960 uv, tint scale
−3000), using the camera's colour matrix from LibRaw (Adobe's D65
ColorMatrix for the a7 IV):

```
illuminant xy  = kelvin_tint_to_xy(T, tint)
camera neutral = CameraMatrix · XYZ(xy)
gains          = 1 / camera neutral          (green = 1)
```

The camera-to-output matrix follows dcraw/LibRaw: CameraMatrix · XYZ←Rec.709,
rows normalised so a balanced neutral maps to (1, 1, 1), inverted, then
Rec.709 → ACEScg/Rec.2020 (Bradford) if asked. Everything is float; negative
values from out-of-gamut colours are clamped at the end of the merge.

Lightroom interpolates between two calibration matrices (Illuminant A and
D65) and adds its own profile, so the same kelvin/tint is close to, not
identical to, Lightroom's rendering away from daylight. For lighting, that's
the right trade: a pure linear transform beats a match to a display-referred
look.

## 5. Alignment

Each merged position also gets a tonemapped JPEG **proxy** at full
resolution, all with the same exposure so overlapping proxies match. Hugin
works only on proxies:

- **No template**: `pto_gen` (circular fisheye, image circle detected by a
  least-squares circle fit, field of view estimated assuming the circle spans
  180°) › positions seeded from `rig.layout` › `cpfind --prealigned` (falls
  back to `--multirow`) › staged `autooptimiser` (positions; + field of view
  and barrel; + full lens a, b, c, d, e) › `cpclean` › re-optimise ›
  `pano_modify --straighten` (rotator axis vertical) or, opt-in, levelling to
  vertical lines (`linefind`).
- **Template**: images swapped into the saved project › `cpfind
  --prealigned` › positions-only optimisation › `cpclean` › again. Any
  position with fewer than 8 control points that moved more than 2° (checked
  with Hugin's own `pano_trafo`) reverts to its template pose.

## 6. Stitching in floating point

Hugin's remapper (`nona`) clamps values to 0–1 and applies photometric
corrections, which would wreck an HDR. So nona never sees HDR pixels:

1. A **coordinate ramp** image is written: pixel (x, y) holds
   ((x + ½)/W, (y + ½)/H, ½), all inside 0–1.
2. nona remaps the ramp through each position's geometry (bilinear
   interpolation, photometric parameters neutralised). Bilinear
   interpolation of a linear ramp is exact, so the output holds the exact
   sub-pixel source coordinate of every panorama pixel, with Hugin's crop
   circle and masks baked into alpha. (The constant ½ channel survives to
   within 10⁻⁶, which shows the photometrics really are neutral.)
3. The merged HDR is resampled at those coordinates with OpenCV. In
   *safe-cubic* mode it's bicubic, except where bicubic and bilinear disagree
   strongly (next to the sun, where bicubic rings and can go negative);
   there bilinear is used.
4. **Blending**: each position's weight is `(1 − r/R)^k` (r = distance from
   the image-circle centre, R = its radius, k = blend sharpness), times a
   feather from mask edges. Every pixel comes mostly from the position that
   saw it closest to the centre of the lens, where a fisheye is sharpest and
   least vignetted. It's a plain weighted average of linear radiance, so
   energy is preserved and no multi-band ringing appears around the sun.
5. Uncovered pixels are filled by push-pull (a pyramid blur) and listed in `_holes.png`.

On synthetic test shoots with a known answer, the stitched result matches
the scene to within ±0.3 px geometrically and within 0.3% in colour and
exposure.

## 7. Sun

The brightest blob's luminance-weighted centroid gives the direction (taken
across the seam correctly). Its irradiance is the sum of
`(L − local sky) × solid angle` within 3° (disc plus lens glow). It's
flagged as clipped when its peak reaches the merge's recordable maximum,
which is stored in the EXR. It's removed by push-pull filling from the
surrounding sky with a soft edge. Blender's Sun lamp strength is irradiance,
in the same units as the HDRI's radiance, so a measured value plugs straight in.

## Files in `work/setNN/`

| File | What |
|---|---|
| `merged/posNN.exr` | Merged HDR per position (camera geometry, output gamut) |
| `proxies/posNN.jpg` | Tonemapped proxies Hugin aligns |
| `project.pto` | The Hugin project (open with `hdri open`) |
| `run.json` | Everything decided for this set: white balance, EV, files, alignment stats |
| `log.txt`, `hugin.log` | What happened |
