//! A stand-in for the Rust core (`tools/clonemaker/core`, #31 #32) until it lands: the same calls
//! with realistic answers. Names, slot lists and colour regions are the Python reference's real
//! output for the retail disc (`mock_fixture.json`); the preview pictures are made-up textures
//! recoloured by hue; `create` only pretends (it writes the `.log`, never a game).

use std::collections::HashMap;
use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock};
use std::thread;
use std::time::Duration;

use base64::Engine;
use serde::Deserialize;

use crate::api::{
    Clone, Failure, GameCheck, Job, JobEvent, Preview, PreviewRequest, Region, Slot, SourceInfo,
    SourceName,
};

pub const GAME_OK: &str = "Mario Superstar Baseball (USA) — OK";
const DISC_IMAGE_OTHER: [&str; 7] = ["rvz", "nkit", "gcz", "wbfs", "ciso", "wia", "tgc"];
const GAMECUBE_MAGIC: u32 = 0xC233_9F3D;
const PRESETS: [(&str, f64); 8] = [
    ("red", 0.0),
    ("orange", 0.08),
    ("yellow", 0.16),
    ("green", 0.33),
    ("cyan", 0.5),
    ("blue", 0.62),
    ("purple", 0.76),
    ("pink", 0.9),
];
const PREVIEW_WIDTH: u32 = 256;
const ATLAS_BG: [u8; 3] = [0x10, 0x10, 0x1a];

#[derive(Deserialize)]
struct Fixture {
    slot_labels: HashMap<u32, String>,
    sources: Vec<FixtureSource>,
}

#[derive(Deserialize)]
struct FixtureSource {
    id: u32,
    name: String,
    textures: u32,
    slots: Vec<u32>,
    default_rows: Vec<Clone>,
    regions: Vec<Region>,
}

fn fixture() -> &'static Fixture {
    static FIXTURE: OnceLock<Fixture> = OnceLock::new();
    FIXTURE.get_or_init(|| {
        serde_json::from_str(include_str!("mock_fixture.json")).expect("mock_fixture.json")
    })
}

fn source(id: u32) -> Result<&'static FixtureSource, Failure> {
    fixture()
        .sources
        .iter()
        .find(|s| s.id == id)
        .ok_or_else(|| Failure::new(format!("Unknown character {id}.")))
}

fn pretty_name(id: u32) -> String {
    source(id).map_or_else(|_| format!("Slot {id}"), |s| s.name.clone())
}

#[derive(Default)]
pub struct Mock {
    cancel: Arc<AtomicBool>,
}

impl Mock {
    pub fn open_game(&self, path: &str) -> GameCheck {
        let p = Path::new(path);
        let (kind, result) = if p.is_dir() {
            ("folder", check_folder(p))
        } else {
            ("iso", check_iso(p))
        };
        match result {
            Ok(()) => GameCheck {
                ok: true,
                message: GAME_OK.into(),
                kind: Some(kind),
            },
            Err(message) => GameCheck {
                ok: false,
                message,
                kind: None,
            },
        }
    }

    pub fn sources(&self) -> Vec<SourceName> {
        fixture()
            .sources
            .iter()
            .map(|s| SourceName {
                id: s.id,
                name: s.name.clone(),
            })
            .collect()
    }

    pub fn source_info(&self, id: u32) -> Result<SourceInfo, Failure> {
        let s = source(id)?;
        Ok(SourceInfo {
            regions: s.regions.clone(),
            slots: s
                .slots
                .iter()
                .map(|&id| Slot {
                    id,
                    label: fixture().slot_labels[&id].clone(),
                })
                .collect(),
            default_rows: s.default_rows.clone(),
        })
    }

    pub fn next_free_slot(&self, source_id: u32, used: &[u32]) -> Result<Option<u32>, Failure> {
        Ok(source(source_id)?
            .slots
            .iter()
            .copied()
            .find(|s| !used.contains(s)))
    }

    pub fn preview(&self, req: &PreviewRequest) -> Result<Preview, Failure> {
        let s = source(req.source)?;
        let hue = colour_hue(&req.colour).ok_or_else(|| Failure::new("Unknown colour."))?;
        let ticked: Vec<u32> = match &req.regions {
            Some(r) => r.clone(),
            None => s
                .regions
                .iter()
                .filter(|r| r.default)
                .map(|r| r.index)
                .collect(),
        };
        let atlas = Atlas::new(s);
        let changes: Vec<String> = s
            .regions
            .iter()
            .filter(|r| ticked.contains(&r.index))
            .map(|r| lower_first(&r.label))
            .collect();
        Ok(Preview {
            before_png: atlas.png(None),
            after_png: atlas.png(Some((hue, req.keeplum, &ticked))),
            caption: format!(
                "All {} textures of {}, before and as {} in {} (changes: {})",
                s.textures,
                s.name,
                pretty_name(req.slot),
                colour_label(&req.colour),
                changes.join(" + ")
            ),
        })
    }

    pub fn default_output(&self, game_path: &str) -> String {
        let p = Path::new(game_path);
        let base = if p.is_file() {
            p.file_stem()
        } else {
            p.file_name()
        };
        let name = format!(
            "{} - Clones.iso",
            base.unwrap_or_default().to_string_lossy()
        );
        p.parent()
            .unwrap_or(Path::new(""))
            .join(name)
            .to_string_lossy()
            .into_owned()
    }

    pub fn validate_job(&self, job: &Job) -> Option<Failure> {
        validate(job).err().map(Failure::new)
    }

    /// Runs the (pretend) job on its own thread; `emit` gets the log, progress and the outcome.
    pub fn create(&self, job: Job, emit: impl Fn(JobEvent) + Send + 'static) {
        self.cancel.store(false, Ordering::SeqCst);
        let cancel = self.cancel.clone();
        thread::spawn(move || {
            let outcome = run_job(&job, &emit, &cancel);
            emit(outcome);
        });
    }

    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::SeqCst);
    }
}

fn check_folder(p: &Path) -> Result<(), String> {
    let p = if !p.join("sys").is_dir() && p.join("DATA").join("sys").is_dir() {
        p.join("DATA")
    } else {
        p.to_path_buf()
    };
    if p.join("sys").join("main.dol").exists() && p.join("files").join("ZZZZ.dat").exists() {
        Ok(())
    } else {
        Err(
            "This folder is not an extracted Mario Superstar Baseball disc (it needs the sys and \
             files folders). Choose the .iso instead."
                .into(),
        )
    }
}

fn check_iso(p: &Path) -> Result<(), String> {
    if !p.is_file() {
        return Err(format!("File not found: {}", p.display()));
    }
    let ext = p
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    if DISC_IMAGE_OTHER.contains(&ext.as_str()) {
        return Err(format!(
            "This is a compressed .{ext} file. Convert it to .iso first: in Dolphin or Rio, \
             right-click the game → Convert File… → Format: ISO."
        ));
    }
    let mut head = Vec::new();
    fs::File::open(p)
        .and_then(|f| f.take(0x440).read_to_end(&mut head))
        .map_err(|e| format!("Couldn't read {}: {e}", p.display()))?;
    let magic = head
        .get(0x1C..0x20)
        .map(|b| u32::from_be_bytes([b[0], b[1], b[2], b[3]]));
    if head.len() < 0x440 || magic != Some(GAMECUBE_MAGIC) {
        return Err("This file is not a GameCube disc image (.iso).".into());
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------
// Colours
// ---------------------------------------------------------------------------------------------
fn colour_hue(colour: &str) -> Option<f64> {
    if let Some(hex) = colour.strip_prefix('#') {
        let v = u32::from_str_radix(hex, 16)
            .ok()
            .filter(|_| hex.len() == 6)?;
        let rgb = [(v >> 16) & 255, (v >> 8) & 255, v & 255].map(|c| c as f64 / 255.0);
        return Some(rgb_to_hsv(rgb)[0]);
    }
    PRESETS.iter().find(|(n, _)| *n == colour).map(|(_, h)| *h)
}

pub fn colour_label(colour: &str) -> String {
    if colour.starts_with('#') {
        format!("Custom {}", colour.to_uppercase())
    } else {
        let mut c = colour.chars();
        c.next()
            .map_or_else(String::new, |f| f.to_uppercase().chain(c).collect())
    }
}

fn colour_spec(colour: &str, keeplum: f64) -> String {
    let hue = if colour.starts_with('#') {
        format!("{:.3}", colour_hue(colour).unwrap_or(0.0))
    } else {
        colour.to_string()
    };
    let keep = format!("{keeplum:.2}");
    let keep = keep.trim_end_matches('0').trim_end_matches('.');
    format!(
        "hue={hue},keeplum={}",
        if keep.is_empty() { "0" } else { keep }
    )
}

fn lower_first(s: &str) -> String {
    let mut c = s.chars();
    c.next()
        .map_or_else(String::new, |f| f.to_lowercase().chain(c).collect())
}

fn rgb_to_hsv([r, g, b]: [f64; 3]) -> [f64; 3] {
    let max = r.max(g).max(b);
    let min = r.min(g).min(b);
    let d = max - min;
    let h = if d == 0.0 {
        0.0
    } else if max == r {
        ((g - b) / d).rem_euclid(6.0) / 6.0
    } else if max == g {
        ((b - r) / d + 2.0) / 6.0
    } else {
        ((r - g) / d + 4.0) / 6.0
    };
    [h, if max == 0.0 { 0.0 } else { d / max }, max]
}

fn hsv_to_rgb([h, s, v]: [f64; 3]) -> [u8; 3] {
    let i = (h * 6.0).floor();
    let f = h * 6.0 - i;
    let (p, q, t) = (v * (1.0 - s), v * (1.0 - s * f), v * (1.0 - s * (1.0 - f)));
    let (r, g, b) = match i as i64 % 6 {
        0 => (v, t, p),
        1 => (q, v, p),
        2 => (p, v, t),
        3 => (p, q, v),
        4 => (t, p, v),
        _ => (v, p, q),
    };
    [r, g, b].map(|c| (c * 255.0).round().clamp(0.0, 255.0) as u8)
}

fn parse_swatch(s: &str) -> [f64; 3] {
    let v = u32::from_str_radix(s.trim_start_matches('#'), 16).unwrap_or(0x808080);
    [(v >> 16) & 255, (v >> 8) & 255, v & 255].map(|c| c as f64 / 255.0)
}

// ---------------------------------------------------------------------------------------------
// Preview: made-up textures shelf-packed like the real atlas (clone_gui.atlas_layout)
// ---------------------------------------------------------------------------------------------
struct Texture {
    w: u32,
    h: u32,
    x: u32,
    y: u32,
    /// Index of the colour region it is painted in, or None for skin / white.
    region: Option<u32>,
    base: [f64; 3],
}

struct Atlas {
    textures: Vec<Texture>,
    height: u32,
}

impl Atlas {
    fn new(s: &FixtureSource) -> Self {
        const SIZES: [(u32, u32); 6] =
            [(128, 128), (64, 64), (64, 32), (32, 64), (32, 32), (16, 16)];
        const NEUTRALS: [[f64; 3]; 2] = [[0.07, 0.45, 0.93], [0.0, 0.0, 0.95]];
        let mut seed = s.id.wrapping_mul(2_654_435_761).wrapping_add(12_345);
        let mut textures: Vec<Texture> = (0..s.textures)
            .map(|i| {
                seed ^= seed << 13;
                seed ^= seed >> 17;
                seed ^= seed << 5;
                let (w, h) = SIZES[if i == 0 { 0 } else { 1 + seed as usize % 5 }];
                let pick = seed as usize >> 8;
                let (region, base) = match s.regions.get(pick % (s.regions.len() + 2)) {
                    Some(r) => (Some(r.index), rgb_to_hsv(parse_swatch(&r.swatch))),
                    None => (None, NEUTRALS[pick % 2]),
                };
                Texture {
                    w,
                    h,
                    x: 0,
                    y: 0,
                    region,
                    base,
                }
            })
            .collect();
        textures.sort_by_key(|t| (std::cmp::Reverse(t.h), std::cmp::Reverse(t.w)));
        let (mut x, mut y, mut shelf) = (0, 0, 0);
        for t in &mut textures {
            if x > 0 && x + t.w > PREVIEW_WIDTH {
                (x, y, shelf) = (0, y + shelf + 2, 0);
            }
            (t.x, t.y) = (x, y);
            x += t.w + 2;
            shelf = shelf.max(t.h);
        }
        Atlas {
            textures,
            height: y + shelf,
        }
    }

    /// Base64 PNG of the atlas; `recolour` = (target hue, keeplum, ticked regions).
    fn png(&self, recolour: Option<(f64, f64, &[u32])>) -> String {
        let w = PREVIEW_WIDTH as usize;
        let mut rgb = ATLAS_BG.repeat(w * self.height as usize);
        for t in &self.textures {
            let change =
                recolour.filter(|(_, _, ticked)| t.region.is_some_and(|r| ticked.contains(&r)));
            for yy in 0..t.h {
                for xx in 0..t.w {
                    let (u, v) = (xx as f64 / t.w as f64, yy as f64 / t.h as f64);
                    let shade = 0.55
                        + 0.3 * (1.0 - ((u - 0.4).powi(2) + (v - 0.35).powi(2)).sqrt())
                        + 0.08 * ((u * 9.0).sin() * (v * 7.0).cos());
                    let [h, s, val] = t.base;
                    let hsv = match change {
                        Some((hue, keeplum, _)) => {
                            let flat = 0.85;
                            [
                                hue,
                                0.85,
                                (val * shade * keeplum + flat * shade * (1.0 - keeplum)).min(1.0),
                            ]
                        }
                        None => [h, s, (val * shade).min(1.0)],
                    };
                    let o = ((t.y + yy) as usize * w + (t.x + xx) as usize) * 3;
                    rgb[o..o + 3].copy_from_slice(&hsv_to_rgb(hsv));
                }
            }
        }
        let mut out = Vec::new();
        {
            let mut enc = png::Encoder::new(&mut out, PREVIEW_WIDTH, self.height);
            enc.set_color(png::ColorType::Rgb);
            enc.set_depth(png::BitDepth::Eight);
            let mut writer = enc.write_header().expect("png header");
            writer.write_image_data(&rgb).expect("png data");
        }
        base64::engine::general_purpose::STANDARD.encode(out)
    }
}

// ---------------------------------------------------------------------------------------------
// The job
// ---------------------------------------------------------------------------------------------
/// Python's `Path.resolve()` for a path that may not exist yet.
fn resolve(p: &Path) -> PathBuf {
    if let Ok(c) = p.canonicalize() {
        return c;
    }
    match (p.parent(), p.file_name()) {
        (Some(parent), Some(name)) => resolve(if parent.as_os_str().is_empty() {
            Path::new(".")
        } else {
            parent
        })
        .join(name),
        _ => p.to_path_buf(),
    }
}

fn validate(job: &Job) -> Result<(), String> {
    if job.clones.is_empty() {
        return Err("Add at least one clone.".into());
    }
    let ids: Vec<u32> = job.clones.iter().map(|c| c.slot).collect();
    if let Some(dup) = ids
        .iter()
        .find(|s| ids.iter().filter(|t| t == s).count() > 1)
    {
        return Err(format!(
            "{} is used by two clones. Pick a different slot for one of them.",
            pretty_name(*dup)
        ));
    }
    if ids.contains(&job.source) {
        return Err("A character can't be a clone of itself. Pick another slot.".into());
    }
    let out = Path::new(&job.output);
    if !out
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("iso"))
    {
        return Err("The output file must end in .iso.".into());
    }
    let (out, src) = (resolve(out), resolve(Path::new(&job.game)));
    if out == src {
        return Err(
            "Choose a different name for the new game: it must not replace your original.".into(),
        );
    }
    if src.is_dir() && out.starts_with(&src) {
        return Err("Save the new game outside the game's folder.".into());
    }
    let parent = out.parent().unwrap_or(Path::new(""));
    if !parent.is_dir() {
        return Err(format!("The folder {} does not exist.", parent.display()));
    }
    let probe = parent.join(format!(".mssb-clone-write-test-{}", std::process::id()));
    if fs::write(&probe, b"")
        .and_then(|_| fs::remove_file(&probe))
        .is_err()
    {
        return Err(format!(
            "Windows won't let this program save in {}. Choose a folder you own, such as \
             Documents or the Desktop.",
            parent.display()
        ));
    }
    Ok(())
}

fn run_job(job: &Job, emit: &dyn Fn(JobEvent), cancel: &AtomicBool) -> JobEvent {
    if let Err(message) = validate(job) {
        return JobEvent::Error {
            message,
            cancelled: false,
        };
    }
    let out = Path::new(&job.output);
    let log_path = out.with_extension("log");
    let mut logf = match fs::File::create(&log_path) {
        Ok(f) => f,
        Err(e) => {
            return JobEvent::Error {
                message: format!(
                    "Couldn't read or write a file ({}): {e}.",
                    log_path.display()
                ),
                cancelled: false,
            }
        }
    };
    let mut log = |line: String| {
        // the log file is a convenience: a failed write must not stop the job
        let _ = writeln!(logf, "{line}");
        emit(JobEvent::Log { line });
    };
    let work = out
        .parent()
        .unwrap_or(Path::new(""))
        .join(format!("MSSB-Clone-Maker-work-{:08x}", std::process::id()));
    let name = pretty_name(job.source);
    log(format!(
        "MSSB Clone Maker: {} from {}",
        job.output, job.game
    ));
    log(format!("working folder: {}", work.display()));
    log("mock backend: nothing is unpacked, patched or written except this log".into());
    let first = if Path::new(&job.game).is_dir() {
        "Copying the game"
    } else {
        "Unpacking the game"
    };
    let stages = [
        (first, 0.0, 0.35),
        ("Patching", 0.35, 0.75),
        ("Writing the new ISO", 0.75, 1.0),
    ];
    for (n, (label, from, to)) in stages.into_iter().enumerate() {
        match n {
            0 => {
                log(format!(
                    "extracting {} -> {}",
                    job.game,
                    work.join("disc").display()
                ));
                log("preflight: clean retail GYQE01 dump".into());
            }
            1 => {
                log(format!("source: {} {name}", job.source));
                let regions = job.regions.as_ref().map_or_else(String::new, |r| {
                    let r: Vec<String> = r.iter().map(u32::to_string).collect();
                    format!(",region={}", r.join("/"))
                });
                for c in &job.clones {
                    log(format!(
                        "  {} (slot {}) -> {}{regions}",
                        pretty_name(c.slot),
                        c.slot,
                        colour_spec(&c.colour, job.keeplum)
                    ));
                }
                if job.ui_source {
                    log(format!(
                        "ui: menus, HUD and the announcer show {name}{}",
                        if job.keep_colour_word {
                            " (with the donor's colour word)"
                        } else {
                            ""
                        }
                    ));
                }
                if job.copy_stats {
                    log(format!(
                        "stats: copied {name}'s row and chemistry to the clones"
                    ));
                }
            }
            _ => log(format!(
                "writing {}",
                out.with_extension("partial.iso").display()
            )),
        }
        for step in 0..=20 {
            if cancel.load(Ordering::SeqCst) {
                log("cancelled".into());
                log("removing the working folder".into());
                return JobEvent::Error {
                    message: "Cancelled. Nothing was saved.".into(),
                    cancelled: true,
                };
            }
            let fraction = from + (to - from) * step as f64 / 20.0;
            emit(JobEvent::Progress {
                fraction,
                label: label.into(),
            });
            thread::sleep(Duration::from_millis(45));
        }
    }
    log(format!("saved {}", job.output));
    let kept = job
        .keep_work
        .then(|| work.join("disc").display().to_string());
    match &kept {
        Some(w) => log(format!("kept the working folder: {w}")),
        None => log("removing the working folder".into()),
    }
    let slots: Vec<u32> = job.clones.iter().map(|c| c.slot).collect();
    JobEvent::Done {
        iso: job.output.clone(),
        work: kept,
        hint: pick_hint(job.source, &slots, job.ui_source),
    }
}

/// The family a slot is a colour variant of ("Toad"), from its "recommended (an extra Toad colour)" label.
fn variant_family(slot: u32) -> Option<&'static str> {
    let label = fixture().slot_labels.get(&slot)?;
    label
        .split_once("an extra ")
        .and_then(|(_, rest)| rest.strip_suffix(" colour)"))
}

fn plural(family: &str) -> String {
    if family.ends_with("Bones") {
        family.to_string()
    } else {
        format!("{family}s")
    }
}

/// What to do in the game, in one or two sentences (clone_gui.pick_hint).
fn pick_hint(source: u32, slots: &[u32], ui_source: bool) -> String {
    let src = pretty_name(source);
    let mut families: Vec<(&str, Vec<u32>)> = Vec::new();
    let mut parts = Vec::new();
    for &s in slots {
        match variant_family(s) {
            Some(f) => match families.iter_mut().find(|(g, _)| *g == f) {
                Some((_, members)) => members.push(s),
                None => families.push((f, vec![s])),
            },
            None => parts.push(format!(
                "pick {} on the select screen{}",
                pretty_name(s),
                if ui_source {
                    format!(" (it now shows as {src})")
                } else {
                    String::new()
                }
            )),
        }
    }
    let mut hints: Vec<String> = families
        .iter()
        .map(|(fam, members)| {
            let names: Vec<String> = members.iter().map(|&s| pretty_name(s)).collect();
            let everyone = fixture()
                .slot_labels
                .keys()
                .filter(|&&s| variant_family(s) == Some(fam))
                .all(|s| members.contains(s));
            if everyone {
                format!(
                    "put several {} on one team: every extra one after the first is a {src} clone ({})",
                    plural(fam),
                    names.join(", ")
                )
            } else {
                format!(
                    "put several {} on one team: the game gives the extra ones colours by itself, \
                     and the {} ones are {src} clones",
                    plural(fam),
                    names.join(", ")
                )
            }
        })
        .collect();
    hints.append(&mut parts);
    if hints.is_empty() {
        String::new()
    } else {
        format!("In the game, {}.", hints.join("; "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job(dir: &Path, output: &str, clones: &[u32]) -> Job {
        Job {
            game: dir.join("game.iso").to_string_lossy().into_owned(),
            output: dir.join(output).to_string_lossy().into_owned(),
            source: 0,
            clones: clones
                .iter()
                .map(|&slot| Clone {
                    slot,
                    colour: "blue".into(),
                })
                .collect(),
            keeplum: 0.5,
            regions: None,
            ui_source: true,
            copy_stats: true,
            keep_colour_word: false,
            keep_work: false,
        }
    }

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("mssb-mock-test-{name}-{}", std::process::id()));
        fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn sources_and_slots() {
        let m = Mock::default();
        assert_eq!(m.sources().len(), 54);
        assert_eq!(pretty_name(19), "Bowser Jr.");
        let mario = m.source_info(0).unwrap();
        assert_eq!(
            mario.slots.iter().take(4).map(|s| s.id).collect::<Vec<_>>(),
            [29, 30, 31, 32]
        );
        assert_eq!(
            mario.slots[0].label,
            "Blue Toad — recommended (an extra Toad colour)"
        );
        assert_eq!(mario.default_rows.len(), 4);
        assert_eq!(mario.regions[0].label, "Red (cap, shirt)");
        assert_eq!(m.next_free_slot(0, &[29, 30]).unwrap(), Some(31));
        assert!(m.source_info(2).unwrap().slots.iter().all(|s| s.id < 48));
    }

    #[test]
    fn colours() {
        assert_eq!(colour_label("blue"), "Blue");
        assert_eq!(colour_label("#12ab9f"), "Custom #12AB9F");
        assert_eq!(colour_spec("blue", 0.5), "hue=blue,keeplum=0.5");
        assert_eq!(colour_spec("#ff0000", 0.0), "hue=0.000,keeplum=0");
        assert!((colour_hue("#0000ff").unwrap() - 2.0 / 3.0).abs() < 1e-9);
    }

    #[test]
    fn default_output_is_next_to_the_game() {
        let d = tmp("out");
        let iso = d.join("Mario Superstar Baseball.iso");
        fs::write(&iso, b"x").unwrap();
        let m = Mock::default();
        assert_eq!(
            m.default_output(&iso.to_string_lossy()),
            d.join("Mario Superstar Baseball - Clones.iso")
                .to_string_lossy()
        );
        assert_eq!(
            m.default_output(&d.join("GYQE01").to_string_lossy()),
            d.join("GYQE01 - Clones.iso").to_string_lossy()
        );
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn open_game_messages() {
        let d = tmp("open");
        let m = Mock::default();
        assert!(m
            .open_game(&d.join("missing.iso").to_string_lossy())
            .message
            .starts_with("File not found"));
        fs::write(d.join("game.rvz"), b"RVZ").unwrap();
        assert!(m
            .open_game(&d.join("game.rvz").to_string_lossy())
            .message
            .contains("Convert File"));
        fs::write(d.join("junk.iso"), [0u8; 0x1000]).unwrap();
        assert_eq!(
            m.open_game(&d.join("junk.iso").to_string_lossy()).message,
            "This file is not a GameCube disc image (.iso)."
        );
        let mut disc = vec![0u8; 0x440];
        disc[0x1C..0x20].copy_from_slice(&GAMECUBE_MAGIC.to_be_bytes());
        fs::write(d.join("game.iso"), disc).unwrap();
        let ok = m.open_game(&d.join("game.iso").to_string_lossy());
        assert!(ok.ok && ok.message == GAME_OK && ok.kind == Some("iso"));
        assert!(m
            .open_game(&d.to_string_lossy())
            .message
            .contains("not an extracted"));
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn validate_job_messages() {
        let d = tmp("validate");
        let m = Mock::default();
        let err = |j: Job| m.validate_job(&j).map(|f| f.error);
        assert_eq!(
            err(job(&d, "a.iso", &[])).unwrap(),
            "Add at least one clone."
        );
        assert_eq!(
            err(job(&d, "a.iso", &[29, 29])).unwrap(),
            "Blue Toad is used by two clones. Pick a different slot for one of them."
        );
        assert!(err(job(&d, "a.iso", &[0]))
            .unwrap()
            .contains("clone of itself"));
        assert_eq!(
            err(job(&d, "a.gcm", &[29])).unwrap(),
            "The output file must end in .iso."
        );
        assert!(err(job(&d, "game.iso", &[29]))
            .unwrap()
            .contains("must not replace your original"));
        assert!(err(job(&d, "nope/a.iso", &[29]))
            .unwrap()
            .contains("does not exist"));
        assert_eq!(err(job(&d, "a.iso", &[29, 30])), None);
        fs::remove_dir_all(d).unwrap();
    }

    #[test]
    fn hints() {
        assert_eq!(
            pick_hint(0, &[29, 30, 31, 32, 1], true),
            "In the game, put several Toads on one team: every extra one after the first is a \
             Mario clone (Blue Toad, Yellow Toad, Green Toad, Purple Toad); pick Luigi on the \
             select screen (it now shows as Mario)."
        );
        assert!(pick_hint(0, &[29], false).contains("the Blue Toad ones are Mario clones"));
    }

    #[test]
    fn create_runs_and_cancels() {
        let d = tmp("create");
        let (tx, rx) = std::sync::mpsc::channel();
        let m = Mock::default();
        let mut j = job(&d, "out.iso", &[29]);
        j.keep_work = true;
        m.create(j, move |e| tx.send(e).unwrap());
        let events: Vec<JobEvent> = rx.iter().collect();
        assert!(matches!(
            events.last(),
            Some(JobEvent::Done { work: Some(_), .. })
        ));
        assert!(events.iter().any(
            |e| matches!(e, JobEvent::Progress { label, .. } if label == "Writing the new ISO")
        ));
        assert!(d.join("out.log").is_file() && !d.join("out.iso").exists());

        let (tx, rx) = std::sync::mpsc::channel();
        m.create(job(&d, "out.iso", &[29]), move |e| tx.send(e).unwrap());
        rx.recv().unwrap();
        m.cancel();
        assert_eq!(
            rx.iter().last(),
            Some(JobEvent::Error {
                message: "Cancelled. Nothing was saved.".into(),
                cancelled: true
            })
        );
        fs::remove_dir_all(d).unwrap();
    }
}
