// The Clone Maker page: the window of the retired Tk app (see tools/modding/clone_gui.py for its logic), talking to the core
// through the commands and events of tools/clonemaker/API.md.
"use strict";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const { getCurrentWindow, LogicalSize } = window.__TAURI__.window;
const dialog = window.__TAURI__.dialog;

const APP_TITLE = "MSSB Clone Maker";
const PRESETS = ["red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink"];
const NEW_ROW_COLOURS = ["blue", "green", "purple", "yellow", "orange", "cyan", "pink", "red"];
// status colours: the theme's tokens (theme.css)
const GREEN = "var(--ok)", RED = "var(--danger)", GREY = "var(--muted)", GOLD = "var(--warn)";
const MAX_REGIONS = 6;

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, children = []) => {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...children);
  return e;
};

const state = {
  game: null,          // path of a game that passed its checks
  sources: [],         // [{ id, name }]
  groups: [],          // one block per cloned character, see newGroup()
  rowSeq: 0,
  previewRow: null,
  selectedGroup: null,
  inputsEnabled: false,
  previewSeq: 0,
  previewTimer: null,
  previewed: new Set(), // sources whose textures have been loaded once
  busy: false,
  cancelling: false,
};

// ---------------------------------------------------------------- helpers
/** Runs row changes one after another (each may wait for the core). */
let rowQueue = Promise.resolve();
const serial = (fn) => (rowQueue = rowQueue.then(fn, fn));

const isCustom = (c) => c.startsWith("#");
const colourLabel = (c) => (isCustom(c) ? "Custom " + c.toUpperCase() : c[0].toUpperCase() + c.slice(1));
const sourceName = (id) => (state.sources.find((s) => s.id === id) || { name: "" }).name;
const allRows = () => state.groups.flatMap((g) => g.rows);
/** The 3D model on the stage (stage.js); null without WebGL, and then the silhouette stays. */
const stage = import("./stage.js").then((m) => m.create(document.querySelector(".stage"))).catch((e) => {
  console.warn("3D stage:", e);
  document.querySelector(".stage").dataset.stage = "fallback";
  return null;
});
const withSuffix = (path, suffix) => path.replace(/\.[^./\\]*$/, "") + suffix;
const fileName = (path) => path.split(/[\\/]/).pop();

/** #rrggbb painted next to a row: a preset is its hue at s 0.85, v 0.9 (clone_gui.colour_swatch). */
function colourSwatch(colour) {
  if (isCustom(colour)) return colour.toLowerCase();
  const hue = { red: 0, orange: 0.08, yellow: 0.16, green: 0.33, cyan: 0.5, blue: 0.62, purple: 0.76, pink: 0.9 }[colour];
  const f = (n) => {
    const k = (n + hue * 6) % 6;
    return Math.trunc((0.9 - 0.9 * 0.85 * Math.max(0, Math.min(k, 4 - k, 1))) * 255);
  };
  return "#" + [f(5), f(3), f(1)].map((v) => v.toString(16).padStart(2, "0")).join("");
}

function setStatus(text, colour, pill, pillColour) {
  const s = $("status");
  s.textContent = text;
  s.className = "status" + (colour === GREEN ? " ok" : colour === RED ? " bad" : "");
  setPill(pill, pillColour);
}

function setPill(text, colour) {
  $("pill").lastElementChild.textContent = text;
  $("pill").firstElementChild.style.background = colour;
}

function log(line) {
  const box = $("log");
  const cls = line.startsWith("ERROR") ? "err"
    : line.startsWith("saved ") || line.startsWith("selftest") ? "ok"
    : line.startsWith("----") ? "head" : "";
  box.append(el("div", { className: cls, textContent: line }));
  box.scrollTop = box.scrollHeight;
}

/** Inputs that wait for a game and are locked while one is being made. */
function setEnabled(on) {
  state.inputsEnabled = on;
  const inputs = ["btn-group", "chk-ui", "chk-colour", "chk-stats", "keeplum", "chk-keep", "out-path", "btn-out", "btn-go"]
    .map($)
    .concat([...document.querySelectorAll("#groups input, #groups select, #groups button, #roster button")]);
  for (const w of inputs) w.disabled = !on;
  for (const r of allRows()) for (const b of r.swatches.children) b.disabled = !on || Boolean(r.textures);
  $("btn-iso").disabled = $("btn-dir").disabled = state.busy;
  if (on && !$("chk-ui").checked) $("chk-colour").disabled = true;  // the colour word only matters with source menus
}

function updateSourceLabels() {
  const many = state.groups.length > 1, n = sourceName(state.groups[0]?.source ?? 0);
  for (const g of state.groups) {
    g.ui.export.title = `Save ${sourceName(g.source)}'s textures as PNG pictures to paint your own`;
    g.ui.blender.title = `Save ${sourceName(g.source)} as a 3D model with its skeleton and animations, for Blender`;
    g.ui.drop.hidden = !many;
    g.ui.drop.textContent = `Remove ${sourceName(g.source)} and its clones`;
    g.ui.root.querySelector(".base-name").textContent = sourceName(g.source);
  }
  renderRoster();
  // With several characters, say "each clone ... it copies" and name them, so nobody reads it as one character.
  const names = state.groups.map((g) => sourceName(g.source)).join(", ");
  $("chk-ui-text").textContent = many
    ? `Show each clone as the character it copies (${names}): name and pictures in menus, HUD, announcer (recommended)`
    : `Show clones as ${n}: name and pictures in menus, HUD, announcer (recommended)`;
  $("chk-stats-text").textContent = many ? `Give each clone the stats of the character it copies (${names})` : `Give clones ${n}'s stats`;
}

// ---------------------------------------------------------------- dialogs
/** Shows or hides a dialog. While one is open the page behind it can't be clicked or tabbed to;
 *  when the last one closes, focus goes back where it was. */
let modalReturn = null;
function setModal(box, open) {
  if (open && !modalReturn) modalReturn = document.activeElement;
  box.hidden = !open;
  const anyOpen = [...document.querySelectorAll(".modal")].some((m) => !m.hidden);
  for (const part of document.querySelectorAll("body > header, body > main, body > nav, body > footer")) part.inert = anyOpen;
  if (!anyOpen && modalReturn) {
    modalReturn.focus?.({ preventScroll: true });
    modalReturn = null;
  }
}

document.addEventListener("keydown", (e) => {
  const box = [...document.querySelectorAll(".modal")].find((m) => !m.hidden);
  if (!box) return;
  if (e.key === "Escape" && (box.id === "preview-modal" || box.id === "welcome")) {
    setModal(box, false);
    e.preventDefault();
  }
  if (e.key !== "Tab") return;
  const controls = [...box.querySelectorAll("button, select, input, a[href], [tabindex='0']")]
    .filter((c) => !c.disabled && c.getClientRects().length);
  const first = controls[0], last = controls[controls.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    last?.focus();
    e.preventDefault();
  } else if (!e.shiftKey && document.activeElement === last) {
    first?.focus();
    e.preventDefault();
  }
});

/** buttons: [[text, value, primary]]; resolves with the value of the button pressed (Escape: the first). */
function showDialog(title, message, chip, tone, buttons) {
  return new Promise((resolve) => {
    const box = $("dialog"), row = $("dialog-buttons");
    $("dialog-title").textContent = title;
    $("dialog-text").textContent = message;
    $("dialog-chip").textContent = chip;
    $("dialog-chip").className = "chip " + tone;
    row.replaceChildren();
    const cancel = buttons.length > 1 ? buttons[0][1] : true;
    let primary;
    const close = (v) => {
      setModal(box, false);
      document.removeEventListener("keydown", keys, true);
      resolve(v);
    };
    const keys = (e) => {
      if (e.key === "Escape") close(cancel);
      else if (e.key === "Enter") close(primary.value === "true");
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    for (const [text, value, isPrimary] of buttons) {
      const b = el("button", { textContent: text, className: isPrimary ? "primary" : "", value: String(value) });
      b.addEventListener("click", () => close(value));
      if (isPrimary) primary = b;
      row.append(b);
    }
    document.addEventListener("keydown", keys, true);
    setModal(box, true);
    primary.focus();
  });
}
const showInfo = (title, message) => showDialog(title, message, "done", "green", [["OK", true, true]]);
const showError = (title, message) => showDialog(title, message, "error", "red", [["OK", true, true]]);
const askYesNo = (title, message) => showDialog(title, message, "confirm", "neutral", [["No", false, false], ["Yes", true, true]]);

// ---------------------------------------------------------------- game
async function chooseIso() {
  const p = await dialog.open({
    title: "Choose your Mario Superstar Baseball game",
    filters: [{ name: "GameCube disc image", extensions: ["iso", "gcm"] }, { name: "All files", extensions: ["*"] }],
  });
  if (p) await startLoad(p);
}

async function chooseFolder() {
  const p = await dialog.open({ title: "Choose the extracted game folder (it contains sys and files)", directory: true });
  if (p) await startLoad(p);
}

async function startLoad(path) {
  $("game-path").value = path;
  setStatus("Checking the game…", GREY, "Checking the game…", GOLD);
  setEnabled(false);
  $("btn-iso").disabled = $("btn-dir").disabled = true;
  const res = await invoke("open_game", { path });
  $("btn-iso").disabled = $("btn-dir").disabled = false;
  state.game = res.ok ? path : null;
  setStatus((res.ok ? "✔ " : "✖ ") + res.message, res.ok ? GREEN : RED,
    res.ok ? "GYQE01 ready" : "Game not usable", res.ok ? GREEN : RED);
  if (!res.ok) {
    setEnabled(false);
    return;
  }
  $("out-path").value = await invoke("default_output", { gamePath: path });
  while (state.groups.length > 1) dropGroup(state.groups[state.groups.length - 1]);
  state.groups[0].info = state.groups[0].infoSource = null;
  await loadSource(state.groups[0], true);
}

// ---------------------------------------------------------------- characters, regions, rows
/** A block for one cloned character: its own source, colour regions and clone rows. The first
 *  block's parts carry the page's old ids (#source, #rows, ...). */
function newGroup(source) {
  const root = $("group-tpl").content.firstElementChild.cloneNode(true);
  const q = (c) => root.querySelector("." + c);
  const roster = el("div", { className: "roster-group" });
  const heading = el("button", { className: "base" });
  const entries = el("div", { className: "entries" });
  const add = el("button", { className: "add addc", textContent: "+ Add another clone", title: "Add another clone" });
  roster.append(heading, entries, add);
  const g = {
    source, rows: [], ticks: null, info: null, animChoices: [], infoSource: null, infoSeq: 0,
    ui: { root, roster, heading, entries, source: q("source"), regionsBox: q("regions-box"), regionsHint: q("regions-hint"),
      noGame: q("no-game-hint"), rows: q("rows"), add, export: q("export"), blender: q("blender"), drop: q("drop") },
  };
  heading.addEventListener("click", () => {
    state.selectedGroup = g;
    state.previewRow = null;
    if (g.rows.length) selectPreviewRow(g.rows[0], true);
    else renderRoster();
  });
  g.ui.source.replaceChildren(...state.sources.map((s) => el("option", { value: s.id, textContent: "Change to… " + s.name })));
  g.ui.source.value = String(source);
  g.ui.source.addEventListener("change", () => sourceChanged(g));
  g.ui.add.addEventListener("click", () => serial(() => addRow(g)));
  g.ui.export.addEventListener("click", () => exportTextures(g));
  g.ui.blender.addEventListener("click", () => exportBlender(g));
  g.ui.drop.addEventListener("click", () => removeGroup(g));
  state.groups.push(g);
  $("groups").append(root);
  $("roster").append(roster);
  setGroupIds();
  updateSourceLabels();
  return g;
}

function setGroupIds() {
  const ids = { source: "source", regionsBox: "regions-box", regionsHint: "regions-hint", noGame: "no-game-hint",
    rows: "rows", add: "btn-add", export: "btn-export", blender: "btn-blender" };
  state.groups.forEach((g, i) => {
    for (const [part, id] of Object.entries(ids)) g.ui[part].id = i === 0 ? id : "";
  });
}

function dropGroup(g) {
  state.groups.splice(state.groups.indexOf(g), 1);
  g.ui.root.remove();
  g.ui.roster.remove();
  if (state.selectedGroup === g) state.selectedGroup = state.groups[0];
  setGroupIds();
  updateSourceLabels();
}

/** Slots and characters that another block uses (a slot or character can only be used once). */
function usedElsewhere(g) {
  return state.groups.filter((o) => o !== g).flatMap((o) => [o.source, ...o.rows.map((r) => r.slot)]);
}

async function addGroup() {
  if (!state.game || state.busy) return;
  const taken = state.groups.flatMap((g) => [g.source, ...g.rows.map((r) => r.slot)]);
  const free = state.sources.find((s) => !taken.includes(s.id));
  if (!free) return;
  const g = newGroup(free.id);
  setEnabled(false);
  await loadSource(g, true);
  state.selectedGroup = g;
  if (!g.rows.length) state.previewRow = null;
  renderRoster();
  g.ui.root.scrollIntoView({ block: "nearest" });
}

function removeGroup(g) {
  if (state.groups.length < 2 || state.busy) return;
  const previewed = g.rows.some((r) => r.id === state.previewRow);
  dropGroup(g);
  if (previewed) selectPreviewRow(state.groups[0].rows[0], true);
  renderRoster();
  (state.groups[0].rows[0]?.entry || state.groups[0].ui.add).focus();
}

/** Fetch a block's source slots and colour regions, then fit its rows (reset: the default rows). */
async function loadSource(g, reset) {
  const src = g.source, seq = ++g.infoSeq;
  if (g.infoSource !== src) showRegions(g, null, `Finding ${sourceName(src)}'s colours…`);
  let info, anims;
  try {
    info = await invoke("source_info", { source: src });
  } catch (e) {
    if (seq === g.infoSeq) showRegions(g, null, `No preview for ${sourceName(src)}: ${e.error}`);
    return;
  }
  try {
    anims = await invoke("anim_choices", { source: src });
  } catch (e) {
    anims = [{ id: src, label: `${sourceName(src)} (own)`, exact: true, note: "" }];
    log(`animations: ${e.error ?? e}`);
  }
  if (seq !== g.infoSeq || !state.game) return;
  g.info = info;
  g.animChoices = anims;
  g.infoSource = src;
  showRegions(g, info.regions, `${sourceName(src)} has no colours that can be changed.`);
  await serial(() => rebuildRows(g, reset));
  setEnabled(!state.busy);
  schedulePreview(true);
}

/** Checkboxes for the block's colour regions (the defaults ticked); `note` when there are none. */
function showRegions(g, regions, note) {
  const box = g.ui.regionsBox;
  box.replaceChildren();
  g.ticks = regions ? new Set(regions.filter((r) => r.default).map((r) => r.index)) : null;
  for (const r of (regions || []).slice(0, MAX_REGIONS)) {
    const chk = el("input", { type: "checkbox", checked: r.default, disabled: state.busy });
    chk.addEventListener("change", () => regionsChanged(g, r.index, chk));
    const swatch = el("i");
    swatch.style.background = r.swatch;
    box.append(el("div", { className: "region" }, [el("label", { className: "check" }, [chk, swatch, el("span", { textContent: r.label })])]));
  }
  g.ui.regionsHint.textContent = note;
  g.ui.regionsHint.hidden = Boolean(regions && regions.length);
}

function regionsChanged(g, index, chk) {
  if (chk.checked) g.ticks.add(index);
  else if (g.ticks.size > 1) g.ticks.delete(index);
  else chk.checked = true;            // at least one colour has to change
  const row = g.rows.find((r) => r.id === state.previewRow);
  if (row) schedulePreview();
  else if (g.rows.length) selectPreviewRow(g.rows[0], true);
}

/** Ticked region indices (the core drops them when they are exactly the defaults). */
const tickedRegions = (g) => [...(g.ticks || [])].sort((a, b) => a - b);

async function rebuildRows(g, reset) {
  g.ui.noGame.hidden = true;
  const valid = new Set(g.info.slots.map((s) => s.id));
  const used = usedElsewhere(g);
  let wanted;
  if ((reset || !g.rows.length) && state.groups.length > 1 && g !== state.groups[0]) {
    // another character starts with one clone, on the first slot no other block uses
    for (const r of g.rows) setRowTextures(r, null);
    const slot = await invoke("next_free_slot", { source: g.source, used });
    wanted = slot === null ? [] : [{ slot, colour: "blue" }];
  } else if (reset || !g.rows.length) {
    wanted = g.info.default_rows;
    for (const r of g.rows) setRowTextures(r, null);
  } else {
    wanted = [];
    for (const r of g.rows) {
      const slot = valid.has(r.slot) && !used.includes(r.slot)
        ? r.slot : await invoke("next_free_slot", { source: g.source, used });
      if (slot !== null) {
        wanted.push({ slot, colour: r.colour, anims: r.anims });
        used.push(slot);
      }
    }
  }
  while (g.rows.length > wanted.length) {
    const row = g.rows.pop();
    row.el.remove();
    row.entry.remove();
  }
  g.rows.forEach((row, i) => {
    fillSlots(row);
    setRow(row, wanted[i].slot, wanted[i].colour);
    setRowAnims(row, wanted[i].anims ?? null);
  });
  for (const w of wanted.slice(g.rows.length)) await addRow(g, w.slot, w.colour);
  if (!allRows().some((r) => r.id === state.previewRow) && g.rows.length) selectPreviewRow(g.rows[0]);
  updateAddLabel(g);
}

/** The block's slot list; slots another block uses are greyed out. */
function fillSlots(row) {
  const g = row.group, taken = usedElsewhere(g);
  row.slotSelect.replaceChildren(...g.info.slots.map((s) => {
    const unavailable = taken.includes(s.id);
    const name = s.label.split(" — ")[0];
    const recommended = s.label.includes(" — recommended (");
    return el("option", {
      value: s.id,
      textContent: `${unavailable ? "× " : recommended ? "★ " : ""}${name}`,
      title: unavailable ? `${s.label} (used by another character)` : s.label,
      disabled: unavailable,
    });
  }));
  row.slotSelect.value = String(row.slot);
  row.slotSelect.title = row.slotSelect.selectedOptions[0]?.title || "";
}

/** Refresh every block's slot lists after a slot or character changed somewhere. */
function refreshSlots() {
  for (const g of state.groups) if (g.info) for (const r of g.rows) fillSlots(r);
  renderRoster();
}

/** The row's "Animations" dropdown: the source's own, exact bone matches, then a group of
 *  characters whose animations fit with warnings (the core leaves out the ones that don't fit). */
function setRowAnims(row, anims) {
  const own = row.group.source;
  const choices = row.group.animChoices;
  if (!choices.some((c) => c.id === anims) || anims === own) anims = null;
  const option = (c) => el("option", { value: c.id, textContent: c.label, title: c.note });
  const warned = choices.filter((c) => !c.exact);
  const opts = choices.filter((c) => c.exact).map(option);
  if (warned.length) opts.push(el("optgroup", { label: "Some bones differ" }, warned.map(option)));
  row.animSelect.replaceChildren(...opts);
  row.anims = anims;
  row.animSelect.value = String(anims ?? own);
  const chosen = choices.find((c) => c.id === (anims ?? own));
  renderRoster();
  row.animSelect.title = chosen && chosen.note
    ? `Animations from ${chosen.label}. Some bones differ:\n${chosen.note}`
    : "Whose animations this clone plays (only characters whose bones fit are listed)";
}

function fillColours(row) {
  const opts = PRESETS.map((c) => el("option", { value: c, textContent: colourLabel(c) }));
  if (isCustom(row.colour)) opts.push(el("option", { value: row.colour, textContent: colourLabel(row.colour) }));
  opts.push(el("option", { value: "custom", textContent: "Custom…" }));
  row.colourSelect.replaceChildren(...opts);
  row.colourSelect.value = row.colour;
}

function setRow(row, slot, colour) {
  row.slot = slot;
  row.colour = colour;
  row.slotSelect.value = String(slot);
  fillColours(row);
  row.swatch.style.background = colourSwatch(colour);
  renderRoster();
}

async function addRow(g, slot = null, colour = null) {
  if (!state.game || !g.info) return;
  if (slot === null) {
    slot = await invoke("next_free_slot", { source: g.source, used: usedElsewhere(g).concat(g.rows.map((r) => r.slot)) });
    if (slot === null) {
      await showInfo(APP_TITLE, "Every available slot already has a clone.");
      return;
    }
  }
  if (colour === null) {
    const taken = new Set(g.rows.map((r) => r.colour));
    colour = NEW_ROW_COLOURS.find((c) => !taken.has(c)) || "blue";
  }
  const row = { id: ++state.rowSeq, group: g };
  row.radio = el("input", { type: "radio", name: "preview-row", title: "Show this clone in the preview" });
  row.slotSelect = el("select", { ariaLabel: "Slot it takes over" });
  row.colourSelect = el("select", { ariaLabel: "Colour" });
  row.swatch = el("div", { className: "swatch" });
  row.animSelect = el("select", { className: "anims", ariaLabel: "Animations" });
  row.texturesPick = el("button", { className: "pick" });
  row.texturesClear = el("button", { className: "icon clear", textContent: "×", title: "Stop using these textures", ariaLabel: "Stop using these textures" });
  row.texturesBox = el("div", { className: "textures" }, [row.texturesPick, row.texturesClear]);
  row.remove = el("button", { className: "icon remove", textContent: "×", title: "Remove this clone", ariaLabel: "Remove this clone" });
  row.remove.textContent = "Remove clone";
  row.remove.className = "remove danger";
  row.radio.hidden = true; // selection lives in the roster; kept for the self-test and preview state
  row.colourSelect.hidden = true;
  row.swatch.hidden = true;
  row.swatches = el("div", { className: "swatches", role: "group", ariaLabel: "Colour presets" });
  for (const c of [...PRESETS, "custom"]) {
    const b = el("button", { className: "sw" + (c === "custom" ? " custom" : ""), title: c === "custom" ? "Custom…" : colourLabel(c), ariaLabel: c === "custom" ? "Custom colour" : colourLabel(c) });
    if (c !== "custom") b.style.setProperty("--s", colourSwatch(c));
    b.dataset.colour = c;
    b.addEventListener("click", () => {
      row.colourSelect.value = c;
      colourChanged(row);
    });
    row.swatches.append(b);
  }
  const field = (label, controls) => {
    const title = el("span", { className: "lab", textContent: label });
    return el("div", { className: "field" }, [title, ...controls]);
  };
  row.colourTitle = el("span", { className: "picked" });
  const colourField = field("Colour ", [row.swatches]);
  colourField.firstElementChild.append(row.colourTitle);
  row.el = el("div", { className: "clone-row" }, [row.radio,
    field("Slot it takes over", [row.slotSelect, el("span", { className: "slot-legend hint", textContent: "★ recommended · × in use" })]),
    colourField, row.colourSelect, row.swatch,
    field("Animations", [row.animSelect]),
    field("Own textures", [el("div", { className: "texbox" }, [row.texturesBox, row.remove])])]);
  row.entry = el("button", { className: "entry", tabIndex: -1 });
  row.entry.addEventListener("click", () => selectPreviewRow(row, true));
  row.entry.addEventListener("keydown", (e) => {
    const rows = allRows(), index = rows.indexOf(row);
    const next = e.key === "ArrowDown" ? rows[(index + 1) % rows.length]
      : e.key === "ArrowUp" ? rows[(index + rows.length - 1) % rows.length]
      : e.key === "Home" ? rows[0] : e.key === "End" ? rows[rows.length - 1] : null;
    if (!next) return;
    e.preventDefault();
    selectPreviewRow(next, true);
    next.entry.focus();
  });
  row.radio.addEventListener("change", () => selectPreviewRow(row, true));
  row.slotSelect.addEventListener("change", () => {
    row.slot = Number(row.slotSelect.value);
    refreshSlots();
    selectPreviewRow(row, true);
  });
  row.colourSelect.addEventListener("change", () => colourChanged(row));
  row.animSelect.addEventListener("change", () => {
    setRowAnims(row, Number(row.animSelect.value));
    selectPreviewRow(row, true);
  });
  row.remove.addEventListener("click", () => removeRow(row));
  row.texturesPick.addEventListener("click", () => pickRowTextures(row));
  row.texturesClear.addEventListener("click", () => setRowTextures(row, null));
  row.slot = slot;
  fillSlots(row);
  setRow(row, slot, colour);
  setRowAnims(row, null);
  setRowTextures(row, null);
  g.rows.push(row);
  g.ui.rows.append(row.el);
  if (!allRows().some((r) => r.id === state.previewRow) || g.rows.length === 1) selectPreviewRow(row);
  updateAddLabel(g);
  refreshSlots();
  schedulePreview();
}

function removeRow(row) {
  const g = row.group;
  const focused = row.el.contains(document.activeElement) || row.entry === document.activeElement;
  row.el.remove();
  row.entry.remove();
  g.rows.splice(g.rows.indexOf(row), 1);
  if (state.previewRow === row.id) {
    const next = g.rows[0];
    state.previewRow = null;
    state.selectedGroup = g;
    if (next) selectPreviewRow(next);
  }
  updateAddLabel(g);
  refreshSlots();
  if (focused) (g.rows[0]?.entry || g.ui.add).focus();
  schedulePreview();
}

function updateAddLabel(g) {
  g.ui.add.textContent = g.rows.length ? "+ Add another clone" : "+ Add a clone";
  renderRoster();
}

function selectPreviewRow(row, refresh = false) {
  if (!row) return;
  if (state.previewRow !== row.id) ++state.previewSeq;
  state.previewRow = row.id;
  state.selectedGroup = row.group;
  row.radio.checked = true;
  renderRoster();
  if (refresh) schedulePreview();
}

/** The roster selects the same row used by the texture preview and its editor. */
function renderRoster() {
  const rows = allRows(), selected = rows.find((r) => r.id === state.previewRow);
  const activeGroup = selected?.group || state.selectedGroup || state.groups[0];
  for (const g of state.groups) {
    g.ui.root.hidden = g !== activeGroup;
    g.ui.heading.replaceChildren(el("span", { textContent: sourceName(g.source) }), el("small", { textContent: String(g.rows.length) }));
    for (const r of g.rows) {
      const on = r === selected;
      const name = r.slotSelect.selectedOptions[0]?.textContent.replace(/^[★×] /, "") || "Clone";
      const conflict = rows.some((other) => other !== r && other.slot === r.slot) || state.groups.some((other) => other.source === r.slot);
      const moves = sourceName(r.anims ?? g.source);
      const subtitle = conflict ? "× Slot used twice" : r.textures ? "Own textures" : `${colourLabel(r.colour)} · ${moves}'s moves`;
      r.entry.classList.toggle("on", on);
      r.entry.tabIndex = on || (!selected && r === rows[0]) ? 0 : -1;
      r.entry.setAttribute("aria-pressed", String(on));
      r.entry.setAttribute("aria-label", `${name}, ${subtitle}`);
      const dot = el("i", { className: "dot" + (r.textures || isCustom(r.colour) ? " custom" : "") });
      dot.style.setProperty("--c", colourSwatch(r.colour));
      r.entry.replaceChildren(dot, el("span", {}, [el("b", { textContent: name }), el("small", { className: conflict ? "warn" : "", textContent: subtitle })]));
      if (r.entry.parentElement !== g.ui.entries) g.ui.entries.append(r.entry);
      r.el.hidden = !on;
      r.radio.checked = on;
      r.colourTitle.textContent = r.textures ? "· Own textures" : "· " + colourLabel(r.colour);
      for (const b of r.swatches.children) {
        const picked = b.dataset.colour === r.colour || (b.dataset.colour === "custom" && isCustom(r.colour));
        b.classList.toggle("on", picked);
        b.setAttribute("aria-pressed", String(picked));
        b.disabled = Boolean(r.textures) || !state.inputsEnabled;
      }
      if (on) {
        document.documentElement.style.setProperty("--clone", colourSwatch(r.colour));
        $("clone-name").textContent = $("ghost-name").textContent = name;
        $("clone-subtitle").textContent = `${sourceName(g.source)}, ${r.textures ? "with your own textures" : "recoloured " + colourLabel(r.colour).toLowerCase()}`;
      }
    }
  }
  if (!selected) {
    ++state.previewSeq; // discard a preview for a removed row
    $("clone-name").textContent = state.game ? "Add a clone" : "Make it your own";
    $("ghost-name").textContent = activeGroup ? sourceName(activeGroup.source) : "Your studio";
    $("clone-subtitle").textContent = state.game ? "Choose a slot and make it yours." : "Choose your game to start creating clones.";
    $("img-before").hidden = $("img-after").hidden = true;
    $("img-before").nextElementSibling.hidden = false;
    stage.then((s) => s?.clear());
  }
}

/** "Custom…" opens the system colour picker (only the hue is used); cancelling keeps the colour. */
function colourChanged(row) {
  const v = row.colourSelect.value;
  if (v !== "custom") {
    setRow(row, row.slot, v);
    selectPreviewRow(row, true);
    return;
  }
  const picker = $("picker");
  picker.value = colourSwatch(row.colour);
  picker.onchange = () => {
    setRow(row, row.slot, picker.value.toLowerCase());
    selectPreviewRow(row, true);
  };
  row.colourSelect.value = row.colour;
  const at = row.swatches?.querySelector(".sw.custom")?.getBoundingClientRect();
  if (at) Object.assign(picker.style, { left: at.left + "px", top: at.bottom + "px" });
  picker.click();
}

// ---------------------------------------------------------------- painted textures
/** Save the shown source's textures as PNGs into a new "<Name> textures" folder inside `folder`. */
async function exportTextures(g = state.groups[0], folder = null) {
  if (!state.game || state.busy) return null;
  const name = sourceName(g.source);
  if (folder === null) {
    folder = await dialog.open({ title: `Choose where to save ${name}'s textures (a "${name} textures" folder is made there)`, directory: true });
    if (!folder) return null;
  }
  let res;
  try {
    res = await invoke("export_textures", { source: g.source, folder });
  } catch (e) {
    await showError(APP_TITLE, e.error ?? String(e));
    return null;
  }
  state.lastExport = res.folder;
  log(`exported ${res.count} textures of ${name} to ${res.folder}`);
  await showInfo("Textures exported", `Saved ${res.count} pictures of ${name} to\n${res.folder}\n\n`
    + "Paint over them in any paint program, keeping each picture's size and file name. Then press "
    + "\"My textures…\" in the clone editor and pick that folder. README.txt in the folder has the details.");
  return res;
}

// ---------------------------------------------------------------- Blender
/** Choose a character (default: the block's) and whether to include every animation, then save it
 *  as a .gltf for Blender. `path` skips the save dialog (the self-test). */
async function exportBlender(g = state.groups[0], path = null) {
  if (!state.game || state.busy) return null;
  const box = $("blender"), select = $("blender-source"), all = $("blender-all");
  const go = $("blender-go"), cancelButton = $("blender-cancel"), note = $("blender-note");
  const controls = [select, all, go, cancelButton];
  const reset = () => {
    setModal(box, false);
    for (const c of controls) c.disabled = false;
    go.textContent = "Save as…";
  };
  select.replaceChildren(...state.sources.map((s) => el("option", { value: s.id, textContent: s.name })));
  select.value = String(g.source);
  all.checked = true;
  note.textContent = "A .gltf file with the textured body, the skeleton and the animations.";
  setModal(box, true);
  go.focus();
  const confirmed = await new Promise((resolve) => {
    const close = (v) => {
      document.removeEventListener("keydown", keys, true);
      resolve(v);
    };
    const keys = (e) => {
      if (e.key === "Escape") close(false);
      else if (e.key === "Enter") close(true);
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    document.addEventListener("keydown", keys, true);
    go.onclick = () => close(true);
    cancelButton.onclick = () => close(false);
  });
  const source = Number(select.value), name = sourceName(source);
  if (confirmed && path === null) {
    path = await dialog.save({
      title: `Save ${name} for Blender as`,
      defaultPath: name.replace(/[.\s]+$/, "") + ".gltf",
      filters: [{ name: "glTF 2.0 (Blender: File > Import > glTF 2.0)", extensions: ["gltf"] }],
    });
    if (path && !/\.gltf$/i.test(path)) path += ".gltf";
  }
  if (!confirmed || !path) {
    reset();
    return null;
  }
  for (const c of controls) c.disabled = true;
  go.textContent = "Exporting…";
  note.textContent = `Exporting ${name}…`;
  let res;
  try {
    res = await invoke("export_gltf", { source, allAnimations: all.checked, path });
  } catch (e) {
    reset();
    await showError(APP_TITLE, e.error ?? String(e));
    return null;
  }
  reset();
  log(`exported ${name} for Blender (${res.animations} animation${res.animations === 1 ? "" : "s"}) to ${res.path}`);
  const closed = showInfo("3D model exported", `Saved ${name} to\n${res.path}\n\n`
    + `${res.bones} bones, ${res.animations} animation${res.animations === 1 ? "" : "s"}.\n\n`
    + "Open in Blender: File > Import > glTF 2.0\n\n"
    + "The file contains the game's models and textures: keep it to yourself.\n\n");
  const guide = el("a", { textContent: "How to play another animation (user guide)", href: "#" });
  guide.addEventListener("click", (e) => {
    e.preventDefault();
    invoke("open_blender_guide");
  });
  $("dialog-text").append(guide);
  await closed;
  return res;
}

async function pickRowTextures(row) {
  const folder = await dialog.open({
    title: "Choose the folder with your painted textures",
    directory: true,
    defaultPath: row.textures || state.lastExport || undefined,
  });
  if (folder) setRowTextures(row, folder);
}

/** A clone row's painted texture folder (null: recolour only), shown by its folder name. */
function setRowTextures(row, folder) {
  row.textures = folder || null;
  row.texturesBox.classList.toggle("set", Boolean(row.textures));
  row.texturesPick.textContent = row.textures ? fileName(row.textures.replace(/[\\/]+$/, "")) : "My textures…";
  row.texturesPick.title = row.textures
    ? `Painted textures from ${row.textures}\nClick to pick another folder.`
    : "Use textures you painted (export them first with \"Export Textures\")";
  row.texturesClear.hidden = !row.textures;
  // Painted rows keep the source's colours, so the colour choice does not apply.
  row.colourSelect.style.visibility = row.swatch.style.visibility = row.textures ? "hidden" : "";
  renderRoster();
  if (row.radio.checked) schedulePreview(true);
}

function sourceChanged(g) {
  const src = Number(g.ui.source.value);
  if (usedElsewhere(g).includes(src)) {
    g.ui.source.value = String(g.source);
    showInfo(APP_TITLE, `${sourceName(src)} is already used by another character's clones. Pick someone else.`);
    return;
  }
  g.source = src;
  updateSourceLabels();
  if (state.game) loadSource(g, false);
}

// ---------------------------------------------------------------- preview
/** Ask for a new preview; quick successive changes (dragging the slider) coalesce. */
function schedulePreview(now = false) {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(requestPreview, now ? 1 : 40);
}

async function requestPreview() {
  const row = allRows().find((r) => r.id === state.previewRow);
  if (!state.game || !row || row.group.infoSource !== row.group.source) return;
  const g = row.group, src = g.source, seq = ++state.previewSeq;
  if (!state.previewed.has(src)) $("preview-caption").textContent = `Loading ${sourceName(src)}'s textures…`;
  const request = { source: src, slot: row.slot, colour: row.colour, keeplum: keeplum(), regions: tickedRegions(g), textures: row.textures, anims: row.anims };
  let res, failure;
  try {
    res = await invoke("preview", { request });
  } catch (e) {
    failure = e.error ?? String(e);
  }
  if (seq !== state.previewSeq) return;     // a newer request is on its way
  const before = $("img-before"), after = $("img-after");
  if (failure) {
    $("preview-caption").textContent = `No preview for ${sourceName(src)}: ${failure}`;
    before.hidden = after.hidden = true;
    stage.then((s) => s?.clear("fallback"));
    return;
  }
  state.previewed.add(src);
  stage.then((s) => s?.show(src, res.textures));
  before.src = "data:image/png;base64," + res.before_png;
  after.src = "data:image/png;base64," + res.after_png;
  before.hidden = after.hidden = false;
  before.nextElementSibling.hidden = true;
  $("preview-chip").textContent = sourceName(src);
  $("preview-caption").textContent = res.caption;
}

const keeplum = () => Math.round(Number($("keeplum").value) * 100) / 100;

// ---------------------------------------------------------------- advanced
function toggleAdvanced() {
  const adv = $("adv");
  adv.hidden = !adv.hidden;
  $("btn-adv").textContent = "Advanced options  " + (adv.hidden ? "▸" : "▾");
  $("btn-adv").ariaExpanded = String(!adv.hidden);
  if (!adv.hidden) adv.scrollIntoView({ block: "nearest" });
  fitHeight();
}

/** Grow the window (within the screen) when its content no longer fits; past that it scrolls. */
async function fitHeight() {
  const need = document.documentElement.scrollHeight;
  const room = screen.availHeight - 80;
  if (window.innerHeight < Math.min(need, room)) {
    await getCurrentWindow().setSize(new LogicalSize(window.innerWidth, Math.min(need, room)));
  }
}

// ---------------------------------------------------------------- output + run
async function chooseOutput() {
  const cur = $("out-path").value.trim();
  const p = await dialog.save({
    title: "Save the patched game as",
    defaultPath: cur || "Mario Superstar Baseball - Clones.iso",
    filters: [{ name: "GameCube disc image", extensions: ["iso"] }],
  });
  if (p) $("out-path").value = p;
}

function makeJob() {
  const [first, ...others] = state.groups;
  const clones = (g, extra) => g.rows.map((r) => {
    const clone = { slot: r.slot, colour: r.colour, ...extra };
    if (r.textures) clone.textures = r.textures;
    if (r.anims !== null) clone.anims = r.anims;
    return clone;
  });
  return {
    game: $("game-path").value,
    output: $("out-path").value.trim(),
    source: first.source,
    clones: clones(first, {}).concat(...others.map((g) => clones(g, { source: g.source, regions: tickedRegions(g) }))),
    keeplum: keeplum(),
    regions: tickedRegions(first),
    ui_source: $("chk-ui").checked,
    copy_stats: $("chk-stats").checked,
    keep_colour_word: $("chk-colour").checked,
    keep_work: $("chk-keep").checked,
  };
}

async function create() {
  if (!state.game || state.busy || state.groups.some((g) => g.infoSource !== g.source)) return;   // a source is still loading
  if (!$("out-path").value.trim()) {
    await chooseOutput();
    if (!$("out-path").value.trim()) return;
  }
  const job = makeJob();
  const invalid = await invoke("validate_job", { job });
  if (invalid) {
    await showError(APP_TITLE, invalid.error);
    return;
  }
  if (await invoke("path_exists", { path: job.output })
      && !(await askYesNo(APP_TITLE, `${fileName(job.output)} already exists. Replace it?`))) return;
  state.busy = true;
  state.cancelling = false;
  setEnabled(false);
  setPill("Creating the game…", GOLD);
  $("progress-bar").style.width = "0";
  $("progress-label").textContent = "Starting…";
  $("btn-cancel").disabled = false;
  $("run-box").hidden = false;
  $("card-save").classList.add("busy");
  $("btn-cancel").focus();
  log("---- creating " + job.output);
  log("the full log is also saved to " + withSuffix(job.output, ".log"));
  try {
    await invoke("create", { job });
  } catch (e) {
    finish();
    setPill("Not created", RED);
    showError("Couldn't create the patched game", e.error ?? String(e));
  }
}

function cancel() {
  state.cancelling = true;
  invoke("cancel");
  $("btn-cancel").disabled = true;
  $("progress-label").textContent = "Cancelling…";
}

function finish() {
  state.busy = false;
  $("run-box").hidden = true;
  $("card-save").classList.remove("busy");
  setEnabled(state.game !== null);
  if (document.activeElement === $("btn-cancel")) $("btn-go").focus();
}

function listenToJob() {
  listen("log", (e) => log(e.payload.line));
  listen("progress", ({ payload: { fraction, label } }) => {
    if (!state.cancelling) $("progress-label").textContent = `${label}…  ${Math.trunc(fraction * 100)}%`;
    $("progress-bar").style.width = `${fraction * 100}%`;
  });
  listen("done", ({ payload: { iso, work, hint } }) => {
    finish();
    setPill("Game created", GREEN);
    const extra = work ? `\n\nThe working folder was kept: ${work}` : "";
    showInfo("Done!", `Saved to ${iso}.\n\nOpen it in Dolphin or Project Rio: File → Open…\n\n${hint}${extra}`);
  });
  listen("error", ({ payload: { message, cancelled } }) => {
    finish();
    if (cancelled) {
      setPill("Cancelled", GREY);
      log(message);
      return;
    }
    setPill("Not created", RED);
    log("ERROR: " + message);
    showError("Couldn't create the patched game", message);
  });
}

// ---------------------------------------------------------------- start
async function main() {
  state.sources = await invoke("sources");
  newGroup(state.sources[0].id);
  setEnabled(false);

  $("btn-iso").addEventListener("click", chooseIso);
  $("btn-dir").addEventListener("click", chooseFolder);
  $("btn-group").addEventListener("click", addGroup);
  $("btn-adv").addEventListener("click", toggleAdvanced);
  $("btn-log").addEventListener("click", () => {
    $("log-drawer").hidden = !$("log-drawer").hidden;
    $("btn-log").ariaExpanded = String(!$("log-drawer").hidden);
    if (!$("log-drawer").hidden) $("log-drawer").scrollIntoView({ block: "nearest" });
  });
  $("btn-enlarge").addEventListener("click", () => {
    for (const part of ["before", "after"]) {
      $("large-" + part).src = $("img-" + part).src;
      $("large-" + part).hidden = $("img-" + part).hidden;
    }
    $("large-caption").textContent = $("preview-caption").textContent;
    setModal($("preview-modal"), true);
    $("preview-close").focus();
  });
  $("preview-close").addEventListener("click", () => setModal($("preview-modal"), false));
  $("chk-ui").addEventListener("change", () => { $("chk-colour").disabled = !$("chk-ui").checked || state.busy || !state.game; });
  $("keeplum").addEventListener("input", () => {
    $("keeplum-value").textContent = Number($("keeplum").value).toFixed(2);
    schedulePreview();
  });
  $("btn-out").addEventListener("click", chooseOutput);
  $("btn-go").addEventListener("click", create);
  $("btn-cancel").addEventListener("click", cancel);
  listenToJob();

  getCurrentWindow().onCloseRequested(async (event) => {
    if (state.busy && !(await askYesNo(APP_TITLE, "The game is still being made. Quit anyway?\n\n"
        + "A half-finished working folder named MSSB-Clone-Maker-work-… will be left next to the output; you can delete it."))) {
      event.preventDefault();
    }
  });

  const options = await invoke("launch_options");
  $("feedback").addEventListener("click", () => invoke("open_feedback"));
  for (const [id, pick] of [["welcome-iso", chooseIso], ["welcome-dir", chooseFolder], ["welcome-later", null]]) {
    $(id).addEventListener("click", () => {
      setModal($("welcome"), false);
      pick?.();
    });
  }
  if (!options.selftest) {
    checkForUpdate();
    showWelcome();
  }
  if (options.selftest) {
    await import("./selftest.js").then((m) => m.run(options, { state, startLoad, exportTextures, exportBlender, setRowTextures, showInfo, showWelcome, stage }));
  }
}

/** On launch with no game yet: ask for it in a dialog instead of leaving it to the top bar. */
function showWelcome() {
  if (state.game) return;
  setModal($("welcome"), true);
  $("welcome-iso").focus();
}

/** Show a header button when GitHub has a newer release; quietly does nothing offline. */
async function checkForUpdate() {
  const parse = (v) => v.replace(/^v/, "").split(/[.-]/).slice(0, 3).map(Number);
  const newer = (a, b) => { for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] > b[i]; return false; };
  try {
    const res = await fetch("https://api.github.com/repos/Cjericho4/mssb-modding/releases/latest");
    if (!res.ok) return;
    const tag = (await res.json()).tag_name;
    if (!newer(parse(tag), parse(await window.__TAURI__.app.getVersion()))) return;
    const button = $("update");
    button.textContent = `New version ${tag}`;
    button.title = "Open the download page";
    button.hidden = false;
    button.addEventListener("click", () => invoke("open_releases"));
  } catch {
    // offline or rate-limited: try again next start
  }
}

main();
