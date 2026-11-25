// These guides stay open while a player tries each control on their own roster.
const pickSide = {
  title:"Start your own team",
  target:".tourney-tabs",
  text:"Use either the Away or Home sheet for your nine players. You can send just this team. The organizer chooses which side it plays when importing it in Tournament Mode."
};
const arrange = {
  title:"Set who bats first and who plays where",
  target:".tourney-order-panel, .tourney-field-panel",
  text:"Drag a portrait up or down the batting order. Drag one fielder onto another to swap their positions. You can also click a portrait and use the Batting spot and Field position menus."
};
const nameAndPaint = {
  title:"Name a player or customize a clone",
  target:".tourney-row.is-selected .tourney-name, .tourney-fielder.is-selected .tourney-name",
  text:"Click a name on the field or in the batting order and type. Both names update together. Select the player’s portrait to attach your painted Studio PNGs under Custom textures or change that player’s stats."
};
const save = {
  title:"Send your team to the organizer",
  target:".tourney-save",
  text:side => "Click Export team… for the " + side + " sheet. Give your team file a name in Save As, then send the .team.json file to the organizer. It includes your lineup, names, positions, stat edits and custom PNGs. Your game files stay on your computer."
};
const reopen = {
  title:"Reuse a team you received or exported",
  target:".tourney-library, .tourney-import",
  text:side => "Imported and exported teams appear in Team library. Pick one, then click Load into " + side + ". Save both sides together under Matchup files."
};
const steps = {
  plan: [pickSide, {
    title:"Choose your nine players", target:".draft-player-pool",
    text:"Click a character in the pool to fill the highlighted spot. The next empty spot is selected for you. For a specific assignment, drag a character onto a batting spot or field position. You can pick the same character twice."
  }, arrange, nameAndPaint, save, {
    title:"Pick up where you left off", target:".tourney-switch-mode",
    text:"Your draft saves automatically on this device. The organizer can import your team into either side of a tournament. To work on your own matchup, open Tournament Mode and import the other player’s team on the opposite side."
  }],
  tournament: [{
    title:"Import a player’s team", target:".tourney-tabs, .tourney-import",
    text:side => "Choose Away or Home, then click Import team into " + side + "… and open the player’s .team.json file. Their lineup, names and customizations fill that side. Switch sides to import the other team."
  }, {
    title:"Change the players in this lineup", target:".tourney-source",
    text:"Select a batting spot or field position. Pick a character from the Character menu or the pool. Fill more spots the same way. You can pick a character more than once."
  }, arrange, nameAndPaint, save, reopen, {
    title:"Build the game for both players", target:".tourney-build",
    text:"Fill all nine spots on each side, choose one captain per team, and enter each player’s Rio username. Click Build tournament matchup…, choose your original USA disc image (.iso), then pick an output folder."
  }, {
    title:"Use the two lineups in Project Rio", target:".tourney-switch-mode",
    text:"On both computers, open the finished-game roster in Game Plan. Choose the built game, load hud.json, then click Use in Rio… with Rio closed. Join a private No Game Mode lobby. The host clicks Assign Controller Ports, assigns each player one controller, then enables Fast Reset. If the ports change, turn Fast Reset off and on again."
  }]
};

export function initPlannerGuide(planner) {
  const header = planner.querySelector(".planner-guide");
  const panel = header.querySelector("#planner-guide-panel");
  const start = header.querySelector(".planner-guide-start");
  const saveHelp = header.querySelector(".planner-guide-save-help");
  const lead = header.querySelector(".planner-guide-lead");
  const previous = panel.querySelector(".planner-guide-prev");
  const next = panel.querySelector(".planner-guide-next");
  const close = panel.querySelector(".planner-guide-close");
  const progress = panel.querySelector(".planner-guide-progress");
  const title = panel.querySelector(".planner-guide-title");
  const body = panel.querySelector(".planner-guide-body");
  let mode = "plan", side = "Away", index = 0;

  function highlight(scroll = false) {
    const step = steps[mode][index];
    for (const old of planner.querySelectorAll(".guide-highlight")) old.classList.remove("guide-highlight");
    if (panel.hidden) return;
    const targets = planner.querySelectorAll(step.target);
    for (const target of targets) target.classList.add("guide-highlight");
    if (scroll && targets[0]?.closest(".tourney-editor")) targets[0].scrollIntoView({block:"nearest", inline:"nearest"});
  }
  function showStep(scroll = false) {
    const step = steps[mode][index];
    progress.textContent = "Step " + (index + 1) + " of " + steps[mode].length;
    title.textContent = step.title;
    body.textContent = typeof step.text === "function" ? step.text(side) : step.text;
    previous.disabled = index === 0;
    next.textContent = index === steps[mode].length - 1 ? "Finish walkthrough" : "Next";
    highlight(scroll);
  }
  function finish(restoreFocus) {
    panel.hidden = true;
    start.hidden = false;
    saveHelp.hidden = false;
    start.setAttribute("aria-expanded", "false");
    highlight();
    if (restoreFocus) opener.focus();
  }
  let opener = start;
  function begin(trigger, step) {
    opener = trigger;
    index = step;
    panel.hidden = false;
    start.hidden = true;
    saveHelp.hidden = true;
    start.setAttribute("aria-expanded", "true");
    showStep(true);
    title.focus({preventScroll:true});
  }
  start.onclick = () => begin(start, 0);
  saveHelp.onclick = () => begin(saveHelp, steps[mode].indexOf(save));
  previous.onclick = () => { if (index > 0) { index--; showStep(true); } };
  next.onclick = () => { if (index + 1 === steps[mode].length) finish(true); else {index++; showStep(true);} };
  close.onclick = () => finish(true);
  planner.addEventListener("keydown", event => {
    if (panel.hidden || event.key !== "Escape" || event.defaultPrevented || event.isComposing ||
        event.target.closest("input, select, dialog:modal")) return;
    event.preventDefault();
    finish(true);
  });
  return {
    openMode(newMode) {
      finish(false);
      mode = newMode;
      index = 0;
      lead.textContent = mode === "plan" ? "Game Plan" : "Tournament Mode";
      start.textContent = mode === "plan" ? "Game Plan walkthrough" : "Tournament walkthrough";
    },
    update(newSide) {
      const switched = side !== newSide;
      side = newSide;
      header.querySelector(".planner-guide-hint").textContent = mode === "plan"
        ? "Draft your players on " + side + ". Export team… shares this lineup and its customizations."
        : "Choose the side each player will play, then import their .team.json file.";
      if (!panel.hidden && switched) showStep();
      else if (!panel.hidden) highlight();
    }
  };
}
