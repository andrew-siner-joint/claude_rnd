# 01 · Shooting

Good HDRIs are made on set. The software can merge, align and blend, but it
can't remove parallax, motion or a tilted tripod after the fact.

## Kit

- Sony a7 IV, 8 mm circular fisheye
- Nodal Ninja: lower rotator set to **60° detents** (6 stops), upper rotator
  for the up and down shots
- Sturdy tripod with a **levelling base** (or a levelled head) and a bubble level
- Remote, or the camera's 2-second bracket self-timer
- For calibration: an 18% gray card, a ColorChecker, and ideally a gray ball
  and a chrome ball

## One-time rig setup

### 1. Find the no-parallax point (NPP)

The camera must rotate around the lens's entrance pupil, or near objects
shift against far ones between shots and no stitcher can line them up.

1. Mount the camera and lens on the Nodal Ninja and set the upper rotator to 0° (level).
2. Put something thin and near (a light stand, a pen on a stand) about 1 m
   away, lined up with something far away (a door frame or pole 10 m+).
3. Turn the rotator so the pair sits near the edge of the frame, about 60–80°
   off-centre for a fisheye, then turn it the other way.
4. If the near object moves against the far one, slide the camera along the
   upper rail and repeat until it doesn't. Write the rail position down.

Nodal Ninja publishes starting values for common lenses. For 8 mm fisheyes
the NPP is near the front of the lens, so expect the camera body to sit well
back on the rail.

### 2. Level the rig

HDRIStitch levels the horizon **to the rotator's axis** (Hugin's
"straighten"). If the tripod is level, the HDRI is level. If it isn't, the
horizon comes out tilted, because nothing in the photos records gravity
(see [Troubleshooting](07-troubleshooting.md#the-horizon-is-tilted) for the
fixes). Level the base every time you set up.

### 3. Camera settings (a7 IV)

| Setting | Value | Why |
|---|---|---|
| Exposure mode | **M** | Identical brackets at every position, so they group and merge cleanly |
| RAW file type | **Uncompressed** or **Lossless Compressed (L)** | Sony's lossy "Compressed" raw leaves artefacts at high-contrast edges, which an HDRI is full of |
| ISO | **100** | Base ISO, most dynamic range |
| Aperture | **f/8** (f/5.6–f/11) | Sharp to the edge of the circle, little vignetting. Don't change it mid-shoot |
| Focus | **Manual**, set once at about 0.5 m and tape the ring (at f/8 an 8 mm lens is then sharp from ~20 cm to infinity) | Autofocus would hunt between positions |
| White balance | Daylight (fixed) | Raw ignores it, but it keeps "as shot" consistent and previews honest |
| Drive | **Continuous Bracket, 5 frames, 3.0 EV** | The widest spread the a7 IV brackets in-camera (if your menu shows other choices, take the widest) |
| Bracket settings | Self-timer during bracket: **2 s**; bracket order: any | No shake from pressing the button; HDRIStitch sorts by exposure |
| SteadyShot | **Off** | On a tripod the stabiliser can shift the sensor between frames |
| Shutter | Mechanical (e-front curtain is fine) | The electronic shutter can band under LED/fluorescent light |
| Long Exposure NR | Off | Otherwise every long frame takes twice as long |
| Release without Lens | Enable | Needed for manual fisheyes with no electronic contacts |

RAW+JPEG is fine: HDRIStitch only reads the `.ARW` files.

## Exposure plan

A sunny exterior spans far more than any bracket: deep shadow to blue sky is
about 15 stops, and the sun's disc is another 10+ above that. Plan the
brackets so that:

1. **The darkest frame holds everything except the sun's core.** Set the
   middle exposure so the darkest frame lands at your fastest shutter
   (1/8000 s). With 5 × 3 EV that puts the middle frame at about 1/125 s and
   the brightest at about 1/2 s, at f/8 and ISO 100.
2. **The brightest frame lifts the deepest shadows** to mid-grey on the
   histogram.
3. Check the darkest frame's histogram on the first position: only the sun
   (and its glow) should be blown.

The sun's core will clip. That's expected and handled: see
[Sun strategy](#sun-strategy) below.

Interiors with windows often need more range than 5 × 3 EV. Fire **two
bursts per position** with the middle exposure 15 EV apart (10 distinct
frames), then process with `--brackets 10`.

At night, exposures get long. Keep Long Exposure NR off, and expect the
bracket to take a while; wait for it to finish before turning the rig.

## Shooting order

Shoot every HDRI in the same order. It's what lets the rig template line up
the next shoot automatically:

1. **6 around** at 0° tilt: start at your "front" (it becomes the centre of
   the HDRI), and turn **right** (clockwise seen from above) one detent each time.
2. **Up**: zenith, upper rotator at +90°.
3. **Down**: nadir, upper rotator at −90°.

That's `rig.layout` in `~/HDRIStitch/config.toml`. If you turn left instead,
or shoot down before up, edit the layout to match (yaws `0, -60, -120, …`).

Per position, fire one bracket and wait for it to finish. **Don't touch the
rig during a bracket.**

Keep the shoot folder clean: one folder per location, holding exactly that
location's HDRI frames. Test shots, slates and reference photos go
elsewhere, or the bracket count won't add up. (Several complete HDRIs in one
folder are fine; they're split into sets automatically.)

## Movement

Everything that moves between frames ghosts: people, cars, leaves, clouds.

- Shoot when the scene is still; wait for people to pass.
- Clouds drift between positions. A sharper seam blend
  (`--sharpness 16`) narrows the overlap, so a cloud edge shows up in one
  place instead of twice.
- Fast bracketing helps: brighter scenes give shorter brackets.

## Reference captures (strongly recommended)

Shoot these into a **separate folder**, right after the HDRI, in the same light:

- **ColorChecker + gray card** in the main light, filling a good part of the
  frame. Use them to check or set white balance (Lightroom's eyedropper on
  the gray patch) and to calibrate exposure in Nuke.
- **Gray card flat on the ground, once in sun and once in shade**, same
  exposure. The brightness ratio sets a clipped sun's strength (`--card-ratio`,
  see [Blender](06-blender.md#calibrating-the-sun)).
- **Gray ball + chrome ball** where your CG will sit, shot from the plate
  camera position. These are the gold standard for checking the lighting in
  Blender: the add-on creates matching CG balls.

## Sun strategy

The camera can't capture the sun's core at any setting it has, so you
choose how to handle it:

- **Default: clipped sun → Sun lamp.** HDRIStitch finds the sun, records its
  exact direction, paints it out of a lighting copy of the HDRI, and the
  Blender add-on adds a Sun lamp in its place. You set the lamp's strength by
  gray card ratio or gray ball (see [Blender](06-blender.md#calibrating-the-sun)).
  This is the standard VFX approach: sharper shadows, much less render noise,
  and a key light you can control.
- **ND gel for the sun (advanced).** Some 8 mm fisheyes take a rear gel
  filter. An extra exposure through an ND 3.0 (10 stops) can record the
  disc, but you then have to merge it in by hand (in Nuke, scaling by the ND
  factor). Most productions don't bother; calibrating the lamp is quicker and
  just as accurate for lighting.

Overcast days are the easy case: no distinct sun, and the whole sky fits in
the bracket.
