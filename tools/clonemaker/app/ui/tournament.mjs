import {sides, positions, captainSlots, moveBatter, swapFieldPosition} from "./rio-roster.mjs";
import {loadDraft, saveDraft} from "./game-plan-draft.mjs";
import {playerPool} from "./game-plan-pool.mjs";
import {initPortraits, lineupFaces} from "./roster-portraits.mjs";

const format = "mssb-tournament-team-v1";
const MiB = 1024 * 1024;
const dialog = document.querySelector("#tournament-plan");
const toolbar = dialog.querySelector(".tourney-toolbar");
const editor = dialog.querySelector(".tourney-editor");
const status = dialog.querySelector("[role=status]");
const fieldset = dialog.querySelector("fieldset");
const close = dialog.querySelector(".tourney-close");
const nav = document.querySelector(".pages");
const studioButton = nav.querySelector("[data-open-studio]");
const showPortraits = initPortraits(dialog.querySelector(".portrait-tools"));
const statDialog = dialog.querySelector(".tourney-stat-dialog");
const invoke = window.__TAURI__.core.invoke;
const teams = Object.fromEntries(sides.map((s, t) => [s, {format, player:"", rows:positions.map((_,i) => ({
  source:i + t * 9, pos:i, captain:i === 0, star:false
}))}]));
const selected = {Away:0, Home:0};
const saved = Object.fromEntries(sides.map(s => [s, {player:teams[s].player, rows:JSON.stringify(teams[s].rows)}]));
const libraryChoice = {Away:"", Home:""};
let side = "Away", names = new Map(), busy = false, dragging, opener;
let statFields = new Map();
let library = [], libraryError = "";
let mode = "plan", initialized = false;
let pendingDraft = false, savingDraft = false, draftTimer;

function draftChanged() {
  pendingDraft = true;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(flushDraft, 200);
}
async function flushDraft() {
  clearTimeout(draftTimer);
  if (savingDraft || !pendingDraft) return;
  savingDraft = true;
  try {
    while (pendingDraft) {
      pendingDraft = false;
      await saveDraft({teams, selected, side});
    }
  } catch (error) {
    pendingDraft = true;
    status.textContent = "Could not save your game plan on this device. " + errorText(error);
  } finally { savingDraft = false; }
}

function el(tag, text = "", className = "") {
  const e = document.createElement(tag);
  if (text) e.textContent = text;
  if (className) e.className = className;
  return e;
}
function button(text, callback, className = "") {
  const b = el("button", text, className);
  b.type = "button";
  b.onclick = callback;
  return b;
}
function labeled(caption, control) {
  const label = el("label", caption);
  label.append(control);
  return label;
}
function select(items, current, cls, callback) {
  const s = el("select", "", cls);
  for (const [key, name] of items) s.add(new Option(name, String(key)));
  s.value = String(current);
  s.onchange = () => callback(Number(s.value));
  return s;
}
function checkbox(text, type, checked, disabled, cls, callback) {
  const control = el("input", "", cls);
  control.type = type;
  control.checked = checked;
  control.disabled = disabled;
  control.onchange = () => callback(control.checked);
  const label = el("label", "", "tourney-check");
  label.append(control, text);
  return label;
}
function errorText(error) { return typeof error === "string" ? error : error?.message || String(error); }
async function run(action) {
  if (busy) return;
  busy = true;
  const switchMode = dialog.querySelector(".tourney-switch-mode");
  fieldset.disabled = close.disabled = switchMode.disabled = true;
  status.textContent = "";
  try { await action(); }
  catch (error) { status.textContent = errorText(error); }
  finally { fieldset.disabled = close.disabled = switchMode.disabled = busy = false; }
}
const integer = (v, max) => Number.isInteger(v) && v >= 0 && v <= max;
const record = v => v !== null && typeof v === "object" && !Array.isArray(v);
const title = row => row.label?.trim() || names.get(row.source);
function textureSize(name, encoded) {
  const match = /^t(\d{2,})_([1-9]\d*)x([1-9]\d*)\.png$/.exec(name);
  if (!match || match[1] !== String(Number(match[1])).padStart(2,"0") || +match[1] >= 256 || +match[2] > 1024 || +match[3] > 1024)
    throw Error("Use texture filenames exported by Studio, such as t03_64x64.png (up to 1024 × 1024).");
  if (typeof encoded !== "string" || encoded.length < 32 || encoded.length > 4 * Math.ceil(2 * MiB / 3) ||
      encoded.length % 4 || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded))
    throw Error(name + " must contain one base64 PNG of up to 2 MiB.");
  const bytes = encoded.length / 4 * 3 - (encoded.endsWith("==") ? 2 : Number(encoded.endsWith("=")));
  if (bytes > 2 * MiB) throw Error(name + " exceeds 2 MiB.");
  const hdr = atob(encoded.slice(0,32));
  const u32 = at => Array.from(hdr.slice(at, at + 4), b => b.charCodeAt(0)).reduce((v,b) => v * 256 + b, 0);
  if (hdr.slice(0,8) !== "\x89PNG\r\n\x1a\n" || u32(8) !== 13 || hdr.slice(12,16) !== "IHDR" ||
      u32(16) !== +match[2] || u32(20) !== +match[3]) throw Error(name + " must be a PNG with the pixel dimensions in its filename.");
  return bytes;
}
function validateTextures(rows) {
  let total = 0;
  for (const row of rows) {
    if (row.textures === undefined) continue;
    if (!record(row.textures) || Object.keys(row.textures).length > 64) throw Error("Use up to 64 changed PNGs per character.");
    for (const [name,data] of Object.entries(row.textures)) {
      total += textureSize(name, data);
      if (total > 16 * MiB) throw Error("A team's changed PNGs must fit in 16 MiB.");
    }
  }
}
function validateStats(stats, batter) {
  if (!record(stats)) throw Error("Check the stat overrides for batter " + batter + ".");
  for (const [key, value] of Object.entries(stats)) {
    const field = statFields.get(key);
    if (!field) throw Error("Unknown stat “" + key + "” for batter " + batter + ".");
    if (!Number.isInteger(value) || value < field.min || value > field.max)
      throw Error("Batter " + batter + "’s " + field.label + " must be a whole number from " + field.min + " to " + field.max + ".");
  }
}
function validateTeam(team, draft = false) {
  if (!record(team) || team.format !== format) throw Error("Choose a portable tournament team saved with Save team.");
  if (typeof team.player !== "string" || team.player.length > 30) throw Error("Use a Rio lobby name of up to 30 characters.");
  const player = draft ? team.player : team.player.trim();
  if (!draft && (!player || ["CPU","No Player Selected"].includes(player))) throw Error("Enter the human player's Rio lobby name (up to 30 characters).");
  if (!Array.isArray(team.rows) || team.rows.length !== 9) throw Error("A tournament team needs nine batters.");
  const rows = team.rows.map((r, i) => {
    if (!record(r) || !names.has(r.source) || !integer(r.pos, 8) || typeof r.captain !== "boolean" || typeof r.star !== "boolean")
      throw Error("Check the character, field position and flags for batter " + (i + 1) + ".");
    if (r.label !== undefined && (typeof r.label !== "string" || r.label.length > 64)) throw Error("Use a player label of up to 64 characters.");
    if (r.stats !== undefined) validateStats(r.stats, i + 1);
    const label = draft ? r.label : r.label?.trim();
    return {source:r.source, pos:r.pos, captain:r.captain, star:r.star,
      ...(label ? {label} : {}), ...(r.textures !== undefined ? {textures:r.textures} : {}),
      ...(r.stats && Object.keys(r.stats).length ? {stats:r.stats} : {})};
  });
  if (new Set(rows.map(r=>r.pos)).size !== 9) throw Error("Assign all nine different field positions.");
  const captains = rows.filter(r=>r.captain);
  if ((!draft && captains.length !== 1) || captains.length > 1 || captains.some(r => !captainSlots.has(r.source)))
    throw Error("Choose one captain from the twelve captain characters.");
  validateTextures(rows);
  return {format, player, rows};
}
function readPng(file) {
  if (file.size > 2 * MiB) throw Error(file.name + " exceeds 2 MiB.");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(Error("Could not read " + file.name + "."));
    reader.onload = () => {
      const data = reader.result.slice(reader.result.indexOf(",") + 1);
      try { textureSize(file.name, data); resolve(data); }
      catch (error) { reject(error); }
    };
    reader.readAsDataURL(file);
  });
}
async function refreshLibrary() {
  try {
    library = await invoke("list_tournament_teams");
    libraryError = "";
  } catch (error) {
    libraryError = "Could not load the team library. " + errorText(error);
  }
}
function replaceTeam(team, keepLobbyName) {
  if (keepLobbyName) team.player = teams[side].player;
  else saved[side].player = team.player;
  teams[side] = team;
  saved[side].rows = JSON.stringify(team.rows);
  selected[side] = 0;
}
function libraryPicker() {
  const panel = el("div", "", "tourney-library");
  const picker = el("select", "", "tourney-team-picker");
  picker.add(new Option("Choose a team…", ""));
  for (const [kind, label] of [["preset","ASSBLAST tournament teams"], ["saved","My saved teams"]]) {
    const entries = library.filter(entry => entry.kind === kind);
    if (!entries.length) continue;
    const group = el("optgroup");
    group.label = label;
    for (const entry of entries) group.append(new Option(entry.label, entry.id));
    picker.append(group);
  }
  const confirm = el("div", "", "tourney-replace");
  confirm.setAttribute("aria-live","polite");
  const load = button("Load into " + side, () => run(async () => {
    const entry = library.find(item => item.id === picker.value);
    if (!entry) return;
    const team = validateTeam(await invoke("load_tournament_team", {id:entry.id}));
    const isPreset = entry.kind === "preset";
    const accept = () => {
      replaceTeam(team, isPreset);
      draw();
      status.textContent = entry.label + " loaded into " + side + "." +
        (team.player.trim() ? "" : " Enter your Rio lobby name for this side.");
      const nextFocus = toolbar.querySelector(team.player.trim() ? ".tourney-team-picker" : ".tourney-lobby");
      requestAnimationFrame(() => nextFocus.focus({preventScroll:true}));
    };
    const current = teams[side];
    const replacingEdits = JSON.stringify(current.rows) !== saved[side].rows ||
      (!isPreset && current.player !== saved[side].player && current.player !== team.player);
    if (!replacingEdits) { accept(); return; }
    const keep = button("Keep editing", () => {confirm.replaceChildren(); picker.focus();}, "tourney-keep");
    confirm.replaceChildren(el("span", "Replace your unsaved " + side + " edits with " + entry.label + "?"),
      keep, button("Replace " + side + " team", accept, "tourney-confirm"));
  }), "tourney-load-picked");
  picker.value = libraryChoice[side];
  load.disabled = !picker.value;
  picker.onchange = () => {
    libraryChoice[side] = picker.value;
    load.disabled = !picker.value;
    confirm.replaceChildren();
    status.textContent = "";
  };
  panel.append(labeled("Team library",picker),load,confirm);
  if (libraryError) panel.append(el("p",libraryError));
  return panel;
}
function makeToolbar() {
  const tabs = el("div", "", "tourney-tabs");
  tabs.setAttribute("role","group");
  tabs.setAttribute("aria-label","Team to edit");
  for (const s of sides) {
    const tab = button(s, () => { side = s; status.textContent = ""; draw('.tourney-tabs [data-side="' + s + '"]'); });
    tab.dataset.side = s;
    tab.setAttribute("aria-pressed",String(side === s));
    tabs.append(tab);
  }
  const name = el("input","","tourney-lobby");
  name.maxLength = 30;
  name.placeholder = "Your Rio lobby name";
  name.value = teams[side].player;
  name.oninput = () => { teams[side].player = name.value; status.textContent = ""; draftChanged(); };
  const input = el("input");
  input.type = "file";
  input.accept = ".json,application/json";
  input.hidden = true;
  input.onchange = () => run(async () => {
    const file = input.files[0];
    input.value = "";
    if (!file) return;
    if (file.size > 24 * MiB) throw Error("Choose a portable team under 24 MiB.");
    const team = validateTeam(JSON.parse(await file.text()));
    replaceTeam(team, false);
    libraryChoice[side] = "";
    draw();
    status.textContent = side + " team loaded for " + team.player + ".";
  });
  const load = el("span","","tourney-import");
  load.append(button("Import team file…",()=>input.click()),input);
  const save = button("Save " + side + " team…", () => run(async () => {
    const path = await invoke("save_tournament_team",{team:validateTeam(teams[side])});
    if (path) {
      saved[side] = {player:teams[side].player, rows:JSON.stringify(teams[side].rows)};
      if (mode === "tournament") await refreshLibrary();
      makeToolbar();
      status.textContent = "Saved " + side + " team to " + path + ".";
    }
  }), "tourney-save");
  toolbar.replaceChildren(tabs, labeled("Rio lobby name",name), load, save);
  if (mode === "tournament") toolbar.append(libraryPicker());
}
function move(team, from, to) {
  moveBatter(team,from,to);
  selected[side] = to;
  draw('.tourney-row[data-index="' + to + '"]');
}
function endDrag() {
  dragging = undefined;
  for (const target of editor.querySelectorAll(".drag-over")) target.classList.remove("drag-over");
}
function playerDrag(elem, index, dropPlayer) {
  elem.draggable = true;
  elem.ondragstart = event => {
    if (busy || !event.dataTransfer) { event.preventDefault(); return; }
    dragging = {side, index};
    event.dataTransfer.setData("text/plain", title(teams[side].rows[index]));
    event.dataTransfer.effectAllowed = "move";
  };
  elem.ondragover = event => {
    if (busy || dragging?.side !== side) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = dragging.source === undefined ? "move" : "copy";
    elem.classList.add("drag-over");
  };
  elem.ondragleave = event => {
    if (!elem.contains(event.relatedTarget)) elem.classList.remove("drag-over");
  };
  elem.ondragend = endDrag;
  elem.ondrop = event => {
    if (busy || dragging?.side !== side) return;
    event.preventDefault();
    if (dragging.source !== undefined) chooseSource(index, dragging.source);
    else dropPlayer(dragging.index);
  };
}
function fieldPanel(team, faceFor) {
  const panel = el("section", "", "tourney-field-panel");
  panel.append(el("h3","Starting field positions"), el("p","Drag one fielder onto another to swap positions."));
  const diamond = el("div","","plan-diamond");
  team.rows.forEach((row, i) => {
    const selector = '.tourney-fielder[data-pos="' + row.pos + '"]';
    const marker = button("", () => { selected[side] = i; draw(selector); }, "plan-fielder tourney-fielder");
    marker.dataset.pos = String(row.pos);
    marker.dataset.index = String(i);
    marker.setAttribute("aria-pressed",String(selected[side] === i));
    const badge = el("b",positions[row.pos],"position-badge");
    badge.append(el("span","#" + (i + 1), "field-bat-num"));
    marker.append(faceFor(row),badge,el("span",title(row),"bat-name tourney-name"));
    playerDrag(marker,i,from => {
      swapFieldPosition(team,from,row.pos);
      selected[side] = from;
      draw(selector);
    });
    diamond.append(marker);
  });
  panel.append(diamond);
  return panel;
}
function orderPanel(team, faceFor) {
  const panel = el("section","","tourney-order-panel");
  panel.append(el("h3","Batting order"), el("p","Drag a batter to move their spot."));
  const ol = el("ol","","tourney-order");
  team.rows.forEach((row,i) => {
    const li = el("li");
    const b = button("", () => { selected[side] = i; draw('.tourney-row[data-index="' + i + '"]'); }, "tourney-row");
    b.dataset.index = String(i);
    b.setAttribute("aria-pressed",String(selected[side] === i));
    const flags = el("span","","tourney-flags");
    if (row.captain) flags.append(el("small","Captain","tourney-captain-flag"));
    if (row.star) {
      const star = el("small","★","tourney-star-flag");
      star.setAttribute("aria-label","Superstar");
      flags.append(star);
    }
    b.append(el("span",String(i+1),"tourney-number"),faceFor(row),
      el("strong",title(row),"tourney-name"),flags,el("b",positions[row.pos],"tourney-position"));
    playerDrag(b,i,from => move(team,from,i));
    li.append(b);
    ol.append(li);
  });
  panel.append(ol);
  return panel;
}
function texturePanel(team, row, i) {
  const area = el("div", "", "tourney-textures");
  const entries = Object.keys(row.textures || {}).sort();
  area.append(el("h4","Custom textures"), el("p",entries.length ? entries.length + " changed PNG" + (entries.length===1 ? "" : "s") : "Using the character’s original textures."));
  const files = el("input","","tourney-pngs");
  files.type = "file";
  files.multiple = true;
  files.accept = ".png,image/png";
  files.setAttribute("aria-label", "Add changed PNGs to batter " + (i + 1));
  files.onchange = () => run(async () => {
    const chosen = Array.from(files.files);
    files.value = "";
    if (!chosen.length) return;
    if (chosen.length > 64 || chosen.reduce((sum,f)=>sum+f.size,0) > 16 * MiB)
      throw Error("Choose up to 64 PNGs. A team's changed PNGs must fit in 16 MiB.");
    const next = {...row.textures};
    for (const file of chosen) next[file.name] = await readPng(file);
    validateTextures(team.rows.map((r,n) => n===i ? {...r,textures:next} : r));
    row.textures = next;
    draw();
    status.textContent = "Updated textures for batter " + (i + 1) + ".";
  });
  area.append(labeled("Add changed Studio PNGs",files));
  if (entries.length) {
    const list = el("ul");
    for (const name of entries) {
      const li = el("li");
      const remove = button("Remove", () => {
        const next = {...row.textures};
        delete next[name];
        if (Object.keys(next).length) row.textures = next;
        else delete row.textures;
        draw(".tourney-pngs");
      }, "tourney-remove-png");
      remove.setAttribute("aria-label","Remove " + name);
      li.append(el("span",name),remove);
      list.append(li);
    }
    area.append(list);
  }
  return area;
}
function editStats(row, i) {
  const form = statDialog.querySelector("form");
  const fields = statDialog.querySelector(".tourney-stat-fields");
  statDialog.querySelector("h2").textContent = "Stats for " + title(row) + " · Batter " + (i + 1);
  statDialog.querySelector("#tourney-stat-note").textContent = "Empty fields inherit " + names.get(row.source) + "’s original stats.";
  fields.replaceChildren();
  for (const {key, label, min, max} of statFields.values()) {
    const input = el("input");
    Object.assign(input, {type:"number", name:key, min, max, step:1, placeholder:"Original", value:row.stats?.[key] ?? ""});
    const field = labeled(label, input);
    field.append(el("small", min + "–" + max));
    fields.append(field);
  }
  statDialog.querySelector(".tourney-stat-reset").onclick = () => {
    for (const input of fields.querySelectorAll("input")) input.value = "";
  };
  statDialog.querySelector(".tourney-stat-cancel").onclick = () => statDialog.close();
  form.onsubmit = event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const overrides = {};
    for (const input of fields.querySelectorAll("input")) {
      if (input.value !== "") overrides[input.name] = input.valueAsNumber;
    }
    if (Object.keys(overrides).length) row.stats = overrides;
    else delete row.stats;
    statDialog.close();
    draw(".tourney-edit-stats");
  };
  statDialog.showModal();
}
function chooseSource(i, id) {
  const row = teams[side].rows[i];
  selected[side] = i;
  endDrag();
  if (row.source === id) { draw(".tourney-source"); return; }
  const change = () => {
    row.source = id;
    if (!captainSlots.has(id)) row.captain = false;
    delete row.textures;
    delete row.stats;
    draw(".tourney-source");
  };
  if (!Object.keys(row.textures||{}).length && !Object.keys(row.stats||{}).length) { change(); return; }
  draw();
  const confirmation = editor.querySelector(".source-change");
  const keep = button("Keep character",()=>{confirmation.replaceChildren(); editor.querySelector(".tourney-source").focus();});
  confirmation.replaceChildren(el("p","Switch to " + names.get(id) + " and clear the previous character’s custom textures and stat overrides?"),
    button("Switch character",change),keep);
  keep.focus();
}
function inspector(team, faceFor) {
  const i = selected[side], row = team.rows[i];
  const panel = el("section","","tourney-inspector");
  const member = el("div", "", "tourney-member");
  const head = el("div","","tourney-member-head");
  const caption = el("div","","tourney-batter-caption");
  const activeName = el("h3", title(row));
  caption.append(el("small","Batter " + (i + 1) + " · " + positions[row.pos]), activeName);
  const notice = el("p","","tourney-portrait-note");
  notice.hidden = true;
  notice.setAttribute("aria-live","polite");
  caption.append(notice);
  head.append(faceFor(row,notice),caption);
  member.append(head);
  const fields = el("div","","tourney-fields");
  const confirmation = el("div","","source-change");
  const source = select(names,row.source,"tourney-source",id=>chooseSource(i,id));
  const label = el("input","","tourney-label");
  label.maxLength = 64;
  label.placeholder = "Optional";
  label.value = row.label || "";
  label.oninput = () => {
    row.label = label.value;
    activeName.textContent = title(row);
    draftChanged();
    for (const name of editor.querySelectorAll('[data-index="' + i + '"] .tourney-name')) name.textContent = title(row);
  };
  const bat = select(positions.map((_,n)=>[n,String(n+1)]),i,"tourney-bat",to=>move(team,i,to));
  const pos = select(positions.map((p,n)=>[n,p]),row.pos,"tourney-pos",to=>{swapFieldPosition(team,i,to); draw(".tourney-pos");});
  const captain = checkbox("Captain","radio",row.captain,!captainSlots.has(row.source),"tourney-captain",()=>{
    team.rows.forEach(r=>{r.captain=r===row;}); draw(".tourney-captain");
  });
  const star = checkbox("Superstar","checkbox",row.star,false,"tourney-star",checked=>{row.star=checked; draw(".tourney-star");});
  fields.append(labeled("Character",source),labeled("Player label",label),labeled("Batting spot",bat),
    labeled("Field position",pos),captain,star);
  member.append(fields,confirmation);
  const customization = texturePanel(team,row,i);
  panel.append(member,customization);
  const stats = el("div","","tourney-stats");
  const overrides = Object.entries(row.stats || {});
  stats.append(el("h4","Character stats"));
  if (overrides.length) {
    const details = el("details");
    details.append(el("summary", overrides.length + " stat override" + (overrides.length === 1 ? "" : "s")));
    const list = el("ul");
    for (const [key,value] of overrides) list.append(el("li",statFields.get(key).label + " · " + value));
    details.append(list);
    stats.append(details);
  } else {
    stats.append(el("p","Using the character’s original stats."));
  }
  const edit = button("Edit stats…", () => editStats(row,i), "tourney-edit-stats");
  edit.setAttribute("aria-haspopup","dialog");
  stats.append(edit);
  customization.append(stats);
  return panel;
}
function draw(focus, persist = true) {
  dragging = undefined;
  makeToolbar();
  const team = teams[side];
  const faceFor = lineupFaces(team.rows,names);
  editor.replaceChildren(fieldPanel(team,faceFor),orderPanel(team,faceFor));
  if (mode === "plan") {
    const i = selected[side];
    editor.append(playerPool({
      names, selectedSource:team.rows[i].source, selectedBatter:i + 1, onChoose:id=>chooseSource(i,id),
      onDragStart(event,id) {
        if (busy || !event.dataTransfer) { event.preventDefault(); return; }
        dragging = {side,source:id};
        event.dataTransfer.setData("text/plain",names.get(id));
        event.dataTransfer.effectAllowed = "copy";
      },
      onDragEnd:endDrag
    }));
  }
  editor.append(inspector(team,faceFor));
  if (focus) dialog.querySelector(focus)?.focus({preventScroll:true});
  if (persist) draftChanged();
}
dialog.querySelector(".tourney-build").onclick = () => run(async () => {
  const away = validateTeam(teams.Away), home = validateTeam(teams.Home);
  if (away.player === home.player) throw Error("Enter different Rio lobby names for Away and Home.");
  status.textContent = "Choose your clean retail ISO and an output folder to build the matchup.";
  const stop = await window.__TAURI__.event?.listen("tournament-progress", event => {
    status.textContent = event.payload;
  });
  let result;
  try { result = await invoke("build_tournament_matchup",{away,home}); }
  finally { if (stop) stop(); }
  if (!result) {status.textContent="Build canceled."; return;}
  const location = result.folder || result.isoPath;
  status.replaceChildren(el("strong","Matchup built"),el("p",location),
    el("p","ISO SHA-256 · "+result.sha256),
    el("p","Rio lineup SHA-256 · "+result.hudSha256),
    el("p","Compare both hashes, then open the finished-game roster in Game plan. Load hud.json with the built ISO and select Use in Rio on both computers."));
});
function setCurrentPage(active) {
  for (const button of nav.querySelectorAll("[data-open-studio],[data-open-plan],[data-open-tournament]")) {
    button.classList.toggle("on", button === active);
    if (button === active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
}
function backToStudio() {
  if (!busy && dialog.open) dialog.close();
}
close.onclick = backToStudio;
studioButton.addEventListener("click", backToStudio);
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && dialog.open && !busy && !event.defaultPrevented &&
      !document.querySelector("dialog:modal") && !event.target.closest?.("select,input[type=search]")) {
    event.preventDefault();
    backToStudio();
  }
});
dialog.onclose = () => {
  if (dialog.open) return;
  dragging = undefined;
  flushDraft();
  document.body.classList.remove("planning");
  setCurrentPage(studioButton);
  if (opener?.getClientRects().length) opener.focus();
  else studioButton.focus();
};
window.addEventListener("pagehide", flushDraft);
editor.addEventListener("input", () => { status.textContent = ""; });
async function openMode(nextMode, trigger) {
  if (busy) return;
  if (!dialog.open) {
    opener = trigger;
    document.body.classList.add("planning");
    dialog.show();
  }
  mode = nextMode;
  const planning = mode === "plan";
  dialog.dataset.mode = mode;
  dialog.querySelector("#tourney-title").textContent = planning ? "Game plan" : "Tournament mode";
  setCurrentPage(nav.querySelector(planning ? "[data-open-plan]" : "[data-open-tournament]"));
  showPortraits();
  dialog.querySelector(".tourney-switch-mode").textContent = planning ? "Open in Tournament mode" : "Back to Game plan";
  dialog.querySelector("[data-open-teams]").hidden = !planning;
  dialog.querySelector(".tourney-build").hidden = planning;
  await run(async () => {
    if (!initialized) {
      const [sources, fields] = await Promise.all([invoke("sources"), invoke("stat_schema")]);
      names = new Map(sources.map(s=>[s.id,s.name]));
      statFields = new Map(fields.map(f=>[f.key,f]));
      try {
        const draft = await loadDraft();
        if (draft) {
          const restored = sides.map(s => validateTeam(draft.teams[s], true));
          sides.forEach((s,i) => {teams[s] = restored[i]; selected[s] = integer(draft.selected?.[s],8) ? draft.selected[s] : 0;});
          side = sides.includes(draft.side) ? draft.side : "Away";
        }
      } catch (error) {status.textContent = "Could not restore your game plan. " + errorText(error);}
      initialized = true;
    }
    if (!planning) await refreshLibrary();
    draw(null, false);
  });
}
dialog.querySelector(".tourney-switch-mode").onclick = () => openMode(mode === "plan" ? "tournament" : "plan");
for (const trigger of document.querySelectorAll("[data-open-tournament],[data-open-plan]")) {
  trigger.onclick = () => openMode(trigger.hasAttribute("data-open-plan") ? "plan" : "tournament", trigger);
}
