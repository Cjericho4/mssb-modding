import {sides, positions, captainSlots, moveBatter, swapFieldPosition} from "./rio-roster.mjs";

const format = "mssb-tournament-team-v1";
const MiB = 1024 * 1024;
const dialog = document.querySelector("#tournament-plan");
const toolbar = dialog.querySelector(".tourney-toolbar");
const editor = dialog.querySelector(".tourney-editor");
const status = dialog.querySelector("[role=status]");
const fieldset = dialog.querySelector("fieldset");
const close = dialog.querySelector(".tourney-close");
const invoke = window.__TAURI__.core.invoke;
const teams = Object.fromEntries(sides.map((s, t) => [s, {format, player:"", rows:positions.map((_,i) => ({
  source:i + t * 9, pos:i, captain:i === 0, star:false
}))}]));
const selected = {Away:0, Home:0};
let side = "Away", names = new Map(), busy = false, dragging, opener;

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
  fieldset.disabled = close.disabled = true;
  status.textContent = "";
  try { await action(); }
  catch (error) { status.textContent = errorText(error); }
  finally { fieldset.disabled = close.disabled = busy = false; }
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
function validateTeam(team) {
  if (!record(team) || team.format !== format) throw Error("Choose a portable tournament team saved with Save team.");
  const player = typeof team.player === "string" && team.player.trim();
  if (!player || player.length > 30 || ["CPU","No Player Selected"].includes(player)) throw Error("Enter the human player's Rio lobby name (up to 30 characters).");
  if (!Array.isArray(team.rows) || team.rows.length !== 9) throw Error("A tournament team needs nine batters.");
  const rows = team.rows.map((r, i) => {
    if (!record(r) || !names.has(r.source) || !integer(r.pos, 8) || typeof r.captain !== "boolean" || typeof r.star !== "boolean")
      throw Error("Check the character, field position and flags for batter " + (i + 1) + ".");
    if (r.label !== undefined && (typeof r.label !== "string" || r.label.length > 64)) throw Error("Use a player label of up to 64 characters.");
    if (r.stats !== undefined && (!record(r.stats) || Object.values(r.stats).some(v => !integer(v,255))))
      throw Error("Each imported stat override needs a whole-number value from 0 to 255.");
    return {source:r.source, pos:r.pos, captain:r.captain, star:r.star,
      ...(r.label?.trim() ? {label:r.label.trim()} : {}), ...(r.textures !== undefined ? {textures:r.textures} : {}),
      ...(r.stats !== undefined ? {stats:r.stats} : {})};
  });
  if (new Set(rows.map(r=>r.pos)).size !== 9) throw Error("Assign all nine different field positions.");
  const captains = rows.filter(r=>r.captain);
  if (captains.length !== 1 || !captainSlots.has(captains[0].source)) throw Error("Choose one captain from the twelve captain characters.");
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
function makeToolbar() {
  const tabs = el("div", "", "tourney-tabs");
  tabs.setAttribute("role","group");
  tabs.setAttribute("aria-label","Tournament team to edit");
  for (const s of sides) {
    const tab = button(s, () => { side = s; status.textContent = ""; draw('.tourney-tabs [data-side="' + s + '"]'); });
    tab.dataset.side = s;
    tab.setAttribute("aria-pressed",String(side === s));
    tabs.append(tab);
  }
  const name = el("input","","tourney-lobby");
  name.maxLength = 30;
  name.value = teams[side].player;
  name.oninput = () => { teams[side].player = name.value; status.textContent = ""; };
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
    teams[side] = team;
    selected[side] = 0;
    draw();
    status.textContent = side + " team loaded for " + team.player + ".";
  });
  const load = el("span","","tourney-import");
  load.append(button("Load team…",()=>input.click()),input);
  const save = button("Save " + side + " team…", () => run(async () => {
    const path = await invoke("save_tournament_team",{team:validateTeam(teams[side])});
    if (path) status.textContent = "Saved " + side + " team to " + path + ".";
  }), "tourney-save");
  toolbar.replaceChildren(tabs, labeled("Rio lobby name",name), load, save);
}
function move(team, from, to) {
  moveBatter(team,from,to);
  selected[side] = to;
  draw('.tourney-row[data-index="' + to + '"]');
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
    elem.classList.add("drag-over");
  };
  elem.ondragleave = event => {
    if (!elem.contains(event.relatedTarget)) elem.classList.remove("drag-over");
  };
  elem.ondragend = () => {
    dragging = undefined;
    for (const target of editor.querySelectorAll(".drag-over")) target.classList.remove("drag-over");
  };
  elem.ondrop = event => {
    if (busy || dragging?.side !== side) return;
    event.preventDefault();
    dropPlayer(dragging.index);
  };
}
function fieldPanel(team) {
  const panel = el("section", "", "tourney-field-panel");
  panel.append(el("h3","Starting field positions"), el("p","Drop one fielder on another to swap. The number is their batting spot."));
  const diamond = el("div","","plan-diamond");
  team.rows.forEach((row, i) => {
    const selector = '.tourney-fielder[data-pos="' + row.pos + '"]';
    const marker = button("", () => { selected[side] = i; draw(selector); }, "plan-fielder tourney-fielder");
    marker.dataset.pos = String(row.pos);
    marker.dataset.index = String(i);
    marker.setAttribute("aria-pressed",String(selected[side] === i));
    const badge = el("b",positions[row.pos],"position-badge");
    badge.append(el("span","#" + (i + 1), "field-bat-num"));
    marker.append(badge,el("span",title(row),"bat-name tourney-name"));
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
function orderPanel(team) {
  const panel = el("section");
  panel.append(el("h3","Batting order"), el("p","Drag the nine batters into order. Each row keeps its field position."));
  const ol = el("ol","","tourney-order");
  team.rows.forEach((row,i) => {
    const li = el("li");
    const b = button("", () => { selected[side] = i; draw('.tourney-row[data-index="' + i + '"]'); }, "tourney-row");
    b.dataset.index = String(i);
    b.setAttribute("aria-pressed",String(selected[side] === i));
    b.append(el("span",String(i+1),"tourney-number"),el("strong",title(row),"tourney-name"),
      el("small",row.captain ? "Captain" : ""),el("small",row.star ? "★" : ""),el("b",positions[row.pos]));
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
        delete row.textures[name];
        if (!Object.keys(row.textures).length) delete row.textures;
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
function inspector(team) {
  const i = selected[side], row = team.rows[i];
  const panel = el("section","","tourney-inspector");
  const member = el("div", "", "tourney-member");
  member.append(el("h3","Edit batter " + (i + 1)));
  const fields = el("div","","tourney-fields");
  const confirmation = el("div","","source-change");
  const changeSource = id => {
    row.source = id;
    if (!captainSlots.has(id)) row.captain = false;
    delete row.textures;
    delete row.stats;
    draw(".tourney-source");
  };
  const source = select(names,row.source,"tourney-source",id => {
    if (id === row.source) return;
    if (!Object.keys(row.textures||{}).length && !Object.keys(row.stats||{}).length) { changeSource(id); return; }
    source.value = String(row.source);
    confirmation.replaceChildren(el("p","Switch to " + names.get(id) + " and clear the previous character’s custom textures and stat overrides?"),
      button("Switch character",()=>changeSource(id)),button("Keep character",()=>{confirmation.replaceChildren(); source.focus();}));
  });
  const label = el("input","","tourney-label");
  label.maxLength = 64;
  label.placeholder = "Optional";
  label.value = row.label || "";
  label.oninput = () => {
    row.label = label.value;
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
  if (Object.keys(row.stats||{}).length) {
    const details = el("details");
    details.append(el("summary",Object.keys(row.stats).length + " imported stat overrides"));
    const list = el("ul");
    for (const [key,value] of Object.entries(row.stats)) list.append(el("li",key+" · "+value));
    details.append(list);
    customization.append(details);
  }
  return panel;
}
function draw(focus) {
  dragging = undefined;
  makeToolbar();
  const team = teams[side];
  editor.replaceChildren(fieldPanel(team),orderPanel(team),inspector(team));
  if (focus) dialog.querySelector(focus)?.focus({preventScroll:true});
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
    el("p","Both players use the same Away and Home teams. Compare both hashes, then load hud.json with the built ISO in Game plan and select Use in Rio on both computers."));
});
close.onclick = () => dialog.close();
dialog.oncancel = event => {if (busy) event.preventDefault();};
dialog.onclose = () => {dragging=undefined; opener?.focus();};
editor.addEventListener("input", () => { status.textContent = ""; });
for (const trigger of document.querySelectorAll("[data-open-tournament]")) {
  trigger.onclick = async () => {
    if (dialog.open || busy) return;
    opener = trigger;
    dialog.showModal();
    await run(async () => {
      if (!names.size) names = new Map((await invoke("sources")).map(s=>[s.id,s.name]));
      draw();
    });
  };
}
