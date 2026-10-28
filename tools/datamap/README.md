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
cd C:\mssb-modding\tools\datamap
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

## Editing character stats, chemistry and Challenge teams

```powershell
py mssb_files.py stats-export $disc stats.json
notepad stats.json                                 # change values; delete what you don't touch
py mssb_files.py stats-import $disc stats.json --out D:\Games\mssb_stats.iso
```

`stats.json` has one entry per character (`id` 0..53, CHAR_ID order) with named fields
(`speed`, `slap_power`, `curve`, `fielding_abilities`, `captain_star`, `star_swing`, ...), its
`chemistry` with every character, and the 12 `captain_teams` (lineup 0 is the Challenge starting
team). Only keys present in your file are written, so a file with just
`{"format": "mssb-stats 1", "characters": [{"id": 0, "speed": 90}]}` is a valid mod. Typos and
out-of-range values are refused before anything is written. Field meanings, ranges and what the
game does with each value: `.agent/stats.md` in the repo. Chemistry is stored twice (A with B and
B with A): change both. The star-player bonus (+50, +20 to pitch speeds) is in `main.dol`, not in
this file. The 1..10 bars are what the menus show; no in-match reader was found.

## Inspecting character animation banks and bones (R5)

Tracks 2..18 in `subfile` are banks; body tracks 0/1 contain the skeleton. Extract first,
then use the stdlib inspector (sequence/track IDs are zero-based):

```powershell
py mssb_files.py extract $disc subfile.2 .\out_anim
py animations.py .\out_anim\subfile.2.anim --out bank.json
py animations.py .\out_anim\subfile.2.anim --sequence 2 --keyframes --out swing.json
py mssb_files.py extract $disc subfile.0 .\out_anim
py animations.py .\out_anim\subfile.0.gpc --layout --out bones.json
py audit_animations.py $disc audit.json
py -m unittest test_animations
```

The audit decodes all character banks and layouts, compares descriptor sharing and decoded-byte
hashes, verifies ARAM/disc track-15 equality, and exports all 54 characters' DOL event windows.
It takes several minutes. With --keyframes, the inspector dumps each key's **channels**
and **auxiliary** data (in/out vectors or quaternion controls and, where present, an ease pair).
For an eased segment, use the current key's ease[1] and the next key's ease[0]; the
segment_ease helper returns that pair. The inspector does not evaluate curves or write animations.
Files/JSON with key values are local game data; don't redistribute them. Extraction uses canonical alias
filenames (check the output manifest). Track purposes, binary layouts, swap requirements and
remaining Blender work: [.agent/animations.md](https://github.com/Cjericho4/mssb-dtk/blob/main/.agent/animations.md).

### Exporting a character mesh, skeleton and clip to glTF

From the repo root, export Mario's normal batting swing (track 2, sequence 2):

```powershell
py tools/datamap/export_gltf.py --disc orig/GYQE01 --character mario --out mario_swing.gltf
py tools/datamap/export_gltf.py --disc orig/GYQE01 --character dk --bank 2 --sequence 2 --out dk_swing.gltf
py tools/datamap/export_gltf.py --disc orig/GYQE01 --character babymario --skeleton-only --out baby_bones.gltf
py tools/datamap/export_gltf.py --disc orig/GYQE01 --character mario --no-mesh --out mario_markers.gltf
py tools/datamap/export_gltf.py --disc orig/GYQE01 --character mario --textures painted_mario --out mario_painted.gltf
py tools/datamap/export_gltf.py --disc orig/GYQE01 --character mario --all-animations --out mario_moves.gltf
py tools/datamap/export_gltf.py --disc orig/GYQE01 --character mario --all-animations --banks 2,4,15 --out mario_bat_field_extra.gltf
py tools/datamap/test_export_gltf.py
```

The self-contained glTF includes body triangles, normals and UVs from the `.gpc` geo palette
(each display list's vertex layout comes from its vertex descriptor, display state 2; every one
of the 54 characters exports),
its embedded body textures as PNGs, skin weights from its SK1, SK2 and accumulator lists,
inverse bind matrices from the full rest transforms, and every rigid display piece (a head,
a hat, a crown) as its own mesh on the same skin, placed at its bone's rest transform and
weighted 1.0 to that bone. The hands and glove are separate models in the game (sub-items,
`subitem.N`); the export attaches the two the game attaches for the first clip, as rigid pieces
`subitem_<slot>` of the wrist bones 19 and 25 with the body's textures: for a fielding or
pitching clip the glove on the non-throwing hand and a bare throwing hand, otherwise both bare
hands by batting stance, each in the hand shape (morph) of the clip's first frame. With
`--all-animations` that is the batting pair. `--no-attachments` leaves them out.
Each draw group uses its primary texture. By default it has
one animation sampled every raw ANIM frame at 30 fps. `--all-animations` adds every nonempty
sequence from normal-match tracks 2 (batting), 3 (running), 4 (fielding), 5 (pitching) and
15 (extra); use `--banks` to choose other disc tracks. The Actions share the same skeleton
and have names such as `t2_s02_id77_BatterSwingBat`. Gameplay IDs and labels are
reverse-engineered annotations, not strings stored in the ANIM bank; unnamed IDs retain
their numeric `tN_sNN_idN` label. `--skeleton-only` omits the clip;
`--no-mesh` leaves out the body and puts a small marker cube on each bone instead. `--textures` reads matching `tNN_WxH.png` files from
Clone Maker's **Export Textures** folder, embedding painted PNGs where present and keeping
the other retail textures. Coordinates: every body's bind-space vertices and rest skeleton (all 54) have the
feet at Y=0, the head toward -Y and the face toward -Z, so the root bones are turned 180° about
X (a rotation, not a mirror) and the glTF stands up along +Y, facing +Z; in Blender it stands
along +Z and faces -Y (the front view). GX winds front faces clockwise, so each triangle is
written counter-clockwise, as glTF requires; the normals already point outwards.
Bones: each bone is one node (T, R, S). A bone whose scale axis an exported clip turns away
from identity keeps the exact T·R·Qs·S·Qs⁻¹ chain as four nodes (`bone_N`,
`bone_N_scale_axis`, `bone_N_scale`, `bone_N_scale_axis_inverse`), so which bones have a chain
depends on the clips exported. Blender's armature rest pose cannot hold scale; the importer
puts a node's rest scale into the pose instead. So a leaf bone's rest scale (Mario's head is
0.14) is folded into its geometry and divided out of its scale keys, and every clip keys the
rest scale of the few non-leaf bones that have one (Donkey Kong, the Shy Guys, Toadsworth and
the Magikoopas), so any Action poses the whole body even after **Pose > Clear Transform**.
What is lost: on those non-leaf bones Blender's own rest pose (**Rest Position**, or a cleared
pose with no Action) shows the parts they move unscaled. Extra TEV texture stages
and transparency are simplified: the primary stage supplies the base colour, and the existing
Clone Maker RGB decoder paints transparent retail texels over a checkerboard; painted PNGs
keep their original alpha. Bat/glove, gameplay
event timing and blend transitions remain to be done. The
animation exporter matches tracks to bones by bone ID; tracks without a
matching bone are omitted. Exports contain local retail animation and mesh data: do not commit
or redistribute them.

**Playing another animation in Blender.** The importer stores every animation as an NLA track
but plays only the first (`t2_s00_id75_BatterIdle`). To switch: select the skeleton, enter Pose
Mode, set a Dope Sheet panel to **Action Editor**, click the browse icon left of the action name
and pick one. Or, in the **Nonlinear Animation** editor, click a track's star to solo it. The
normal swing (`t2_s02_id77_BatterSwingBat`) is only 6 frames, so set the timeline end to 6 or
step through it.

## Voices, announcer, sound effects and music

Every voice line, sound effect and announcer call is a sample inside one of 48 sound groups
(`sound.0` .. `sound.47`). Character voices are `sound.5` .. `sound.36` (Mario is `sound.5`; the
first command lists which is which), the announcer's name calls are in `sound.37`.

```powershell
py mssb_files.py sound-list $disc                    # the 48 groups: what each one is
py mssb_files.py sound-list $disc sound.5            # Mario: 13 voice lines, which sample each plays

# export, edit, put back (only WAVs you changed are re-encoded)
py mssb_files.py sound-export $disc sound.5 .\mario_voice      # smp_XXXX.wav + manifest.json
#   edit or replace a WAV in Audacity etc.; keep the file name. Mono or stereo, any rate, 8/16/24/32-bit.
py mssb_files.py sound-import $disc sound.5 .\mario_voice --out D:\Games\mssb_voice.iso

# give every Toad (all colours) Mario's voice; lossless, one command
py mssb_files.py voice-swap $disc Toad Mario --out D:\Games\mssb_toad_mario.iso

# music: the stadium themes etc. are snd\my_snd_h\*.adp; export them to WAV
py mssb_files.py music-export $disc mario_01_h .\music      # or 'all'
```

* `manifest.json` says which SFX (and for voices which line: 0 = happy, 10 = hurt, ...) uses each
  sample. Several lines can share one sample.
* Re-encoding is DSP-ADPCM, like the game (about 40 dB SNR). Keep replacements about as long as
  the originals: all 18 players' voice banks are loaded into sound memory at match start.
* `voice-swap <target> <voice>` takes a character name (`BlueToad`, `Mario`), a charID or a voice
  name (`Toad`). It changes the voice of every character that shares the target's voice bank
  (colour variants, and clones made with `clone_character.py --ui source` of that character).
* The menu has its own copies of some captains' clips (`sound.38`); change them there too.
* Music: replace the `.adp` in an extracted disc folder (Dolphin rebuilds the file table); any
  length works. The tools can export but not yet encode `.adp` (use a DTK/ADP encoder).
* Details and what is still unknown: `.agent/sounds.md`.

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
