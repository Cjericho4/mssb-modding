#!/bin/sh
# Runs inside the container (see run.sh): checks, build, then the self-test under Xvfb.
set -e
cargo fmt --check
cargo clippy --release -- -D warnings
cargo test --release
cargo build --release
Xvfb :99 -screen 0 "${SCREEN}x24" -dpi 96 >/dev/null 2>&1 &
sleep 1
export DISPLAY=:99 WEBKIT_DISABLE_DMABUF_RENDERER=1 MSSB_SHOT_CMD=/app/selftest/linux/shot.sh
/cache/target/release/MSSB-Clone-Maker --selftest --log /shots/selftest.log || status=$?
cat /shots/selftest.log
exit ${status:-0}
