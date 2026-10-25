// The Clone Maker page: the same window as tools/modding/clone_gui.py's App, talking to the core
// through the commands and events of tools/clonemaker/API.md.
"use strict";

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const { getCurrentWindow, LogicalSize } = window.__TAURI__.window;
const dialog = window.__TAURI__.dialog;

const APP_TITLE = "MSSB Clone Maker";
const PRESETS = ["red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink"];
const NEW_ROW_COLOURS = ["blue", "green", "purple", "yellow", "orange", "cyan", "pink", "red"];
const GREEN = "#22c55e", RED = "#ef4444", GREY = "#8f8fa3", GOLD = "#f5bb00";
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
  info: null,          // source_info of the shown source: { regions, slots, default_rows }
  infoSource: null,
  infoSeq: 0,
  rows: [],            // { id, slot, colour, el }
  rowSeq: 0,
  previewRow: null,
  previewSeq: 0,
  previewTimer: null,
  previewed: new Set(), // sources whose textures have been loaded once
  ticks: null,          // Set of ticked region indices, null until the regions are known
  busy: false,
  cancelling: false,
};

// ---------------------------------------------------------------- helpers
/** Runs row changes one after another (each may wait for the core). */
let rowQueue = Promise.resolve();
const serial = (fn) => (rowQueue = rowQueue.then(fn, fn));

const isCustom = (c) => c.startsWith("#");
const colourLabel = (c) => (isCustom(c) ? "Custom " + c.toUpperCase() : c[0].toUpperCase() + c.slice(1));
const sourceId = () => Number($("source").value);
const sourceName = (id = sourceId()) => (state.sources.find((s) => s.id === id) || { name: "" }).name;
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
  const inputs = ["source", "btn-add", "chk-ui", "chk-colour", "chk-stats", "keeplum", "chk-keep", "out-path", "btn-out", "btn-go"]
    .map($)
    .concat([...document.querySelectorAll("#rows input, #rows select, #rows button, #regions-box input")]);
  for (const w of inputs) w.disabled = !on;
  $("btn-iso").disabled = $("btn-dir").disabled = state.busy;
  if (on && !$("chk-ui").checked) $("chk-colour").disabled = true;  // the colour word only matters with source menus
}

function updateSourceLabels() {
  const n = sourceName();
  $("chk-ui-text").textContent = `Show clones as ${n}: name and pictures in menus, HUD, announcer (recommended)`;
  $("chk-stats-text").textContent = `Give clones ${n}'s stats`;
}

// ---------------------------------------------------------------- dialogs (gui_theme.Dialogs)
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
      box.hidden = true;
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
    box.hidden = false;
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
  state.info = state.infoSource = null;
  await loadSource(true);
}

// ---------------------------------------------------------------- source, regions, rows
/** Fetch the shown source's slots and colour regions, then fit the rows (reset: the default rows). */
async function loadSource(reset) {
  const src = sourceId(), seq = ++state.infoSeq;
  if (state.infoSource !== src) showRegions(null, `Finding ${sourceName(src)}'s colours…`);
  let info;
  try {
    info = await invoke("source_info", { source: src });
  } catch (e) {
    if (seq === state.infoSeq) showRegions(null, `No preview for ${sourceName(src)}: ${e.error}`);
    return;
  }
  if (seq !== state.infoSeq || !state.game) return;
  state.info = info;
  state.infoSource = src;
  showRegions(info.regions, `${sourceName(src)} has no colours that can be changed.`);
  await serial(() => rebuildRows(reset));
  setEnabled(!state.busy);
  schedulePreview(true);
}

/** Checkboxes for the source's colour regions (the defaults ticked); `note` when there are none. */
function showRegions(regions, note) {
  const box = $("regions-box");
  box.replaceChildren();
  state.ticks = regions ? new Set(regions.filter((r) => r.default).map((r) => r.index)) : null;
  for (const r of (regions || []).slice(0, MAX_REGIONS)) {
    const chk = el("input", { type: "checkbox", checked: r.default, disabled: state.busy });
    chk.addEventListener("change", () => regionsChanged(r.index, chk));
    const swatch = el("i");
    swatch.style.background = r.swatch;
    box.append(el("div", { className: "region" }, [swatch, el("label", { className: "check" }, [chk, el("span", { textContent: r.label })])]));
  }
  $("regions-hint").textContent = note;
  $("regions-hint").hidden = Boolean(regions && regions.length);
}

function regionsChanged(index, chk) {
  if (chk.checked) state.ticks.add(index);
  else if (state.ticks.size > 1) state.ticks.delete(index);
  else chk.checked = true;            // at least one colour has to change
  schedulePreview();
}

/** Ticked region indices (the core drops them when they are exactly the defaults). */
const tickedRegions = () => [...state.ticks].sort((a, b) => a - b);

async function rebuildRows(reset) {
  $("no-game-hint").hidden = true;
  const valid = new Set(state.info.slots.map((s) => s.id));
  let wanted;
  if (reset || !state.rows.length) {
    wanted = state.info.default_rows;
  } else {
    wanted = [];
    const used = [];
    for (const r of state.rows) {
      const slot = valid.has(r.slot) && !used.includes(r.slot)
        ? r.slot : await invoke("next_free_slot", { source: sourceId(), used });
      if (slot !== null) {
        wanted.push({ slot, colour: r.colour });
        used.push(slot);
      }
    }
  }
  while (state.rows.length > wanted.length) state.rows.pop().el.remove();
  state.rows.forEach((row, i) => {
    fillSlots(row);
    setRow(row, wanted[i].slot, wanted[i].colour);
  });
  for (const w of wanted.slice(state.rows.length)) await addRow(w.slot, w.colour);
  if (state.rows.length && !state.rows.some((r) => r.id === state.previewRow)) selectPreviewRow(state.rows[0]);
  updateAddLabel();
}

function fillSlots(row) {
  row.slotSelect.replaceChildren(...state.info.slots.map((s) => el("option", { value: s.id, textContent: s.label })));
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
}

async function addRow(slot = null, colour = null) {
  if (!state.game) return;
  if (slot === null) {
    slot = await invoke("next_free_slot", { source: sourceId(), used: state.rows.map((r) => r.slot) });
    if (slot === null) {
      await showInfo(APP_TITLE, "Every available slot already has a clone.");
      return;
    }
  }
  if (colour === null) {
    const taken = new Set(state.rows.map((r) => r.colour));
    colour = NEW_ROW_COLOURS.find((c) => !taken.has(c)) || "blue";
  }
  const row = { id: ++state.rowSeq };
  row.radio = el("input", { type: "radio", name: "preview-row", title: "Show this clone in the preview" });
  row.slotSelect = el("select");
  row.colourSelect = el("select");
  row.swatch = el("div", { className: "swatch" });
  row.remove = el("button", { className: "icon", textContent: "×", title: "Remove this clone" });
  row.el = el("div", { className: "clone-row" }, [row.radio, row.slotSelect, row.colourSelect, row.swatch, row.remove]);
  row.radio.addEventListener("change", () => selectPreviewRow(row, true));
  row.slotSelect.addEventListener("change", () => {
    row.slot = Number(row.slotSelect.value);
    schedulePreview();
  });
  row.colourSelect.addEventListener("change", () => colourChanged(row));
  row.remove.addEventListener("click", () => removeRow(row));
  fillSlots(row);
  setRow(row, slot, colour);
  state.rows.push(row);
  $("rows").append(row.el);
  if (state.rows.length === 1) selectPreviewRow(row);
  updateAddLabel();
  schedulePreview();
}

function removeRow(row) {
  row.el.remove();
  state.rows.splice(state.rows.indexOf(row), 1);
  if (state.rows.length && state.previewRow === row.id) selectPreviewRow(state.rows[0]);
  updateAddLabel();
  schedulePreview();
}

function updateAddLabel() {
  $("btn-add").textContent = state.rows.length ? "+ Add another clone" : "+ Add a clone";
}

function selectPreviewRow(row, refresh = false) {
  state.previewRow = row.id;
  row.radio.checked = true;
  if (refresh) schedulePreview();
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
  picker.click();
}

function sourceChanged() {
  updateSourceLabels();
  $("preview-chip").textContent = sourceName();
  if (state.game) loadSource(false);
}

// ---------------------------------------------------------------- preview
/** Ask for a new preview; quick successive changes (dragging the slider) coalesce. */
function schedulePreview(now = false) {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(requestPreview, now ? 1 : 40);
}

async function requestPreview() {
  const row = state.rows.find((r) => r.id === state.previewRow);
  if (!state.game || !row || state.infoSource !== sourceId()) return;
  const src = sourceId(), seq = ++state.previewSeq;
  if (!state.previewed.has(src)) $("preview-caption").textContent = `Loading ${sourceName(src)}'s textures…`;
  const request = { source: src, slot: row.slot, colour: row.colour, keeplum: keeplum(), regions: tickedRegions() };
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
    return;
  }
  state.previewed.add(src);
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
  return {
    game: $("game-path").value,
    output: $("out-path").value.trim(),
    source: sourceId(),
    clones: state.rows.map((r) => ({ slot: r.slot, colour: r.colour })),
    keeplum: keeplum(),
    regions: tickedRegions(),
    ui_source: $("chk-ui").checked,
    copy_stats: $("chk-stats").checked,
    keep_colour_word: $("chk-colour").checked,
    keep_work: $("chk-keep").checked,
  };
}

async function create() {
  if (!state.game || state.busy || state.infoSource !== sourceId()) return;   // the source is still loading
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
  log("---- creating " + job.output);
  log("the full log is also saved to " + withSuffix(job.output, ".log"));
  await invoke("create", { job });
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
  setEnabled(state.game !== null);
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
  $("source").replaceChildren(...state.sources.map((s) => el("option", { value: s.id, textContent: s.name })));
  updateSourceLabels();
  setEnabled(false);

  $("btn-iso").addEventListener("click", chooseIso);
  $("btn-dir").addEventListener("click", chooseFolder);
  $("source").addEventListener("change", sourceChanged);
  $("btn-add").addEventListener("click", () => serial(() => addRow()));
  $("btn-adv").addEventListener("click", toggleAdvanced);
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
  if (options.selftest) {
    await import("./selftest.js").then((m) => m.run(options, { state, startLoad }));
  }
}

main();
