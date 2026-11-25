import test from "node:test";
import assert from "node:assert/strict";
import { singleTeam, unpackTeam, makeHUD, teamsFromHUD } from "../ui/rio-roster.mjs";

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
