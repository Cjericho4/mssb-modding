# Clone Maker window (Tauri, #33)

The desktop window for the Rust core in `../core`. It ports `tools/modding/clone_gui.py` (same texts,
defaults, error messages and layout) and talks to the core only through the commands and events in
[`../API.md`](../API.md).

- `src/main.rs`: one `api::Core` kept as Tauri state; the API.md commands map one to one onto it.
  `open_game`, `source_info`, `preview` and `validate_job` run off the main thread; `create` forwards
  each core `Event` (`{event, payload}`) as the Tauri event of that name.
- `src/selftest.rs`: the `--selftest` mode and the screenshot hook.
- `ui/`: plain HTML/CSS/JS, no framework and no npm (`withGlobalTauri`). `theme.css` holds the
  night-arena palette as custom properties; the fonts are bundled with `@font-face` (OFL texts in
  `ui/fonts/`).
- Its own crate and `Cargo.lock` (no workspace root), depending on `../core` by path.

## Build

```sh
cargo build --release --manifest-path tools/clonemaker/app/Cargo.toml
```

The release build embeds `ui/` (`custom-protocol` feature) and gives one portable
`MSSB-Clone-Maker.exe` on Windows (about 8 MB, no installer, no DLLs; uses the WebView2 that ships
with Windows 10/11).

## Self-test

`MSSB-Clone-Maker --selftest [--game <iso or folder>] [--log <file>]` walks through the window and
exits 0 or 1. A Windows exe has no console, so read the result from `--log`. Without `--game` it
checks the window only: every widget, bundled fonts, the dialog API, inputs waiting for a game, the
narrow (stacked) and wide (two-column) layouts, an unusable game's core message and a timed resize
(fails if any median is 100 ms or more; the metric is set-size to next painted frame, so about 16 ms
means the next frame, and it is not comparable with Tk's "busy time per step"). With `--game` it also
loads previews, switches sources and colours, adds and removes rows, answers the Replace and error
dialogs, and runs and cancels a real create in a temporary folder. With `MSSB_SHOT_CMD` set, the
page runs that program with a state name at each step (screenshots; scripts in `selftest/windows/`).

On Linux, `selftest/linux/run.sh [OUT_DIR] [SCREEN]` builds and runs the self-test in Docker
(WebKitGTK under Xvfb) with a screenshot per state; `GAME=<disc folder or .iso>` mounts a game
read-only for the full walk-through. It keeps cargo caches in `~/.cache/mssb-clonemaker-linux` and
the image `mssb-clonemaker-tauri`; both are safe to delete. Screenshots contain game textures: keep
them out of commits.

## CI

`.github/workflows/build-clonemaker.yml` (windows-latest; pushes to `main` / `tauri-*` touching
`tools/clonemaker/**`, and manual runs): fmt and clippy, `cargo test` (app and core), release build,
a 1920x1080 screen (the default 1024x768 is too small for two columns), the self-test without a game,
then uploads `MSSB-Clone-Maker-Tauri` (the exe) and the self-test screenshots. Retail files cannot be
in CI, so the full walk-through is run locally.

## Not covered by the self-test; known differences

- Native file, folder and save dialogs (only their API is checked), the `Custom…` colour dialog
  (Windows' own; its title cannot be set), the quit-while-busy confirm, hover glow on Windows, and
  dragging the window by hand: part of the user's Rio walk-through (#34).
- Dialogs are overlays inside the window, not separate OS windows (styled like Tk's).
