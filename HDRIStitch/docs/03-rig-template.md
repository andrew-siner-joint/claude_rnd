# 03 · The rig template (once per camera + lens + rig)

A Nodal Ninja puts the camera in the same eight orientations every time, and
the lens doesn't change. So the hard part of stitching (lens calibration,
image circle, positions) only needs solving once. That solution, saved as a
Hugin project, is the **rig template**. With it every later shoot:

- aligns even where the overlap is featureless (blue sky at the zenith, a
  plain floor at the nadir), because those positions keep the template pose;
- fine-tunes the positions that do have detail, for a little rig play;
- keeps the masks you drew once (e.g. the Nodal Ninja arm).

## 1. Process your first shoot

Use a shoot with plenty of detail all round (buildings or trees, not open
desert), taken with the rig set up as in [01 · Shooting](01-shooting.md).

```bash
hdri process ~/HDRI/first_shoot
```

With no template, HDRIStitch aligns from scratch:

1. finds the fisheye's image circle and starts from your `rig.layout` positions,
2. finds control points (matching features) between overlapping positions,
3. optimises positions, then field of view, then the full lens model (radial
   distortion, centre shift), removes outlier points and optimises again,
4. levels the horizon to the rotator axis.

The log ends with something like:

```
  alignment: 1450 control points, mean error 0.9 px, max 3.8 px
```

**Under about 2 px mean** (at full resolution) is a good alignment. Look at
`hdri/first_shoot_preview.jpg` and check the seams: lines should stay
straight and nothing should appear twice.

## 2. If something is off: fix it in Hugin

```bash
hdri open ~/HDRI/first_shoot      # opens hdri/work/set01/project.pto in Hugin
```

The project uses tonemapped JPEG proxies of each merged position, so it's
quick to work with. In Hugin (*Interface* menu › *Expert*, for all the tabs):

1. **See the problem**: open the **Fast Panorama Preview** (toolbar button,
   or *View* menu). Misaligned seams show as doubled edges.
2. **Fix control points** between the two positions at a bad seam:
   *Control Points* tab › pick the pair left and right › delete points that
   join different features › add a few by clicking the same feature in both
   images (spread them out; corners and small high-contrast details work best).
3. **Re-optimise**: *Photos* tab › *Optimise* › *Geometric*: **Positions and
   barrel distortion (y, p, r, b)** › *Calculate*. Hugin reports the average
   control point distance; aim for under 2.
4. **Mask the rig (optional, recommended)**: the bottom of each "around" shot
   shows the Nodal Ninja arm and tripod. *Masks* tab › pick a position › click
   points around the arm, double-click to close › type **Exclude region**.
   Where the arm sits in the same place in every shot, use **Exclude region
   from all images of the same lens** and draw it once. That mask also lands
   on the up and down shots, so check those: if the arm sits elsewhere there,
   mask each position on its own.
   Masked areas are filled from the neighbouring positions, or left for the
   nadir patch.
5. **Image circle**: *Masks* tab › *Crop*: the circle should sit just inside
   the bright edge of the fisheye image, on every position.
6. **Level by hand, if needed**: Fast Panorama Preview › *Move/Drag* tab ›
   drag the horizon flat. (*Straighten* does it automatically, to the rotator axis.)
7. **Save** (Cmd+S) and re-render the HDR with your changes:

```bash
hdri render ~/HDRI/first_shoot
```

Repeat until the preview looks right.

## 3. Save the template

```bash
hdri template save ~/HDRI/first_shoot
hdri template show          # positions, lens, crop, masks
```

The template lives at `~/HDRIStitch/templates/default.pto` (control points
are stripped; they belong to that one shoot). From now on `hdri process`
uses it automatically:

```
aligning with rig template ~/HDRIStitch/templates/default.pto
  alignment: 1380 control points, mean error 0.8 px, max 3.1 px
```

## Several rigs

Give each its own name and pick it per shoot:

```bash
hdri template save ~/HDRI/first_fisheye_shoot --name a7iv_8mm
hdri process ~/HDRI/next_shoot --template a7iv_8mm
```

or set `rig.template = "a7iv_8mm"` in the config.

## When to remake the template

- Different camera body or lens, or a different crop mode
- You moved the camera on the rail (new NPP) or changed the detent spacing
- You changed the shooting order or the number of positions
- Alignment errors creep up across shoots (`mean error` in the log)

Small things don't need a new template: re-mounting the lens, rig play,
a handheld nadir. Refinement absorbs them. A position backed by fewer than 8
control points (featureless sky) keeps its template pose unless it moved less
than `stitch.max_refine_deg` (2°).
