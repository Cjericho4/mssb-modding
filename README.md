# MSSB Modding

Tools for modding Mario Superstar Baseball (GameCube, USA, GYQE01), built on what the
[mssb-dtk](https://github.com/Cjericho4/mssb-dtk) decompilation found out about the game.

You need your own copy of the game. Nothing here contains game files, and the tools never upload
or share yours.

| Folder | What it is |
|---|---|
| `tools/clonemaker` | **MSSB Clone Maker**: puts a recoloured copy of a character (or your own painted textures) on another character's slot, with the right voice, size and stats. Rust core + Tauri window for Windows, Mac and Linux. Start at [tools/modding/README_GUI.md](tools/modding/README_GUI.md). |
| `tools/datamap` | List, extract and replace the game's files; stats, sounds and animations ([README](tools/datamap/README.md)). |
| `tools/modding` | The original Python clone patcher (the reference the Rust core is tested against), the recolour audit and small inspectors. |

## Download

Get the newest version from **[Releases](https://github.com/Cjericho4/mssb-modding/releases/latest)**:
`MSSB-Clone-Maker.exe` (Windows) or `MSSB-Clone-Maker-Mac.zip` (Mac). The app tells you when a newer
release is out. Test builds of every change are under
[Actions](https://github.com/Cjericho4/mssb-modding/actions) (artifacts, GitHub login needed).

Found a problem? [Report it](https://github.com/Cjericho4/mssb-modding/issues/new/choose).

## Making a release

1. Set the same new version in `tools/clonemaker/app/tauri.conf.json` and `tools/clonemaker/app/Cargo.toml`, commit and push.
2. `git tag v0.2.0 && git push origin v0.2.0`. Both build workflows check the tag matches the version,
   run the tests and attach the Windows exe and the Mac zip to the release `v0.2.0`.
3. Edit the release notes on GitHub (known issues!).

## How it relates to mssb-dtk

The game knowledge (addresses, table layouts, formats, the evidence behind them) lives in the
decompilation repository's `.agent/` docs; a comment here that says `.agent/stats.md` means
[that file in mssb-dtk](https://github.com/Cjericho4/mssb-dtk/tree/main/.agent). Developer helpers that
read the decompilation (e.g. `tools/datamap/dev_tables.py`) expect a built mssb-dtk checkout next
to this one (`../mssb-dtk`) or at `$MSSB_DTK`.

Put the extracted game at `orig/GYQE01/` (ignored by git) for the tests that need it, e.g.
`python3 tools/clonemaker/difftest.py`.

## License

MIT (see [LICENSE](LICENSE)). The bundled fonts are under the SIL Open Font License (their
`OFL-*.txt` files).
