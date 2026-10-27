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
| `anim_choices` | `source` | `[{ id, label, exact, note }]`: the source's own first (`"<Name> (own)"`), then every other set of animation banks its body can wear, exact bone matches before the rest; refused sets are left out, a family that shares its banks appears once by family name (`id` = its first member). `note` lists what differs, one line each | none (#9) |
| `preview` | `request: { source, slot, colour, keeplum, regions: [index], textures?, anims? }` (with `textures`, the after side is the painted body, no recolour; `anims` other than the source adds `"; animations from <Name>"` to the caption) | `{ before_png, after_png, caption }` (base64 PNG) | `SourcePreview`, `colour_spec`, `atlas_layout` |
| `default_output` | `game_path` | `path` | `default_output` |
| `validate_job` | `job` | `null \| { error }` | `validate_job` |
| `export_textures` | `source, folder` | `{ folder, count }`: the PNGs and README.txt went into the new `<folder>/<Name> textures` | none (#37) |
| `create` | `job` | starts the job; results come as events | `run_job` |
| `cancel` | | stops the running job at its next progress step | `run_job(cancel=)` |

`job` = `{ game, output, source, clones: [{ slot, colour, textures?, anims?, source?, regions? }], keeplum, regions: [index],
ui_source, copy_stats, keep_colour_word, keep_work }`.

`clones[].source` and `clones[].regions` (optional, #18; no Python reference) let one game hold clones
of several characters: the charID that clone copies and its ticked colour regions (`source_info`
of that character). Omitted: the job's `source` and `regions`, and a job whose clones all copy
`source` is byte-identical to one without the fields. Every per-character row (tracks, ARAM body,
model scale, game.rel tables, stats row, voice) comes from the clone's own character; chemistry
between two clones of different characters is those characters' chemistry. `validate_job` refuses
a slot that is also one of the job's cloned characters ("X is being cloned, so it can't also be
replaced"). With `ui_source: false`, a donor family bank used by clones of several characters gets
all of their voices (one merge per character, one ARAM check for the whole set). The `done` hint
has one sentence per character.

`clones[].textures` (optional, #37; no Python reference) is a folder of painted PNGs made by
`export_textures` for the job's source. Each edited PNG replaces that body texture in every copy
the clone uses (tracks 0 and 1, the ARAM body), and that clone's body is not recoloured: unedited
PNGs and missing files keep the source's original texture. `validate_job` checks the folders (names, sizes, PNG) against the
source before the job starts, so it needs the opened game. Omitted or `null`: recolour only.

`clones[].anims` (optional, #9; no Python reference) is the charID whose animations that clone plays
("Animations from"); omitted, `null` or the source: its own, and the job is byte-identical to one
without the field. The clone's ANIM descriptors (`lbl_800F1D78` tracks 2..18, ARAM `master[432+slot]`)
then point at that character's banks, and the frame-event windows and clip-frame rows follow them
(`core/README.md`, "Animations from"). `validate_job` refuses animations that move bones the
source's body lacks (`anim_choices` never offers them), so it needs the opened game.

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
