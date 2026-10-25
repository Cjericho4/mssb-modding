#!/bin/sh
# Build the window and run its self-test on Linux (WebKitGTK under Xvfb) in Docker, with a screenshot
# of every state.  usage: selftest/linux/run.sh [OUT_DIR] [SCREEN]   (defaults: ./shots, 1600x1000)
# GAME=<extracted disc folder or .iso>: also walk through every feature with it (mounted read-only).
# Caches (cargo registry, target) go to $CACHE, default ~/.cache/mssb-clonemaker-linux.
set -e
here=$(cd "$(dirname "$0")" && pwd)
app=$(cd "$here/../.." && pwd)
clonemaker=$(dirname "$app")
out=$(mkdir -p "${1:-shots}" && cd "${1:-shots}" && pwd)
cache=${CACHE:-$HOME/.cache/mssb-clonemaker-linux}
mkdir -p "$cache/cargo" "$cache/target"
game_args=""
if [ -n "$GAME" ]; then
    game_args="-v $(realpath "$GAME"):/game/$(basename "$GAME"):ro -e GAME=/game/$(basename "$GAME")"
fi
docker build -q -t mssb-clonemaker-tauri "$here" >/dev/null
# shellcheck disable=SC2086
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e CARGO_HOME=/cache/cargo \
    -e CARGO_TARGET_DIR=/cache/target -e SCREEN="${2:-1600x1000}" $game_args \
    -v "$clonemaker:/clonemaker" -v "$cache:/cache" -v "$out:/shots" -w /clonemaker/app \
    mssb-clonemaker-tauri sh /clonemaker/app/selftest/linux/inside.sh
