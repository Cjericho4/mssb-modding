# Clone Maker: window ↔ core contract (#30)

The Tauri window (`app/`, #33) talks to the Rust core (`core/`, #31 #32) only through these commands
and events. Each one ports a function of the Python reference (`tools/modding/clone_gui.py` logic
layer, `clone_character.py`); behaviour, wording of messages and outputs must match it. Change this
file in the same commit as any change to a command.

Intended divergence (#29): the core always copies the source's voice SFX base (game.rel 0x8168) to
the clone rows, and with `ui_source: false` merges the source's voice into each donor family's
voice bank (`sound.(5+u)`, appended to ZZZZ.dat; DOL and challenge.rel descriptors updated), with
`voice: ` log lines. The Python reference does neither; `difftest.py` allows exactly these bytes.

Types: `colour` is a preset name (`red orange yellow green cyan blue purple pink`) or `"#rrggbb"`
(custom). Paths are strings. Errors are `{ "error": "<friendly message>" }` (see `friendly_error`).
`regions` (in `preview` and `job`) are the ticked region indices; the core drops them when they are
exactly the source's defaults (`region_selection`). `fraction` is 0..1 of the whole job.

## Commands
| Command | Args | Returns | Python reference |
|---|---|---|---|
| `open_game` | `path` | `{ ok, message, kind: "iso"\|"folder" }` | `load_game`, `friendly_preflight` |
| `sources` | | `[{ id, name }]` | `SOURCES`, `pretty_name` |
| `source_info` | `source` | `{ regions: [{ index, label, default, swatch }], slots: [{ id, label }], default_rows: [{ slot, colour }] }` | `source_regions`, `region_label`, `donor_choices`, `default_rows` |
| `next_free_slot` | `source, used: [id]` | `id \| null` (in `source_info().slots` order) | `next_free_slot` |
| `preview` | `request: { source, slot, colour, keeplum, regions: [index], textures? }` (with `textures`, the after side is the painted body, no recolour) | `{ before_png, after_png, caption }` (base64 PNG) | `SourcePreview`, `colour_spec`, `atlas_layout` |
| `default_output` | `game_path` | `path` | `default_output` |
| `validate_job` | `job` | `null \| { error }` | `validate_job` |
| `export_textures` | `source, folder` | `{ folder, count }`: the PNGs and README.txt went into the new `<folder>/<Name> textures` | none (#37) |
| `create` | `job` | starts the job; results come as events | `run_job` |
| `cancel` | | stops the running job at its next progress step | `run_job(cancel=)` |

`job` = `{ game, output, source, clones: [{ slot, colour, textures? }], keeplum, regions: [index],
ui_source, copy_stats, keep_colour_word, keep_work }`.

`clones[].textures` (optional, #37; no Python reference) is a folder of painted PNGs made by
`export_textures` for the job's source. Each edited PNG replaces that body texture in every copy
the clone uses (tracks 0 and 1, the ARAM body), and that clone's body is not recoloured: unedited
PNGs and missing files keep the source's original texture. `validate_job` checks the folders (names, sizes, PNG) against the
source before the job starts, so it needs the opened game. Omitted or `null`: recolour only.

## Events (core → window, while `create` runs)
| Event | Payload |
|---|---|
| `log` | `{ line }` (also written to `<output>.log`) |
| `progress` | `{ fraction, label }` (stages: "Unpacking the game", "Copying the game", "Patching", "Writing the new ISO") |
| `done` | `{ iso, work \| null, hint }` (`pick_hint`) |
| `error` | `{ message, cancelled: bool }` |

## Window-side commands (in `app/`, not the core)
| Command | Args | Returns | Why |
|---|---|---|---|
| `path_exists` | `path` | `bool` | the "`<name>` already exists. Replace it?" question before `create` |
| `launch_options` | | `{ selftest, dir, game }` | tells the page to run its `--selftest` walk-through (`--game`, scratch `dir`) |
| `selftest_mark` | `name` | | screenshot hook between self-test states (`MSSB_SHOT_CMD`) |
| `selftest_report` | `ok, lines` | exits the app | the self-test result (stdout or `--log`), exit code 0/1 |
