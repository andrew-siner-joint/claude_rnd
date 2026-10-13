#!/usr/bin/env bash
# Build the core as a shared library for the Python tests, with strict
# warnings (the plugin build uses CMake; this is the quick path).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$here/build"
"${CXX:-g++}" -O3 -std=c++17 -fPIC -shared -pthread \
    -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
    "$here"/../src/core/*.cpp -o "$here/build/libsdcore.so"
