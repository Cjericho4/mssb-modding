# Fonts for the Clone Maker window

`clone_gui.py` loads these for its own process only (Windows: `AddFontResourceExW(..., FR_PRIVATE)`),
so nothing is installed on the user's machine. If they can't be loaded the window falls back to
Segoe UI / Consolas. All three families are licensed under the **SIL Open Font License 1.1**, which
allows bundling them with software; each license text sits next to the fonts (`OFL-*.txt`).

| File | Family (as Tk asks for it) | Version | Source |
| --- | --- | --- | --- |
| `Rajdhani-SemiBold.ttf` | `Rajdhani SemiBold` | 1.201 (Indian Type Foundry) | https://raw.githubusercontent.com/google/fonts/e7db6ca603fd4862010383ff17ffd6cc4ffac1c0/ofl/rajdhani/Rajdhani-SemiBold.ttf |
| `Rajdhani-Bold.ttf` | `Rajdhani` bold | 1.201 | https://raw.githubusercontent.com/google/fonts/e7db6ca603fd4862010383ff17ffd6cc4ffac1c0/ofl/rajdhani/Rajdhani-Bold.ttf |
| `OFL-Rajdhani.txt` | | | https://raw.githubusercontent.com/google/fonts/e7db6ca603fd4862010383ff17ffd6cc4ffac1c0/ofl/rajdhani/OFL.txt |
| `Inter-Regular.ttf`, `Inter-Medium.ttf`, `Inter-SemiBold.ttf` | `Inter`, `Inter Medium`, `Inter SemiBold` | 4.1 | https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip (`extras/ttf/`) |
| `OFL-Inter.txt` | | | `LICENSE.txt` from the same zip |
| `JetBrainsMono-Regular.ttf` | `JetBrains Mono` | 2.304 | https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip (`fonts/ttf/`) |
| `OFL-JetBrainsMono.txt` | | | `OFL.txt` from the same zip |

SHA-256:

```
94bbd25a18ca665999feb05a537de9fd2b860dcfb78bbe9ca00270825bf235da  Rajdhani-SemiBold.ttf
691470dd3286a14e9677940d0bf75796179841ba5215cbda1a2c8910a3226afd  Rajdhani-Bold.ttf
40d692fce188e4471e2b3cba937be967878f631ad3ebbbdcd587687c7ebe0c82  Inter-Regular.ttf
97ad806f526e41546d46365bb3a393145f75b7b1568913db74549ad8b8dba872  Inter-Medium.ttf
78a843fade9d4612a5567302fb595b56976eb5fcebf4fea5a5912d638bafcde3  Inter-SemiBold.ttf
a0bf60ef0f83c5ed4d7a75d45838548b1f6873372dfac88f71804491898d138f  JetBrainsMono-Regular.ttf
```

The PyInstaller build (`.github/workflows/build-gui.yml`) bundles this folder with
`--add-data "tools/modding/fonts;fonts"`; the exe finds it under `sys._MEIPASS`.
