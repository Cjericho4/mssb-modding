# Duplicate characters in Mario Superstar Baseball on Windows (Dolphin)

`clone_character.py` copies one character (default: Mario) onto other roster slots with recoloured
textures, so several Marios in different colours can be on the field at once.  It only edits data
(disc tables, model textures, stats); nothing is recompiled.  Python standard library only.

Files you need from the repo: `tools/modding/clone_character.py` and `tools/modding/test_clone.py`
(same folder).

## 1. Install Python

1. https://www.python.org/downloads/windows/ -> latest Python 3 (3.8 or newer).
2. In the installer tick **"Add python.exe to PATH"** (the `py` launcher is installed by default).
3. Open PowerShell and check: `py --version`

## 2. Extract the disc in Dolphin

1. Dolphin game list -> right-click *Mario Superstar Baseball (GYQE01)* -> **Properties**.
2. **Filesystem** tab -> right-click **Disc** (the top node) -> **Extract Entire Disc...**
3. Pick an empty folder, e.g. `C:\mssb\GYQE01`.  Afterwards it contains `sys\` (main.dol, boot.bin,
   bi2.bin, apploader.img, fst.bin) and `files\` (ZZZZ.dat 429 MB, game.rel, menus.rel, ...).

The patcher never modifies this folder; it is your clean copy.

## 3. Baseline: boot the clean folder once

Before patching anything, Dolphin -> **File -> Open...** -> `C:\mssb\GYQE01\sys\main.dol` and start an
exhibition game.  Dolphin boots an extracted folder as a virtual disc (it rebuilds the file table from
`files\`).  If the *clean* folder does not boot or hangs loading a stadium, folder-boot itself is the
problem (not the patch): use the `--iso` output below instead, which keeps the retail file table.

## 4. Run the patcher

```powershell
cd C:\mssb\tools          # wherever you put clone_character.py

# see the 54 slots, which ones can be donors, and how the game picks colour variants
py .\clone_character.py --disc "C:\mssb\GYQE01" --list-slots

# TEST 1 (simplest): Luigi becomes a green Mario.  Luigi is a normal, directly selectable character.
py .\clone_character.py --disc "C:\mssb\GYQE01" --out "C:\mssb\GYQE01-luigi" --source mario --slot Luigi:green

# TEST 2: all four Toad colours become Marios (blue, green, purple, yellow)
py .\clone_character.py --disc "C:\mssb\GYQE01" --out "C:\mssb\GYQE01-marios" --source mario `
    --slot BlueToad:blue --slot YellowToad:green --slot GreenToad:purple --slot PurpleToad:yellow
```

(The four Toad slots are also the default if you give no `--slot`.)  It copies the folder (450 MB, takes a moment), appends the new model files to
`files\ZZZZ.dat`, patches `sys\main.dol`, `sys\fst.bin`, `files\game.rel` and the stats block, and ends
with a `Done.` line telling you which menu characters are now Marios.

Options:
* `--iso "C:\mssb\marios.iso"` additionally writes a bootable ISO (1.46 GB; slightly larger than a
  retail disc image, Dolphin does not mind).  The ISO keeps the retail file table (`sys\fst.bin`, with
  the new ZZZZ.dat size), so it is the higher-fidelity path if folder boot misbehaves.
* Recolour spec after the colon.  Presets: `red orange yellow green cyan blue purple pink`.  Or
  `hue=0.62` (0..1), `hue=0.62,sat=1.2,val=0.9`, `all=1` (recolour every saturated colour, skin too),
  `tint=00FF00:0.5` (blend towards a colour), `solid=0000FF` (debug: flat colour).  By default only
  Mario's reds (hat, shirt) are retargeted; skin, eyes, blue overalls and the white glove stay.
* `--source Luigi` etc. to clone someone else.  Characters with a per-character setup case (DK, Boo,
  Piantas, ...) can only be cloned onto slots 0..47; `--list-slots` shows the "setup" column.
* `--no-aram-overlay`: skip the in-match (ARAM) body recolour.  Use it only to isolate a problem; with
  it the clones look like plain Mario in matches.
* `--no-stats` / `--no-gameplay`: keep the donor's stats / timing tables instead of Mario's.
* `--force`: overwrite `--out`.
* `--dry-run`: print every planned edit (ZZZZ.dat offsets and sizes, master entries, pairs, rows) and
  write nothing.  Also runs the preflight check that `--disc` is a clean, complete USA dump.

Verify an output folder you already made (same `--source`/`--slot` as when you made it):

```powershell
py .\test_clone.py --disc "C:\mssb\GYQE01" --patched "C:\mssb\GYQE01-marios" --source mario --slot Luigi:green
```

Start over / undo: delete the output folder (or re-run with `--force`).  The extracted input is never
modified; the patcher refuses to run if `--disc` is already patched or `--out` equals `--disc`.

Optional self-check (runs the patcher into a scratch folder and verifies everything it wrote; takes
about a minute):

```powershell
py .\test_clone.py --disc "C:\mssb\GYQE01" --work "C:\mssb\clone_test"
```

It must end with `ALL CHECKS PASSED`.

## 5. Boot the patched folder

Dolphin -> **File -> Open...** -> `C:\mssb\GYQE01-marios\sys\main.dol`.  Dolphin boots an extracted
folder as if it were a disc (it rebuilds the file table from `files\`).  Or open the `.iso` from step 3.

Tip: add `C:\mssb` as a game folder (Config -> Paths) and the patched folder shows up in the game list.

## 6. Picking the clones in the game

**Test 1 (Luigi):** Exhibition -> any captain -> put **Luigi** on a team next to Mario.  Luigi is a green
Mario on the field (menus still say Luigi).  No colour logic involved; if this works, the whole
pipeline works.

**Test 2 (Toads):** the clones live on the **Toad colour slots**.  The game has no colour button: when you put *several
Toads* on one team, the first is Red Toad (the real Toad) and each further Toad gets one of the other
colours automatically (Blue, Yellow, Green, Purple, in a partly random order).  With the test-2
command every non-red Toad is a Mario, so the order does not matter.

1. Exhibition -> pick a captain (Mario is fine) -> on the roster screen replace players with **Toad**
   several times on the same team.
2. Start the game.  On the field you get Mario, Red Toad and a recoloured Mario per extra Toad.
   Put Toads on both teams to see them batting and fielding.

The menus still show the donor's name and portrait ("Toad"), and the announcer says "Toad".  The
model, animations, bat/glove, stats and voice are Mario's.

Want different donors?  `--list-slots`.  Good donors are the colour variants (Toads 29-32, Shy Guys
44-47, Piantas 22-23, Nokis 25-26, Magikoopas 34-36, Koopa 42, Paratroopa 43, Dry Bones 49-51) and any
stand-alone character (e.g. `--slot Luigi:green` makes Luigi a green Mario, trivially selectable).
Family base slots (Red Toad 13, Red Shy Guy 16, ...) share their body with their colour variants and
are refused unless you pass `--no-aram-overlay`.

## 7. What to expect / troubleshooting

Verified offline (test_clone.py): the LZSS codec round-trips real disc files; the retail colour-variant
mechanism works exactly the way the patcher uses it (Blue Toad's model == Red Toad's body + overlay
34); every patched table entry re-parses; the recoloured models have intact headers and only texture
bytes differ; everything outside the intended edits is byte-identical.  **Not verified until it runs
in Dolphin:** the game actually loading the appended files, the relocated ARAM blob, and the menus
accepting a Toad slot whose stats row says "Mario class".

Reading a crash in Dolphin:
* Options -> Configuration -> Interface -> **Show Debugging UI**, then View -> **Code** / **Registers**.
  On a crash Dolphin pauses or shows a panic alert like `Invalid read from 0x0000000C, PC = 0x800233A0`;
  the PC is the important number.  View -> **Show Log** (+ Options -> Set Log Verbosity -> Info, enable
  the OSReport channel) shows the game's own messages, which include the `OSReport` just before a model
  crash.
* **PC 0x800233A0** = `f_InitActorWithLayout` dereferencing NULL because the model's layout
  `skinFileID` is out of range of its geometry palette, i.e. the body file the game loaded is not a
  valid character model (wrong disc offset/size, or a bad decompression).  The patcher checks exactly
  this on the files it writes, so if it shows up the file was read from the wrong place: make sure you
  booted the **patched** folder's `sys\main.dol`, and if you used an ISO try the folder.
* **Hangs on the loading screen**: the LZSS decompressor hit an error and the loader waits forever.
  Same causes as above.  Try `--no-aram-overlay` to find out whether the ARAM overlay or the disc files
  are at fault.
* **Black / missing / garbled textures** on a clone only: the texture overlay.  Try
  `--no-aram-overlay`; if the clones are then plain Mario without glitches, report it.
* **Clones look like plain Mario in matches** (but the patcher said overlay written): the ARAM path
  did not take; report it with the Dolphin log.
* **Crash as soon as a match starts loading, before any character shows**: the relocated ARAM blob or
  its size patch.  `--no-aram-overlay` leaves the blob untouched, so if that boots the blob move is the
  culprit.
* Wrong colours but no crash: play with the spec, e.g. `hue=0.62,width=0.08` to widen the band of reds
  that gets recoloured, `all=1` to recolour everything.

### Symptom -> what to try next

| Symptom | Meaning | Next step |
|---|---|---|
| `preflight: ...` error from the patcher | `--disc` is not a clean USA (GYQE01) extraction, or already patched | re-extract the disc in Dolphin into an empty folder |
| Clean folder (step 3) does not boot | Dolphin folder-boot, not the patch | use `--iso` and open the .iso |
| Patched folder hangs on the Nintendo/Namco logos or the first loading screen | blob move / ARAM size patch (fn_800227E4) | re-run with `--no-aram-overlay`; if that boots, report "blob" |
| Boots, menus fine, hang when a match starts loading | LZSS stream error on an appended file, or blob | `--no-aram-overlay`; then `--no-gameplay`; report which one helped |
| Panic `PC = 0x800233A0` | body file read from the wrong disc offset/size | make sure you booted the **patched** `sys\main.dol`; try `--iso` |
| Clone shows as plain Mario in matches, coloured in menus/replays | overlay not applied (pair / master entry) | report; the data-only part still proved the clone |
| Clone has black/garbled textures, others fine | overlay archive | `--no-aram-overlay` to confirm, report |
| Crash when the clone swings/catches | sub-item (bat/glove) alias | `--no-gameplay` first (timing tables), then report |
| Roster screen refuses the Toad or shows odd stats | stats row | `--no-stats` |
| Colours wrong but stable | recolour spec | widen the band `hue=0.62,width=0.08`, or `all=1` |

Always run `test_clone.py --patched <folder> ...` on a failing folder first: if it does not say
`ALL CHECKS PASSED`, the folder was altered after patching (e.g. a half-finished copy).

Undo: delete the output folder (and the ISO).  The extracted input was never touched.
