# MSSB Clone Maker: extra Marios (or anyone) in Mario Superstar Baseball

A small Windows program that makes a copy of your Mario Superstar Baseball game in which some
characters are replaced by recoloured copies of another character, for example a blue, a green, a
purple and a yellow Mario.  It never changes your original game; it saves a new `.iso` next to it.

You need:

* Your own copy of **Mario Superstar Baseball (USA)** as an `.iso` file (game ID GYQE01).
* **Dolphin** or **Project Rio** to play it.
* About **3.5 GB of free disk space** while the new game is being made (it ends up using 1.5 GB).

## 1. Get the program

**Option A: download the ready-made `.exe` (no Python needed)**

1. You need a (free) GitHub account and to be signed in; GitHub only lets signed-in users download
   build files.
2. Open https://github.com/Cjericho4/mssb-dtk/actions/workflows/build-gui.yml
3. Click the newest run in the list that has a **green tick**.
4. Scroll to the bottom of that page, to **Artifacts**, and click **MSSB-Clone-Maker**.  A `.zip`
   downloads.
5. Open the `.zip` and drag **MSSB-Clone-Maker.exe** to your Desktop (or any folder).
6. Double-click **MSSB-Clone-Maker.exe**.  The first start takes a few seconds.  If Windows shows a
   blue "Windows protected your PC" box, see *Troubleshooting* below.

(If there is no run yet, someone with access to the repository can start one: same page ->
**Run workflow** -> **Run workflow**.)

**Option B: run it with Python**

1. Install Python 3 from https://www.python.org/downloads/windows/ (tick **"Add python.exe to PATH"**).
2. Put `clone_gui.py` and `clone_character.py` (both from `tools/modding/`) in the same folder.
3. Double-click `clone_gui.py`, or open PowerShell in that folder and run: `py clone_gui.py`

## 2. Choose your game

Click **Choose your game (.iso)...** and pick your Mario Superstar Baseball `.iso`.  A line under the
button turns green, **"Mario Superstar Baseball (USA) - OK"**, or red with the reason (wrong file,
not the USA version, already patched, ...).  An extracted game folder works too (**or a folder...**).

## 3. Choose the character and the colours

* **Character to clone**: who gets copied (Mario by default).
* **Colours to change**: the character's own colours, found in its textures, each with a small
  swatch.  The main one is ticked: Mario's red, Luigi's green, Yoshi's green, Peach's pink dress,
  Daisy's orange/yellow dress, Donkey Kong's brown fur, a Koopa's shell and so on.  Tick more to
  change them too (e.g. Donkey Kong's red tie, Mario's blue overalls); at least one stays ticked.
  Skin, eyes and the baseball glove never change.  White, black and grey characters (Boo, King Boo,
  Black Shy Guy, Gray Dry Bones) are tinted: pick Blue and Boo becomes a blue Boo with his eyes and
  mouth as they were.
* **Clones**: one row per copy.  On each row:
  * **Slot it takes over**: which existing character becomes the copy.  The first choices, marked
    *recommended*, are the extra colours of Toad (and of Shy Guy, Koopa, Magikoopa, ...).  They are
    the cleanest choice because the character select screen stays exactly as it was.  Choices marked
    *replaces X on the select screen* turn that character (e.g. Luigi) into the copy.
  * **Colour**: pick one of the colours, or **Custom...** to choose any colour (only its hue is used;
    the light and dark shading comes from the original).
  * The round button on the left shows that row in the **Preview** on the right: every texture of
    the character, before and after.  On a small screen (or with Windows display scaling at 125 % or
    more) the Preview and the Log move under the form; scroll down to see them.
  * **+ Add a clone** / the **×** at the end of a row to change how many copies you make.

The default is four Marios on the four Toad colours: blue, green, purple and yellow.

**Advanced options** (you normally don't need them):

* *Show clones as Mario: name and pictures in menus, HUD, announcer*: on by default.  The roster
  screen, the select grid, the lineup and the in-match card then show Mario's name and a Mario picture
  in the clone's colour, and the announcer says "Mario".  Untick it if a menu shows something odd; the
  clones then keep their old name, face and voice in menus.
  * *Keep the donor's colour word (e.g. 'Purple')*: off by default.  The roster screen draws a colour
    label for colour variants such as Purple Toad; normally the clone gets Mario's instead (Mario has
    no colour).  Tick this to keep the donor's.
* *Give clones Mario's stats*: on by default.
* *Keep shading*: how closely the new colour follows the original's light and dark areas.
* *Keep the working folder*: for troubleshooting only.

## 4. Create the game

The new game is saved next to your original as `... - Clones.iso` (change it with **Save as...**).
Click **Create patched game**.  It takes under a minute, mostly copying the 1.4 GB game; the bar shows
each stage and **Cancel** stops it and cleans up.  The **Log** on the right shows what it is doing, and
the same log is saved as `... - Clones.log` next to the new game (send that file if something fails).
When it is done a message says where the file is.

## 5. Play it in Dolphin or Rio

**File -> Open...** and pick the new `.iso`, or add its folder to your game list
(Config -> Paths -> Add...).  Keep your original `.iso` too; the new one is a separate game.

## 6. Picking the clones in the game

* **Toad slots (the default):** the game has no colour button.  On the roster screen, put **Toad** on a
  team several times.  The first Toad is the normal red Toad; every extra Toad gets one of the other
  colours by itself, and with the default settings every one of those is a Mario in that colour.  The
  extra Toads are listed as **Mario** with a Mario picture in their colour, and the announcer calls
  them "Mario".  The Toad square on the select screen itself stays Toad.
  The same goes for other families (e.g. put several Shy Guys on a team if you used Shy Guy slots).
* **A main character such as Luigi:** pick Luigi as usual.  His square on the select screen now shows
  Mario, and he plays as a Mario in your colour.

## 7. Troubleshooting

* **"Windows protected your PC" / "unrecognized app"** when starting the `.exe`: the program isn't
  signed by a company, so Windows SmartScreen warns about it.  Click **More info**, then
  **Run anyway**.  (If you'd rather not, use option B with Python.)
* **"This is a compressed .rvz file"**: the program needs a plain `.iso`.  In Dolphin or Rio,
  right-click the game in the list -> **Convert File...** -> Format **ISO** -> **Convert...**, then
  choose the new `.iso`.  `.nkit`, `.gcz` and `.wbfs` files are converted the same way.
* **"already been patched"**: choose your original game, not a `- Clones.iso` you made earlier.
* **"not the USA version"**: only the USA (NTSC-U) disc, game ID GYQE01, is supported.
* **Project Rio: the game crashes or the clones look wrong**: turn off Rio's Gecko codes for this
  game (right-click the game -> **Properties** -> **Gecko Codes**, untick them all).  Rio's online
  and ranked modes expect the original game, so play the clones game offline.
* **Not enough disk space**: free up about 3.5 GB on the drive you save to, or **Save as...** onto
  another drive.
* **Something else went wrong**: the message box says what; the **Log** on the right of the window has
  the technical details.  Copy them when you ask for help.

To undo everything, delete the `- Clones.iso`; your original game was never changed.

For command-line use and the technical details, see `README_WINDOWS.md`.
