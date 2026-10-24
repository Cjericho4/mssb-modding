# mssb_files.py: list, extract and replace Mario Superstar Baseball data files

Almost everything in Mario Superstar Baseball (GameCube, USA, `GYQE01`) lives inside two big
files on the disc, `files/ZZZZ.dat` (450 MB) and `files/aaaa.dat` (the game's code modules).
They have no directory: the game finds each file through tables hard-coded in its code.
This tool knows all of those tables, so it can list every file, unpack it, and put a modified
copy back.

Requirements: Python 3.8 or newer (`py --version`). Nothing else to install. Keep the
`tools/modding` folder next to `tools/datamap`, because the tool uses its LZSS packer.

Your disc can be:
* a `.iso` file (convert `.rvz`/`.nkit` first: Dolphin, right-click the game, **Convert File**, ISO), or
* an extracted folder (Dolphin, right-click the game, **Properties**, **Filesystem**,
  right-click the disc, **Extract Entire Disc**). The folder contains `sys\` and `files\`.

The tool never writes to your input. `replace` always writes a **new** folder or ISO.
Extracted files are for your own copy of the game only. Don't share them.

## Quick start (Windows PowerShell)

```powershell
cd C:\mssb-dtk\tools\datamap
$disc = "D:\Games\Mario Superstar Baseball (US).iso"

# every file: id, offset, sizes, format, what it is
py mssb_files.py list $disc
py mssb_files.py list $disc --csv files.csv          # open in Excel
py mssb_files.py list $disc --table subfile          # one table only
py mssb_files.py tables                              # what each table is and which code loads it

# details of one file: every descriptor that names it, texture list, sequences, ...
py mssb_files.py info $disc res.15

# unpack one file, or everything (about 1 GB), with manifest.json
py mssb_files.py extract $disc res.15 .\out
py mssb_files.py extract $disc all .\out_all

# put a modified file back; writes a new ISO (or a folder if --out has no .iso)
py mssb_files.py replace $disc res.15 .\out\res.15.ui.bin --out D:\Games\mssb_mod.iso
```

## Editing text (dialogue, character bios, tutorials)

The game has five text banks: `text_menu.0` (954 strings: Challenge dialogue, items),
`menu_1ff84.1` (599 strings: records, character bios), `game_284e8.0` (168 strings:
practice/tutorial), `game_common.4` (23 strings: minigames) and `text_sys.0` (11 strings).

```powershell
py mssb_files.py text-export $disc menu_1ff84.1 bios.txt
notepad bios.txt                                   # edit the text, keep the leading number + TAB
py mssb_files.py text-import bios.txt bios.bin
py mssb_files.py replace $disc menu_1ff84.1 bios.bin --out D:\Games\mssb_bios.iso
```

In the text file, `\n` is a line break, `<XX>` is a control code (colours, text box, voice:
leave them as they are or copy them) and `{XXXX}` is a glyph with no ASCII equivalent (button
icons, symbols). Letters, digits, space and `! " ' ( ) , - . ?` are supported. Strings can get
longer or shorter.

## How replace works

1. The new file is packed the same way as the old one (LZSS 11/4 for almost everything; stored
   files stay stored).
2. If it fits the old slot, it's written in place. Otherwise it's appended to the end of
   `ZZZZ.dat`. With `--reuse-gaps` it goes into the ~190 MB of `ZZZZ.dat` that no table
   references instead, so the ISO keeps its retail size. Use that if the larger ISO is a problem
   on your loader.
3. **Every** descriptor that names the file is updated. The common UI files, for example, have up
   to 8 copies across `main.dol`, `game.rel` and `challenge.rel`. When a REL changes, it's re-packed
   into `aaaa.dat` (this takes 15 to 40 seconds).
4. For files inside the ARAM blob (`master.*`: character bodies, texture overlays, track-15 ANIM banks),
   the blob is moved to the end of `ZZZZ.dat` when a member grows. The blob descriptor and the
   hard-coded ARAM allocation size in `main.dol` are updated too.
5. `sys\fst.bin` sizes are updated. For an ISO, a file that grew is moved past the last file.
6. The output is re-read and checked. The replaced file must decode to your file, every other
   file must be byte-identical, and the only changed bytes in `main.dol` and the RELs may be the
   descriptor rows the tool rewrote.

`replace` works on files inside `ZZZZ.dat` and `aaaa.dat` (everything `list` shows without
`--fst`). The FST files (`snd\my_snd_h\*.adp` music, `opening.bnr`) are listed with `--fst` and
copied by `extract --fst`, but the tool doesn't replace them. To change music, swap the `.adp` in
an extracted folder (Dolphin rebuilds the FST from the folder).

Size limits to keep in mind: the ARAM blob lives in 16 MB of ARAM (retail 4.9 MB). A texture
overlay (`master.33` to `master.53`) must stay exactly the size of the body texture archive it
overwrites.

## Tests

```powershell
py test_mssb_files.py                      # synthetic disc (no game data) + quick real-disc checks
$env:MSSB_DISC = "D:\Games\Mario Superstar Baseball (US).iso"
$env:MSSB_SLOW = "1"; py test_mssb_files.py RealDisc   # decompress every file, real replace round trip
```

The full map of the data, with every table, format and loader, is in `.agent/data_map.md`.
