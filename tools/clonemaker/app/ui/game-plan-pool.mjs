let query = "";
const normalized = text => text.toLowerCase().replace(/[^a-z0-9]+/g, "");

export function playerPool({names, selectedSource, selectedBatter, onChoose, onDragStart, onDragEnd}) {
  const section = document.createElement("section");
  section.className = "draft-player-pool";
  const heading = document.createElement("h3");
  heading.textContent = "Character pool";
  const instructions = document.createElement("p");
  instructions.textContent = "Drop a character on the lineup or field. Click to place one in batting spot " + selectedBatter + ".";
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
      tile.textContent = name;
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
