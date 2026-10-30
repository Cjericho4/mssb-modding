# MSSB Clone Maker

Put copies of one character on other characters' slots in **Mario Superstar Baseball**: a blue,
green, purple and yellow Mario on the four Toad colours, a Luigi wearing your own painted
textures, a Mario who moves like Luigi, and so on.

Each clone gets the character's look, voice, size and (if you like) stats. The Clone Maker never
changes your original game. It saves a new `.iso` next to it.

> **Beta.** Clones of Mario, Peach, King Boo and DK, and games mixing several of them, have been
> played in Project Rio; other characters are still being tested. See [Known issues](#known-issues), and use the **Feedback** button in the app to tell us
> what you find.

**Contents**
[What you need](#what-you-need) ·
[1. Get the app](#1-get-the-app) ·
[2. Choose your game](#2-choose-your-game) ·
[Finding your way around](#finding-your-way-around) ·
[3. Set up your clones](#3-set-up-your-clones) ·
[4. Create the game](#4-create-the-game) ·
[5. Play it](#5-play-it) ·
[Clones of several characters](#clones-of-several-characters) ·
[Your own textures](#your-own-textures) ·
[Animations from another character](#animations-from-another-character) ·
[Open a character in Blender](#open-a-character-in-blender) ·
[Advanced options](#advanced-options) ·
[Known issues](#known-issues) ·
[Troubleshooting](#troubleshooting) ·
[Feedback](#feedback)

## What you need

- Your own copy of **Mario Superstar Baseball (USA)** as an `.iso` file (game ID GYQE01).
- **Project Rio** or **Dolphin** to play it.
- About **3.5 GB of free disk space** while the new game is being made (it ends up using 1.5 GB).

## 1. Get the app

1. Open the **[latest release](https://github.com/Cjericho4/mssb-modding/releases/latest)**
   (no GitHub account needed).
2. Under **Assets**, download:
   - **Windows:** `MSSB-Clone-Maker.exe`. Put it anywhere (the Desktop is fine) and double-click it.
     Nothing to install. If Windows blocks it, see [Troubleshooting](#troubleshooting).
   - **Mac:** `MSSB-Clone-Maker-Mac.zip`. Double-click the zip to get **MSSB Clone Maker.app**.
     The first time, macOS blocks it because the app isn't signed with a paid Apple account:
     double-click it, click **Done**, then go to **System Settings → Privacy & Security** and click
     **Open Anyway**.
3. When a newer version is out, a **New version** button appears at the top of the app. Click it to
   open the download page.

## 2. Choose your game

The first time you open the app it asks for your game. Click **Choose your game (.iso)…** and pick
your Mario Superstar Baseball `.iso` (an extracted game folder works too: **A folder…**). The dot at
the top turns green, **GYQE01 ready**, or the app says what's wrong (wrong file, not the USA version,
already patched, …). You can choose a different game later from the top bar.

The first time a game loads, a short **tour** points out each part of the screen. **Take the tour**
under **Advanced options**, or in the right-click menu, shows it again.

## Finding your way around

- **The dock (left):** a badge for each character you clone (**Ma** for Mario), with a coloured dot
  for each of its clones. Click a dot to edit that clone. A red **×** on a dot means two clones want
  the same slot. **+** under a character adds another clone of it; the **+** at the bottom clones
  another character. The arrow at the bottom opens the dock: names, a ✓ or ⚠ per clone, and a
  **Checks** card that says what still needs fixing.
- **The stage (middle):** the selected clone as it will look in the game. Drag it to turn it, or
  use **Front**, **Side**, **Back**, the turn arrows, slow spin and reset under it. **Textures** (top
  right) shows every texture of the character, before and after, at full size; **Enlarge** on the small
  before/after box goes there too.
- **The editor (right):** the first tab is the selected clone (see below). The second tab, named after
  the character (for example **Mario · 4 clones**), is about the character these clones copy.
- **Right-click** a clone, a character or the stage for shortcuts (remove a clone, export, turn the
  model, …). **Start over…** clears every clone and choice after asking; your game is never touched.
- **Game plan** and **Stats** are marked *Later*: they're coming in a future version.

## 3. Set up your clones

The app starts with one clone of Mario. On the clone's tab:

| Setting | What it does |
|---|---|
| **Slot it takes over** | Which existing character becomes the copy. The first choices, marked ★ *recommended*, are the extra colours of Toad (and of Shy Guy, Koopa, Magikoopa, …): the character select screen stays exactly as it was. Choices marked *replaces X on the select screen* turn that character (for example Luigi) into the copy. × marks a slot another clone already uses. |
| **Colour** | One of the swatches, or the rainbow one for any colour (only its hue is used; the light and dark shading comes from the original). On a Mac the rainbow swatch may need a double-click. |
| **Colours to change** | The character's own colours, each with a small swatch. The main one is ticked: Mario's red, Luigi's green, Peach's pink dress, Donkey Kong's brown fur, a Koopa's shell and so on. Tick more to change them too (for example Mario's blue overalls). Skin and eyes never change, and the baseball glove keeps its leather colour (only bits of the character on it, like Yoshi's green thumb, change). White, black and grey characters (Boo, King Boo, Black Shy Guy, Gray Dry Bones) are tinted instead: pick Blue and Boo becomes a blue Boo. These ticks apply to **all** clones of that character. |
| **Keep shading** | How closely the new colour follows the original's light and dark areas. Higher keeps more of the original shading; lower is brighter and flatter. The same for every clone. |
| **Own textures** | **My textures…** uses pictures you painted instead of a colour. See [Your own textures](#your-own-textures). **×** goes back to the colour. **Remove clone** removes it. |

On the character's tab, **These clones copy** picks a different character to make the clones from;
they keep their colours, and their slots where they can. That tab also has the exports and, at the
bottom, the selected clone's **Animations** (rarely needed; see
[Animations from another character](#animations-from-another-character)).

Add more clones with the **+** under the character in the dock: for example four Marios on the four
Toad colours.

## 4. Create the game

The new game is saved next to your original as `… - Clones.iso` (change it with **Save as…**).
Click **Create Patched Game** (bottom right). It takes under a minute, mostly copying the 1.4 GB game. The bar shows
each stage, and **Cancel** stops it and cleans up.

**Log** (bottom left) shows what it is doing. The same log is saved as `… - Clones.log` next to the
new game: attach that file if you report a problem.

## 5. Play it

**In Project Rio or Dolphin:** **File → Open…** and pick the new `.iso`, or add its folder to your
game list (**Config → Paths → Add…**). Keep your original `.iso` too; the new one is a separate game.

**Picking the clones:**

In **Slot it takes over**, ★ marks a recommended extra colour; × marks a slot another clone
already uses. Hover over the slot list to read the full explanation.

- **Toad slots (the default):** the game has no colour button. On the roster screen, put **Toad** on
  a team several times. The first Toad is the normal red Toad; every extra Toad takes one of the
  other colours by itself, and each of those is now your clone. The same goes for other families
  (several Shy Guys if you used Shy Guy slots, and so on).
- **A main character such as Luigi:** pick Luigi as usual. He plays as your clone.

With [Show clones as …](#advanced-options) on (the default), the extra Toads are listed with the
cloned character's name and a picture in their colour, and the announcer uses that name.

## Clones of several characters

One game can hold clones of more than one character, for example two Mario recolours on the Toad
colours and a green Bowser on a Shy Guy slot.

1. Set up the first character's clones as usual.
2. Click the **+** at the bottom of the dock (**Clone another character**). A new badge appears with
   one clone on a free slot, copying the next character nobody uses yet. Pick who it copies with
   **These clones copy** on the character's tab.
3. Each character works like the first: its own colours to change, clones, animations, textures and
   exports. To remove a character and its clones, use **Remove … and its clones** on its tab, or
   right-click its badge.

A slot can only be used once in the whole game, and a character you clone can't also be replaced.
Slots another character's clones already use are greyed out with *(used by another character)*.
**Advanced options** apply to every character.

## Your own textures

Paint a character any way you like instead of picking a colour.

1. On the character's tab, click **Export Textures** and pick a place to save.
   The app makes a folder (for example *Mario textures*) with one PNG picture per texture and a
   `README.txt`.
2. Paint over the pictures in any paint program. **Keep each picture's size and file name.**
   To leave a texture as it was, don't change its picture (or delete it).
3. On the clone's tab, click **My textures…** and pick that folder. The stage shows your textures,
   and the colour swatches are turned off for that clone: a clone with its own textures gets no colour
   change, so everything you didn't paint keeps the original colours.

If a picture has the wrong size or name, the app says which one before it makes the game.

## Animations from another character

The **Animations for …** dropdown at the bottom of the character's tab makes the clone move like someone else, for example
Mario with Luigi's animations.

- The list only offers characters whose skeleton fits the clone.
- Characters at the top fit exactly.
- Characters under **Some bones differ** fit with a few bones that don't line up; hover over the
  dropdown to see which. They work, but some moves can look **glitchy** (limbs bending oddly for a
  moment). Mario with Luigi's animations plays fine but isn't perfect.
- **Not well tested yet:** borrowed animations may change the clone's hitboxes (how it swings,
  fields and catches), not just how it looks.
- Leave it on the character's own animations if you only want a new look.

## Open a character in Blender

Look at any character's model, skeleton and animations in [Blender](https://www.blender.org/)
(3.6 or newer), for example to study a move up close.

1. Choose your game, then click **Export 3D Model** on the character's tab.
2. Pick the **Character**; it starts with the one you're cloning. Leave **All animations** ticked
   for every batting, running, fielding and pitching move (about 115), or untick it for just the
   normal swing (a much smaller file).
3. Click **Save as…** and choose where to save the `.gltf` file. It takes a second or two.

Then, in Blender:

1. **File > Import > glTF 2.0**, and pick the file.
2. Blender plays only the first animation (`t2_s00_id75_BatterIdle`). To pick another: select the
   skeleton, enter **Pose Mode**, set a Dope Sheet panel to **Action Editor**, click the browse
   icon left of the action name and choose one. Names start with `t2` (batting), `t3` (running),
   `t4` (fielding), `t5` (pitching) or `t15` (extras).
3. The normal swing (`t2_s02_id77_BatterSwingBat`) is only **6 frames**: set the timeline end to 6,
   or step through it with the arrow keys.

Each part shows its main texture; see-through parts of a texture show as a grey checkerboard.
The file contains the game's models and textures: **keep it to yourself**, don't share or upload
it.

## Advanced options

Click **Advanced options ▸**. You normally don't need these. They apply to every clone in the game;
with several characters, each clone follows the character it copies.

- **Show clones as … (recommended):** the roster screen, select grid, lineup and in-match card show
  the cloned character's name and a picture in the clone's colour, and the announcer says that name.
  Untick it if a menu shows something odd; clones then keep the replaced character's name and
  picture in menus.
  - **Keep the donor's name-box colour (e.g. Purple Toad's purple):** off by default. On the roster
    screen a colour variant's name sits on a coloured box; normally a clone's box takes the colour
    closest to the clone's (red, blue, yellow, green or purple). Tick it to keep the replaced
    character's box colour.
- **Give clones …'s stats:** on by default. Untick to keep the replaced character's stats.
- **Keep the working folder:** for troubleshooting only.
- **Theme:** **Night** (dark, the default), **Honey** (dark, honey gold), **Ballpark** (dark, grass and clay), **Light** or **Strawberry milk** (light, cream and pink).
- **Take the tour:** shows the first-time tour again.

## Known issues

- **Animations marked "Some bones differ" can look glitchy** in a few moves.
- **Some recolours have rough spots**, especially on Donkey Kong and Peach (small areas that keep
  the old colour or change when they shouldn't). For Peach, tick both **Pink (dress)** and
  **Dark pink (dress)**: part of her dress is the darker pink, so with only the first ticked a
  pink patch stays ([#16](https://github.com/Cjericho4/mssb-modding/issues/16)).
- **On the select screen, the big 3D character behind the team shows the cloned character in
  its own colours**, not the clone's colour (those menu models can't be recoloured yet).
- **Not every character has been played as a clone in Rio yet.** Mario, Peach, King Boo and DK have.
- **Rio's online and ranked modes** expect the original game. Play the clones game offline.

## Troubleshooting

- **"This is a compressed .rvz file"**: the app needs a plain `.iso`. In Dolphin or Rio, right-click
  the game in the list → **Convert File…** → Format **ISO** → **Convert…**, then choose the new
  `.iso`. `.nkit`, `.gcz` and `.wbfs` files are converted the same way.
- **"already been patched"**: choose your original game, not a `- Clones.iso` you made earlier.
- **"not the USA version"**: only the USA (NTSC-U) disc, game ID GYQE01, is supported.
- **"Windows protected your PC"** when starting the `.exe` (not everyone sees this): click
  **More info**, then **Run anyway**.
- **Smart App Control blocked it** (Windows 11; there's no Run anyway button): right-click the
  downloaded file → **Properties**, tick **Unblock** at the bottom, click **OK**, and open it again.
  If it is still blocked, the only other way is turning Smart App Control off (Windows Security →
  App & browser control), which Windows won't let you turn back on without resetting Windows, so
  only do that if you're sure.
- **Project Rio: the game crashes or the clones look wrong**: turn off Rio's Gecko codes for this game
  (right-click the game → **Properties** → **Gecko Codes**, untick them all).
- **Not enough disk space**: free up about 3.5 GB on the drive you save to, or **Save as…** onto
  another drive.
- **The game won't start, or hangs on a loading screen**: first check that your *original* game still
  starts. If it doesn't, the problem is your Rio or Dolphin setup, not the clones. Then open the new
  `.iso` (not an extracted folder) and make sure no cheat or Gecko codes are on for it.
- **A clone looks like the plain character, has black or garbled textures, or a menu shows an odd
  name or picture**: untick **Show clones as …** under Advanced options, make the game again, and see
  whether that changes it. Then report it (below) with the `.log` file.
- **Colours look wrong but the game runs**: tick the other colour boxes, or pick a different colour.

To undo everything, delete the `- Clones.iso`. Your original game was never changed.

## Feedback

Click **Feedback** at the top of the app, or
**[open a report on GitHub](https://github.com/Cjericho4/mssb-modding/issues/new/choose)**.
Choose **Bug report** or **Idea**. For a bug, please include:

- the app version (in the release name, for example *v0.1.0-beta*) and Windows or Mac,
- what you cloned: the character, the slots, colours, animations and whether you used your own
  textures,
- what happened and on which screen, and the `… - Clones.log` file.

Please **don't attach game files** (`.iso`, extracted files): we can't accept them.

---

Developers: the app's design is in [app/README.md](app/README.md), its commands in [API.md](API.md),
and the original Python patcher is `tools/modding/clone_character.py` (`--help` lists every option).
