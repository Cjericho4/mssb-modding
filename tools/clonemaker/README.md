# MSSB Clone Maker

Put copies of one character on other characters' slots in **Mario Superstar Baseball**: a blue,
green, purple and yellow Mario on the four Toad colours, a Luigi wearing your own painted
textures, a Mario who moves like Luigi, and so on.

Each clone gets the character's look, voice, size and (if you like) stats. The Clone Maker never
changes your original game. It saves a new `.iso` next to it.

> **Beta.** Clones based on Mario have been played in Project Rio; other characters are still being
> tested. See [Known issues](#known-issues), and use the **Feedback** button in the app to tell us
> what you find.

**Contents**
[What you need](#what-you-need) ·
[1. Get the app](#1-get-the-app) ·
[2. Choose your game](#2-choose-your-game) ·
[3. Set up your clones](#3-set-up-your-clones) ·
[4. Create the game](#4-create-the-game) ·
[5. Play it](#5-play-it) ·
[Clones of several characters](#clones-of-several-characters) ·
[Your own textures](#your-own-textures) ·
[Animations from another character](#animations-from-another-character) ·
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
     Nothing to install.
   - **Mac:** `MSSB-Clone-Maker-Mac.zip`. Double-click the zip to get **MSSB Clone Maker.app**.
     The first time, macOS blocks it because the app isn't signed with a paid Apple account:
     double-click it, click **Done**, then go to **System Settings → Privacy & Security** and click
     **Open Anyway**.
3. When a newer version is out, a **New version** button appears at the top of the app. Click it to
   open the download page.

## 2. Choose your game

Click **Choose your game (.iso)…** and pick your Mario Superstar Baseball `.iso`. The line under the
button turns green, **"Mario Superstar Baseball (USA) - OK"**, or red with the reason (wrong file,
not the USA version, already patched, …). An extracted game folder works too (**or a folder…**).

## 3. Set up your clones

**Character to clone** is who gets copied (Mario by default).

**Colours to change** lists the character's own colours, each with a small swatch. The main one is
ticked: Mario's red, Luigi's green, Peach's pink dress, Donkey Kong's brown fur, a Koopa's shell and
so on. Tick more to change them too (for example Mario's blue overalls). Skin, eyes and the
baseball glove never change. White, black and grey characters (Boo, King Boo, Black Shy Guy, Gray
Dry Bones) are tinted instead: pick Blue and Boo becomes a blue Boo.

**Clones** has one row per copy:

| Column | What it does |
|---|---|
| ◉ (round button) | Shows this clone in the **Preview** on the right: every texture, before and after. |
| **Slot it takes over** | Which existing character becomes the copy. The first choices, marked *recommended*, are the extra colours of Toad (and of Shy Guy, Koopa, Magikoopa, …): the character select screen stays exactly as it was. Choices marked *replaces X on the select screen* turn that character (for example Luigi) into the copy. |
| **Colour** | A colour from the list, or **Custom…** for any colour (only its hue is used; the light and dark shading comes from the original). Hidden when the clone uses its own textures. |
| **Animations** | Whose animations the clone plays. Normally the character's own. See [Animations from another character](#animations-from-another-character). |
| **Own textures** | **My textures…** uses pictures you painted instead of a colour. See [Your own textures](#your-own-textures). **×** goes back to the colour. |

**+ Add a clone** adds a row; the **×** at the end of a row removes it. The default is four Marios
on the four Toad colours: blue, green, purple and yellow.

## 4. Create the game

The new game is saved next to your original as `… - Clones.iso` (change it with **Save as…**).
Click **Create patched game**. It takes under a minute, mostly copying the 1.4 GB game. The bar shows
each stage, and **Cancel** stops it and cleans up.

The **Log** on the right shows what it is doing. The same log is saved as `… - Clones.log` next to the
new game: attach that file if you report a problem.

## 5. Play it

**In Project Rio or Dolphin:** **File → Open…** and pick the new `.iso`, or add its folder to your
game list (**Config → Paths → Add…**). Keep your original `.iso` too; the new one is a separate game.

**Picking the clones:**

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
2. Click **+ Clone another character** (under the clones). A new section appears with its own
   **Character to clone** (the next character nobody uses yet) and one clone on a free slot.
3. Each section works like the first: its own colours to change, clones, animations, textures and
   **Export textures…**. The **×** at the top right of a section removes that character and its
   clones.

A slot can only be used once in the whole game, and a character you clone can't also be replaced.
Slots another section already uses are greyed out with *(used by another character)*.
**Advanced options** apply to every section.

## Your own textures

Paint a character any way you like instead of picking a colour.

1. Choose the **Character to clone**, then click **Export textures…** and pick a place to save.
   The app makes a folder (for example *Mario textures*) with one PNG picture per texture and a
   `README.txt`.
2. Paint over the pictures in any paint program. **Keep each picture's size and file name.**
   To leave a texture as it was, don't change its picture (or delete it).
3. On a clone row, click **My textures…** and pick that folder. The Preview shows your textures,
   and the **Colour** column is hidden for that row: a clone with its own textures gets no colour
   change, so everything you didn't paint keeps the original colours.

If a picture has the wrong size or name, the app says which one before it makes the game.

## Animations from another character

The **Animations** dropdown on a clone row makes the clone move like someone else, for example
Mario with Luigi's animations.

- The list only offers characters whose skeleton fits the clone.
- Characters at the top fit exactly.
- Characters under **Some bones differ** fit with a few bones that don't line up; hover over the
  dropdown to see which. They work, but some moves can look **glitchy** (limbs bending oddly for a
  moment). Mario with Luigi's animations plays fine but isn't perfect.
- Leave it on the character's own animations if you only want a new look.

## Advanced options

Click **Advanced options ▸**. You normally don't need these. They apply to every clone in the game;
with several characters, each clone follows the character it copies.

- **Show clones as … (recommended):** the roster screen, select grid, lineup and in-match card show
  the cloned character's name and a picture in the clone's colour, and the announcer says that name.
  Untick it if a menu shows something odd; clones then keep the replaced character's name and
  picture in menus.
  - **Keep the donor's colour word (e.g. 'Purple'):** off by default. The roster screen labels colour
    variants such as *Purple Toad*; normally the clone gets the cloned character's label instead.
- **Give clones …'s stats:** on by default. Untick to keep the replaced character's stats.
- **Keep shading:** how closely the new colour follows the original's light and dark areas. Higher
  keeps more of the original shading; lower is brighter and flatter.
- **Keep the working folder:** for troubleshooting only.

## Known issues

- **Animations marked "Some bones differ" can look glitchy** in a few moves.
- **Some recolours have rough spots**, especially on Donkey Kong and Peach (small areas that keep
  the old colour or change when they shouldn't).
- **The slot list cuts off long names** in narrow windows. Widen the window to read them.
- **Only Mario-based clones have been fully played in Rio so far.** Other characters as the source,
  and matches with several clones that each have their own voice, are still being tested.
- **Rio's online and ranked modes** expect the original game. Play the clones game offline.

## Troubleshooting

- **"This is a compressed .rvz file"**: the app needs a plain `.iso`. In Dolphin or Rio, right-click
  the game in the list → **Convert File…** → Format **ISO** → **Convert…**, then choose the new
  `.iso`. `.nkit`, `.gcz` and `.wbfs` files are converted the same way.
- **"already been patched"**: choose your original game, not a `- Clones.iso` you made earlier.
- **"not the USA version"**: only the USA (NTSC-U) disc, game ID GYQE01, is supported.
- **"Windows protected your PC"** when starting the `.exe` (not everyone sees this): click
  **More info**, then **Run anyway**.
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
