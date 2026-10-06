# 07 · Troubleshooting

First: `hdri doctor`. Then the shoot's log: `<shoot>/hdri/work/set01/log.txt`,
plus `hugin.log` (every Hugin command and its output) in the same folder. For
a full Python traceback, run with `HDRI_DEBUG=1 hdri process …`.

## Installing

**`hdri: command not found`**: open a new Terminal window (the installer
added `~/.local/bin` to your PATH in `~/.zprofile`), or run `~/.local/bin/hdri`.

**Hugin tools not found**: install Hugin ([Setup](02-setup-mac.md#2-hugin-free-stitching-engine)).
If it lives somewhere unusual, find `nona` (`mdfind -name nona`) and set
`[tools] hugin_bin` in `~/HDRIStitch/config.toml` to its folder.

**Hugin tools found "but won't run"**: macOS is blocking the download
(`xattr -dr com.apple.quarantine /Applications/Hugin*`), or it's an Intel
build on Apple Silicon without Rosetta (`softwareupdate --install-rosetta`).
Re-running `./install.sh` handles both.

**The droplet does nothing**: allow it to control Terminal (*System Settings ›
Privacy & Security › Automation › HDRI Process › Terminal*).

## Grouping

**"not a multiple of 8 positions"**: a frame is missing or extra (a test
shot, a slate, a position fired twice). The message lists every bracket with
its time and files; delete or move the odd ones out. `--dry-run` shows the
grouping without processing anything.

**"Could not split the frames into equal brackets"**: shot in A or S mode
(the exposure pattern changes between positions) with bursts too close
together to separate by time. Pass `--brackets 5` (your frames per bracket).

**"A bracket contains two frames with the same exposure"**: the bracket
count is wrong (`--brackets`), or a bracket was interrupted and restarted.

## Colour and exposure

**Colours look wrong / too warm or cool**: check the log's
`white balance:` line. `auto` uses Lightroom sidecars when they exist; did
you save metadata (Cmd+S)? Force one with `--wb 5600` or `--wb asshot`.

**The whole HDRI is too dark or bright in Blender**: that's the exposure
anchor (see [Processing](04-processing.md#exposure-anchoring)). Adjust
Blender's *Film › Exposure* or the add-on's *Strength*, or calibrate to a
gray card in Nuke. The data itself is linear and correct.

**Banding or steps in smooth gradients near bright areas**: the brackets were
too far apart, or one frame moved. Check `bracket steps` in the log: a `*`
marks a step that couldn't be measured (too few pixels well exposed in both
frames), so it relies on the EXIF shutter speeds.

**Magenta or blown sun core**: shouldn't happen (clipped pixels are lifted
to neutral white). If it does, the darkest frame clipped over a large area:
shoot darker (1/8000 s, f/11).

## Alignment

**`WARNING: control point error is high`**: `hdri open <shoot>`, look at the
Fast Panorama Preview, fix the bad pair's control points, re-optimise, save,
`hdri render`. See [Rig template](03-rig-template.md#2-if-something-is-off-fix-it-in-hugin).

**Doubled objects near the camera, fine far away**: parallax. The camera
isn't rotating around the lens's no-parallax point. Re-check the Nodal Ninja
rail position ([Shooting](01-shooting.md#1-find-the-no-parallax-point-npp)).

**Doubled clouds or people**: they moved between positions. Raise
`--sharpness` (e.g. 16) for narrower seams, or paint them in Nuke.

**"position N moved … keeping the template pose"**: that position found
too few control points to trust a big move (featureless sky). Fine if the
rig was set up the same as for the template. If it wasn't, remake the
template.

**The first shoot (no template) won't align**: too little overlapping
detail, or the shooting order doesn't match `rig.layout`. Check the order;
otherwise open the project in Hugin and add control points by hand.

### The horizon is tilted

The panorama is levelled to the rotator's axis, so a tilted tripod gives a
tilted (sine-wave) horizon. Fixes, best first:

1. Level the tripod next time.
2. Architecture or other true verticals: `stitch.vertical_lines = true`
   in the config (Hugin finds vertical lines and levels to them), then
   `hdri process` again.
3. In Hugin: `hdri open`, Fast Panorama Preview › *Move/Drag*, drag the
   horizon level, save, `hdri render`.
4. In Nuke: `Orient_HDRI` (rx / rz).

## Output

**Black area at the bottom**: no photo covered it (the tripod was masked, or
the nadir shot is missing). It's filled with a blur and marked in
`<shoot>_holes.png`; patch it in Nuke.

**Seam where the image circle ends / dark fringe**: increase
`lens.crop_margin` (e.g. 0.05) and remake the template, or tighten the crop
circle in Hugin (*Masks › Crop*).

**Out of memory**: lower `merge.jobs` (e.g. 1) or `output.width`. 16K
output needs about 32 GB.

## Blender

**HDRI rotated vs. the photo / sun lamp in the wrong place**: use the
add-on's *Rotation*, not the Mapping node directly, so the sun follows. If
you edited the EXR's orientation in Nuke, regenerate the sun data:
`hdri sun <clean>.exr --remove`.

**No sun lamp was created**: there's no `<name>_sun.json` next to the EXR
you picked (overcast, sun detection off, or a renamed file). Run `hdri sun`.
