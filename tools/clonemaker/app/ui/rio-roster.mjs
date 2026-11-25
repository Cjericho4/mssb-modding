// Clone Maker's exchange files bind roster slots to the full game image used by both peers.
export const sides = ["Away", "Home"];
export const positions = ["P", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF"];
export const captainSlots = new Set([0,1,4,6,2,9,5,17,11,3,10,19]);
const format = "mssb-rio-team-v1";
const buildKey = "Clone Maker SHA256";

function integer(value, max) { return Number.isInteger(value) && value >= 0 && value <= max; }
function checkBuild(hash, expected) {
  if (!/^[a-f0-9]{64}$/.test(hash) || hash !== expected)
    throw Error("This team belongs to a different game image. Choose the same finished ISO on both computers.");
}
export function checkTeam(team) {
  if (!team || typeof team.player !== "string" || !team.player.trim() ||
      team.player.trim().length > 30 || ["CPU", "No Player Selected"].includes(team.player.trim()))
    throw Error("Enter the human player's Rio lobby name (up to 30 characters).");
  if (!Array.isArray(team.rows) || team.rows.length !== 9) throw Error("A team needs nine batters.");
  if (team.rows.some(r => !r || !integer(r.id, 53) || !integer(r.pos, 8) ||
      typeof r.star !== "boolean" || typeof r.captain !== "boolean"))
    throw Error("Each batter needs a character slot, a fielding position and valid captain and star settings.");
  if (new Set(team.rows.map(r => r.id)).size !== 9) throw Error("Use each character slot once per team.");
  if (new Set(team.rows.map(r => r.pos)).size !== 9) throw Error("Assign each fielding position once per team.");
  if (team.rows.filter(r => r.captain).length !== 1) throw Error("Select one captain per team.");
  if (!captainSlots.has(team.rows.find(r => r.captain).id))
    throw Error("Choose a captain in one of the game’s twelve captain slots.");
  if (team.rows.some(r => r.label !== undefined && (typeof r.label !== "string" || r.label.length > 64)))
    throw Error("Use a clone label of at most 64 characters.");
  return {player: team.player.trim(), rows: team.rows.map(({id,pos,star,captain,label}) =>
    ({id,pos,star,captain,...(label?.trim() ? {label:label.trim()} : {})}))};
}
export function singleTeam(team, hash) {
  checkBuild(hash, hash);
  return {format, [buildKey]:hash, team:checkTeam(team)};
}
export function unpackTeam(doc, hash) {
  if (doc?.format !== format) throw Error("Choose a team saved with Save team.");
  checkBuild(doc[buildKey], hash);
  return checkTeam(doc.team);
}
export function makeHUD(teams, image) {
  checkBuild(image.sha256, image.sha256);
  const doc = {[buildKey]: image.sha256, TagSetID:-1, Inning:1, "Half Inning":0,
    "Away Batter Roster Loc":0, "Home Batter Roster Loc":0};
  for (const side of sides) {
    const {player, rows} = checkTeam(teams[side]);
    doc[side + " Player"] = player;
    rows.forEach((r, i) => {
      const hands = image.hands[r.id];
      if (!Array.isArray(hands) || hands.length !== 2 || !hands.every(h => integer(h, 1)))
        throw Error("Invalid handedness in the selected game image.");
      doc[side + " Roster " + i] = {CharID:r.id, "Fielding Position":r.pos,
        "Batting Hand":hands[0], "Fielding Hand":hands[1], Captain:Number(r.captain),
        Superstar:Number(r.star), "Defensive Stats":{}, ...(r.label ? {"Clone Maker Label":r.label} : {})};
    });
  }
  if (doc["Away Player"] === doc["Home Player"]) throw Error("Enter two different Rio lobby names.");
  return doc;
}
// Read only the team sheets. Never carry live scores, runners, or a mid-game batting rotation forward.
export function teamsFromHUD(doc, image) {
  if (!doc || typeof doc !== "object") throw Error("Choose a Rio HUD or a Clone Maker matchup.");
  const verified = buildKey in doc;
  if (verified) checkBuild(doc[buildKey], image.sha256);
  const teams = {};
  for (const side of sides) {
    const rows = positions.map((_, i) => {
      const r = doc[side + " Roster " + i];
      if (!r || (r.Captain !== undefined && !integer(r.Captain, 1)) || !integer(r.Superstar, 1)) {
        throw Error("Missing or invalid roster entry for " + side + " batter " + (i+1) + ".");
      }
      return {id:r.CharID,pos:r["Fielding Position"],captain:r.Captain === 1,star:r.Superstar === 1,
        ...(verified ? {label:r["Clone Maker Label"]} : {})};
    });
    teams[side] = checkTeam({player:doc[side+" Player"], rows});
  }
  makeHUD(teams, image);
  return {teams, verified};
}
