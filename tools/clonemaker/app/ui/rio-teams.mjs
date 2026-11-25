import {
  sides, positions, captainSlots, singleTeam, unpackTeam, makeHUD, teamsFromHUD,
  moveBatter, swapFieldPosition, replaceBatter
} from "./rio-roster.mjs";

const form = document.getElementById("rio-teams");
const body = form.querySelector(".team-sheets");
const message = form.querySelector("[role=status]");
const imageInfo = form.querySelector(".team-image");
const fieldset = form.querySelector("fieldset");
const close = form.querySelector(".team-close");
const invoke = window.__TAURI__.core.invoke;
const positionNames = ["Pitcher", "Catcher", "First base", "Second base", "Third base",
  "Shortstop", "Left field", "Center field", "Right field"];
const initialTeams = () => Object.fromEntries(sides.map((side, t) => [side, {
  player: "", rows: positions.map((_, i) => ({
    id: t === 0 ? i : i + 9, pos: i, captain: i === 0, star: false
  }))
}]));
const teams = initialTeams();
const selected = {Away: 0, Home: 0};
const queries = {Away: "", Home: ""};
const clonesOnly = {Away: false, Home: false};
let side = "Away", image, names = new Map(), working = false, lastFocus, drag;
let pendingImage, previousDraft;

function node(tag, text = "", className = "") {
  const elem = document.createElement(tag);
  if (text) elem.textContent = text;
  if (className) elem.className = className;
  return elem;
}
function button(text, action, className = "") {
  const b = node("button", text, className);
  b.type = "button";
  b.addEventListener("click", action);
  return b;
}
function select(items, value, label, className, changed) {
  const s = node("select", "", className);
  s.setAttribute("aria-label", label);
  for (const [v, name] of items) s.add(new Option(name, String(v)));
  s.value = String(value);
  s.onchange = () => changed(Number(s.value));
  return s;
}
function labeled(text, input) {
  const label = node("label", text);
  label.append(input);
  return label;
}
function check(text, kind, checked, action) {
  const input = node("input");
  input.type = kind;
  input.checked = checked;
  input.onchange = () => action(input.checked);
  const label = node("label", "", "plan-check");
  label.append(input, document.createTextNode(text));
  return label;
}
function setWorking(value) {
  working = value;
  fieldset.disabled = value;
  close.disabled = value;
}
function showError(error) {
  message.textContent = typeof error === "string" ? error : error.message || String(error);
}
async function run(action) {
  if (working) return;
  setWorking(true);
  message.textContent = "";
  try { await action(); }
  catch (error) { showError(error); }
  finally { setWorking(false); }
}
function edit(action, index, focus) {
  if (working) return;
  try {
    action();
    selected[side] = index;
    message.textContent = "";
    draw(focus);
  } catch (error) { showError(error); }
}

async function readJson(file) {
  if (file.size > 1024 * 1024) throw Error("Choose a team or HUD JSON file under 1 MiB.");
  return JSON.parse(await file.text());
}
function importButton(text, callback) {
  const input = node("input");
  input.type = "file";
  input.accept = ".json,application/json";
  input.hidden = true;
  const span = node("span");
  const b = button(text, () => input.click());
  input.onchange = () => run(async () => {
    const file = input.files[0];
    input.value = "";
    if (file) await callback(await readJson(file));
  });
  span.append(b, input);
  return span;
}
async function save(doc, single) {
  const path = await invoke("save_rio_roster", {json: JSON.stringify(doc, null, 2) + "\n", single});
  if (path) message.textContent = "Saved " + path + (single ? "" : " · Matchup " + await matchupId(doc));
}
async function matchupId(doc) {
  const data = new TextEncoder().encode(JSON.stringify(doc));
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest).slice(0, 8), b => b.toString(16).padStart(2, "0")).join("");
}

const slotName = id => names.get(id) || "Slot " + id;
function cloneSource(id, game = image) {
  const source = game.cloneSources?.[id];
  return Number.isInteger(source) && source !== id ? source : null;
}
function displayName(id) {
  const source = cloneSource(id);
  return source === null ? slotName(id) : slotName(source) + " · #" + id;
}
const playerName = row => row.label?.trim() || displayName(row.id);
function slotOption(id) {
  const name = cloneSource(id) === null ? slotName(id) + " (#" + id + ")" :
    displayName(id) + " (" + slotName(id) + " slot)";
  return [id, name];
}
function matchesSearch(id, filter) {
  const source = cloneSource(id);
  const words = [slotName(id), "#" + id];
  if (source !== null) words.push(slotName(source), "clone");
  if (id === 2 || source === 2) words.push("DK");
  const text = words.join(" ").toLowerCase();
  return filter.split(/\s+/).every(term => text.includes(term));
}
function addPortrait(elem, id, game = image) {
  if (!game.portraits?.[id]) return;
  const pic = node("img", "", "plan-portrait");
  pic.alt = "";
  pic.draggable = false;
  pic.width = pic.height = 32;
  pic.src = "data:image/png;base64," + game.portraits[id];
  elem.append(pic);
}
function endDrag() {
  drag = undefined;
  for (const elem of body.querySelectorAll(".drag-over, .is-dragging"))
    elem.classList.remove("drag-over", "is-dragging");
}
function dragSource(elem, kind, index) {
  elem.draggable = true;
  elem.addEventListener("dragstart", event => {
    if (working || !event.dataTransfer) { event.preventDefault(); return; }
    endDrag();
    drag = {kind, index, side};
    event.dataTransfer.effectAllowed = kind === "pool" ? "copy" : "move";
    event.dataTransfer.setData("text/plain", elem.getAttribute("aria-label") || elem.textContent);
    elem.classList.add("is-dragging");
  });
  elem.addEventListener("dragend", endDrag);
}
function dropTarget(elem, index, onField) {
  elem.addEventListener("dragover", event => {
    if (working || !drag || drag.side !== side) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = drag.kind === "pool" ? "copy" : "move";
    elem.classList.add("drag-over");
  });
  elem.addEventListener("dragleave", event => {
    if (!elem.contains(event.relatedTarget)) elem.classList.remove("drag-over");
  });
  elem.addEventListener("drop", event => {
    if (working || !drag || drag.side !== side) return;
    event.preventDefault();
    const source = drag;
    endDrag();
    const team = teams[side];
    if (source.kind === "pool") {
      edit(() => replaceBatter(team, index, source.index), index, rowSelector(index));
    } else if (onField) {
      const pos = team.rows[index].pos;
      edit(() => swapFieldPosition(team, source.index, pos), source.index, fieldSelector(pos));
    } else {
      edit(() => moveBatter(team, source.index, index), index, rowSelector(index));
    }
  });
}
const rowSelector = i => '.plan-batter[data-index="' + i + '"]';
const fieldSelector = p => '.plan-fielder[data-pos="' + p + '"]';

function toolbar(team) {
  const bar = node("div", "", "plan-toolbar");
  const tabs = node("div", "", "plan-tabs");
  tabs.setAttribute("role", "group");
  tabs.setAttribute("aria-label", "Team to edit");
  for (const s of sides) {
    const tab = button(s, () => {
      side = s;
      message.textContent = "";
      draw('.plan-tabs button[data-side="' + s + '"]');
    });
    tab.dataset.side = s;
    tab.setAttribute("aria-pressed", String(s === side));
    tabs.append(tab);
  }
  const name = node("input", "", "lobby-name");
  name.type = "text";
  name.value = team.player;
  name.maxLength = 30;
  name.placeholder = "Rio player name";
  name.oninput = () => { team.player = name.value; };
  const load = importButton("Load team…", doc => {
    teams[side] = unpackTeam(doc, image.sha256);
    selected[side] = 0;
    draw();
    message.textContent = side + " team loaded.";
  });
  const saveSide = button("Save " + side + " team…", () => run(() => save(singleTeam(team, image.sha256), true)));
  bar.append(tabs, labeled("Rio lobby name", name), load, saveSide);
  return bar;
}
function panel(title, hint, className) {
  const section = node("section", "", className);
  section.append(node("h3", title), node("p", hint, "plan-hint"));
  return section;
}
function fieldPanel(team, current) {
  const section = panel("Starting field positions", "Drop one fielder on another to swap positions.", "plan-field-panel");
  const diamond = node("div", "", "plan-diamond");
  for (const [index, row] of team.rows.entries()) {
    const selectFielder = () => { selected[side] = index; draw(fieldSelector(row.pos)); };
    const marker = button("", selectFielder, "plan-fielder");
    marker.dataset.pos = String(row.pos);
    marker.dataset.index = String(index);
    marker.setAttribute("aria-label", positionNames[row.pos] + ", " + playerName(row) + ", batting " + (index + 1));
    marker.setAttribute("aria-pressed", String(index === current));
    marker.title = playerName(row) + " · " + positionNames[row.pos] + " · bats " + (index + 1);
    addPortrait(marker, row.id);
    marker.append(node("b", positions[row.pos], "position-badge"), node("span", playerName(row), "bat-name"));
    dragSource(marker, "player", index);
    dropTarget(marker, index, true);
    diamond.append(marker);
  }
  section.append(diamond);
  return section;
}
function orderPanel(team, current) {
  const section = panel("Batting order", "Drag a player to a batting spot to reorder.", "plan-order-panel");
  const order = node("ol", "", "plan-order");
  for (const [index, row] of team.rows.entries()) {
    const li = node("li");
    const item = button("", () => { selected[side] = index; draw(rowSelector(index)); }, "plan-batter");
    item.dataset.index = String(index);
    item.setAttribute("aria-pressed", String(index === current));
    item.setAttribute("aria-label", "Batter " + (index + 1) + ", " + playerName(row) + ", " + positions[row.pos]);
    item.append(node("span", String(index + 1), "bat-number"));
    addPortrait(item, row.id);
    item.append(node("span", playerName(row), "bat-name"), node("b", positions[row.pos], "position-badge"));
    const grip = node("span", "⠿", "grip");
    grip.setAttribute("aria-hidden", "true");
    item.append(grip);
    dragSource(item, "player", index);
    dropTarget(item, index, false);
    li.append(item);
    order.append(li);
  }
  section.append(order);
  return section;
}
function inspector(team, i, available) {
  const row = team.rows[i];
  const section = node("section", "", "plan-inspector");
  section.append(node("h3", "Edit batter " + (i + 1)));
  const controls = node("div", "", "plan-controls");
  const character = select([...available, row.id].sort((a, b) => a - b).map(slotOption),
    row.id, "Character slot", "character-slot", id => edit(() => replaceBatter(team, i, id), i, ".character-slot"));
  const nickname = node("input", "", "roster-label");
  nickname.maxLength = 64;
  nickname.value = row.label || "";
  nickname.placeholder = "Optional";
  nickname.oninput = () => {
    row.label = nickname.value;
    // Refresh both names without interrupting the text field's selection or caret.
    for (const elem of [body.querySelector(rowSelector(i)), body.querySelector(fieldSelector(row.pos))]) {
      elem.querySelector(".bat-name").textContent = playerName(row);
      const desc = playerName(row) + " · " + positionNames[row.pos] + " · bats " + (i + 1);
      elem.setAttribute("aria-label", desc);
      elem.title = desc;
    }
  };
  const bat = select(positions.map((_, n) => [n, String(n + 1)]), i,
    "Batting spot", "batting-slot", to => edit(() => moveBatter(team, i, to), to, ".batting-slot"));
  const field = select(positionNames.map((s, n) => [n, positions[n] + " · " + s]), row.pos,
    "Fielding position", "fielding-slot", pos => edit(() => swapFieldPosition(team, i, pos), i, ".fielding-slot"));
  const captain = check("Captain", "radio", row.captain, () => {
    team.rows.forEach(r => { r.captain = r === row; });
  });
  const captainInput = captain.querySelector("input");
  captainInput.disabled = !captainSlots.has(row.id);
  captainInput.name = "team-captain-" + side;
  if (captainInput.disabled) captain.title = "Choose a character in one of the twelve captain slots.";
  const star = check("Superstar", "checkbox", row.star, checked => { row.star = checked; });
  controls.append(labeled("Character slot", character), labeled("Clone label", nickname),
    labeled("Batting spot", bat), labeled("Fielding position", field), captain, star);
  section.append(controls);
  return section;
}
function characterPool(team, i, available) {
  const section = panel("Character pool", "Drag a character onto a player, or click to replace batter " + (i + 1) + ".", "plan-pool");
  const search = node("input", "", "pool-search");
  search.type = "search";
  search.placeholder = "Find a character";
  search.setAttribute("aria-label", "Find a character");
  search.value = queries[side];
  const filters = node("div", "", "pool-filters");
  const onlyClones = check("Clones only", "checkbox", clonesOnly[side], checked => {
    clonesOnly[side] = checked;
    fill();
  });
  onlyClones.querySelector("input").className = "pool-clones";
  filters.append(search, onlyClones);
  const options = node("div", "", "pool-options");
  const fill = () => {
    options.replaceChildren();
    const filter = search.value.toLowerCase().trim();
    for (const id of available) {
      const source = cloneSource(id);
      if (clonesOnly[side] && source === null) continue;
      if (!matchesSearch(id, filter)) continue;
      const tile = button("", () => edit(() => replaceBatter(team, i, id), i, ".pool-search"));
      tile.dataset.character = String(id);
      tile.setAttribute("aria-label", "Replace batter " + (i + 1) + " with " + slotOption(id)[1]);
      addPortrait(tile, id);
      tile.title = slotOption(id)[1];
      const name = node("span", slotName(source ?? id), "pool-name");
      name.append(node("small", (source === null ? "#" : "Clone #") + id));
      tile.append(name);
      dragSource(tile, "pool", id);
      options.append(tile);
    }
    if (!options.childElementCount) options.append(node("p", "No available characters match."));
  };
  search.oninput = () => { queries[side] = search.value; fill(); };
  fill();
  const help = node("p", "For another copy of the same character, add a clone in Studio, build the ISO, then choose that finished ISO here.", "pool-help plan-hint");
  const studio = button("Back to Studio", () => form.close(), "pool-studio");
  help.append(" ", studio);
  section.append(filters, options, help);
  return section;
}
function draw(focus) {
  endDrag();
  body.replaceChildren();
  if (!image) return;
  if (pendingImage) {
    body.append(imageChangePanel(pendingImage));
    return;
  }
  const team = teams[side], i = selected[side];
  const inUse = new Set(team.rows.map(r => r.id));
  const available = [...names.keys()].filter(id => !inUse.has(id));
  const panels = node("div", "", "plan-workspace");
  panels.append(fieldPanel(team, i), orderPanel(team, i));
  body.append(toolbar(team), panels, inspector(team, i, available), characterPool(team, i, available));
  if (focus) body.querySelector(focus)?.focus({preventScroll:true});
}
function hasDraftEdits() {
  const defaults = initialTeams();
  return sides.some(s => teams[s].player !== "" || teams[s].rows.some((row, i) => {
    const original = defaults[s].rows[i];
    return row.id !== original.id || row.pos !== original.pos || row.star !== original.star ||
      row.captain !== original.captain || Boolean(row.label);
  }));
}
function snapshot() {
  const copy = Object.fromEntries(sides.map(s => [s, {
    player: teams[s].player, rows: teams[s].rows.map(row => ({...row}))
  }]));
  return {image, teams: copy, side, selected: {...selected}, queries: {...queries}, clonesOnly: {...clonesOnly}};
}
function occupiedChanges(next) {
  const changes = [];
  for (const s of sides) {
    teams[s].rows.forEach((row, index) => {
      const id = row.id;
      const before = cloneSource(id) ?? id, after = cloneSource(id, next) ?? id;
      const sourceChanged = image.cloneSources?.length > id && next.cloneSources?.length > id && before !== after;
      const oldPic = image.portraits?.[id], newPic = next.portraits?.[id];
      const portraitChanged = Boolean(oldPic && newPic && oldPic !== newPic);
      if (sourceChanged || portraitChanged) changes.push({side:s, index, row, before, after, portraitChanged});
    });
  }
  return changes;
}
const imageName = game => game.name + " (" + game.sha256.slice(0, 8) + ")";
function imageChangePanel(next) {
  const section = panel("Use this rebuilt ISO?", "Current: " + imageName(image) + " · Selected: " + imageName(next), "plan-image-change plan-inspector");
  section.append(node("p", "Carry both lineups to keep every batting spot and field position. The previous ISO draft stays available in this session."));
  const changes = occupiedChanges(next);
  if (changes.length) {
    section.append(node("h4", "Occupied slots that changed"));
    const list = node("ul", "", "changed-slots");
    for (const {side:s, index, row, before, after, portraitChanged} of changes) {
      const li = node("li");
      li.dataset.side = s;
      li.dataset.index = String(index);
      li.append(s + " batter " + (index + 1) + " · " + positions[row.pos] + " · #" + row.id + " · ");
      addPortrait(li, row.id, image);
      li.append(slotName(before) + " → ");
      addPortrait(li, row.id, next);
      li.append(slotName(after));
      if (before === after && portraitChanged) li.append(" (portrait changed)");
      if (row.label?.trim()) li.append(" · Clear label “" + row.label + "”");
      list.append(li);
    }
    section.append(list);
  } else {
    section.append(node("p", "No occupied slot showed a change in the available source or portrait data."));
  }
  const actions = node("div", "", "plan-toolbar");
  actions.append(
    button("Carry both lineups", () => {
      previousDraft = snapshot();
      for (const change of changes) delete change.row.label;
      activateImage(next, "Both lineups now use " + imageName(next) + ". Save the matchup for this ISO.");
    }, "carry-teams"),
    button("Start fresh", () => {
      previousDraft = snapshot();
      Object.assign(teams, initialTeams());
      for (const s of sides) { selected[s] = 0; queries[s] = ""; clonesOnly[s] = false; }
      side = "Away";
      activateImage(next, "Started two new teams for " + imageName(next) + ".");
    }, "fresh-teams"),
    button("Keep previous ISO", () => activateImage(image, "Kept both lineups on " + imageName(image) + "."), "keep-iso")
  );
  section.append(actions);
  return section;
}
function restorePrevious() {
  const restore = previousDraft;
  previousDraft = snapshot();
  Object.assign(teams, restore.teams);
  Object.assign(selected, restore.selected);
  Object.assign(queries, restore.queries);
  Object.assign(clonesOnly, restore.clonesOnly);
  side = restore.side;
  activateImage(restore.image, "Restored both lineups for " + imageName(restore.image) + ".");
}
function updateImageControls() {
  const deciding = Boolean(pendingImage);
  form.querySelector(".team-actions").hidden = !image || deciding;
  pick.hidden = deciding;
  previous.hidden = deciding || !previousDraft;
  if (previousDraft) previous.title = "Return to " + imageName(previousDraft.image) + " with its saved draft.";
}
function activateImage(next, notice) {
  pendingImage = undefined;
  image = next;
  imageInfo.textContent = image.name + " · SHA-256 " + image.sha256;
  updateImageControls();
  draw();
  message.textContent = notice;
}
export function showTeamImage(next) {
  if (image && image.sha256 !== next.sha256 && hasDraftEdits()) {
    pendingImage = next;
    updateImageControls();
    draw();
    message.textContent = "Choose how to use your existing teams with this ISO.";
    return;
  }
  // If both teams are untouched, switching the disc is enough to use its slots and portraits.
  activateImage(next, "Team slots and handedness will use this game image.");
}

const pick = button("Choose finished ISO…", () => run(async () => {
  message.textContent = "Reading the finished game and calculating its SHA-256…";
  const next = await invoke("rio_team_image");
  if (next) showTeamImage(next);
  else message.textContent = "";
}));
const previous = button("Previous ISO draft", restorePrevious, "previous-iso");
previous.hidden = true;
form.querySelector(".team-pick").append(pick, previous);
form.querySelector(".team-actions").append(
  importButton("Load matchup…", async doc => {
    const loaded = teamsFromHUD(doc, image);
    Object.assign(teams, loaded.teams);
    for (const s of sides) selected[s] = 0;
    draw();
    message.textContent = loaded.verified ? "Game image verified · Matchup " + await matchupId(makeHUD(teams, image)) :
      "Imported rosters from Rio. Check the character slots against your game before saving.";
  }),
  button("Save matchup…", () => run(() => save(makeHUD(teams, image), false))),
  button("Use in Rio…", () => run(async () => {
    const doc = makeHUD(teams, image);
    const result = await invoke("install_rio_matchup", {json: JSON.stringify(doc, null, 2) + "\n"});
    if (result) message.textContent = "Matchup " + await matchupId(doc) + " · " + result;
  }))
);
close.onclick = () => form.close();
body.addEventListener("input", () => { message.textContent = ""; });
form.addEventListener("close", () => {
  if (pendingImage) activateImage(image, "");
  endDrag();
  lastFocus?.focus();
});
form.addEventListener("cancel", event => { if (working) event.preventDefault(); });
for (const trigger of document.querySelectorAll("[data-open-teams]")) {
  trigger.onclick = async () => {
    if (form.open || working) return;
    lastFocus = trigger;
    setWorking(true);
    try {
      const sources = await invoke("sources");
      names = new Map(sources.map(source => [source.id, source.name]));
      draw();
    } catch (error) { showError(error); }
    finally { setWorking(false); form.showModal(); }
  };
}
