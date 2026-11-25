import {sides, positions, captainSlots, moveBatter, swapFieldPosition} from "./rio-roster.mjs";
import {loadDraft, saveDraft} from "./game-plan-draft.mjs";
import {playerPool, resetPoolSearch} from "./game-plan-pool.mjs";
import {initPortraits, lineupFaces} from "./roster-portraits.mjs";
import {initPlannerGuide} from "./planner-guide.mjs";

const format = "mssb-tournament-team-v1";
const matchupFormat = "mssb-tournament-matchup-v1";
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
const guide = initPlannerGuide(dialog);
const statDialog = dialog.querySelector(".tourney-stat-dialog");
const invoke = window.__TAURI__.core.invoke;
const emptyRow = pos => ({source:null, pos, captain:false, star:false});
const emptyTeam = () => ({format, player:"", rows:positions.map((_,i) => emptyRow(i))});
const teams = Object.fromEntries(sides.map(s => [s, emptyTeam()]));
const selected = {Away:0, Home:0};
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
const title = row => row.label?.trim() || names.get(row.source) || "Open spot";
const filledCount = team => team.rows.filter(row => row.source !== null).length;
const hasSheet = team => Boolean(team.player) || team.rows.some((row,i) => row.source !== null || row.pos !== i);
const humanLobbyName = name => Boolean(name.trim()) && !["CPU","No Player Selected"].includes(name.trim());
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
  if (typeof team.player !== "string" || team.player.length > 30 || /\p{Cc}/u.test(team.player)) throw Error("Use a Rio lobby name of up to 30 characters without control characters.");
  const player = draft ? team.player : team.player.trim();
  if (!Array.isArray(team.rows) || team.rows.length !== 9) throw Error("A tournament team needs nine batters.");
  const rows = team.rows.map((r, i) => {
    if (!record(r) || (r.source !== null && !names.has(r.source)) || !integer(r.pos, 8) || typeof r.captain !== "boolean" || typeof r.star !== "boolean")
      throw Error("Check the character, field position and flags for batter " + (i + 1) + ".");
    if (r.source === null) {
      if (r.captain || r.star || (r.label !== undefined && r.label !== "") ||
          (r.textures !== undefined && (!record(r.textures) || Object.keys(r.textures).length)) ||
          (r.stats !== undefined && (!record(r.stats) || Object.keys(r.stats).length)))
        throw Error("Empty batting spot " + (i + 1) + " contains player details. Choose a character for that spot first.");
      return emptyRow(r.pos);
    }
    if (r.label !== undefined && (typeof r.label !== "string" || r.label.length > 64 || /\p{Cc}/u.test(r.label))) throw Error("Use a player label of up to 64 characters without control characters.");
    if (r.stats !== undefined) validateStats(r.stats, i + 1);
    const label = draft ? r.label : r.label?.trim();
    return {source:r.source, pos:r.pos, captain:r.captain, star:r.star,
      ...(label ? {label} : {}), ...(r.textures !== undefined ? {textures:r.textures} : {}),
      ...(r.stats && Object.keys(r.stats).length ? {stats:r.stats} : {})};
  });
  if (new Set(rows.map(r=>r.pos)).size !== 9) throw Error("Assign all nine different field positions.");
  const captains = rows.filter(r=>r.captain);
  if (captains.length > 1 || captains.some(r => !captainSlots.has(r.source)))
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
function replaceTeam(which, team) {
  resetPoolSearch();
  teams[which] = team;
  selected[which] = 0;
  libraryChoice[which] = "";
}
function confirmReplace(needed, message, actionLabel, action, returnTo) {
  if (!needed) { action(); return; }
  const panel = toolbar.querySelector(".tourney-replace");
  const cancel = button("Keep editing", () => {
    panel.replaceChildren();
    returnTo?.focus();
  }, "tourney-keep");
  panel.replaceChildren(el("span", message), cancel, button(actionLabel, action, "tourney-confirm"));
  requestAnimationFrame(() => { if (cancel.isConnected) cancel.focus(); });
}
function loadSheet(team, label, trigger) {
  confirmReplace(hasSheet(teams[side]), "Replace the current " + side + " sheet with " + label + "?",
    "Replace " + side + " team", () => {
      replaceTeam(side,team);
      draw('.tourney-tabs [data-side="' + side + '"]');
      status.textContent = label + " loaded into " + side + ".";
    }, trigger);
}
function replaceMatchup(away, home) {
  replaceTeam("Away",away);
  replaceTeam("Home",home);
  side = "Away";
  draw('.tourney-tabs [data-side="Away"]');
}
function libraryPicker() {
  const panel = el("div", "", "tourney-library");
  const picker = el("select", "", "tourney-team-picker");
  const entries = library.filter(entry => entry.kind === "saved");
  picker.add(new Option(entries.length ? "Choose a saved team…" : "No saved teams yet", ""));
  if (entries.length) {
    const group = el("optgroup");
    group.label = "My saved teams";
    for (const entry of entries) group.append(new Option(entry.label, entry.id));
    picker.append(group);
  }
  picker.disabled = !entries.length;
  const load = button("Load into " + side, () => run(async () => {
    const entry = entries.find(item => item.id === picker.value);
    if (entry) loadSheet(validateTeam(await invoke("load_tournament_team", {id:entry.id})), entry.label, picker);
  }), "tourney-load-picked");
  picker.value = libraryChoice[side];
  load.disabled = !picker.value;
  picker.onchange = () => {
    libraryChoice[side] = picker.value;
    load.disabled = !picker.value;
    toolbar.querySelector(".tourney-replace").replaceChildren();
    status.textContent = "";
  };
  panel.append(labeled("Team library",picker),load);
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
  name.placeholder = "Optional for now";
  name.title = "Enter the " + side + " player’s Project Rio username before building the game.";
  name.value = teams[side].player;
  name.oninput = () => { teams[side].player = name.value; name.setCustomValidity(""); status.textContent = ""; draftChanged(); };
  const input = el("input");
  input.type = "file";
  input.accept = ".json,application/json";
  input.hidden = true;
  input.onchange = () => run(async () => {
    const file = input.files[0];
    input.value = "";
    if (!file) return;
    if (file.size > 48 * MiB) throw Error("Choose a matchup under 48 MiB or a team sheet under 24 MiB.");
    let doc;
    try { doc = JSON.parse(await file.text()); }
    catch { throw Error("Choose a team or matchup JSON file saved by Clone Maker."); }
    const trigger = load.querySelector("button");
    if (doc?.format === matchupFormat) {
      const away = validateTeam(doc.away), home = validateTeam(doc.home);
      confirmReplace(sides.some(s => hasSheet(teams[s])), "Replace both the Away and Home sheets with " + file.name + "?",
        "Import both teams", () => {
          replaceMatchup(away,home);
          status.textContent = "Imported Away and Home from " + file.name + ".";
        }, trigger);
    } else {
      if (file.size > 24 * MiB) throw Error("Choose a team sheet under 24 MiB.");
      loadSheet(validateTeam(doc), file.name, trigger);
    }
  });
  const load = el("span","","tourney-import");
  load.append(button("Import team or matchup…",()=>input.click()),input);
  const save = button("Save " + side + " team…", () => run(async () => {
    const path = await invoke("save_tournament_team",{team:validateTeam(teams[side]),side});
    if (path) {
      if (mode === "tournament") await refreshLibrary();
      makeToolbar();
      status.textContent = "Saved " + side + " team to " + path + ". Reopen it in Tournament mode under Team library → My saved teams.";
    }
  }), "tourney-save");
  const saveMatch = button("Save matchup…", () => run(async () => {
    const path = await invoke("save_tournament_matchup_sheet", {
      away:validateTeam(teams.Away), home:validateTeam(teams.Home)
    });
    if (path) status.textContent = "Saved both teams to " + path + ". Use Import team or matchup… to reopen them together.";
  }), "tourney-save-matchup");
  const newSheets = el("details","","tourney-new-sheets");
  const newMenu = el("summary","Start new…");
  const newTeam = button("New " + side + " sheet…", () => {
    newSheets.open = false;
    confirmReplace(hasSheet(teams[side]), "Start an empty " + side + " sheet in place of this lineup?",
      "Start empty " + side + " sheet", () => {
        replaceTeam(side,emptyTeam());
        draw(".tourney-source");
        status.textContent = "Started an empty " + side + " team sheet.";
      }, newMenu);
  }, "tourney-new-team");
  const newMatch = button("New matchup…", () => {
    newSheets.open = false;
    confirmReplace(sides.some(s => hasSheet(teams[s])), "Start a new matchup with empty Away and Home sheets? Save any sheets you want to keep first.",
      "Start new matchup", () => {
        replaceMatchup(emptyTeam(),emptyTeam());
        status.textContent = "New matchup. Choose a character for the first Away batting spot or import a team file.";
      }, newMenu);
  }, "tourney-new-matchup");
  const choices = el("div","","tourney-new-choices");
  choices.append(newTeam,newMatch);
  newSheets.append(newMenu,choices);
  toolbar.replaceChildren(el("span","Edit team","tourney-side-label"), tabs, labeled("Rio lobby name",name), newSheets, load, save, saveMatch);
  if (mode === "tournament") toolbar.append(libraryPicker());
  const confirmation = el("div", "", "tourney-replace");
  confirmation.setAttribute("aria-live","polite");
  toolbar.append(confirmation);
  guide.update(side);
}
function move(team, from, to) {
  moveBatter(team,from,to);
  selected[side] = to;
  draw('.tourney-row[data-index="' + to + '"] .tourney-pick');
}
function endDrag() {
  dragging = undefined;
  for (const target of editor.querySelectorAll(".drag-over")) target.classList.remove("drag-over");
}
function playerDrag(elem, index, dropPlayer) {
  elem.draggable = teams[side].rows[index].source !== null;
  elem.ondragstart = event => {
    if (busy || !elem.draggable || !event.dataTransfer) { event.preventDefault(); return; }
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
function showCardName(card, row, i) {
  const name = title(row);
  const player = name + ", batter " + (i + 1) + ", " + positions[row.pos];
  const label = card.querySelector(".tourney-name");
  label.textContent = name;
  label.setAttribute("aria-label", "Rename " + player);
  label.title = "Rename " + name;
  card.querySelector(".tourney-pick").setAttribute("aria-label", "Select " + player);
}
function setPlayerName(row, i, value) {
  if (value) row.label = value;
  else delete row.label;
  for (const card of editor.querySelectorAll('.tourney-row[data-index="' + i + '"], .tourney-fielder[data-index="' + i + '"]')) {
    showCardName(card, row, i);
  }
  if (selected[side] === i) {
    editor.querySelector(".tourney-batter-caption h3").textContent = title(row);
    const label = editor.querySelector(".tourney-label");
    if (label.value !== value) label.value = value;
  }
  draftChanged();
}
function editPlayerName(selector, i) {
  status.textContent = "";
  if (selected[side] !== i) {
    selected[side] = i;
    draw();
  }
  const card = editor.querySelector(selector);
  const slot = card.querySelector(".tourney-name-slot");
  const trigger = slot.querySelector(".tourney-name");
  const row = teams[side].rows[i];
  const original = row.label || "";
  const input = el("input", "", "tourney-name-edit");
  input.type = "text";
  input.maxLength = 64;
  input.placeholder = names.get(row.source);
  input.value = original;
  input.setAttribute("aria-label", "Player name for batter " + (i + 1) + ", " + positions[row.pos]);
  input.oninput = () => setPlayerName(row, i, input.value);
  input.onblur = () => {
    input.remove();
    slot.classList.remove("editing");
    card.draggable = true;
  };
  input.onkeydown = event => {
    if (event.key !== "Escape" && event.key !== "Enter") return;
    event.stopPropagation();
    if (event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (event.key === "Escape") setPlayerName(row, i, original);
    input.blur();
    trigger.focus({preventScroll:true});
  };
  // A draggable ancestor captures text selection gestures in WebKit.
  card.draggable = false;
  slot.classList.add("editing");
  slot.append(input);
  input.focus({preventScroll:true});
  input.select();
}
function playerCard(row, i, type) {
  const field = type === "tourney-fielder";
  const selector = "." + type + '[data-index="' + i + '"]';
  const card = el("div", "", type);
  card.dataset.index = i;
  if (field) {
    card.classList.add("plan-fielder");
    card.dataset.pos = row.pos;
  }
  const pick = el("button", "", "tourney-pick");
  pick.type = "button";
  pick.setAttribute("aria-pressed", String(selected[side] === i));
  card.classList.toggle("is-selected", selected[side] === i);
  const slot = el("span", "", field ? "tourney-name-slot bat-name" : "tourney-name-slot");
  const vacant = row.source === null;
  card.classList.toggle("is-empty", vacant);
  card.append(pick, slot);
  if (vacant) {
    slot.append(el("span", "Add player", "tourney-name"));
    pick.setAttribute("aria-label", "Add player to batting spot " + (i + 1) + ", " + positions[row.pos]);
  } else {
    slot.append(button("", () => editPlayerName(selector, i), "tourney-name"));
    showCardName(card, row, i);
  }
  card.onclick = event => {
    if (!vacant && event.target.closest(".tourney-name-slot")) return;
    selected[side] = i;
    draw(vacant ? ".tourney-source" : selector + " .tourney-pick");
    if (vacant) editor.querySelector(".tourney-source").scrollIntoView({block:"nearest"});
  };
  return card;
}
function fieldPanel(team, faceFor) {
  const panel = el("section", "", "tourney-field-panel");
  panel.append(el("h3","Starting field positions"), el("p", filledCount(team) < 9
    ? "Click an open position to add a player. Drag portraits to swap positions." : "Drag to swap positions. Click a name to edit."));
  const diamond = el("div","","plan-diamond");
  team.rows.forEach((row, i) => {
    const selector = '.tourney-fielder[data-pos="' + row.pos + '"]';
    const marker = playerCard(row, i, "tourney-fielder");
    const badge = el("b",positions[row.pos],"position-badge");
    badge.append(el("span","#" + (i + 1), "field-bat-num"));
    marker.append(faceFor(row),badge);
    playerDrag(marker,i,from => {
      swapFieldPosition(team,from,row.pos);
      selected[side] = from;
      draw(selector + " .tourney-pick");
    });
    diamond.append(marker);
  });
  panel.append(diamond);
  return panel;
}
function orderPanel(team, faceFor) {
  const panel = el("section","","tourney-order-panel");
  const heading = el("h3","Batting order");
  heading.append(el("small", filledCount(team) + " of 9 filled", "tourney-count"));
  panel.append(heading, el("p", filledCount(team) ? "Drag to reorder. Click a name to edit." : "Choose an open batting spot or import a team file."));
  const ol = el("ol","","tourney-order");
  team.rows.forEach((row,i) => {
    const li = el("li");
    const card = playerCard(row, i, "tourney-row");
    const flags = el("span","","tourney-flags");
    if (row.captain) flags.append(el("small","Captain","tourney-captain-flag"));
    if (row.star) {
      const star = el("small","★","tourney-star-flag");
      star.setAttribute("aria-label","Superstar");
      flags.append(star);
    }
    card.append(el("span",String(i+1),"tourney-number"),faceFor(row),
      flags,el("b",positions[row.pos],"tourney-position"));
    playerDrag(card,i,from => move(team,from,i));
    li.append(card);
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
function chooseSource(i, id, advance = false) {
  const row = teams[side].rows[i];
  selected[side] = i;
  endDrag();
  if (row.source === id) { draw(".tourney-source"); return; }
  const change = () => {
    // Presets often spell out the default character name as a label. A substitution should
    // change that name too; a nickname the player chose stays with the roster spot.
    if (row.source !== null && row.label?.trim().toLowerCase() === names.get(row.source).toLowerCase()) delete row.label;
    row.source = id;
    if (!captainSlots.has(id)) row.captain = false;
    delete row.textures;
    delete row.stats;
    if (advance) {
      const next = Array.from({length:8},(_,n) => (i + n + 1) % 9).find(n => teams[side].rows[n].source === null);
      if (next !== undefined) selected[side] = next;
    }
    draw(advance && selected[side] !== i ? '.draft-player-choice[data-character="' + id + '"]' : ".tourney-source");
    if (advance) {
      status.textContent = names.get(id) + " added to batting spot " + (i + 1) + ". " +
        (selected[side] !== i ? "Next open spot: " + (selected[side] + 1) + "." : side + " has nine players.");
    } else if (row.label?.trim()) status.textContent = names.get(id) + " now plays as " + row.label.trim() + ". Click the name to change it.";
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
  if (row.source === null) {
    const placeholder = new Option("Choose a character…", "");
    placeholder.disabled = true;
    source.add(placeholder, 0);
    source.value = "";
    fields.append(labeled("Character",source));
    member.append(fields, el("p", "Choose a character here or click a portrait in the character pool.", "tourney-empty-hint"));
    panel.append(member);
    return panel;
  }
  const label = el("input","","tourney-label");
  label.maxLength = 64;
  label.placeholder = "Optional";
  label.value = row.label || "";
  label.oninput = () => setPlayerName(row, i, label.value);
  const bat = select(positions.map((_,n)=>[n,String(n+1)]),i,"tourney-bat",to=>move(team,i,to));
  const pos = select(positions.map((p,n)=>[n,p]),row.pos,"tourney-pos",to=>{swapFieldPosition(team,i,to); draw(".tourney-pos");});
  const captain = checkbox("Captain","radio",row.captain,!captainSlots.has(row.source),"tourney-captain",()=>{
    team.rows.forEach(r=>{r.captain=r===row;}); draw(".tourney-captain");
  });
  const star = checkbox("Superstar","checkbox",row.star,false,"tourney-star",checked=>{row.star=checked; draw(".tourney-star");});
  fields.append(labeled("Character",source),labeled("Player label",label),labeled("Batting spot",bat),
    labeled("Field position",pos),captain,star);
  const clear = button("Remove from sheet", () => {
    const discard = () => {
      team.rows[i] = emptyRow(row.pos);
      draw(".tourney-source");
      status.textContent = "Batting spot " + (i + 1) + " is open.";
    };
    const keep = button("Keep player", () => { confirmation.replaceChildren(); clear.focus(); });
    confirmation.replaceChildren(el("p", "Remove " + title(row) + " and its player settings from this batting spot?"),
      button("Remove player", discard, "tourney-confirm-clear"), keep);
    keep.focus();
  }, "tourney-clear-player");
  member.append(fields,clear,confirmation);
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
  const memberFace = lineupFaces(team.rows,names);
  const faceFor = (row, notice) => {
    if (row.source !== null) return memberFace(row, notice);
    const empty = el("span", "+", "roster-face tourney-open-face");
    empty.setAttribute("aria-hidden","true");
    return empty;
  };
  const showPool = mode === "plan" || filledCount(team) < 9;
  editor.classList.toggle("without-pool", !showPool);
  editor.replaceChildren(fieldPanel(team,faceFor),orderPanel(team,faceFor));
  if (showPool) {
    const i = selected[side];
    editor.append(playerPool({
      names, selectedSource:team.rows[i].source, selectedBatter:i + 1, onChoose:id=>chooseSource(i,id,team.rows[i].source === null),
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
  guide.update(side);
  if (focus) dialog.querySelector(focus)?.focus({preventScroll:true});
  if (persist) draftChanged();
}
function readyToBuild() {
  for (const s of sides) {
    const team = teams[s];
    const missing = team.rows.findIndex(row => row.source === null);
    if (missing !== -1) {
      side = s;
      selected[s] = missing;
      draw(".tourney-source");
      editor.querySelector(".tourney-source").scrollIntoView({block:"nearest"});
      status.textContent = s + " has " + filledCount(team) + " of 9 players. Choose a character for batting spot " + (missing + 1) + " to finish the lineup.";
      return false;
    }
    if (!humanLobbyName(team.player) || (s === "Home" && team.player.trim() === teams.Away.player.trim())) {
      side = s;
      draw(".tourney-lobby");
      status.textContent = s + " needs its own player's Project Rio username to build the matchup.";
      const input = toolbar.querySelector(".tourney-lobby");
      input.setCustomValidity(status.textContent);
      input.reportValidity();
      return false;
    }
    if (!team.rows.some(row => row.captain)) {
      side = s;
      const eligible = team.rows.findIndex(row => captainSlots.has(row.source));
      if (eligible !== -1) selected[s] = eligible;
      draw(eligible !== -1 ? ".tourney-captain" : ".tourney-source");
      status.textContent = eligible !== -1 ? "Select a captain for " + s + ". " + title(team.rows[eligible]) + " is eligible."
        : "Choose an eligible character such as Mario or Donkey Kong for " + s + ", then select Captain.";
      return false;
    }
  }
  return true;
}
dialog.querySelector(".tourney-build").onclick = () => { if (readyToBuild()) return run(buildMatchup); };
async function buildMatchup() {
  const away = validateTeam(teams.Away), home = validateTeam(teams.Home);
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
}
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
  guide.openMode(mode);
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
