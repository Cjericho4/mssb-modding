// --selftest (src/selftest.rs): drive every feature of the page against the stand-in game the way a
// user would (clicks, choices), check what the window shows, time resizing, then report and exit.
const { invoke } = window.__TAURI__.core;
const { getCurrentWindow, LogicalSize } = window.__TAURI__.window;

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** Resolves once the next frame has been painted. */
const painted = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));

const WIDGETS = ["pill", "btn-iso", "btn-dir", "game-path", "status", "source", "regions-box", "rows", "btn-add",
  "btn-export", "btn-blender", "blender", "blender-source", "blender-all", "blender-go", "blender-cancel", "btn-adv", "adv", "chk-ui", "chk-colour", "chk-stats", "keeplum", "chk-keep", "out-path", "btn-out", "btn-go",
  "run-box", "progress-bar", "btn-cancel", "preview-chip", "preview-caption", "img-before", "img-after", "log"];

export async function run(options, app) {
  const lines = [];
  let ok = true;
  const say = (line) => lines.push("selftest: " + line);
  const check = (cond, what) => {
    if (!cond) {
      ok = false;
      say("FAIL " + what);
    }
    return cond;
  };
  const waitFor = async (cond, what, ms = 10000) => {
    for (const end = performance.now() + ms; !cond();) {
      if (performance.now() > end) return check(false, "timed out waiting for " + what);
      await sleep(20);
    }
    return true;
  };
  const shot = async (name) => {
    await painted();
    await sleep(150);
    const failed = await invoke("selftest_mark", { name });
    if (failed) say(`no screenshot ${name}: ${failed}`);
  };
  const sep = options.dir.includes("\\") ? "\\" : "/";
  const inDir = (name) => options.dir + sep + name;
  const rows = () => [...document.querySelectorAll("#rows .clone-row")];
  const caption = () => $("preview-caption").textContent;
  const choose = (select, value) => {
    select.value = String(value);
    select.dispatchEvent(new Event("change"));
  };
  const dialogButton = (text) => [...document.querySelectorAll("#dialog-buttons button")].find((b) => b.textContent === text);
  const dialogOpen = (text) => !$("dialog").hidden && $("dialog-text").textContent.includes(text);
  const logHas = (text) => $("log").textContent.includes(text);
  const stacked = () => $("card-preview").getBoundingClientRect().top >= $("card-save").getBoundingClientRect().bottom;

  try {
    check(WIDGETS.every((id) => $(id)), "every widget is on the page: missing " + WIDGETS.filter((id) => !$(id)).join(", "));
    for (const font of ["600 16px Rajdhani", "700 16px Rajdhani", "13px Inter", "500 13px Inter", "600 10px Inter", "12px 'JetBrains Mono'"]) {
      check((await document.fonts.load(font)).length > 0, "bundled font " + font);
    }
    const { dialog } = window.__TAURI__;
    check(typeof dialog?.open === "function" && typeof dialog?.save === "function", "the file dialogs are available");
    say(`window ${innerWidth}x${innerHeight}, device pixel ratio ${devicePixelRatio}, screen ${screen.availWidth}x${screen.availHeight}`);
    check($("btn-go").disabled && $("source").disabled && $("btn-export").disabled && $("btn-blender").disabled && !$("btn-iso").disabled, "inputs wait for a game");
    await shot("empty");

    // narrow window: the preview goes under the form; wide again: two columns
    await resizeTo(1000, 700);
    check(stacked(), "narrow window stacks the preview under the form");
    await resizeTo(1320, 780);
    if (innerWidth >= 1200) check(!stacked(), "wide window shows the preview beside the form");
    else say(`screen too small for the two-column check (window ${innerWidth} px wide)`);

    // a game that can't be used
    await app.startLoad(inDir("game.rvz"));
    check($("status").textContent.startsWith("✖ File not found") && $("pill").textContent === "Game not usable", "missing game: " + $("status").textContent);
    check($("btn-go").disabled, "no game, no create");
    await shot("bad_game");

    if (options.game) await walkThrough();
    else say("no --game: the features that need the game were not tested");

    // a long done message (several characters' pick hints, long paths) fits the window at any size (#20)
    await longDialog("dialog_narrow", 640, 560);
    await longDialog("dialog_long", 1320, 780);
    await longDialog("dialog_scroll", 640, 560, 4);

    // a 1366 x 768 screen at 125 %
    await resizeTo(1093, 614);
    check(stacked(), "1093 px wide is stacked");
    await shot("small_top");

    // resizing: time from each new size to the next painted frame
    const longTasks = [];
    const observer = PerformanceObserver.supportedEntryTypes?.includes("longtask")
      ? new PerformanceObserver((l) => longTasks.push(...l.getEntries())) : null;
    observer?.observe({ entryTypes: ["longtask"] });
    const medians = [await bench("narrow", 640, 560)];
    if (screen.availWidth >= 1600 && screen.availHeight >= 900) medians.push(await bench("wide", 1200, 700));
    observer?.disconnect();
    if (observer) say(`resize: ${longTasks.length} long tasks (> 50 ms) on the page during the drags`);
    check(medians.every((m) => m < 100), "resizing stays under 100 ms per step");
    await resizeTo(1320, 780);
  } catch (e) {
    check(false, "exception: " + (e.stack || e.error || e));
  }
  say(ok ? "OK" : "FAILED");
  await invoke("selftest_report", { ok, lines });

  async function walkThrough() {
    await app.startLoad(options.game);
    await waitFor(() => $("pill").textContent === "GYQE01 ready", "the game check");
    check($("status").textContent === "✔ Mario Superstar Baseball (USA) — OK", "status line: " + $("status").textContent);
    await waitFor(() => rows().length === 4, "the four default clone rows");
    const firstSlot = rows()[0].querySelector("select");
    check(firstSlot.selectedOptions[0].textContent === "★ Blue Toad" &&
      firstSlot.title === "Blue Toad — recommended (an extra Toad colour)",
      "Blue Toad is visibly recommended and keeps its full explanation");
    check(document.querySelector(".slot-legend")?.textContent === "★ recommended · × in use",
      "slot markers have a visible legend");
    check(rows().map((r) => r.querySelectorAll("select")[1].value).join() === "blue,green,purple,yellow", "default colours");
    check($("out-path").value.endsWith(" - Clones.iso"), "default output: " + $("out-path").value);
    await waitFor(() => !$("img-after").hidden && caption().startsWith("All 9 textures of Mario"), "the first preview");
    check(document.querySelectorAll("#regions-box input").length === 2, "Mario's two colour regions");
    await shot("idle");
    await resizeTo(640, 560);
    const slotFont = getComputedStyle(firstSlot);
    const measure = document.createElement("canvas").getContext("2d");
    measure.font = slotFont.font;
    check(measure.measureText(firstSlot.selectedOptions[0].textContent).width + 42 < firstSlot.clientWidth,
      "the selected slot name and marker fit at 640 px");
    await shot("slots_narrow");
    await resizeTo(1320, 780);
    await shot("hover");

    // Advanced
    $("btn-adv").click();
    check(!$("adv").hidden && $("btn-adv").textContent === "Advanced options  ▾", "Advanced opens");
    check($("chk-stats").nextElementSibling.textContent === "Give clones Mario's stats", "Advanced names the source");
    $("chk-ui").click();
    check($("chk-colour").disabled, "the colour word needs \"Show clones as\"");
    $("chk-ui").click();
    check(!$("chk-colour").disabled, "the colour word comes back");
    $("keeplum").value = "0.8";
    $("keeplum").dispatchEvent(new Event("input"));
    check($("keeplum-value").textContent === "0.80", "keep shading shows its value");
    await shot("advanced");
    $("keeplum").value = "0.5";
    $("keeplum").dispatchEvent(new Event("input"));

    // colour regions: at least one stays ticked
    const regionBoxes = () => [...document.querySelectorAll("#regions-box input")];
    regionBoxes()[1].click();
    await waitFor(() => caption().endsWith("(changes: red (cap, shirt) + blue)"), "the preview with both regions");
    regionBoxes()[0].click();
    regionBoxes()[1].click();
    check(regionBoxes()[1].checked && !regionBoxes()[0].checked, "the last ticked region can't be unticked");
    await waitFor(() => caption().endsWith("(changes: blue)"), "the preview with one region");
    regionBoxes()[0].click();
    regionBoxes()[1].click();

    // animations from another character: only skeletons that fit are offered
    const animSelect = (i = 0) => rows()[i].querySelector("select.anims");
    const animIds = [...animSelect().options].map((o) => Number(o.value));
    check(animSelect().options[0].textContent === "Mario (own)" && animSelect().value === "0", "a row starts with Mario's own animations");
    check(animIds.includes(1) && animIds.includes(13) && !animIds.includes(2) && !animIds.includes(29),
      "Luigi's and the Toads' animations are offered (once), DK's (bones Mario lacks) are not: " + animIds.join());
    choose(animSelect(1), 1);
    await waitFor(() => caption().endsWith("; animations from Luigi"), "the caption names the animations");
    check(rows()[1].querySelector("input").checked && animSelect(1).title.includes("Some bones differ"), "the row previews and explains: " + animSelect(1).title);
    await shot("animations");
    choose(animSelect(1), 0);
    await waitFor(() => caption().endsWith(")") && rows()[1].querySelector("input").checked, "own animations again");
    choose(animSelect(0), 1);

    // another source keeps the rows (a choice equal to the new source becomes its own), then back
    choose($("source"), 1);
    await waitFor(() => caption().includes("of Luigi") && $("preview-chip").textContent === "Luigi", "Luigi's preview");
    check(rows().length === 4 && $("chk-stats").nextElementSibling.textContent === "Give clones Luigi's stats", "Luigi keeps the rows");
    check(animSelect().value === "1" && animSelect().options[0].textContent === "Luigi (own)" && app.state.groups[0].rows[0].anims === null,
      "Luigi's own animations");
    choose($("source"), 0);
    await waitFor(() => caption().includes("of Mario"), "Mario's preview again");
    choose(animSelect(), 1);

    // colours and preview pick
    choose(rows()[1].querySelectorAll("select")[1], "pink");
    await waitFor(() => caption().includes("in Pink"), "the preview follows the recoloured row");
    check(rows()[1].querySelector("input").checked, "recolouring a row previews it");
    check(rows()[1].querySelector(".swatch").style.background !== "", "the swatch shows the colour");

    // adding and removing clones
    for (let i = 0; i < 4; i++) $("btn-add").click();
    await waitFor(() => rows().length === 8, "eight rows");
    check($("btn-add").textContent === "+ Add another clone", "add button label");
    check(new Set(rows().map((r) => r.querySelector("select").value)).size === 8, "new rows take free slots");
    await shot("many_rows");
    for (const r of rows().slice(4)) r.querySelector("button.remove").click();
    rows()[1].querySelector("button.remove").click();
    check(rows().length === 3 && rows()[0].querySelector("input").checked, "removing the previewed row previews the first");
    $("btn-add").click();
    await waitFor(() => rows().length === 4, "back to four rows");

    // painted textures: export, a broken folder, then a painted picture on the first clone
    const textures = () => rows()[0].querySelector(".textures");
    check(textures().querySelector(".pick").textContent === "My textures…" && textures().querySelector(".clear").hidden,
      "a new row has no textures");
    const exporting = app.exportTextures(app.state.groups[0], options.dir);
    await waitFor(() => dialogOpen("Saved 9 pictures of Mario to"), "the export dialog");
    await shot("textures_exported");
    dialogButton("OK").click();
    const exported = await exporting;
    check(exported?.count === 9 && exported.folder === inDir("Mario textures"), "export result: " + JSON.stringify(exported));
    check(logHas(`exported 9 textures of Mario to ${inDir("Mario textures")}`), "the export log line");
    const exportingBroken = app.exportTextures(app.state.groups[0], inDir("broken"));
    await waitFor(() => dialogOpen(inDir("broken")), "the second export dialog");
    dialogButton("OK").click();
    await exportingBroken;
    await invoke("selftest_edit_png", { path: [inDir("broken"), "Mario textures", "t04_64x64.png"].join(sep), how: "junk" });
    app.setRowTextures(app.state.groups[0].rows[0], [inDir("broken"), "Mario textures"].join(sep));
    $("out-path").value = inDir("Broken.iso");
    $("btn-go").click();
    await waitFor(() => dialogOpen("t04_64x64.png is not a PNG picture"), "the broken-texture error");
    check($("dialog-text").textContent.startsWith("Your textures in \"Mario textures\": "), "error names the folder: " + $("dialog-text").textContent);
    await shot("textures_error");
    dialogButton("OK").click();
    await invoke("selftest_edit_png", { path: [exported.folder, "t01_256x256.png"].join(sep), how: "paint" });
    app.setRowTextures(app.state.groups[0].rows[0], exported.folder);
    check(textures().classList.contains("set") && textures().querySelector(".pick").textContent === "Mario textures"
      && !textures().querySelector(".clear").hidden, "the row shows the folder");
    await shot("textures_row");
    textures().querySelector(".clear").click();
    check(!textures().classList.contains("set") && textures().querySelector(".pick").textContent === "My textures…", "clear");
    app.setRowTextures(app.state.groups[0].rows[0], exported.folder);

    // Export 3D Model: the button opens the choice (the shown character, all animations); Cancel
    // closes it. Then an export to a known path (no save dialog) that must parse as glTF.
    $("btn-blender").click();
    await waitFor(() => !$("blender").hidden, "the Blender export window");
    check($("blender-source").value === "0" && $("blender-all").checked && !$("blender-go").disabled,
      "Export 3D Model starts with Mario and all animations");
    await shot("blender");
    await resizeTo(640, 560);
    const blenderBox = document.querySelector("#blender .dialog").getBoundingClientRect();
    check(blenderBox.left >= 0 && blenderBox.right <= document.documentElement.clientWidth,
      "the Blender window fits at 640 px: " + JSON.stringify(blenderBox));
    await shot("blender_narrow");
    await resizeTo(1320, 780);
    $("blender-cancel").click();
    await waitFor(() => $("blender").hidden, "Cancel closes the Blender window");
    const blenderPath = inDir("Mario.gltf");
    const exportingBlender = app.exportBlender(app.state.groups[0], blenderPath);
    await waitFor(() => !$("blender").hidden, "the Blender export window again");
    $("blender-go").click();
    await waitFor(() => dialogOpen("Open in Blender: File > Import > glTF 2.0"), "the Blender done dialog", 60000);
    check($("dialog-text").textContent.startsWith(`Saved Mario to\n${blenderPath}\n\n37 bones, 115 animations.`),
      "Blender done text: " + $("dialog-text").textContent);
    check($("dialog-text").querySelector("a")?.textContent === "How to play another animation (user guide)", "the guide link");
    await shot("blender_done");
    dialogButton("OK").click();
    const blenderResult = await exportingBlender;
    check(blenderResult?.animations === 115 && blenderResult.path === blenderPath, "export result: " + JSON.stringify(blenderResult));
    check(logHas(`exported Mario for Blender (115 animations) to ${blenderPath}`), "the Blender log line");
    let parsed = "";
    try {
      parsed = await invoke("selftest_check_gltf", { path: blenderPath });
    } catch (e) {
      parsed = String(e);
    }
    check(parsed.startsWith("39 nodes, 115 animations, 9 images, "), "the .gltf file parses: " + parsed);

    // clones of a second character: its own block with its own character, colours and first clone
    const before = rows().map((r) => r.querySelector("select").value).join();
    $("btn-group").click();
    const blocks = () => [...document.querySelectorAll("#groups .group")];
    const second = () => [...blocks()[1].querySelectorAll(".rows .clone-row")];
    await waitFor(() => blocks().length === 2 && second().length === 1 && caption().includes("of Luigi"), "Luigi's block");
    const luigiSlot = second()[0].querySelector("select").value;
    check(blocks()[1].querySelector(".source").value === "1" && !["0", "1", ...before.split(",")].includes(luigiSlot),
      "Luigi's clone takes a free slot: " + luigiSlot);
    check(rows().map((r) => r.querySelector("select").value).join() === before, "Mario's clones keep their slots");
    const usedOption = [...rows()[0].querySelector("select").options].find((o) => o.value === luigiSlot);
    check(usedOption?.disabled && usedOption.textContent.startsWith("× ") &&
      usedOption.title.endsWith("(used by another character)"),
      "Luigi's slot is greyed out and marked as used for Mario");
    check(blocks()[1].querySelectorAll(".regions-box input").length > 0, "Luigi's own colours to change");
    check($("chk-stats").nextElementSibling.textContent === "Give each clone the stats of the character it copies (Mario, Luigi)",
      "labels for several characters: " + $("chk-stats").nextElementSibling.textContent);
    await shot("two_characters");

    // a wrong output name, an existing file
    $("out-path").value = inDir("x.gcm");
    $("btn-go").click();
    await waitFor(() => dialogOpen("The output file must end in .iso."), "the error dialog");
    await shot("dialog_error");
    dialogButton("OK").click();
    $("out-path").value = inDir("Old Clones.iso");
    $("btn-go").click();
    await waitFor(() => dialogOpen("Old Clones.iso already exists. Replace it?"), "the replace question");
    await shot("dialog_confirm");
    dialogButton("No").click();
    check($("run-box").hidden && !$("btn-go").disabled, "No leaves the window as it was");

    // create
    $("out-path").value = inDir("My Clones.iso");
    $("btn-go").click();
    await waitFor(() => !$("run-box").hidden && $("progress-label").textContent.includes("%"), "the progress bar");
    check($("btn-go").disabled && $("btn-iso").disabled && $("pill").textContent === "Creating the game…", "inputs locked while busy");
    check(logHas("the full log is also saved to " + inDir("My Clones.log")), "the .log note");
    await sleep(800);
    await shot("busy");
    await waitFor(() => !$("dialog").hidden && $("dialog-title").textContent === "Done!", "the done dialog", 900000);
    check($("dialog-text").textContent.startsWith("Saved to " + inDir("My Clones.iso") + ".\n\nOpen it in Dolphin"), "done text");
    check($("dialog-text").textContent.includes("In the game, put several Toads on one team"), "done hint");
    check(logHas("1 of 9 PNGs") && logHas("track 0: painted t01") && logHas("ARAM body: painted t01"), "the painted texture went in");
    check(logHas("animations: Luigi's (tracks 2-18 and ARAM master[461]") && logHas("animation frame rows of BlueToad"),
      "the first clone plays Luigi's animations");
    check(logHas("source: 1 Luigi") && logHas("stats: copied Luigi's row and chemistry to "), "Luigi's clone went in too");
    await shot("done");
    dialogButton("OK").click();
    check($("pill").textContent === "Game created" && !$("btn-go").disabled, "Game created");
    check([...$("log").children].some((l) => l.className === "ok" && l.textContent.startsWith("saved ")), "saved line in green");

    // replace the game just made, then cancel
    $("btn-go").click();
    await waitFor(() => dialogOpen("My Clones.iso already exists. Replace it?"), "the replace question");
    dialogButton("Yes").click();
    await waitFor(() => !$("run-box").hidden && $("progress-label").textContent.includes("%"), "the second run");
    $("btn-cancel").click();
    check($("progress-label").textContent === "Cancelling…", "Cancelling…");
    await waitFor(() => $("pill").textContent === "Cancelled", "the cancelled job");
    check(logHas("Cancelled. Nothing was saved.") && $("run-box").hidden, "cancel message, bar hidden");

  }

  async function longDialog(name, w, h, times = 1) {
    await resizeTo(w, h);
    const dir = "C:\\Users\\someone with a long name\\Documents\\Mario Superstar Baseball mods\\";
    const hint = ["Blue Toad ones are Peach clones", "Purple Toad, Yellow Toad, Green Toad ones are King Boo clones",
      "Red Noki, Green Noki ones are Donkey Kong clones"]
      .map((c) => `In the game, put several Toads on one team: the game gives the extra ones colours by itself, and the ${c}.`).join(" ");
    const closed = app.showInfo("Done!", `Saved to ${dir}Mario Superstar Baseball (US) - Multi Clones.iso.\n\n`
      + `Open it in Dolphin or Project Rio: File → Open…\n\n${Array(times).fill(hint).join("\n\n")}\n\n`
      + `The working folder was kept: ${dir}MSSB-Clone-Maker-work-PKWafm\\${"x".repeat(150)}`);
    await painted();
    // the window less its scroll bars
    const view = { width: document.documentElement.clientWidth, height: document.documentElement.clientHeight };
    const inside = (r) => r.left >= 0 && r.top >= 0 && r.right <= view.width && r.bottom <= view.height;
    const box = document.querySelector(".dialog").getBoundingClientRect(), text = $("dialog-text");
    const ok = dialogButton("OK"), okBox = ok.getBoundingClientRect();
    const at = `${innerWidth}x${innerHeight}`;
    check(inside(box), `${at}: the dialog is inside the window: ${JSON.stringify(box)}`);
    check(Math.abs(box.left - (view.width - box.right)) <= 2, `${at}: the dialog is centred: ${box.left}..${box.right}`);
    check(inside(okBox) && document.elementFromPoint((okBox.left + okBox.right) / 2, (okBox.top + okBox.bottom) / 2) === ok,
      `${at}: the OK button is visible: ${JSON.stringify(okBox)}`);
    check(text.scrollWidth <= text.clientWidth, `${at}: the text wraps (${text.scrollWidth} > ${text.clientWidth} px)`);
    const scrolls = text.scrollHeight > text.clientHeight;
    check(!scrolls || getComputedStyle(text).overflowY === "auto", `${at}: the text scrolls`);
    check(scrolls === times > 1, `${at}: ${times > 1 ? "a very long" : "this"} text ${times > 1 ? "scrolls" : "fits"}`);
    await shot(name);
    ok.click();
    await closed;
  }

  async function resizeTo(w, h) {
    const t0 = performance.now();
    const resized = new Promise((r) => {
      const done = () => {
        window.removeEventListener("resize", done);
        r();
      };
      window.addEventListener("resize", done);
      setTimeout(done, 1000);
    });
    await getCurrentWindow().setSize(new LogicalSize(w, h));
    await resized;
    await painted();
    return performance.now() - t0;
  }

  async function bench(name, w, h) {
    const steps = [];
    for (let i = 0; i < 80; i++) steps.push(await resizeTo(w + 4 * i, h + 2 * i));
    steps.sort((a, b) => a - b);
    const at = (q) => steps[Math.min(steps.length - 1, Math.floor(q * steps.length))].toFixed(1);
    say(`resize ${name} (${w}x${h} -> ${w + 316}x${h + 158}): 80 steps, median ${at(0.5)} ms, p95 ${at(0.95)} ms, max ${at(1)} ms per step`);
    return Number(at(0.5));
  }
}
