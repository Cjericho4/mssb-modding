# MSSB Clone Maker — Game Plan and Tournament Mode

*Apple silicon beta build · November 25, 2026 · Changes since v0.4.0-beta*

Draft both teams before you open a game disc. Want two Donkey Kongs in the same lineup? Give one a turquoise jersey and a stronger throwing arm, put him in right field, and bat the original DK at cleanup. Save that team for another opponent, or share the whole matchup for a private Project Rio game.

## Game Plan starts with an empty field

Choose **Away** or **Home**. Each side starts with nine open spots. Click a character to fill the next spot, or drop a portrait directly onto a batting spot or field position. Drag within the batting order to change who hits first. Drag one fielder onto another to swap their positions. Click a name in either view to rename the player in both.

Game Plan and Tournament Mode share your draft and autosave it on this device. The tournament team library starts empty and fills with the teams you save. You can come back with spots still empty and add Rio usernames later. Each mode has a walkthrough you can follow while editing your own lineup.

| What you want to keep | What to click |
| --- | --- |
| One team for another game | **Save Away team…** or **Save Home team…**. The team also appears in your Tournament Mode library. |
| Both teams as one matchup | **Save matchup…**. When both Rio names are filled in, the suggested filename includes both. |
| A lineup someone sent you | Choose a side, then **Import team or matchup…**. A team file fills that side. A matchup file restores both. |

## Each clone can look and play differently

Pick the same character more than once. Tournament Mode finds compatible game slots for the copies. Each player can have a nickname, custom textures, and individual stats. Changed texture files travel inside the team or matchup file.

Use **Add player portraits…** once with your original USA game or extracted game folder. The Cloner remembers the artwork and uses each clone’s paint on its head in the batting order and on the field diagram.

In Studio or the lineup editor, change any of **22 stat fields** for one clone. For example, raise running speed for a leadoff hitter or charge power for a cleanup hitter. Leave a field blank to inherit its value. The four menu rating bars have separate controls from the stats that affect play.

## Take both teams into Project Rio

1. Finish both lineups in **Tournament Mode**. Give each team a captain and enter the two players’ Rio lobby names.
2. Share the saved matchup. On each computer, choose **Build tournament matchup…** with the same clean USA game ISO. The build creates `matchup.iso` and `hud.json`, which holds the starting lineups.
3. On each computer, open **Game Plan → Open finished-game roster…**. Choose the built ISO, click **Load matchup…**, and open the generated `hud.json`. Compare matchup IDs. With Rio closed, click **Use in Rio…** to install the lineup. The Cloner backs up the previous HUD file.
4. Have both players join a private **No Game Mode** lobby. The host chooses **Assign Controller Ports** and gives each player one controller **before enabling Fast Reset**. If the ports change, turn Fast Reset off and on again before starting.

## What we saw in live play

Two players on Mac and Linux completed **4½ innings and 81 pitches** in Project Rio 2.2.0. Both screens showed all **nine custom-painted players out of 18**, including a turquoise DK beside a stock DK on Away and a violet Yoshi beside a stock Yoshi on Home. Rio’s small in-game HUD portraits kept their original colors.

The first game gave each player the other team. A one-pitch rematch confirmed the controller setup order above. Both players then controlled their intended teams, with all 18 batting spots and field positions correct. Both clients showed a dismissible warning during the first game’s setup. The Linux client also crashed when closing that game after play.

## Get the Mac build

Unzip `MSSB-Clone-Maker-Apple-Silicon.zip` and open **MSSB Clone Maker**. To report a problem, choose **Feedback** in the app.
