// --selftest (src/selftest.rs): drive every feature of the page against the stand-in game the way a
// user would (clicks, choices), check what the window shows, time resizing, then report and exit.
const { invoke } = window.__TAURI__.core;
const { getCurrentWindow, LogicalSize } = window.__TAURI__.window;

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/** Resolves once the next frame has been painted. */
const painted = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));

const WIDGETS = ["pill", "btn-iso", "btn-dir", "game-path", "status", "source", "regions-box", "rows", "btn-add",
  "btn-adv", "adv", "chk-ui", "chk-colour", "chk-stats", "keeplum", "chk-keep", "out-path", "btn-out", "btn-go",
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
  const sep = options.game.includes("\\") ? "\\" : "/";
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
    check($("btn-go").disabled && $("source").disabled && !$("btn-iso").disabled, "inputs wait for a game");
    await shot("empty");

    // narrow window: the preview goes under the form; wide again: two columns
    await resizeTo(1000, 700);
    check(stacked(), "narrow window stacks the preview under the form");
    await resizeTo(1320, 780);
    if (innerWidth >= 1200) check(!stacked(), "wide window shows the preview beside the form");
    else say(`screen too small for the two-column check (window ${innerWidth} px wide)`);

    // the game
    await app.startLoad(options.game);
    await waitFor(() => $("pill").textContent === "GYQE01 ready", "the game check");
    check($("status").textContent === "✔ Mario Superstar Baseball (USA) — OK", "status line: " + $("status").textContent);
    await waitFor(() => rows().length === 4, "the four default clone rows");
    check(rows()[0].querySelector("select").selectedOptions[0].textContent === "Blue Toad — recommended (an extra Toad colour)",
      "first row is Blue Toad");
    check(rows().map((r) => r.querySelectorAll("select")[1].value).join() === "blue,green,purple,yellow", "default colours");
    check($("out-path").value === inDir("Mario Superstar Baseball - Clones.iso"), "default output: " + $("out-path").value);
    await waitFor(() => !$("img-after").hidden && caption().startsWith("All 9 textures of Mario"), "the first preview");
    check(document.querySelectorAll("#regions-box input").length === 2, "Mario's two colour regions");
    await shot("idle");
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

    // another source keeps the rows, then back
    choose($("source"), 1);
    await waitFor(() => caption().includes("of Luigi") && $("preview-chip").textContent === "Luigi", "Luigi's preview");
    check(rows().length === 4 && $("chk-stats").nextElementSibling.textContent === "Give clones Luigi's stats", "Luigi keeps the rows");
    choose($("source"), 0);
    await waitFor(() => caption().includes("of Mario"), "Mario's preview again");

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
    for (const r of rows().slice(4)) r.querySelector("button").click();
    rows()[1].querySelector("button").click();
    check(rows().length === 3 && rows()[0].querySelector("input").checked, "removing the previewed row previews the first");
    $("btn-add").click();
    await waitFor(() => rows().length === 4, "back to four rows");

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
    await waitFor(() => !$("dialog").hidden && $("dialog-title").textContent === "Done!", "the done dialog", 30000);
    check($("dialog-text").textContent.startsWith("Saved to " + inDir("My Clones.iso") + ".\n\nOpen it in Dolphin"), "done text");
    check($("dialog-text").textContent.includes("In the game, put several Toads on one team"), "done hint");
    await shot("done");
    dialogButton("OK").click();
    check($("pill").textContent === "Game created" && !$("btn-go").disabled, "Game created");
    check([...$("log").children].some((l) => l.className === "ok" && l.textContent.startsWith("saved ")), "saved line in green");

    // cancel
    $("btn-go").click();
    await waitFor(() => !$("run-box").hidden && $("progress-label").textContent.includes("%"), "the second run");
    $("btn-cancel").click();
    check($("progress-label").textContent === "Cancelling…", "Cancelling…");
    await waitFor(() => $("pill").textContent === "Cancelled", "the cancelled job");
    check(logHas("Cancelled. Nothing was saved.") && $("run-box").hidden, "cancel message, bar hidden");

    // a game that can't be used, then the real one again
    await app.startLoad(inDir("game.rvz"));
    check($("status").textContent.startsWith("✖ File not found") && $("pill").textContent === "Game not usable", "missing game: " + $("status").textContent);
    check($("btn-go").disabled, "no game, no create");
    await shot("bad_game");
    await app.startLoad(options.game);
    await waitFor(() => !$("btn-go").disabled, "the game again");

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
