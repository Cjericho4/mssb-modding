# MSSB Blender exporter

Exports a Mario Superstar Baseball character from **your own copy of the game** to a `.gltf` file
that Blender opens: the textured body, its skeleton and its animations.

**Using the Clone Maker app (0.3.0-beta or newer)?** Its **Export to Blender…** button does the
same without Python ([guide](https://github.com/Cjericho4/mssb-modding/blob/main/tools/clonemaker/README.md#open-a-character-in-blender)).
This script is for Linux, and for the extra options (one clip, other animation banks, the
skeleton only).

## What you need

- **Python 3** ([python.org](https://www.python.org/downloads/); on Windows tick "Add python.exe to PATH").
- Your game as an `.iso`, or an extracted game folder.
- Blender 3.6 or newer.

## Export a character

Unzip this folder, open a terminal in it (on Windows: right-click the folder, **Open in Terminal**)
and run, with your own game path:

```powershell
py tools/datamap/export_gltf.py --disc "C:\Games\MSSB.iso" --character mario --all-animations --out mario.gltf
```

On Mac or Linux use `python3` instead of `py`. Use any character name, e.g. `dk`, `peach`,
`babymario`, `kingboo`. Without `--all-animations` you get only the normal swing.

## Open it in Blender

1. **File > Import > glTF 2.0**, pick `mario.gltf`.
2. Blender plays only the first animation (`t2_s00_id75_BatterIdle`). To pick another: select the
   skeleton, enter **Pose Mode**, set a Dope Sheet panel to **Action Editor**, click the browse
   icon left of the action name and choose one. Names start with `t2` (batting), `t3` (running),
   `t4` (fielding), `t5` (pitching) or `t15` (extras).
3. The normal swing (`t2_s02_id77_BatterSwingBat`) is only 6 frames: set the timeline end to 6 or
   step through it with the arrow keys.

The exported file contains the game's models and textures: keep it to yourself, don't share it.
More options: [the full exporter guide](https://github.com/Cjericho4/mssb-modding/blob/main/tools/datamap/README.md#exporting-a-character-mesh-skeleton-and-clip-to-gltf).
