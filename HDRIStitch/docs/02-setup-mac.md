# 02 · Setup on macOS (once)

About 10 minutes. You need an admin account and an internet connection.

## 1. Homebrew

If `brew --version` in Terminal prints a version, skip this. Otherwise
install it from [brew.sh](https://brew.sh) (one line pasted into Terminal),
then close and reopen Terminal.

## 2. Hugin (free stitching engine)

HDRIStitch drives Hugin's command-line tools. Homebrew's official Hugin
package has been retired, so pick one of these:

- **Official download** (simplest): [hugin.sourceforge.io/download](https://hugin.sourceforge.io/download/).
  Open the `.dmg` and drag the **Hugin** folder into `/Applications`. On Apple
  Silicon this build runs through Rosetta; the installer offers to set it up.
- **MacPorts** (native Apple Silicon build), if you use MacPorts:
  `sudo port install hugin-app`.

The installer finds either. If you put Hugin somewhere else, set
`[tools] hugin_bin` in `~/HDRIStitch/config.toml` to the folder that holds
`nona`, `cpfind` and the rest.

## 3. Run the installer

```bash
cd /path/to/claude_rnd/HDRIStitch
./install.sh
```

It's safe to re-run at any time (for example after a `git pull`). It:

| Installs | Where |
|---|---|
| Python 3.12 + exiftool (Homebrew) | `/opt/homebrew` |
| A private Python environment with HDRIStitch and its libraries (LibRaw via rawpy, OpenEXR, OpenCV) | `~/HDRIStitch/venv` |
| The `hdri` command | `~/.local/bin/hdri` (added to your PATH) |
| Your settings file | `~/HDRIStitch/config.toml` (kept if it already exists) |
| **HDRI Process** droplet app | `~/Applications/HDRI Process.app` |
| Nuke menu | `~/.nuke/HDRIStitch` → this repo's `nuke/`, plus one line in `~/.nuke/init.py` |
| Blender add-on | linked into each `~/Library/Application Support/Blender/<version>/scripts/addons` |

It also removes the download quarantine from Hugin (otherwise macOS blocks
its command-line tools), and on Apple Silicon offers to install Rosetta if
your Hugin needs it.

## 4. Check

Open a **new** Terminal window and run:

```bash
hdri doctor
```

Every line should be filled in, ending with `All good.` The template line
saying "not created yet" is expected until your first shoot.

## 5. Finish the app hooks

- **Droplet**: drag `~/Applications/HDRI Process.app` into your Dock. The
  first time you drop a folder on it, macOS asks whether it may control
  Terminal: allow it (that's how it shows progress).
- **Blender**: Edit › Preferences › Add-ons, search "HDRIStitch", tick it. If
  Blender wasn't installed when you ran the installer: Add-ons › (⌄ menu, top
  right) › **Install from Disk…** › `HDRIStitch/blender/hdristitch_blender.py`.
- **Nuke**: restart Nuke; the tools are under **Nodes › HDRIStitch** (the Tab
  menu finds them too).

## Optional: a Finder Quick Action

If you'd rather right-click a folder than drag it onto the droplet:

1. Open **Automator** › New › **Quick Action**.
2. Set "Workflow receives current **folders** in **Finder**".
3. Add **Run AppleScript** and paste:
   ```applescript
   on run {input, parameters}
       repeat with f in input
           set p to POSIX path of f
           tell application "Terminal"
               activate
               do script "~/.local/bin/hdri process " & quoted form of p
           end tell
       end repeat
   end run
   ```
4. Save as "Process HDRI". It's now under right-click › Quick Actions.

## Updating

```bash
cd /path/to/claude_rnd && git pull
cd HDRIStitch && ./install.sh
```

Your config and rig templates in `~/HDRIStitch` are never overwritten.

## Uninstalling

Delete `~/HDRIStitch` (this removes your templates too), `~/.local/bin/hdri`,
`~/Applications/HDRI Process.app`, `~/.nuke/HDRIStitch` and the HDRIStitch
lines in `~/.nuke/init.py`, and the add-on in Blender's preferences.
