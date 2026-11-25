// These guides stay open while a player tries each control on their own roster.
const pickSide = {
  title:"Choose the team you want to edit",
  target:".tourney-tabs",
  text:"Click Away or Home. Each side has nine players. The batting order, field and player details all show the side you picked."
};
const arrange = {
  title:"Set who bats first and who plays where",
  target:".tourney-order-panel, .tourney-field-panel",
  text:"Drag a portrait up or down the batting order. Drag one fielder onto another to swap their positions. You can also click a portrait and use the Batting spot and Field position menus."
};
const nameAndPaint = {
  title:"Name a player or customize a clone",
  target:".tourney-row.is-selected .tourney-name, .tourney-fielder.is-selected .tourney-name",
  text:"Click a name on the field or in the batting order and type. Both names update together. Click a portrait to add custom textures or edit stats for just that player."
};
const save = {
  title:"Save a team you can share",
  target:".tourney-lobby, .tourney-save",
  text:side => "Enter the " + side + " player’s Project Rio username under Rio lobby name. Pick one eligible captain, such as Mario or DK. Click Save " + side + " team… and choose a filename. Repeat for the other side to save both teams."
};
const reopen = {
  title:"Edit a team you saved earlier",
  target:".tourney-library, .tourney-import",
  text:side => "Under Team library, pick your team from My saved teams and click Load into " + side + ". Edit it, then save again. Choose the same file to update it or a new filename for another version. Use Import team file… to open a file someone sent you."
};
const steps = {
  plan: [pickSide, {
    title:"Choose your nine players", target:".draft-player-pool",
    text:"Click the portrait of the player you want to replace. Click a character in the pool to put them in that spot. You can pick the same character twice for two DKs."
  }, arrange, nameAndPaint, save, {
    title:"Pick up where you left off", target:".tourney-switch-mode",
    text:"Your draft saves automatically on this device. Click Import team file… to open a team file. Open Tournament mode to find My saved teams in the team library or to build a game from both lineups."
  }],
  tournament: [{
    title:"Choose a side and load a lineup", target:".tourney-tabs, .tourney-library",
    text:side => "Your Game Plan lineups are already here. To use a different team, click Away or Home, pick a team under Team library, then click Load into " + side + ". You can also import a team file someone sent you."
  }, {
    title:"Change the players in this lineup", target:".tourney-source",
    text:"Click a player’s portrait. Use the Character menu to put a different character in that spot. You can choose a character who already plays for this team."
  }, arrange, nameAndPaint, save, reopen, {
    title:"Build the game for both players", target:".tourney-build",
    text:"Set up Away and Home with different Rio lobby names. Click Build tournament matchup…, choose your original USA game disc image (.iso), then pick an output folder. The cloner writes the game and the Rio lineup there."
  }, {
    title:"Use the two lineups in Project Rio", target:".tourney-switch-mode",
    text:"On each computer, go to Game Plan and open the finished-game roster. Choose the built game image, click Load matchup… and open hud.json from the build folder. Close Rio, then click Use in Rio… to install the lineup. The roster editor shows the lobby settings."
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
      header.querySelector(".planner-guide-hint").textContent =
        "Both teams autosave on this device. Save " + side + " team… makes a file for the " + side + " lineup.";
      if (!panel.hidden && switched) showStep();
      else if (!panel.hidden) highlight();
    }
  };
}
