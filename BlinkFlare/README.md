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

## Placement: light and articulation point

Two keyframable points (both with viewer handles) control the flare:

- **Light Position**: the light source. Track or keyframe it.
- **Articulation Point**: the optical center the flare pivots through,
  normally the frame center. The flare axis runs from the light through this
  point. An element at *axis position* `t` sits at `light + t * (articulation - light)`:
  `0` is on the light, `1` on the articulation point, `2` mirrored to the far
  side. Ghosts are scattered between **Axis Start** and **Axis End**, and the
  ring has its own **Axis Position**.

With **Rotate With Light** on, glints and spectral streaks turn as the light
orbits the articulation point:

![Articulation](docs/previews/articulation.gif)

Also on the Flare tab:

- **Off-screen Fade** dims the flare as the light leaves the frame. Ghosts
  still sweep in from off-frame lights within the fade distance.
- **Use Occlusion Input** samples the alpha of the `occlusion` input in a
  disk around the light, so the flare dims as things pass in front of it.
- **Flicker** adds noise-driven brightness variation over time.
- **Output** is either *Composite* (merged onto `src` with the linked Merge
  operation, `plus` by default; alpha passes through untouched) or *Flare Only*
  (on black, for stacking several flares).
- **Use GPU if available** and **Vectorize on CPU** are linked straight to
  the BlinkScript node.
- **Presets** reset every look knob. They don't touch positions, output or
  render settings, and you can undo them.

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
ToolSet.

### Inputs

| Input | Use |
|---|---|
| `src` | The plate. Also sets the format the flare renders at. |
| `occlusion` | Optional matte (alpha) of whatever passes in front of the light. |
| `dirt` | Optional lens dirt texture, fitted to the frame automatically. |

## Performance

- Every element is closed-form math per pixel. There are no blurs or
  convolutions and no intermediate buffers.
- Glints, ghosts and spectral streaks skip pixels outside their bounds
  (the longest ray, the strip of axis the ghosts occupy, the ring of space
  around the light). That is bit-identical to evaluating everything, and about
  2.4x faster.
- Cost grows with **Ghosts > Count** and **Spectral > Count** (up to 64 each)
  and with **Occlusion > Samples** when occlusion is on. Glint ray count is
  essentially free.
- Disabled elements cost nothing.

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
    presets.py               preset looks (only the knobs they change)
    builder.py               builds the Group in Nuke from spec.py
  tests/                     tests + a CPU harness for the kernel
  docs/previews/             images in this README (generated)
```

`builder.create()` builds a Group containing a BlinkScript node. It loads the
kernel source, compiles it, and links every kernel parameter to a knob on the
group by expression. Inside the group, the kernel draws the flare onto a black
canvas cropped to the `src` format, and a Merge composites the result.
`spec.py` is the single source of truth: the tests check that the kernel's
params, its defaults, the spec, the builder and the presets all agree.

## Development

The kernel can't be tested without Nuke directly, so `tests/harness/` compiles
the *actual* `.blink` file as C++ against a small, deliberately strict
stand-in for the Blink API. It rejects double literals, implicit narrowing,
functions Blink doesn't have, and the wrong image access mode. The harness
renders frames on the CPU.

```sh
pip install numpy pillow
python3 -m unittest discover -s tests          # 38 tests: spec, builder, kernel
python3 tests/harness/preview.py --preset "Sci-Fi Hoop" --out flare.png
python3 tests/harness/preview.py --set ghost_count=30 --set ghost_blades=5 --out flare.png
cd tests/harness && python3 preview.py --docs ../../docs/previews   # README images
```

`tests/test_builder.py` runs the builder against `tests/fake_nuke.py`. That
fake BlinkScript node creates its param knobs by parsing the kernel source,
the way Nuke does when it compiles.

When adding a parameter: declare it in the kernel's `param:` block, add a
`defineParam` with label == variable name, and add a knob to `spec.py` with
the same default. The tests fail until all three agree.

## Not yet verified inside Nuke

This was built and tested without a Nuke license, so the Nuke-specific parts
need a first run in NukeX before production use:

- **Blink compile.** The kernel compiles cleanly against the strict shim, but
  Nuke's Blink compiler is the real judge, on both the GPU and CPU paths.
- **Knob names on the BlinkScript node.** The builder assumes param knobs are
  named `BlinkFlareKernel_<param>` (it falls back to matching the suffix), and
  that the GPU and vectorize toggles are `useGPUIfAvailable` and `vectorize`
  (it falls back to searching for them).
- **Expressions.** The format is passed in as `input.width` and
  `input.height`, and the dirt is fitted with `Canvas.width`.
- **Look parity.** The previews come from the harness. Nuke should match
  except for the viewer transform.

If something fails on first build, the error from `blinkflare.create()` in the
Script Editor is the thing to send back.

## Roadmap

- **Camera / Axis driven placement.** The kernel only consumes screen-space
  `lightPos` and `axisCenter`, so 3D support lives entirely in the group: add
  `camera` and `axis` inputs, and drive Light Position by projecting the Axis
  through the Camera each frame. Also a "behind camera" kill, and optionally
  depth-scaled intensity. The articulation point can stay at the lens center or
  follow a second Axis.
- Multiple instances per element type (e.g. two rings, two ghost sets).
- Custom ghost/iris shapes from a texture input.
- More element types: caustics, sparkles, iris "shimmer".
- Brightness and color sampled from the plate at the light position.
