import {rosterFace} from "./roster-portraits.mjs";

let query = "";
export function resetPoolSearch() { query = ""; }
const normalized = text => text.toLowerCase().replace(/[^a-z0-9]+/g, "");

export function playerPool({names, selectedSource, selectedBatter, onChoose, onDragStart, onDragEnd}) {
  const section = document.createElement("section");
  section.className = "draft-player-pool";
  const heading = document.createElement("h3");
  heading.textContent = "Character pool";
  const instructions = document.createElement("p");
  instructions.textContent = selectedSource === null
    ? "Pick a character for spot " + selectedBatter + ". Each pick moves to the next open spot."
    : "Choose a replacement for batting spot " + selectedBatter + ", or drag a character onto another spot.";
  const search = document.createElement("input");
  search.type = "search";
  search.placeholder = "Find a character";
  search.setAttribute("aria-label", "Search character pool");
  search.value = query;
  const candidates = document.createElement("div");
  candidates.className = "draft-player-choices";
  const populate = () => {
    query = search.value;
    const needle = normalized(query);
    candidates.replaceChildren();
    for (const [id, name] of names) {
      const initials = name.split(/[\s-]+/).map(part => part[0]).join("");
      if (![name, initials].some(text => normalized(text).includes(needle))) continue;
      const tile = document.createElement("button");
      tile.type = "button";
      tile.className = "draft-player-choice";
      const caption = document.createElement("span");
      caption.className = "pool-name";
      caption.textContent = name;
      tile.append(rosterFace(id, name), caption);
      tile.dataset.character = id;
      tile.draggable = true;
      tile.setAttribute("aria-label", "Place " + name + " in batting spot " + selectedBatter);
      tile.setAttribute("aria-pressed", String(id === selectedSource));
      tile.addEventListener("click", () => onChoose(id));
      tile.addEventListener("dragstart", event => onDragStart(event, id));
      tile.addEventListener("dragend", onDragEnd);
      candidates.append(tile);
    }
    if (!candidates.childElementCount) {
      const empty = document.createElement("p");
      empty.textContent = "No matching characters";
      candidates.append(empty);
    }
  };
  search.addEventListener("input", populate);
  populate();
  section.append(heading, instructions, search, candidates);
  return section;
}
