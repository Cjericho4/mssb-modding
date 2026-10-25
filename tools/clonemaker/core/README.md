# Clone Maker core

Native Rust port of `tools/modding/clone_character.py` and the logic in
`clone_gui.py`, for clean Mario Superstar Baseball USA (GYQE01) discs. It does
not invoke Python or compile the game. Original inputs are read-only.

```sh
export PATH="$HOME/.cargo/bin:$PATH"
cargo build --release --manifest-path tools/clonemaker/core/Cargo.toml
./tools/clonemaker/core/target/release/mssb-clone --help
./tools/clonemaker/core/target/release/mssb-clone \
  --disc orig/GYQE01 --source luigi --slot 29:blue --slot 30:red \
  --out .scratch/luigi --iso .scratch/luigi.iso
```

The CLI accepts the Python patcher's flags and retains its help, planned-edit,
listing and patch messages. `mssb-clone-probe` is a file-oriented interface for
local differential tests, separate from the user CLI.

## Window integration

`api::Core` implements the commands in [`../API.md`](../API.md). Keep one
instance as Tauri state. Run `open_game`, `source_info` and `preview` off the
window thread: first previews load and analyse the actual body textures.
`Core::create(job, emit)` starts a worker and returns its join handle. Forward
each `api::Event` to the corresponding Tauri event name with the payload;
serialized events have `{event, payload}`. `Core::cancel()` sets a flag checked
at the next progress step. The standalone `api::run_job` supports a caller-owned
worker and cancellation token instead.

`Core::validate_job` returns `Option<ApiError>` (`null` or `{error}` in JSON).
Jobs, previews and return types derive serde traits. `api::load_game`,
`donor_choices`, `default_rows`, `colour_spec`, `SourcePreview`, `pick_hint` and
`friendly_error` are also available to non-Tauri callers.

Preview PNG *pixels* match Python; PNG compression bytes are not part of the
window contract. Before/after atlases use the actual patcher's recolour path.
Jobs write the reference's adjacent `name.log`, build `name.partial.iso`, then
rename it to the chosen destination. Scratch extractions are removed unless
`keep_work` is true. Cancellation preserves the log and removes the partial ISO.

## Verification

CI tests require no retail assets:

```sh
cargo test --manifest-path tools/clonemaker/core/Cargo.toml
cargo fmt --manifest-path tools/clonemaker/core/Cargo.toml --check
cargo clippy --manifest-path tools/clonemaker/core/Cargo.toml --all-targets -- -D warnings
```

Local differential checks require user-provided retail files. The runner builds
the C codec for the Python reference and the release Rust binaries, and removes
its generated discs/ISOs automatically, including on failed comparisons:

```sh
source .venv/bin/activate
python tools/clonemaker/difftest.py --synthetic --codecs --helpers --iso
python tools/clonemaker/difftest.py --textures --api --gui
python tools/clonemaker/difftest.py --full-grid
```

`--grid` runs the sampled matrix and option switches. `--full-grid` additionally
compares every source with every preset, grouping the eight presets into eight
clone rows per source. Bowser uses two four-clone groups because eight HUD
portraits exceed the reference slot; the eight-clone rejection is compared too.
Each successful case compares all output files,
the ISO bytes and normalized logs; sampled default-compatible cases also run
`test_clone.py --patched` (excluding disabling flags and forced menu relocation,
whose changed behavior contradicts some validator assumptions). `--textures` compares every source's decoded textures,
regions, 12 recolours and all 65,536 RGB5A3 entries per spec. `--api` checks all
54 sources' donor choices, defaults, hints, region controls, captions and preview
pixels. `--gui` checks end-to-end ISO/folder jobs, logs, cancellation and cleanup.

All local differential artifacts live under the checkout's `.scratch` directory.
No retail data or generated discs belong in commits or CI.

For large local grids, `--ram-isos` (Linux) keeps ISO contents in anonymous RAM
files exposed through temporary symlinks under `.scratch`, avoiding sustained
ISO writes to the host disk. In this mode the two CLIs produce their folders
with identical arguments, then their production ISO writers are called directly
(the CLI input-protection checks reject anonymous-file paths). Every byte is
still compared. Ordinary runs exercise the CLI `--iso` path. `--skip-passed LOG`
resumes a grid from the `PASS clone` labels in a previous log. Both are optional;
normal runs use ordinary files and start the complete grid.

The CLI additionally rejects output folders that overlap the original input,
and ISO destinations inside the original extraction. These guards enforce the
original-disc boundary even when `--force` is used.

## Standalone texture encoding

`texture_encode::encode_texture(rgba, &target)` accepts row-major RGBA8 and a
`texture::Texture`, returning tiled pixel bytes and an optional separate TLUT.
It checks dimensions, byte count, palette capacity and the exact pixel slot size.
It supports the retail sources' CMPR, CI4 and CI8 formats with IA8, RGB565 or
RGB5A3 palettes. `decode_texture_rgba` reads pixels followed by the TLUT and
preserves alpha; the older `decode_texture` remains the checkerboard RGB preview.
The encoder is not connected to cloning, PNG import or the window.

The read-only local inventory/quality probe scans all 54 sources, both disc
tracks, effective ARAM bodies, menu art and HUD portraits. It writes no assets:

```sh
cargo run --release --manifest-path tools/clonemaker/core/Cargo.toml \
  --example texture_quality -- orig/GYQE01
# Add --inventory-only to skip round-trip measurements.
```

It reports texture counts, exact RGBA matches and per-texture mean/worst PSNR
(infinite for exact results). The probe also cross-checks RGBA compositing
against the established preview decoder, except IA8 menu names, which the
legacy preview decoder does not support.
