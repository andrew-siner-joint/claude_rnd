# SpectralDenoise

A profile-based noise reduction OFX plugin for Nuke and Flame, in the spirit of
Neat Video. You show it a patch of the shot with no detail in it (sky, a wall,
a grey card, out-of-focus background). It measures exactly what the noise looks
like there: how strong it is at every spatial frequency, separately for
brightness and colour, at four scales, plus how it changes from shadows to
highlights. It then removes only what matches that profile, so detail that
doesn't look like the noise is kept. With a temporal radius it also matches
every block in the neighbouring frames, accounting for motion, which makes the
reduction much stronger.

![Noisy, spatial, temporal, clean](docs/previews/compare.png)

*Film-grain-like noise with blotchy colour noise on top (25.8 dB). Spatial:
32.7 dB. Temporal, radius 2: 34.4 dB. Crops at 100%.*

![Removed noise](docs/previews/removed.png)

*Output → Removed Noise shows what was taken out. It should look like the
actual noise (right) with no edges or texture in it.*

## Install

### Get the plugin

**Download:** every push builds the plugin for macOS (Apple Silicon and Intel),
Linux and Windows. Open the repository's **Actions** tab, then the latest
**SpectralDenoise** run, and download the artifact for your platform from the
bottom of the page.

**Or build it yourself** (macOS or Linux; you need CMake, git and a C++
compiler: on a Mac, `xcode-select --install` and `brew install cmake`):

```sh
cd SpectralDenoise
./build.sh            # makes build/SpectralDenoise.ofx.bundle
```

### Put it where the host looks

```sh
./install.sh                                   # the bundle you just built
./install.sh ~/Downloads/SpectralDenoise.ofx.bundle   # or a downloaded one
```

This copies the bundle to the standard OFX folder (macOS:
`/Library/OFX/Plugins`, Linux: `/usr/OFX/Plugins`), removes the macOS
download quarantine and clears Nuke's OFX plugin cache. Windows: copy the
bundle to `C:\Program Files\Common Files\OFX\Plugins`.

You can also keep it anywhere and point `OFX_PLUGIN_PATH` at the folder that
contains the bundle.

**Nuke:** restart Nuke. The node is under **JointPost › SpectralDenoise** in
the Nodes toolbar (or Tab, then type SpectralDenoise).

**Flame:** restart Flame. It's in the OFX section of the effects browser /
Batch node bin under JointPost.

## Using it

1. Put the node after your plate (linear, as Nuke works, or log / video, see
   **Input Colourspace**).
2. In the viewer, drag the yellow **analysis box** onto an area with no
   detail. Bigger is better: 256 × 256 or more catches blotchy, low-frequency
   noise too. Drag a corner to resize, drag inside to move.
3. Press **Analyse Noise**. The **Status** line shows the measured luma and
   chroma noise. The profile is saved in the script, so the shot renders the
   same on the farm.
4. Set **Output** to **Removed Noise** and check that what's being removed
   looks like noise, with no edges or texture. If you see detail, lower
   **Noise Level** (or Fine / Medium for that size of detail). If noise is
   left, raise it.
5. For a moving shot, leave **Temporal Radius** at 1 or raise it to 2. If
   fast motion shows ghosting, lower **Motion Tolerance**.

Analyse on a frame that's typical of the shot. One profile normally serves
the whole shot, because noise depends on the camera and exposure, not on the
content.

### Controls

**Noise Profile**

| Control | What it does |
|---|---|
| Profile | **Analysed** (default): the profile from Analyse Noise, saved with the script. **Region (every frame)**: measures the box on every frame while rendering. Use it if your host can't analyse on the button, or when the noise changes over the shot (a gain ramp). **Auto (every frame)**: finds the flattest parts of each frame itself. It's convenient but deliberately cautious with blotchy colour noise. Before you analyse, Analysed mode renders as Auto |
| Box Bottom Left / Top Right | The analysis box. Drag it in the viewer |
| Analyse Noise | Measures the box on the current frame |
| Status | What was measured, and warnings |
| Input Colourspace | **Scene Linear**: for linear-light plates. A log-like curve is applied internally so shadow and highlight noise are handled evenly. **Log / Video**: log or display-referred material, processed as is. Re-analyse after changing it |
| Brightness Adaptive | Follows how noise changes with brightness (measured over the whole frame when you analyse). Off: the box's noise level is used everywhere |

**Reduction**

| Control | What it does |
|---|---|
| Noise Level | Scales the whole profile. 1 = remove what was measured. Above 1 removes more, and starts to eat fine detail; below 1 keeps more |
| Luma Level / Chroma Level | The same, for brightness noise and colour noise separately. Colour noise can usually go higher |
| Luma Reduction / Chroma Reduction | How much of the removed noise to take away (1 = all). 0.7 on luma keeps a little grain, which often looks more natural |
| Fine / Medium / Coarse / Very Coarse | Noise level per scale: pixel-sized grain, a few pixels, blotches (~10 px), large blotches |
| Sharpen | A little luma sharpening on the result |

**Temporal**

| Control | What it does |
|---|---|
| Temporal Radius | Frames used on each side (0 to 3). Each 8 × 8 block is matched in the neighbouring frames with quarter-pixel motion search. Where a match is too different (motion the search can't follow, occlusion, a flash), that frame is left out for that block. A frame that hardly matches anywhere, such as across a cut, is left out entirely |
| Motion Tolerance | How different a matched block may be. Lower it if moving objects ghost; raise it for more reduction on static shots |

**Output**

| Control | What it does |
|---|---|
| Quality | Fast: one pass, sparse blocks. Normal: two passes. Best: two passes, denser blocks (about 1.5× Normal's time, about 0.3 dB better) |
| Output | Denoised, or Removed Noise (source minus result) to check your settings |
| Mix | Blend with the source |
| Mask / Invert Mask | In Nuke, the node has a mask input: denoise only inside it |

## How well it works

Measured on test images with known noise added. All figures are PSNR
(higher is better), with the measured profile and default settings:

| Noise | Noisy | Spatial | Temporal r=1 | Temporal r=2 |
|---|---|---|---|---|
| Sensor noise (photon + read), panning shot | 31.5 dB | 40.7 | 42.1 | 42.8 |
| Film-grain-like (correlated), panning shot | 28.1 dB | 34.6 | 35.7 | 36.3 |
| Grain + blotchy chroma noise, region profile | 27.6 dB | 33.9 | | |

For reference, on single images with an exact profile it comes within
0.3 to 1 dB of colour BM3D, the standard academic benchmark, at roughly 100×
the speed.

## Performance

A 2048 × 1080 frame on a 4-core cloud machine:

| Quality | Spatial | Radius 1 | Radius 2 |
|---|---|---|---|
| Fast | 0.45 s | 1.2 s | 2.1 s |
| Normal | 0.9 s | 2.9 s | 4.5 s |
| Best | 1.3 s | 5.5 s | 9.5 s |

It uses all CPU cores, so a 16-core workstation should be roughly 3 to 4×
faster. Auto profile mode adds about 0.4 s per frame. In a downscaled viewer
or proxy mode, the profile is converted for that scale (cached), so previews
match the full-resolution render.

## How it works

The work is done in a small C++ core (`src/core`) with no OFX dependency. The
OFX plugin (`src/ofx/SpectralDenoise.cpp`) is a thin layer over it.

1. **Working space.** Scene-linear input goes through an asinh curve, which is
   linear in the shadows and logarithmic above. Then an orthonormal opponent
   transform separates luma from two chroma channels.
2. **Pyramid.** Four levels, each half the size of the previous one, so noise
   of every size is handled by 8 × 8 blocks at some level.
3. **Profile.** For each level and channel, the noise standard deviation of
   each of the 64 DCT coefficients. It's measured on differences between
   neighbouring blocks, which cancels gradients in the box, with a median so
   stray detail doesn't count. The brightness curve comes from the flattest
   blocks of the whole frame, grouped by brightness. Levels the box is too
   small for are predicted by synthesising noise with the measured spectrum
   and downsampling it.
4. **Denoising.** Overlapping 8 × 8 blocks (every 2 to 4 pixels) are
   transformed with a 2D DCT, plus a DCT along time when there are matched
   blocks from other frames. Pass 1 zeroes coefficients below 2.7 × the
   profile's noise, giving a pilot image. Pass 2 Wiener-filters every
   coefficient using the pilot as the estimate of the clean signal. Results
   are blended with a Kaiser window.
5. **Recombination.** Each level contributes its own band of frequencies
   (Laplacian pyramid).

## Tests

```sh
cd tests
python3 -m pytest -q        # needs numpy, scipy, pytest, cmake, expat, GL headers
```

- `test_core.py` tests the maths through a C API (`src/core/capi.cpp`):
  profile accuracy against known noise, noise reduction, edge sharpness,
  every control, temporal gain without ghosting, shot cuts, odd sizes,
  NaN/inf input, and determinism across thread counts.
- `test_ofx.py` loads the **built plugin** into a scriptable OFX host
  (`tests/host`, built on the OpenFX HostSupport reference library). It sets
  parameters, presses Analyse Noise, renders, and checks that the frames
  requested, mask, mix, Removed Noise output, identity, profile modes,
  colourspace mismatch and half render scale all behave correctly. It also
  checks that rendered pixels match the core exactly.

CI (`.github/workflows/spectraldenoise.yml`) runs the tests and builds the
macOS, Linux and Windows bundles.

## Not yet verified inside a real host

Everything above is tested against the OpenFX reference host library, not in
Nuke or Flame themselves. Please check these and report back:

- [ ] Nuke loads the bundle, and the node appears under JointPost.
- [ ] The analysis box draws in the Nuke viewer and its corners and body drag.
- [ ] **Analyse Noise** works in Nuke. It reads the source image from the
      button handler, which the OFX spec allows but not every host
      implements. If it says it couldn't read the image, use
      Profile → Region (every frame).
- [ ] The Status line updates after Analyse.
- [ ] Temporal Radius fetches neighbouring frames in Nuke and at clip ends.
- [ ] The mask input works in Nuke.
- [ ] Proxy / downscaled viewer gives a preview that matches the full render.
- [ ] Flame: loads (Linux and macOS), renders, and temporal frames work.
      Flame's support for overlays and for reading images on a button is
      untested; Region (every frame) is the fallback.
- [ ] Windows build: compiled in CI, never run.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Node not in Nuke's menu | Check the bundle is in `/Library/OFX/Plugins` (macOS) or `/usr/OFX/Plugins` (Linux), or on `OFX_PLUGIN_PATH`. Delete `~/.nuke/ofxplugincache` and restart |
| macOS says the plugin is damaged or can't be checked | It's quarantined after download: `sudo xattr -dr com.apple.quarantine /Library/OFX/Plugins/SpectralDenoise.ofx.bundle` (install.sh does this) |
| "Could not read the source image" on Analyse | The host doesn't allow it on a button. Use Profile → Region (every frame) |
| Status says the profile was measured with the other colourspace | You changed Input Colourspace after analysing. Analyse again |
| Detail is being removed | Look at Output → Removed Noise. Lower Noise Level, or the Fine / Medium level for that size of detail. Make sure the box had no detail in it |
| Blotchy colour noise left | Use a bigger analysis box (it measures the coarse levels), raise Chroma Level or Coarse / Very Coarse, or add temporal radius |
| Ghosting on fast motion | Lower Motion Tolerance, or Temporal Radius |
| Slow | Quality Fast and Temporal Radius 1 while working; Normal or Best for the final render |
