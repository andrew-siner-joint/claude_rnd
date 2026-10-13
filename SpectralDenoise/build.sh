#!/usr/bin/env bash
# Build SpectralDenoise.ofx.bundle into ./build (macOS: universal binary).
# Needs CMake 3.16+, a C++17 compiler and git (the OpenFX SDK is downloaded).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
args=(-S "$here" -B "$here/build" -DCMAKE_BUILD_TYPE=Release)
if [[ "$(uname)" == "Darwin" ]]; then
    args+=(-DCMAKE_OSX_ARCHITECTURES="arm64;x86_64" -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0)
fi
cmake "${args[@]}"
cmake --build "$here/build" --config Release -j
if [[ "$(uname)" == "Darwin" ]]; then
    codesign --force --deep -s - "$here/build/SpectralDenoise.ofx.bundle"
fi
echo "Built $here/build/SpectralDenoise.ofx.bundle"
