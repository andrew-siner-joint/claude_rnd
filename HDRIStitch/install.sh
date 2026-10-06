#!/bin/bash
# HDRIStitch installer for macOS. Safe to re-run (it updates in place).
#
#   cd HDRIStitch && ./install.sh
#
# Installs: Python + exiftool (Homebrew), a private Python environment with
# the `hdri` command, ~/HDRIStitch/config.toml, the "HDRI Process" droplet app,
# the Nuke menu and the Blender add-on. Hugin is checked and guided (it has
# no reliable unattended installer on macOS any more).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
HDRI_HOME="${HDRISTITCH_HOME:-$HOME/HDRIStitch}"
VENV="$HDRI_HOME/venv"
BIN_DIR="$HOME/.local/bin"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33m    %s\033[0m\n' "$*"; }
ask()  { printf '%s [y/N] ' "$1"; read -r reply; [ "$reply" = "y" ] || [ "$reply" = "Y" ]; }

if [ "$(uname)" != "Darwin" ]; then
    warn "This installer is for macOS. On Linux: pip install -e $HERE, and install hugin-tools."
fi

# --------------------------------------------------------------- Homebrew
say "Homebrew"
BREW=""
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && BREW="$b" && break
done
if [ -z "$BREW" ]; then
    warn "Homebrew is not installed. Install it first (one line, from https://brew.sh):"
    # shellcheck disable=SC2016  # printed for the user to copy, not run here
    warn '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    warn "then run this installer again."
    exit 1
fi
echo "    $BREW"
eval "$("$BREW" shellenv)"

say "Python 3.12 and exiftool"
"$BREW" list python@3.12 >/dev/null 2>&1 || "$BREW" install python@3.12
"$BREW" list exiftool >/dev/null 2>&1 || "$BREW" install exiftool
PY="$("$BREW" --prefix python@3.12)/bin/python3.12"
echo "    $PY"

# --------------------------------------------------------------- Python env
say "HDRIStitch Python environment ($VENV)"
mkdir -p "$HDRI_HOME/templates"
if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
# editable: pulling a newer HDRIStitch (git pull) takes effect immediately
"$VENV/bin/python" -m pip install --quiet -e "$HERE"
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/hdri" "$BIN_DIR/hdri"
echo "    hdri -> $BIN_DIR/hdri"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        if ! grep -qs 'HDRIStitch' "$HOME/.zprofile"; then
            # shellcheck disable=SC2016  # written literally into ~/.zprofile
            printf '\n# HDRIStitch\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zprofile"
            warn "Added ~/.local/bin to your PATH in ~/.zprofile (open a new Terminal window)."
        fi
        ;;
esac

if [ ! -f "$HDRI_HOME/config.toml" ]; then
    "$VENV/bin/hdri" config --init >/dev/null
    echo "    wrote $HDRI_HOME/config.toml"
else
    echo "    keeping your $HDRI_HOME/config.toml"
fi

# --------------------------------------------------------------- Hugin
say "Hugin (stitching engine)"
if "$VENV/bin/python" -c "from hdristitch import hugin; hugin.find_tools()" >/dev/null 2>&1; then
    NONA="$("$VENV/bin/python" -c "from hdristitch import hugin; print(hugin.find_tools()['nona'])")"
    echo "    found: $(dirname "$NONA")"
else
    warn "Hugin is not installed. Pick one:"
    warn "  a) Official download: https://hugin.sourceforge.io/download/  (open the .dmg,"
    warn "     drag the Hugin folder into /Applications). Runs on Apple Silicon via Rosetta."
    warn "  b) MacPorts (native build): sudo port install hugin-app"
    if command -v port >/dev/null 2>&1 && ask "    MacPorts is installed. Run 'sudo port install hugin-app' now?"; then
        sudo port install hugin-app
    else
        open "https://hugin.sourceforge.io/download/" 2>/dev/null || true
        warn "Install Hugin, then run this installer again (or just: hdri doctor)."
    fi
fi
for d in /Applications/Hugin*; do
    if [ -d "$d" ] && xattr -r "$d" 2>/dev/null | grep -q com.apple.quarantine; then
        echo "    clearing the download quarantine on $d"
        xattr -dr com.apple.quarantine "$d" 2>/dev/null || sudo xattr -dr com.apple.quarantine "$d"
    fi
done
if [ "$(uname -m)" = "arm64" ] && ! /usr/bin/pgrep -q oahd 2>/dev/null; then
    NONA="$("$VENV/bin/python" -c "from hdristitch import hugin; print(hugin.find_tools()['nona'])" 2>/dev/null || true)"
    if [ -n "$NONA" ] && ! lipo -archs "$NONA" 2>/dev/null | grep -q arm64; then
        warn "Your Hugin is an Intel build; Apple Silicon needs Rosetta 2 to run it."
        if ask "    Install Rosetta 2 now?"; then
            softwareupdate --install-rosetta --agree-to-license
        fi
    fi
fi

# --------------------------------------------------------------- droplet app
say "HDRI Process app (drag a shoot folder onto it)"
APP="$HOME/Applications/HDRI Process.app"
mkdir -p "$HOME/Applications"
TMP_DIR="$(mktemp -d)"
SCRIPT="$TMP_DIR/droplet.applescript"
cat > "$SCRIPT" <<APPLESCRIPT
on open droppedItems
    repeat with anItem in droppedItems
        set p to POSIX path of anItem
        set cmd to quoted form of "$BIN_DIR/hdri" & " process " & quoted form of p
        set cmd to cmd & " && open " & quoted form of (p & "/hdri")
        set cmd to cmd & "; echo; echo 'Finished. You can close this window.'"
        tell application "Terminal"
            activate
            do script cmd
        end tell
    end repeat
end open

on run
    display dialog "Drag a shoot folder (the .ARW files from one location) onto this icon to build its HDRI." & return & return & "Progress shows in a Terminal window; the results open in Finder when it's done." buttons {"OK"} default button 1 with title "HDRI Process"
end run
APPLESCRIPT
rm -rf "$APP"
osacompile -o "$APP" "$SCRIPT"
rm -rf "$TMP_DIR"
echo "    $APP  (drag it to your Dock for easy access)"

# --------------------------------------------------------------- Nuke
say "Nuke menu"
mkdir -p "$HOME/.nuke"
ln -sfn "$HERE/nuke" "$HOME/.nuke/HDRIStitch"
if ! grep -qs 'pluginAddPath("HDRIStitch")' "$HOME/.nuke/init.py"; then
    printf '\n# HDRIStitch\nimport nuke\nnuke.pluginAddPath("HDRIStitch")\n' >> "$HOME/.nuke/init.py"
fi
echo "    ~/.nuke/HDRIStitch -> $HERE/nuke  (menu: Nodes > HDRIStitch)"

# --------------------------------------------------------------- Blender
say "Blender add-on"
FOUND=0
for v in "$HOME/Library/Application Support/Blender"/[0-9]*; do
    [ -d "$v" ] || continue
    mkdir -p "$v/scripts/addons"
    ln -sf "$HERE/blender/hdristitch_blender.py" "$v/scripts/addons/hdristitch_blender.py"
    echo "    linked into Blender $(basename "$v")"
    FOUND=1
done
if [ "$FOUND" = 1 ]; then
    echo "    In Blender: Edit > Preferences > Add-ons, search 'HDRIStitch', tick it."
else
    echo "    In Blender: Edit > Preferences > Add-ons > (v menu) Install from Disk..."
    echo "    and pick $HERE/blender/hdristitch_blender.py"
fi

# --------------------------------------------------------------- check
say "Checking"
"$VENV/bin/hdri" doctor || true
echo
echo "Next: read $HERE/README.md (the workflow), then shoot your rig once and run"
echo "    hdri process /path/to/shoot"
