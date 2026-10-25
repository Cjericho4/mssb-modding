# Clone Maker: window ↔ core contract (#30)

The Tauri window (`app/`, #33) talks to the Rust core (`core/`, #31 #32) only through these commands
and events. Each one ports a function of the Python reference (`tools/modding/clone_gui.py` logic
layer, `clone_character.py`); behaviour, wording of messages and outputs must match it. Change this
file in the same commit as any change to a command.

Types: `colour` is a preset name (`red orange yellow green cyan blue purple pink`) or `"#rrggbb"`
(custom). Paths are strings. Errors are `{ "error": "<friendly message>" }` (see `friendly_error`).

## Commands
| Command | Args | Returns | Python reference |
|---|---|---|---|
| `open_game` | `path` | `{ ok, message, kind: "iso"\|"folder" }` | `load_game`, `friendly_preflight` |
| `sources` | | `[{ id, name }]` | `SOURCES`, `pretty_name` |
| `source_info` | `source` | `{ regions: [{ index, label, default, swatch }], slots: [{ id, label }], default_rows: [{ slot, colour }] }` | `source_regions`, `region_label`, `donor_choices`, `default_rows` |
| `next_free_slot` | `source, used: [id]` | `id \| null` | `next_free_slot` |
| `preview` | `{ source, slot, colour, keeplum, regions: [index] }` | `{ before_png, after_png, caption }` (base64 PNG) | `SourcePreview`, `colour_spec`, `atlas_layout` |
| `default_output` | `game_path` | `path` | `default_output` |
| `validate_job` | `job` | `null \| { error }` | `validate_job` |
| `create` | `job` | starts the job; results come as events | `run_job` |
| `cancel` | | stops the running job at its next progress step | `run_job(cancel=)` |

`job` = `{ game, output, source, clones: [{ slot, colour }], keeplum, regions: [index], ui_source,
copy_stats, keep_colour_word, keep_work }`.

## Events (core → window, while `create` runs)
| Event | Payload |
|---|---|
| `log` | `{ line }` (also written to `<output>.log`) |
| `progress` | `{ fraction, label }` (stages: "Unpacking the game", "Copying the game", "Patching", "Writing the new ISO") |
| `done` | `{ iso, work \| null, hint }` (`pick_hint`) |
| `error` | `{ message, cancelled: bool }` |
