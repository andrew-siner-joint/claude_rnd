# 06 · Lighting in Blender

## Build the world (add-on)

Enable the add-on once (see [Setup](02-setup-mac.md#5-finish-the-app-hooks)), then:

1. *Properties* › **World** tab › **HDRIStitch** panel.
2. **HDRI**: pick `<shoot>.exr`, or `<shoot>_clean.exr` after clean-up. Pick
   the main file, not `_nosun`; the add-on finds the sun files next to it.
3. **Colour Space**: *Auto* reads it from the EXR (Linear Rec.709, ACEScg or Rec.2020).
4. **Use Sun Lamp** (on if a `_sun.json` exists) and **Camera Sees Original** (on).
5. **Build World**.

You get a world called *HDRIStitch World*:

```
Texture Coordinate ─► Mapping (Z = Rotation) ─┬─► HDRI Lighting (_nosun.exr) ─► Background ─┐
                                              └─► HDRI Camera   (.exr)       ─► Background ─┤ Mix (Is Camera Ray) ─► World Output
```

plus an **HDRI Sun** light: a Sun lamp aimed exactly where the sun is in the
photo, with the sun's real angular size (0.53°).

- Lighting comes from the sun-removed HDRI plus the Sun lamp: crisp shadows
  and far less noise than a sun baked into the HDRI.
- The camera sees the original HDRI, sun included, for backgrounds and reflections.
- **Rotation** turns the HDRI *and* the sun together (a driver keeps them
  aligned; keyframe it freely). **Strength** scales both. **Sun Calibration**
  scales only the sun.
- **Add Gray + Chrome Balls** puts an 18% gray ball and a chrome ball at the
  3D cursor, for checking the lighting against your on-set reference balls.

Without a `_sun.json` (overcast, or sun detection off), you get the plain
world: HDRI › Background › Output.

## Calibrating the sun

Look at the panel: if it says **"Sun was clipped: calibrate it"** (the
usual case outdoors), the lamp's strength is an estimate. The camera couldn't
record the sun's core, so its true brightness isn't in the HDRI. The
starting estimate assumes a clear day, where direct sun is about 7× the
light from the whole sky. Pick one way to pin it down:

### a) Gray card in sun and shade (quick, from the HDRI shoot)

On set, lay a gray card flat on the ground in full sun and shoot it, then
shade just the card (your body, a flag) and shoot again at the **same
exposure**. Measure the card in both frames (Lightroom's RGB readout, or
Nuke's Viewer) and divide: lit ÷ shaded = **R**, typically 3–10. Then:

```bash
hdri sun ~/HDRI/rooftop/hdri/rooftop.exr --remove --card-ratio 6.2
```

and click **Build World** again. The strength becomes
sky light × (R − 1) / sin(sun elevation): the extra light the sun adds on a
flat card, converted to light hitting the sun-facing side.

### b) Gray ball (most accurate)

1. Match your plate camera, put the CG gray ball where the real one was
   (**Add Gray + Chrome Balls**).
2. Render and compare with the photographed gray ball: shadow side, lit side,
   and the terminator between them.
3. Adjust **Sun Calibration** until the lit/shadow contrast matches. Adjust
   **Strength** if the overall level is off.
4. The chrome ball checks orientation: the sun's highlight and the
   environment should sit in the same places as in the photo.

### c) By eye

Clear midday sun is about 7–10× the sky light, hazy 3–5×, and on overcast
days there's no distinct sun. *Sun Calibration* of 1.0 is the clear-sky
starting point.

If the sun was **not** clipped (low sun through haze, or an ND frame), the
strength is measured directly from the HDRI, and calibration should land
near 1.0.

## Colour management

| HDRIStitch `gamut` | Image colour space set by the add-on | Use when |
|---|---|---|
| `rec709` (default) | *Linear Rec.709* | Blender's default working space |
| `acescg` | *ACEScg* | ACES pipelines; Blender 5's ACEScg working space |
| `rec2020` | *Linear Rec.2020* | Rec.2020 working space |

Blender converts the texture to whatever working space the file uses. In
Blender 5 the working space can be changed per file (*Render Properties ›
Color Management*). If you render in ACEScg, process with `--gamut acescg`
to avoid a gamut conversion and keep saturated colours unclipped.

View transform: *AgX* (default) or *Standard*/*Filmic*. With the default
`reference` exposure anchor, *Exposure 0* renders look like the middle
bracket. With a fixed EV100 anchor, set the exposure once for the location.

## Render tips

- **Cycles world sampling**: *World › Settings*, Sampling *Auto*. For HDRIs
  with small bright areas (windows, lamps), *Manual* with Map Resolution
  2048–4096 cuts noise.
- **Film › Transparent** for compositing over plates. The world still
  lights; the camera stops seeing it.
- **Shadow catcher**: a ground plane with *Object › Visibility › Shadow
  Catcher* (Cycles) gives contact shadows for the comp.
- **EEVEE**: works with the same world and lamp. EEVEE can also extract a sun
  from the world (*World › Settings › Sun*); the HDRIStitch lamp already does
  that, so the lighting HDRI has no sun left to extract.
- The HDRI is infinitely far away. For CG sitting on the ground in the
  photo, use a shadow catcher plane rather than expecting the HDRI's ground
  to line up.

## Without the add-on

The same thing by hand:

1. World nodes: *Texture Coordinate* (Generated) › *Mapping* (Point) ›
   *Environment Texture* (the EXR, Color Space *Linear Rec.709*) ›
   *Background* › *World Output*.
2. Sun: add a Sun light. From `<shoot>_sun.json`, set *Rotation* (XYZ Euler)
   to `blender_rotation_deg`, *Angle* to `angle_deg`, *Strength* to
   `strength` and *Color* to `color`.
3. If you rotate the Mapping node by θ around Z, set the Sun's Z rotation to
   `blender_rotation_deg[2] − θ`. (The add-on's driver does this for you.)
4. Light with `_nosun.exr` in step 1 when you use the Sun lamp; otherwise
   the sun is counted twice.
