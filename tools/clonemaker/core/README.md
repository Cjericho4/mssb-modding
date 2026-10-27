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

The `textures DISC CHARID OUTDIR` probe writes decoded textures and fixed-spec
recolours; `api DISC OUTDIR` captures window API results. To measure recolours
through the window's colour-spec path, use:

```sh
./tools/clonemaker/core/target/release/mssb-clone-probe \
  recolour orig/GYQE01 2 .scratch/recolour/2/cases.json .scratch/recolour/2
```

`recolour DISC CHARID CASES.json OUTDIR` reads the audit tool's
`{"source":2,"cases":[{"name":"blue_keep0.5","colour":"blue","keeplum":0.5,"regions":null}]}`
format and writes `OUTDIR/<name>.gpc` from the source's effective body. Colours
accept presets or custom `#rrggbb`; regions are indices, or `null`/omitted for
defaults (an empty list selects no regions). It calls `api::colour_spec`,
`api::region_selection` and `SourcePreview::recoloured`, preserving the current
window behaviour, including custom-colour limitations.

`recolour_audit.py --backend rust --rust-dir .scratch/recolour` writes a
`cases.json` per source and requires the corresponding GPC files. On the first
run it reports missing bodies; generate each source's files with the probe,
then rerun the audit with the same `--rust-dir`.

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
python tools/clonemaker/difftest.py --sound --voice-grid
```

Voice (#29): a clone always speaks with its source's voice. The core writes the
source's SFX base (game.rel 0x8168) to the clone rows in every mode, and with
`--ui donor` merges the source's voice into the donor family's bank
(`src/sound.rs`), checks the merged file (`verify_merged`) and refuses a merge
whose worst-case match could overflow MusyX's ARAM sample heap. These are the
only intended differences from the frozen Python reference: `--voice-grid` runs
the voice cases and every other case accepts exactly those bytes (0x8168 rows,
merged banks at the end of ZZZZ.dat, their DOL/challenge.rel descriptors, the
re-packed RELs) and reads each merged bank back with `tools/datamap/sound.py`.
`--sound` rebuilds all 32 retail voice banks byte-identically and verifies all
992 donor/source merges.

Name box (#19): on the roster screen a colour-variant charID's name sits on a
coloured box (menus `fn_2_74F40`, labels 1..6 red, blue, yellow, green, purple,
black; 0 plain). The core's default `--colour-word clone` gives each such donor
the box of the preset nearest its clone's hue (`menu::label_for_hue`) and leaves
entries that already match, so a clone coloured like its donor changes nothing in
menus.rel. `--colour-word none` (the reference's default, plain boxes) and
`donor` stay byte-identical to the reference; difftest accepts exactly the
changed jump-table addends in the decompressed menus.rel (API.md).

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

Parity covers outputs and the expected validation messages. Where the Python
reference would die with an interpreter traceback (malformed numeric recolour
input, badly corrupt binary data), the core returns its own error instead.

## Standalone texture encoding

`texture_encode::encode_texture(rgba, &target)` accepts row-major RGBA8 and a
`texture::Texture`, returning tiled pixel bytes and an optional separate TLUT.
It checks dimensions, byte count, palette capacity and the exact pixel slot size.
It supports the retail sources' CMPR, CI4 and CI8 formats with IA8, RGB565 or
RGB5A3 palettes. `decode_texture_rgba` reads pixels followed by the TLUT and
preserves alpha; the older `decode_texture` remains the checkerboard RGB preview.
Cloning uses it for painted textures (below).

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

## Painted textures (#37)

`custom_textures` lets players paint a clone's body textures:

```sh
# one PNG per body texture (tNN_WxH.png, alpha kept, unrecoloured) and a README.txt
./tools/clonemaker/core/target/release/mssb-clone --disc orig/GYQE01 --source mario \
  --export-textures .scratch/mario-textures
# use a painted folder for one clone row (repeat per row; SLOT is an id or name)
./tools/clonemaker/core/target/release/mssb-clone --disc orig/GYQE01 --source mario \
  --slot 29:blue --textures 29:.scratch/mario-textures --out .scratch/m --iso .scratch/m.iso
```

Only `*.png` files are read; other files (README.txt, editor files) are ignored. A PNG whose
pixels equal the original (colour under fully transparent texels aside) changes nothing, so an
unedited export gives a byte-identical ISO. A clone with a texture folder is not recoloured;
each edited PNG is encoded with `encode_texture` into every body copy: track 0 and the ARAM body by index, and track 1 (the
low-detail model of fielders and runners) wherever its retail texture is the same picture or a
shrunk copy of it (whole factor, at least 25 dB after a box filter); a box-filtered copy of the
PNG goes there. Low-detail textures drawn separately (Mario's eyes, 128x128 in the body, 32x32 in
track 1) keep the original, and the log says so. Bad folders fail before any game data is copied
(`validate_job` in the window, before the copy in the CLI) with a friendly message: unknown
names, wrong sizes, files that are not PNGs, textures exported from another source.

`--help` and the usage text stay the Python reference's (`difftest --cli` compares them), so the
two flags are documented here. The window exports with `export_textures` and sets
`clones[].textures` (API.md).

Read-only check of a painted clone against the same clone without textures (every copy must
equal the plain copy with the folder painted over it, each painted texture must be exactly the
encoder's output); it prints per-texture RGB PSNR:

```sh
cargo run --release --manifest-path tools/clonemaker/core/Cargo.toml --example \
  custom_texture_check -- orig/GYQE01 plain.iso painted.iso mario 29 .scratch/mario-textures
```

Not covered yet: menu art and HUD portraits (they stay the recoloured source's).

## Animations from (#9)

A clone always wears its source's body; `--anims SLOT:CHARACTER` (repeatable; `clones[].anims` in
the window) makes it play another character's animations:

```sh
./tools/clonemaker/core/target/release/mssb-clone --disc orig/GYQE01 --source mario \
  --slot 29:blue --anims 29:luigi --out .scratch/m --iso .scratch/m.iso
```

Only descriptors change: the slot's `lbl_800F1D78` tracks 2..18 and the ARAM copy of track 15
(`master[432+slot]`) point at that character's retail banks; no shared file is rewritten. Retail
banks fit the game's buffers: `fn_80018B74` sizes them from the largest bank of each kind over all
54 characters. Without `--anims` (or with the source, or a character that shares every bank with
it) the output is byte-identical to before.

`src/anims.rs` checks the skeletons first, by bone ID, which is how the game binds a track to a bone
(`ACTSetAnimation` → `ANIMGetTrackFromSequence`; mssb-dtk `.agent/animations.md` §3). For both body
variants (tracks 0 and 1) and each bank it compares the bones the bank drives on its own character
with the source's body. Bones the body lacks in a bank the game uses refuse the choice (before the
game is copied); the same in tracks 7/8, which nothing is known to request, only warns. Bones the
source's own animations move but the new ones don't ("they stay put") and driven bones with a
different parent bone also warn, in the log (`animations: warning: ...`) and the dropdown's tooltip.
Equal bone counts don't matter. `0xFFFF` is skipped (several unrelated bones share it).

Timing that belongs to the clips follows the animations (`TIMING_ROWS` in `src/clone.rs`, with the
evidence): the 15 DOL frame-event windows (`lbl_800FB798`), game.rel `0x7870` (runner clip frames)
and bytes 0-1 of `0x7E34` (body-check clip frames). Hitboxes, charge frames `0x7690`, foot height
`0x17000`, size, voice, attachment bones and model script stay the source's. `--no-gameplay` leaves
all of them as the slot's own. The log says which were taken from where.

Read-only roster matrix (exact / warned / refused for every body and animation set), the window's
dropdown for one source, and the messages for one pair:

```sh
cargo run --release --manifest-path tools/clonemaker/core/Cargo.toml --example anim_matrix -- \
  orig/GYQE01 --check --choices mario mario:luigi
```

`difftest.py --anims` runs each case with and without `--anims` and requires the outputs to differ
only in those descriptors, windows and game.rel rows (plus the re-packed game.rel), and checks the
refusals. `--help` stays the Python reference's, so the flag is documented here.
