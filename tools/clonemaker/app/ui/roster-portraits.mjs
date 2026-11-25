import {renderCustomPortrait} from "./clone-head-render.mjs";

let stock = [];
let modelsReady = false;
let assetCheckComplete = false;
let tools;
const pendingAssets = new Map();
const customFaces = new WeakMap();
const renders = [];
const missingModels = new Set();
const maxCachedBytes = 32 * 1024 * 1024;

function pngImage(className, png) {
  const image = document.createElement("img");
  image.className = className;
  image.alt = "";
  image.draggable = false;
  image.src = "data:image/png;base64," + png;
  return image;
}

function setHead(face, png) {
  const image = pngImage("roster-portrait", png);
  if (face.firstElementChild) face.firstElementChild.replaceWith(image);
  else face.append(image);
}

function status(face, state, text = "") {
  face.dataset.appearance = state;
  face.title = text;
  const notice = customFaces.get(face)?.notice;
  if (notice) {
    notice.textContent = text;
    notice.hidden = !text;
  }
}

function setDetail(face, look, key) {
  const current = face.querySelector(".roster-texture-detail");
  if (current?.dataset.texture === key) return;
  current?.remove();
  if (!key) return;
  const image = pngImage("roster-texture-detail", look.textures[key]);
  image.dataset.texture = key;
  face.append(image);
}

function bestDetail(look, peers, fallback) {
  let best = fallback, score = 0;
  for (const key of look.keys) {
    const count = peers.filter(peer => peer.textures[key] !== look.textures[key]).length;
    if (count > score) { best = key; score = count; }
  }
  return best;
}

function pixelIdentity(render, key) {
  render.pixelKeys ??= new Map();
  if (!render.pixelKeys.has(key)) {
    const identity = (async () => {
      const image = pngImage("", render.textures[key]);
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(image, 0, 0);
      if (!globalThis.crypto?.subtle) return canvas.toDataURL();
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const digest = await crypto.subtle.digest("SHA-256", pixels.data);
      return Array.from(new Uint8Array(digest)).join(",");
    })().catch(() => render.textures[key]);
    render.pixelKeys.set(key, identity);
  }
  return render.pixelKeys.get(key);
}

async function matchingHeadDetail(look, peers) {
  let best, score = 0;
  for (const key of look.keys) {
    let count = 0;
    for (const peer of peers) {
      if (look.textures[key] === peer.textures[key]) continue;
      if (!peer.textures[key] || await pixelIdentity(look.render,key) !== await pixelIdentity(peer.render,key)) count++;
    }
    if (count > score) { best = key; score = count; }
  }
  return best;
}

function applyDetail(look) {
  for (const face of look.faces) {
    if (face.dataset.appearance !== "custom") continue;
    setDetail(face, look, look.detail);
    status(face, "custom", look.detail ? "Texture detail · " + look.detail : "");
  }
}

function updateDetails(look) {
  const result = look.render?.result;
  if (!result) return;
  // Painted heads can match when a team shares face paint but selects different uniforms.
  if (result.headChanged) {
    const matches = look.peers.filter(peer => peer.render !== look.render && peer.render?.result?.png === result.png);
    if (!look.matches || matches.length !== look.matches.length || matches.some((peer, i) => peer !== look.matches[i])) {
      look.matches = matches;
      look.detail = undefined;
      if (matches.length) matchingHeadDetail(look, matches).then(key => {
        if (look.matches !== matches) return;
        look.detail = key;
        applyDetail(look);
      });
    }
  } else {
    look.detail = bestDetail(look, look.peers, look.keys[0]);
  }
  applyDetail(look);
}

export function lineupFaces(rows, names) {
  const looks = new Map();
  for (const row of rows) {
    const keys = Object.keys(row.textures || {}).sort();
    if (keys.length) looks.set(row, {source:row.source, textures:row.textures, keys, faces:[]});
  }
  for (const look of looks.values()) {
    look.peers = [...looks.values()].filter(other => other !== look && other.source === look.source);
  }
  return (row, notice) => rosterFace(row.source, names.get(row.source), looks.get(row), notice);
}

function trimRenderCache() {
  let bytes = renders.reduce((n, r) => n + r.bytes, 0);
  for (let i = 0; i < renders.length && (renders.length > 64 || bytes > maxCachedBytes);) {
    const r = renders[i];
    if (!r.finished) { i++; continue; }
    bytes -= r.bytes;
    renders.splice(i, 1);
  }
}

function renderFor(source, textures) {
  const keys = Object.keys(textures).sort();
  const i = renders.findIndex(r => r.source === source && keys.length === r.keys.length &&
    keys.every((key, n) => key === r.keys[n] && textures[key] === r.textures[key]));
  if (i !== -1) {
    const [r] = renders.splice(i, 1);
    renders.push(r);
    return r;
  }
  // Canonical names AND full PNG contents identify an appearance across edits and team imports.
  const r = {source, keys, textures, bytes:keys.reduce((n,k) => n + textures[k].length, 0), finished:false};
  renders.push(r);
  r.promise = Promise.resolve().then(() => renderCustomPortrait(source, textures)).then(result => {
    r.result = result;
    r.bytes += result?.png?.length || 0;
  }, error => { r.error = error?.message || String(error); }).finally(() => {
    r.finished = true;
    trimRenderCache();
  });
  return r;
}

function paint(face) {
  const binding = customFaces.get(face);
  const {look} = binding;
  if (!modelsReady) {
    if (assetCheckComplete) {
      setDetail(face, look, bestDetail(look, look.peers, look.keys[0]));
      status(face, "unavailable", "Use “Add player portraits…” to load the model for this head.");
    } else status(face, "pending", "Loading portrait assets…");
    return;
  }
  const render = renderFor(look.source, look.textures);
  if (binding.render === render) return;
  binding.render = look.render = render;
  const apply = () => {
    if (binding.render !== render) return;
    if (!render.result) {
      setDetail(face, look, bestDetail(look, look.peers, look.keys[0]));
      if (!render.error) {
        missingModels.add(look.source);
        tools.hidden = false;
      }
      status(face, "unavailable", "Head preview unavailable. " + (render.error || "Use “Add player portraits…” to reload the model."));
      return;
    }
    if (render.result.headChanged || !stock[look.source]) setHead(face, render.result.png);
    status(face, "custom");
    updateDetails(look);
    for (const peer of look.peers) updateDetails(peer);
  };
  if (render.finished) apply();
  else {
    status(face, "pending", "Rendering custom head…");
    render.promise.then(() => { if (face.isConnected) apply(); });
  }
}

export function rosterFace(source, name, look, notice) {
  const face = document.createElement("span");
  face.className = "roster-face";
  face.dataset.source = source;
  face.dataset.appearance = "stock";
  face.setAttribute("aria-hidden", "true");
  if (stock[source]) setHead(face, stock[source]);
  else {
    const initials = document.createElement("span");
    initials.className = "roster-initials";
    initials.textContent = name.split(/[\s-]+/).map(part => part[0]).slice(0,2).join("");
    face.append(initials);
  }
  if (look) {
    customFaces.set(face, {look, notice});
    look.faces.push(face);
    paint(face);
  }
  return face;
}

function syncFaces() {
  for (const face of document.querySelectorAll("#tournament-plan .roster-face")) {
    const png = stock[face.dataset.source];
    if (png && face.firstElementChild?.classList.contains("roster-initials")) setHead(face, png);
    if (customFaces.has(face)) paint(face);
  }
  tools.hidden = !!stock.length && modelsReady && !missingModels.size;
}

function readAssets(sourcePath = null) {
  if (pendingAssets.has(sourcePath)) return pendingAssets.get(sourcePath);
  const invoke = window.__TAURI__.core.invoke;
  const requests = [];
  if (!stock.length) requests.push(invoke("load_roster_portraits", {sourcePath}).then(result => {
    if (result.length === 54 && !stock.length) {
      stock = result;
      syncFaces();
    }
  }));
  if (!modelsReady || missingModels.size) requests.push(invoke("prepare_custom_portraits", {sourcePath}).then(ready => {
    if (!ready) return;
    modelsReady = true;
    if (missingModels.size) {
      for (let i = renders.length - 1; i >= 0; i--) {
        if (missingModels.has(renders[i].source) && renders[i].finished && !renders[i].result) renders.splice(i, 1);
      }
      missingModels.clear();
    }
    syncFaces();
  }));
  const task = Promise.allSettled(requests).then(results => {
    const failed = results.find(result => result.status === "rejected");
    if (failed) throw failed.reason;
  }).finally(() => {
    pendingAssets.delete(sourcePath);
    assetCheckComplete = !pendingAssets.size;
    syncFaces();
  });
  pendingAssets.set(sourcePath, task);
  return task;
}

export function initPortraits(controls) {
  tools = controls;
  const buttons = tools.querySelectorAll("button");
  const message = tools.querySelector(".portrait-message");
  let gamePath;
  let opened = false;
  const refresh = async () => {
    try {
      await readAssets();
      if (gamePath && (!stock.length || !modelsReady || missingModels.size)) await readAssets(gamePath);
    } catch (error) {
      message.textContent = error?.message || String(error);
    }
  };
  window.addEventListener("studio-game-opened", event => {
    gamePath = event.detail.path;
    if (opened) void refresh();
  });
  for (const button of buttons) {
    button.addEventListener("click", async () => {
      const folder = button.dataset.kind === "folder";
      for (const control of buttons) control.disabled = true;
      message.textContent = "";
      try {
        const file = await window.__TAURI__.dialog.open(folder
          ? {directory: true, title: "Choose the original extracted USA game folder"}
          : {title: "Choose the original USA Mario Superstar Baseball disc", filters: [{name:"GameCube image",extensions:["iso","gcm"]}]});
        if (!file) return;
        gamePath = file;
        message.textContent = "Reading player faces and models…";
        await readAssets(file);
        message.textContent = stock.length && modelsReady && !missingModels.size ? "" : "Choose your original USA disc or extracted game folder.";
      } catch (error) {
        message.textContent = error?.message || String(error);
      } finally {
        for (const control of buttons) control.disabled = false;
      }
    });
  }
  return () => {
    if (!opened) { opened = true; void refresh(); }
  };
}
