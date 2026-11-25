// Stock roster IDs have the same faces in either planner. A painted clone keeps its source's HUD face.
const pending = new Map();
let portraits = [];

function setImage(face) {
  const source = face.dataset.source;
  if (!portraits[source]) return;
  const image = document.createElement("img");
  image.className = "roster-portrait";
  image.alt = "";
  image.draggable = false;
  image.width = image.height = 50;
  image.src = "data:image/png;base64," + portraits[source];
  face.replaceChildren(image);
}

export function rosterFace(source, name) {
  const face = document.createElement("span");
  face.className = "roster-face";
  face.dataset.source = source;
  face.setAttribute("aria-hidden", "true");
  if (portraits[source]) {
    setImage(face);
  } else {
    const initials = document.createElement("span");
    initials.className = "roster-initials";
    initials.textContent = name.split(/[\s-]+/).map(part => part[0]).slice(0,2).join("");
    face.append(initials);
  }
  return face;
}

function fetchPortraits(sourcePath = null) {
  if (portraits.length) return Promise.resolve(true);
  if (pending.has(sourcePath)) return pending.get(sourcePath);
  const request = window.__TAURI__.core.invoke("load_roster_portraits", {sourcePath}).then(result => {
    if (result.length === 54 && !portraits.length) {
      portraits = result;
      for (const face of document.querySelectorAll("#tournament-plan .roster-face")) setImage(face);
      document.querySelector(".portrait-tools").hidden = true;
    }
    return portraits.length > 0;
  }).finally(() => pending.delete(sourcePath));
  pending.set(sourcePath, request);
  return request;
}

export function initPortraits(tools) {
  const buttons = tools.querySelectorAll("button");
  const message = tools.querySelector(".portrait-message");
  let gamePath;
  let checked = false;
  const readCache = async () => {
    try {
      if (await fetchPortraits()) return;
      if (gamePath && await fetchPortraits(gamePath)) return;
    } catch {
      // A missing/unsupported image source leaves both rosters usable.
    } finally {
      checked = true;
      tools.hidden = !!portraits.length;
    }
  };
  window.addEventListener("studio-game-opened", event => {
    gamePath = event.detail.path;
    if (!portraits.length) void readCache();
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
        message.textContent = "Reading the character portraits…";
        await fetchPortraits(file);
      } catch (error) {
        message.textContent = typeof error === "string" ? error : error.message;
      } finally {
        for (const control of buttons) control.disabled = false;
      }
    });
  }
  return () => { if (!checked) void readCache(); };
}
