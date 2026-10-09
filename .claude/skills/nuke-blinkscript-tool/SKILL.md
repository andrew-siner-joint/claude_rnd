---
name: nuke-blinkscript-tool
description: How to build (or change) a Nuke tool driven by a BlinkScript kernel, e.g. a GPU image effect wrapped in a Group with its own panel. Use when writing a .blink kernel, generating the Nuke node from Python, compiling Blink from Python, writing an installer for a Nuke tool, or testing Nuke tools without a Nuke licence. BlinkFlare/ in this repo is the worked example.
---

# Building a BlinkScript tool for Nuke

Distilled from building BlinkFlare (`BlinkFlare/`), which went from nothing to
a production lens flare tool the user was happy with. The agent had no Nuke
licence: everything was developed against stand-ins, and the user ran it in
real Nuke. Follow the same shape and you can do the same. File paths below
are relative to `BlinkFlare/`.

## Shape that works

- **One `.blink` kernel does all the per-pixel work**, analytically (no blurs,
  no intermediate buffers), so it runs on the GPU and vectorizes on the CPU.
- **A Python package builds a Nuke Group around it**: input nodes, helper
  nodes, the BlinkScript node, the group's knobs (tabs), and expressions that
  link each kernel param to a group knob. See `blinkflare/builder.py`.
- **One declarative spec is the single source of truth** for knobs: name,
  kind, label, default, range, tab, and which kernel param it drives
  (`blinkflare/spec.py`). Tests check kernel params, kernel defaults, spec, builder and
  presets all agree.
- **Nothing evaluates Python at render time.** All live behaviour is Nuke
  expressions and nodes, so saved scripts render on a farm without the
  package. Python only runs when the user clicks something.
- **Variable-length data goes in as an image, not params.** Blink has no
  dynamic arrays and every param is a compiled knob. BlinkFlare's element
  stack is a small image (one column per element, 6 RGBA rows) built inside
  the group by Expression nodes that read the element knobs; the kernel loops
  over the columns with random access. Adding elements never recompiles.

## Writing the kernel

Blink is a C++-like subset, and Nuke's compiler is stricter and less helpful
than you'd hope. Rules that kept BlinkFlare compiling first time in Nuke:

- `kernel X : ImageComputationKernel<ePixelWise>`, `process(int2 pos)`.
  Inputs: `Image<eRead, eAccessRandom, eEdgeClamped|eEdgeConstant>` for
  sampling (`bilinear(img, x, y)`, `img(x, y)`), `eAccessPoint` for the
  current pixel only. Output `Image<eWrite> dst;`.
- **Floats only**: every literal gets an `f` (`0.5f`). No doubles.
- **ASCII only**, comments included.
- Don't rely on overloads that vary between Nuke versions: write your own
  `abs` and integer `clamp/min/max` helpers (`lfAbs`, `lfClampi`).
- Avoid locals named `all` or `out`.
- In `define()`, `defineParam(x, "x", default)` with **label == variable
  name**. Nuke then names the knob `<KernelClass>_<param>`, which the builder
  can find reliably (also search by suffix, case-insensitively, as a
  fallback).
- Precompute per-frame values in `init()` into `local:` members.
- Early-out by footprint before expensive work (distance from an element's
  centre vs its bound), and share work across R, G and B instead of running a
  function three times. Measure rather than guess (see Testing).
- Keep the Group's input count small. With six inputs the user couldn't find
  the `axis` input arrow on the node; after dropping to five they could.

## Compiling from Python (the part that bit us)

- Setting `kernelSource` and executing `recompile` **may only start** the
  compile. It completes once control returns to Nuke's event loop. **Never
  block waiting** (sleep loops, `forceValidate`, `processEvents`): it froze
  the user's Nuke at 20%. Poll on a Qt timer (`QTimer.singleShot`) until the
  param knob for a known param exists, with a timeout, a cancellable progress
  dialog, and "node in error for N seconds" as rejection. If it hasn't
  appeared after ~10 s, also try the load-from-file route (`kernelSourceFile`
  + `reloadKernelSourceFile` + `recompile`). See `blinkflare/compiling.py`.
- Don't touch param knobs until they exist. "BlinkScript has no knob
  lightPos" meant "not compiled yet".
- **Compile once, then paste.** After the first successful compile, save the
  compiled BlinkScript node (`nuke.nodeCopy`) to a cache file keyed by a hash
  of the kernel source. Every later node, and every extra kernel instance,
  is a `nuke.nodePaste` of it: instant, no compile. A changed kernel gets a
  new hash, so it recompiles once.
- Compiling needs NukeX/Nuke Studio. Offer a manual route (user loads the
  `.blink` into a BlinkScript node, presses Recompile, then a "Build From
  Compiled BlinkScript" command builds around it) and a "Save ToolSet" so
  plain-Nuke seats get a ready-built node.

## Building the Group

- Name Input nodes (`src`, `occlusion`, `cam`, `axis`, `mask`). Nuke numbers
  them in creation order; keep the main input first and `mask` last.
- **Nuke can only append knobs.** Anything you add at runtime lands on the
  last tab. Either keep the dynamic tab last, or (as BlinkFlare does to keep
  3D/Output after Elements) remove the trailing tabs' knobs, append, and add
  them back, snapshotting and restoring values (`toScript`/`fromScript`) and
  links in case removal drops them. Make it exception-safe and re-entrant.
- Runtime-added knobs: collapsible groups via `Tab_Knob(name, label,
  nuke.TABBEGINCLOSEDGROUP)` ... `TABENDGROUP`; relabel/hide with `setLabel`/
  `setVisible`, and re-apply that UI on `showPanel` so it doesn't depend on
  what the `.nk` kept. Bookkeeping goes in hidden `String_Knob`s (ids,
  version).
- Keep structural edits (adding/removing knobs) **out of undo**
  (`nuke.Undo().disable()/enable()`): Nuke can't undo knob additions, and
  undoing only the values leaves the node inconsistent.
- The group's `knobChanged` script is stored on the node, so it must be
  **self-contained** (no package import needed for its core job). Use it for
  camera links, input changes and dynamic-knob updates.
- Expressions written into Expression nodes: plain fixed-point number
  literals (no `1e-05`), `floor(x)`/`floor(y)` for pixel indices, and
  arithmetic selection (`(floor(x) == 3) * value`) rather than ternaries.
- 3D: classic Axis nodes inside the group, parented to the `cam`/`axis`
  inputs, mirror their `world_matrix`; a NoOp with expression knobs does the
  projection. Link camera lens knobs by expression from `knobChanged`.
- Bump a version string and store it on the node. Each node carries its own
  kernel, so existing nodes in scripts stay the version that built them.

## The installer / diagnostic

The user's preferred install route: one `.py` they run from the Script
Editor (see `install_blinkflare.py`). It:

1. Finds its own folder (or asks for it / the `.blink` file).
2. Re-imports the package fresh, so a stale copy in memory isn't used.
3. Compiles a tiny test kernel, then the real one, without blocking (same
   timer approach), with a progress window and Cancel.
4. Builds a test node and **checks, in real Nuke, every assumption you
   couldn't test**: e.g. reads the element table back with `node.sample()`
   (through an impulse-filtered 9x Transform so filtering can't mix cells),
   checks tab order and that knob values survive, lists inputs as Nuke
   numbers them.
5. If the kernel is rejected, compiles copies with function bodies stubbed
   out to find which function Nuke rejects.
6. Writes a timestamped report to `~/.nuke/<tool>_report.txt` line by line,
   copies it to the clipboard, and offers to add the folder to `init.py`.

Ask the user to send the report back whenever something fails.

## Testing without Nuke

This is what made it possible to ship working code without a licence:

- **Compile the real `.blink` as C++** against a small, deliberately strict
  Blink stand-in (`tests/harness/blink_shim.h`: no system headers, only the
  Blink subset you allow) with `-Wconversion -Wdouble-promotion
  -Wfloat-conversion -Werror`. A double literal, a missing function or a type
  mix-up fails here, not in Nuke.
- The same harness **renders on the CPU** to raw floats: numpy tests check
  behaviour (each element renders, passes sum to the whole, geometry lands
  where the maths says), and PNG contact sheets let you look at the result.
  Look at renders before telling the user something looks good.
- A **fake `nuke` module** (`tests/fake_nuke.py`) runs the builder and the
  stored callbacks; its BlinkScript node creates param knobs by parsing the
  kernel, and it can simulate Nuke quirks (deferred compiles, knobs that lose
  state on removal) so error paths get tested.
- A small **Nuke expression evaluator** (`tests/nuke_expr.py`) checks
  generated expressions numerically against the Python reference.
- **Mutation checks**: break a line on purpose, confirm a test fails, revert.
  Fix any surviving mutant with a test, or explain why it's equivalent.
- Keep a "Not yet verified inside Nuke" list in the README, and make the
  installer check those items.

## Working with the user

- Ship complete, tested increments, and say plainly what is unverified in
  real Nuke.
- Fix exactly what was asked. Don't add commands, buttons or features the
  user didn't ask for (an unrequested "Upgrade Selected" command had to be
  removed); propose them instead.
- When something can't be reproduced (e.g. an input not showing), make the
  likely fix, add a check that reports the facts from real Nuke, and say so.
- README per tool: what it does with preview images, install (installer
  first), troubleshooting table, performance notes, how it's built, the
  not-verified list.
