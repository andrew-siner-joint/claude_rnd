# BlinkFlare

A procedural lens flare node for Nuke, in the spirit of Optical Flares and
Sapphire LensFlare. One BlinkScript kernel draws every element analytically
per pixel, so it runs on the GPU when one is available and falls back to
vectorized CPU otherwise.

![Presets](docs/previews/presets.png)

## Elements

| Tab | Element | What you get |
|---|---|---|
| Glow | Glow + hot core | Soft halo with adjustable falloff, plus a tight bright core |
| Glints | Starburst rays | Random-length rays with taper, width variation, and a second layer of fine rays |
| Streaks | Anamorphic streaks | 1 = classic horizontal line, 2 = cross, more = star; core plus soft haze |
| Ring | Halo / hoop | Plain or rainbow ring; can sit anywhere on the flare axis and fade into a partial arc (hoop) |
| Ghosts | Iris reflections | Aperture-polygon ghosts scattered along the axis, with blade count, roundness, hollowness, chromatic fringe and hue variation |
| Spectral | Little rainbow streaks | Short spectral streaks around the light; Orientation turns them from radial spikes into curved rainbow arcs |
| Dirt | Lens dirt | A texture on the `dirt` input lit by the glow and by the flare itself |

![Elements](docs/previews/elements.png)

## Inputs

| Input | Use |
|---|---|
| `src` | The plate. Sets the render format, and is sampled when the flare follows the source. |
| `occlusion` | Matte (alpha) or CG render (depth.Z) of whatever passes in front of the light. |
| `dirt` | Lens dirt texture, fitted to the frame automatically. |
| `cam` | Camera, for 3D placement. Dots in between are fine. |
| `axis` | Axis, Light, or any 3D transform marking the light in 3D. |
| `mask` | Standard effect mask (alpha) on the composite. |

## Placement

Two points control the flare, both with viewer handles and keyframable:

- **Light Position**: the light source. Track or keyframe it.
- **Articulation Point**: the optical center the flare pivots through. The
  flare axis runs from the light through this point. An element at *axis
  position* `t` sits at `light + t * (articulation - light)`: `0` is on the
  light, `1` on the articulation point, `2` mirrored to the far side. Ghosts
  are scattered between **Axis Start** and **Axis End**, and the ring has its
  own **Axis Position**.

**Articulation** picks where that pivot comes from. *Manual* uses the knob,
*Frame Center* follows the format, and *Lens Center* follows the camera's
optical center, including any window translate (lens shift). Without a camera,
Lens Center is the frame center.

With **Rotate With Light** on, glints and spectral streaks turn as the light
orbits the articulation point:

![Articulation](docs/previews/articulation.gif)

### 3D: camera and axis

Set **Light Source** to *3D Camera + Axis*, connect a camera to `cam`, and
connect an Axis, Light or any 3D transform to `axis`. The light is then the
axis seen through the camera, every frame and every motion-blur sub-frame.
**3D Offset** (3D tab) moves the light in the axis's space; with nothing on
`axis` it is simply the light's world position. When the light goes behind
the camera the flare switches off, and in depth-occlusion mode the light's
real depth is used.

![3D](docs/previews/camera.gif)

**Bake to 2D** (3D tab) writes the projected light, and the lens center if
Articulation is Lens Center, into keyframes on the 2D knobs over a frame
range. It then switches to 2D, so you can hand-tweak a 3D-driven flare or
hand it to someone without the camera.

How it works, for pipeline TDs:

- Two Axis nodes inside the group are parented to the `cam` and `axis`
  inputs. Their `world_matrix` mirrors the incoming transforms, so Dots,
  parent hierarchies and animated cameras all just work.
- A NoOp called `Projection` turns those into a screen position using plain
  Nuke expressions. Nothing evaluates Python at render time, so it works on
  a render farm without this package installed.
- The camera's lens knobs (focal, horizontal aperture, window
  translate/scale/roll) are linked by expression to the connected camera, by
  absolute path. The group's knobChanged callback updates that link whenever
  the `cam` input changes, and re-checks it when the panel opens. The callback
  is stored on the node and needs no package either. After renaming a camera,
  press **Refresh Camera Link**.
- Supported: classic 3D system cameras in perspective mode. Not handled:
  orthographic or spherical projection, and the camera's own lens distortion.
  Undistort or redistort around the flare as you would for any 2D element.

## Visibility

- **Off-screen Fade** dims the flare as the light leaves the frame. Ghosts
  still sweep in from off-frame lights within the fade distance.
- **Use Occlusion Input** samples a disk around the light. Pick the **Mode**:
  - *Alpha*: a roto or matte of whatever covers the light.
  - *Depth (1/z)* or *Depth (z)*: a CG render's depth.Z, compared with the
    light's depth, so CG geometry occludes the flare correctly. Nuke's
    ScanlineRender writes 1/z. In 2D mode, set **Light Depth** by hand.
- **Follow Source** samples the plate at the light:
  - **Brightness** scales the flare with the plate brightness between
    **Black Point** and **White Point**, up to 10×. A flare on a practical
    light then flickers with it, and dims when leaves or people pass in front
    of it, with no roto.
  - **Color** tints the flare with the plate's color there.
- **Flicker** adds noise-driven brightness variation over time.

Sample radii are in thousandths of the frame height, so results don't change
with resolution.

## Output

- **Output** is *Composite* (merged onto `src` with the linked Merge
  **Operation**, `plus` by default, with **Mix** and the `mask` input) or
  *Flare Only* (on black, for stacking or comping yourself). The source alpha
  and other layers pass through untouched.
- **Solo** shows one element while you tune it.
- **Element Layers** also writes each element to its own layer
  (`flare_glow`, `flare_glints`, `flare_streaks`, `flare_ring`,
  `flare_ghosts`, `flare_spectral`, `flare_dirt`), so ghosts or streaks can be
  graded separately downstream. The first time you switch it on, the node
  builds one solo kernel per element.
- **Render Region** is *Format*, or *Input BBox* for overscan comps.
- **Motion Blur** renders sub-frames with a TimeBlur (Samples, Shutter,
  Shutter Offset). Light, camera and axis motion all blur correctly, because
  everything is evaluated at each sub-frame.
- **Use GPU if available** and **Vectorize on CPU** are linked straight to
  the kernel.

## Presets

Built-in presets plus your own. **Save...** writes the current look to a JSON
file, and it then shows up in the Preset menu. Presets only store and reset
*look* knobs; placement, visibility, output and render settings are left
alone. Applying a preset can be undone.

| Folder | Purpose |
|---|---|
| `~/.nuke/blinkflare_presets` | Personal presets; Save writes here (override with `BLINKFLARE_PRESET_SAVE_DIR`) |
| `BLINKFLARE_PRESET_PATH` | Extra folders, `os.pathsep`-separated, e.g. studio and show libraries. Earlier folders win when names clash. |

## Install

Requires Nuke 13 or later (Python 3).

1. Copy the `BlinkFlare` folder somewhere permanent.
2. Add it to the plugin path in `~/.nuke/init.py` (or your studio's init):

   ```python
   nuke.pluginAddPath("/path/to/BlinkFlare")
   ```

3. Restart Nuke. The node is under **Nodes > Draw > BlinkFlare**, or press Tab
   and type `BlinkFlare`.

Creating the node compiles the Blink kernel, which needs **NukeX or Nuke
Studio**. For seats that can't compile kernels, a NukeX user runs **Draw >
BlinkFlare > Save ToolSet**. That writes a ready-built node with the compiled
kernel to `~/.nuke/ToolSets/BlinkFlare.nk`, which you can share like any
ToolSet. Saved scripts render without the package. Only the buttons (presets,
bake, building element layers) need it.

## Performance

- Every element is closed-form math per pixel. There are no blurs or
  convolutions and no intermediate buffers.
- Glints, ghosts and spectral streaks skip pixels outside their bounds. That
  is bit-identical to evaluating everything, and about 2.4x faster.
- Cost grows with **Ghosts > Count** and **Spectral > Count** (up to 64
  each), and with occlusion and source sampling, which take 16-64 samples per
  pixel when on. Glint ray count is essentially free, and disabled elements
  cost nothing.
- **Motion Blur** multiplies the cost by its sample count.
- **Element Layers** roughly triples it, because each element is rendered
  again on its own and dirt needs the full flare.

As a rough reference, the CPU test harness (plain scalar C++, 4 threads)
renders 1080p in about 180 ms with the Default preset. Nuke's vectorized CPU
path should be faster than that, and the GPU path much faster, but neither has
been measured yet.

## How it's built

```
BlinkFlare/
  menu.py, init.py           Nuke startup files (menu entries)
  blinkflare/
    kernel/BlinkFlare.blink  the BlinkScript kernel (all rendering)
    spec.py                  every group knob and the kernel param it drives
    camera.py                3D projection: Nuke expressions + Python reference
    presets.py               built-in presets and the saved-preset library
    builder.py               builds the Group in Nuke from spec.py
  tests/                     tests + a CPU harness for the kernel
  docs/previews/             images in this README (generated)
```

`builder.create()` builds a Group around the kernel and links every kernel
parameter to a group knob by expression. `spec.py` is the single source of
truth: the tests check that the kernel's params and defaults, the spec, the
builder and the presets all agree.

## Development

Nuke isn't needed to run the tests:

- `tests/harness/` compiles the *actual* `.blink` file as C++ against a small,
  deliberately strict stand-in for the Blink API, and renders on the CPU.
- `tests/fake_nuke.py` runs the builder and the stored callbacks. Its
  BlinkScript node creates param knobs by parsing the kernel, like Nuke does on
  compile.
- `tests/nuke_expr.py` evaluates the generated Nuke expressions, so the 3D
  projection expressions are checked numerically against `camera.project()`
  over 200 random cameras, lights and lens settings.

```sh
pip install numpy pillow
python3 -m unittest discover -s tests          # 84 tests
python3 tests/harness/preview.py --preset "Sci-Fi Hoop" --out flare.png
python3 tests/harness/preview.py --set ghost_count=30 --set ghost_blades=5 --out flare.png
cd tests/harness && python3 preview.py --docs ../../docs/previews   # README images
```

When adding a parameter: declare it in the kernel's `param:` block, add a
`defineParam` with label == variable name, and add a knob to `spec.py` with
the same default. The tests fail until all three agree.

## Not yet verified inside Nuke

This was built and tested without a Nuke license. These Nuke-specific
assumptions need a first run in NukeX before production use:

- **Blink compile.** The kernel compiles cleanly against the strict shim, but
  Nuke's Blink compiler is the real judge, on both the GPU and CPU paths.
- **BlinkScript knob names.** Param knobs are assumed to be
  `BlinkFlareKernel_<param>`, and the GPU and vectorize toggles to be
  `useGPUIfAvailable` and `vectorize`. The builder falls back to searching
  when they differ.
- **Matrix layout.** `world_matrix` is assumed row-major, with translation in
  elements 3, 7 and 11, and expressions are assumed to accept numeric channel
  indices such as `world_matrix.3` and `win_translate.1`.
- **Window conventions.** Window translate/scale/roll follow the same
  convention as `nukescripts.snap3d`. Compare against a Reconcile3D or a
  ScanlineRendered sphere on a camera with non-zero window translate.
- **Other node knobs.** The builder assumes these knob names: TimeBlur
  `divisions`/`shutter`/`shutteroffset`, Merge2 `maskChannelMask` and
  `invert_mask`, AddChannels `channels2`, and Copy `from0`/`to0`. Missing link
  knobs are skipped rather than failing.
- **Callbacks.** The `inputChange` and `showPanel` events need to fire on the
  group, for the camera link and the mask.
- **Proxy mode** hasn't been checked.

If something fails on first build, the error from `blinkflare.create()` in the
Script Editor is the thing to send back.

## Roadmap

- Nuke 14+ USD-based 3D system cameras, and orthographic projection.
- Multiple instances per element type (e.g. two rings, two ghost sets).
- Custom ghost/iris shapes from a texture input.
- More element types: caustics, sparkles, iris "shimmer".
- Several lights from one node (e.g. a row of street lamps from a point cloud).
