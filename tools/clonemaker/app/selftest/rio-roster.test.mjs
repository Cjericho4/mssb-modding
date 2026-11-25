import test from "node:test";
import assert from "node:assert/strict";
import {
  singleTeam, unpackTeam, makeHUD, teamsFromHUD, moveBatter, swapFieldPosition, replaceBatter
} from "../ui/rio-roster.mjs";

const image = {sha256:"a".repeat(64), hands:Array.from({length:54},(_,i)=>[i%2,1-i%2])};
function team(player, start) {
  return {player,rows:Array.from({length:9},(_,i)=>({
    id:start===24 && i===4 ? 2 : start+i,pos:(i+4)%9,captain:i===4,star:i===7,...(i===5 ? {label:"Custom clone"} : {})
  }))};
}
test("independent saved teams combine into the same Rio lineup on either computer", () => {
  const away=singleTeam(team("AwayTest",24),image.sha256);
  const home=singleTeam(team("HomeTest",6),image.sha256);
  const fixture={Away:unpackTeam(away,image.sha256),Home:unpackTeam(home,image.sha256)};
  const exported=makeHUD(fixture,image);
  assert.equal(exported["Away Roster 5"].CharID,29);
  assert.equal(exported["Away Roster 5"]["Clone Maker Label"],"Custom clone");
  assert.equal(exported["Away Roster 5"]["Fielding Position"],0);
  assert.equal(exported["Away Roster 5"]["Batting Hand"],1);
  assert.equal(exported["Away Roster 5"]["Fielding Hand"],0);
  assert.equal(exported["Home Roster 4"].Captain,1);
  const loaded=teamsFromHUD(JSON.parse(JSON.stringify(exported)),image);
  assert.equal(loaded.verified,true); assert.deepEqual(loaded.teams,fixture);
  assert.deepEqual(makeHUD(loaded.teams,image),exported);
});
test("mismatched games and incomplete or ambiguous rosters fail before export",()=>{
  const away=team("AwayTest",24), home=team("HomeTest",6);
  const saved=singleTeam(away,image.sha256);
  assert.throws(()=>unpackTeam(saved,"b".repeat(64)),/different game/);
  const doc=makeHUD({Away:away,Home:home},image);
  assert.throws(()=>teamsFromHUD(doc,{...image,sha256:"b".repeat(64)}),/different game/);
  assert.throws(()=>makeHUD({Away:away,Home:away},image),/different Rio/);
  delete doc["Away Roster 1"];
  assert.throws(()=>teamsFromHUD(doc,image),/roster entry/);
  away.rows[0].pos=away.rows[1].pos;
  assert.throws(()=>singleTeam(away,image.sha256),/fielding position/);
});
test("existing Rio HUD imports lineup only, resets gameplay and uses current disc hands",()=>{
  const doc=makeHUD({Away:team("A",24),Home:team("B",6)},image);
  delete doc["Clone Maker SHA256"];
  delete doc["Away Roster 0"].Captain; // Rio permits an omitted marker on non-captains.
  Object.assign(doc,{Inning:6,"Home Score":8,"Away Batter Roster Loc":5});
  doc["Away Roster 5"]["Batting Hand"]=0;
  const loaded=teamsFromHUD(doc,image);
  assert.equal(loaded.verified,false);
  assert.equal(loaded.teams.Away.rows[5].label,undefined);
  const fresh=makeHUD(loaded.teams,image);
  assert.equal(fresh.Inning,1);assert.equal(fresh["Away Batter Roster Loc"],0);
  assert.equal(fresh["Home Score"],undefined);assert.equal(fresh["Away Roster 5"]["Batting Hand"],1);
});

test("moving a batter retains the whole player, and swapping fielders retains the batting order", () => {
  const away = team("AwayTest", 24);
  const initial = structuredClone(away);
  moveBatter(away, 5, 0);
  assert.deepEqual(away.rows, [initial.rows[5], ...initial.rows.slice(0, 5), ...initial.rows.slice(6)]);
  moveBatter(away, 0, 5);
  assert.deepEqual(away, initial);

  const pitcher = initial.rows[5];
  const rightFielder = initial.rows[4];
  swapFieldPosition(away, 4, pitcher.pos);
  const expected = structuredClone(initial);
  expected.rows[4].pos = pitcher.pos;
  expected.rows[5].pos = rightFielder.pos;
  assert.deepEqual(away, expected);
  swapFieldPosition(away, 4, pitcher.pos);
  moveBatter(away, 4, 4);
  assert.deepEqual(away, expected);
});

test("replacing a player keeps the batting spot and field position and handles clone labels and captain eligibility", () => {
  const away = team("AwayTest", 24);
  const captain = away.rows[4];
  captain.label = "Captain clone";
  captain.star = true;
  const initial = structuredClone(away);
  replaceBatter(away, 4, captain.id);
  assert.deepEqual(away, initial);
  replaceBatter(away, 4, 10);
  assert.deepEqual(away.rows[4], {id:10, pos:8, star:true, captain:true});
  replaceBatter(away, 4, 53);
  assert.deepEqual(away.rows[4], {id:53, pos:8, star:true, captain:false});
  assert.deepEqual(away.rows.filter((_, i) => i !== 4), initial.rows.filter((_, i) => i !== 4));
});

test("invalid edit targets or duplicate player replacements leave the whole draft intact", () => {
  const away = team("AwayTest", 24);
  const initial = structuredClone(away);
  assert.throws(() => moveBatter(away, 3, 9), /batting spot/);
  assert.throws(() => moveBatter(away, -1, 0), /batting spot/);
  assert.throws(() => swapFieldPosition(away, 4, "0"), /field position/);
  assert.throws(() => replaceBatter(away, 4, 54), /character/);
  assert.throws(() => replaceBatter(away, 4, 29), /already/);
  assert.deepEqual(away, initial);
});

test("both edited team files retain independent batting orders and starting defenses in the combined Rio matchup", () => {
  const original = {Away: team("AwayTest", 24), Home: team("HomeTest", 6)};
  const edited = teamsFromHUD(makeHUD(original, image), image).teams;
  moveBatter(edited.Away, 5, 0);
  swapFieldPosition(edited.Away, 0, 2);
  replaceBatter(edited.Away, 2, 52);
  assert.deepEqual(edited.Home, original.Home);
  moveBatter(edited.Home, 0, 8);
  swapFieldPosition(edited.Home, 8, 0);

  const awayFile = singleTeam(edited.Away, image.sha256);
  const homeFile = singleTeam(edited.Home, image.sha256);
  const reopened = {
    Away: unpackTeam(awayFile, image.sha256), Home: unpackTeam(homeFile, image.sha256)
  };
  assert.deepEqual(reopened, edited);
  const hud = makeHUD(reopened, image);
  for (const [side, lineup] of Object.entries(edited)) {
    assert.equal(hud[side + " Batter Roster Loc"], 0);
    for (const [index, row] of lineup.rows.entries()) {
      const entry = hud[side + " Roster " + index];
      assert.equal(entry.CharID, row.id);
      assert.equal(entry["Fielding Position"], row.pos);
    }
  }
  assert.equal(hud["Away Roster 0"].CharID, 29);
  assert.equal(hud["Away Roster 0"]["Fielding Position"], 2);
  assert.equal(hud["Home Roster 8"].CharID, 6);
  assert.equal(hud["Home Roster 8"]["Fielding Position"], 0);
  assert.deepEqual(teamsFromHUD(hud, image).teams, edited);
});
