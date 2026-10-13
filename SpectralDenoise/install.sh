#!/usr/bin/env bash
# Install a built (or downloaded) SpectralDenoise.ofx.bundle where Nuke and
# Flame look for OFX plugins, and clear Nuke's OFX plugin cache.
#   ./install.sh [path/to/SpectralDenoise.ofx.bundle]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bundle="${1:-$here/build/SpectralDenoise.ofx.bundle}"
if [[ ! -d "$bundle" ]]; then
    echo "No bundle at $bundle (run ./build.sh, or pass the path)." >&2
    exit 1
fi
if [[ "$(uname)" == "Darwin" ]]; then
    dest=/Library/OFX/Plugins
else
    dest=/usr/OFX/Plugins
fi
echo "Installing to $dest (may ask for your password)"
sudo mkdir -p "$dest"
sudo rm -rf "$dest/SpectralDenoise.ofx.bundle"
sudo cp -R "$bundle" "$dest/"
if [[ "$(uname)" == "Darwin" ]]; then
    # downloaded bundles are quarantined by Gatekeeper
    sudo xattr -dr com.apple.quarantine "$dest/SpectralDenoise.ofx.bundle" 2>/dev/null || true
fi
# Nuke caches OFX plugin descriptions; clear it so a new version is picked up.
rm -rf "$HOME/.nuke/ofxplugincache" 2>/dev/null || true
echo "Done. Restart Nuke / Flame."
