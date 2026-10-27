//! Window-facing commands and job events described by ../API.md.
use crate::anims::{self, Rigs};
use crate::binary::{Dol, fst_paths, u32_at};
use crate::clone::{CloneArgs, CloneResult, ColourWord, absolute_path};
use crate::colour::{Recolour, Region, hsv_to_rgb, rgb_to_hsv, source_regions};
use crate::constants::*;
use crate::custom_textures::{self, Painted};
use crate::disc::{Disc, preflight};
use crate::texture::{Gpc, decode_texture};
use crate::{Error, Result};
use base64::Engine;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};

/// Name and ID of a selectable clone source.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct Source {
    /// The game's charID.
    pub id: usize,
    /// Friendly displayed character name.
    pub name: String,
}
/// Convert the source's internal character name to the Python window's friendly spelling.
pub fn pretty_name(c: usize) -> String {
    match c {
        2 => "Donkey Kong".into(),
        3 => "Diddy Kong".into(),
        19 => "Bowser Jr.".into(),
        37 => "King Boo".into(),
        7 => "Baby Mario".into(),
        8 => "Baby Luigi".into(),
        27 => "Hammer Bro".into(),
        52 => "Fire Bro".into(),
        53 => "Boomerang Bro".into(),
        _ => {
            let mut out = String::new();
            let mut previous = ' ';
            for ch in CHAR_NAMES[c].chars() {
                if previous.is_ascii_lowercase() && ch.is_ascii_uppercase() {
                    out.push(' ');
                }
                out.push(ch);
                previous = ch;
            }
            out
        }
    }
}
fn family_name(fam: u8, plural: bool) -> String {
    let (one, many) = match fam {
        3 => ("Shy Guy", "Shy Guys"),
        9 => ("Dry Bones", "Dry Bones"),
        7 => ("Hammer Bro", "Hammer Bros"),
        _ => {
            let n = FAMILY_NAMES[fam as usize];
            return if plural { format!("{n}s") } else { n.into() };
        }
    };
    if plural { many.into() } else { one.into() }
}
/// List every character that can supply a clone body.
pub fn sources() -> Vec<Source> {
    (0..54)
        .map(|id| Source {
            id,
            name: pretty_name(id),
        })
        .collect()
}
/// One compatible donor, with the select-screen consequence of replacing it.
#[derive(Debug, Serialize, Clone)]
pub struct SlotChoice {
    /// Donor charID.
    pub id: usize,
    /// Friendly donor name.
    pub name: String,
    /// Explanation of how the donor is selected in game.
    pub note: String,
    /// Friendly name plus consequence.
    pub label: String,
    /// Whether it replaces an extra colour rather than a select-grid character.
    pub recommended: bool,
}
/// Return compatible donors, recommended colour variants first and Toads before other families.
pub fn donor_choices(dol: &Dol, source: usize) -> Result<Vec<SlotChoice>> {
    check_id(source)?;
    let main: HashSet<_> = dol.read(0x80108db8, 32)?.iter().copied().collect();
    let default_case = dol.u32(ADDR_JUMPTABLE)?;
    let src_case = if source < 48 {
        dol.u32(ADDR_JUMPTABLE + source as u32 * 4)?
    } else {
        default_case
    };
    let mut out = Vec::new();
    for s in 0..54 {
        if s == source || dol.private_entry(s)?.is_none() || (s >= 48 && src_case != default_case) {
            continue;
        }
        let fam = dol.family(s)?;
        let recommended = !main.contains(&(s as u8)) && fam[0] != 0 && fam[3] == 0;
        let name = pretty_name(s);
        let note = if recommended {
            format!(
                "recommended (an extra {} colour)",
                family_name(fam[0], false)
            )
        } else {
            format!("replaces {name} on the select screen")
        };
        let label = format!("{name} — {note}");
        out.push(SlotChoice {
            id: s,
            name,
            note,
            label,
            recommended,
        });
    }
    out.sort_by_key(|c| (!c.recommended, dol.family(c.id).unwrap()[0] != 2, c.id));
    Ok(out)
}
/// Default clone rows: four Toad colours where possible, topped up with recommended slots.
pub fn default_rows(choices: &[SlotChoice]) -> Vec<CloneRow> {
    let mut slots: Vec<_> = [29, 30, 31, 32]
        .into_iter()
        .filter(|s| choices.iter().any(|c| c.id == *s))
        .collect();
    let more: Vec<_> = choices
        .iter()
        .filter(|c| c.recommended && !slots.contains(&c.id))
        .map(|c| c.id)
        .take(4 - slots.len())
        .collect();
    slots.extend(more);
    slots
        .into_iter()
        .zip(["blue", "green", "purple", "yellow"])
        .map(|(slot, colour)| CloneRow {
            slot,
            colour: colour.into(),
            textures: None,
            anims: None,
        })
        .collect()
}
/// First available donor in the same order as the source's dropdown.
pub fn next_free_slot(choices: &[SlotChoice], used: &[usize]) -> Option<usize> {
    choices.iter().find(|c| !used.contains(&c.id)).map(|c| c.id)
}
/// Explain how to select the patched slots in the game, including automatic family colours.
pub fn pick_hint(dol: &Dol, source: usize, slots: &[usize], ui_source: bool) -> Result<String> {
    check_id(source)?;
    let mut fams: Vec<(u8, Vec<usize>)> = Vec::new();
    let mut mains = Vec::new();
    for s in slots {
        check_id(*s)?;
        let fam = dol.family(*s)?;
        if fam[0] != 0 && fam[3] == 0 {
            if let Some((_, members)) = fams.iter_mut().find(|f| f.0 == fam[0]) {
                members.push(*s);
            } else {
                fams.push((fam[0], vec![*s]));
            }
        } else {
            mains.push(*s);
        }
    }
    let src = pretty_name(source);
    let mut parts = Vec::new();
    for (fam, members) in fams {
        let mut everyone = Vec::new();
        for c in 0..54 {
            let f = dol.family(c)?;
            if f[0] == fam && f[3] == 0 {
                everyone.push(c);
            }
        }
        let names = members
            .iter()
            .map(|s| pretty_name(*s))
            .collect::<Vec<_>>()
            .join(", ");
        parts.push(if everyone.iter().all(|s|members.contains(s)) {format!("put several {} on one team: every extra one after the first is a {src} clone ({names})",family_name(fam,true))}else {format!("put several {} on one team: the game gives the extra ones colours by itself, and the {names} ones are {src} clones",family_name(fam,true))});
    }
    for s in mains {
        parts.push(format!(
            "pick {} on the select screen{}",
            pretty_name(s),
            if ui_source {
                format!(" (it now shows as {src})")
            } else {
                String::new()
            }
        ));
    }
    Ok(if parts.is_empty() {
        String::new()
    } else {
        format!("In the game, {}.", parts.join("; "))
    })
}
fn check_id(id: usize) -> Result<()> {
    if id >= 54 {
        Err(Error::new(format!("character id out of range: {id}")))
    } else {
        Ok(())
    }
}
fn capitalize(s: &str) -> String {
    let mut c = s.chars();
    c.next().map_or(String::new(), |first| {
        first.to_uppercase().collect::<String>() + c.as_str()
    })
}
/// Convert a preset or RGB hex choice to its hue, matching the Python window.
pub fn colour_hue(colour: &str) -> Result<f64> {
    if let Some(hex) = colour.strip_prefix('#') {
        if hex.len() != 6 {
            return Err(Error::new("invalid custom colour".into()));
        }
        let v =
            u32::from_str_radix(hex, 16).map_err(|_| Error::new("invalid custom colour".into()))?;
        return Ok(rgb_to_hsv([
            ((v >> 16) & 255) as f64 / 255.,
            ((v >> 8) & 255) as f64 / 255.,
            (v & 255) as f64 / 255.,
        ])[0]);
    }
    PRESETS
        .iter()
        .find(|p| p.0 == colour)
        .map(|p| p.1)
        .ok_or_else(|| Error::new(format!("unknown colour '{colour}'")))
}
/// Generate the row's preview swatch from a preset or custom hex colour.
pub fn colour_swatch(colour: &str) -> Result<String> {
    if colour.starts_with('#') {
        colour_hue(colour)?;
        return Ok(colour.to_lowercase());
    }
    let [r, g, b] = hsv_to_rgb([colour_hue(colour)?, 0.85, 0.9]).map(|x| (x * 255.) as u8);
    Ok(format!("#{r:02x}{g:02x}{b:02x}"))
}
/// Human-readable colour label used in preview captions.
pub fn colour_label(colour: &str) -> String {
    if colour.starts_with('#') {
        format!("Custom {}", colour.to_uppercase())
    } else {
        capitalize(colour)
    }
}
/// Build the exact CLI recolour spec the Python window uses, including its hue rounding.
pub fn colour_spec(colour: &str, keeplum: f64, regions: Option<&[usize]>) -> Result<String> {
    let hue = if colour.starts_with('#') {
        format!("{:.3}", colour_hue(colour)?)
    } else {
        colour_hue(colour)?;
        colour.into()
    };
    let k = format!("{keeplum:.2}");
    let k = k.trim_end_matches('0').trim_end_matches('.');
    let mut spec = format!("hue={hue},keeplum={}", if k.is_empty() { "0" } else { k });
    if let Some(regions) = regions {
        let mut indices = regions.to_vec();
        indices.sort();
        spec += &format!(
            ",region={}",
            indices
                .iter()
                .map(usize::to_string)
                .collect::<Vec<_>>()
                .join("/")
        );
    }
    Recolour::new(&spec)?;
    Ok(spec)
}
/// Omit explicit regions when the selected checkboxes equal the source defaults.
pub fn region_selection(regions: &[Region], ticked: &[usize]) -> Option<Vec<usize>> {
    let mut ticked = ticked.to_vec();
    ticked.sort();
    ticked.dedup();
    let defaults: Vec<_> = regions
        .iter()
        .enumerate()
        .filter(|(_, r)| r.default)
        .map(|(i, _)| i)
        .collect();
    if ticked == defaults {
        None
    } else {
        Some(ticked)
    }
}
/// Result of checking a selected ISO or folder for the status line.
#[derive(Debug, Serialize)]
pub struct OpenGame {
    /// Whether the input is a clean retail USA disc.
    pub ok: bool,
    /// Friendly status message.
    pub message: String,
    /// Input format: iso or folder.
    pub kind: String,
}
/// Friendly form of raw preflight lines, using the Python window's precedence.
pub fn friendly_preflight(lines: &[String]) -> String {
    let text = lines.join(" ");
    if text.contains("game id") {
        "This is not the USA version of Mario Superstar Baseball (game ID GYQE01). Only the USA disc is supported.".into()
    } else if text.contains("already patched") || text.contains("not retail") {
        "This copy has already been patched. Choose your original, unmodified game.".into()
    } else if text.contains("truncated") {
        "This copy of the game is incomplete. Make a fresh copy of the disc.".into()
    } else if text.contains("main.dol is") || text.contains("do not look like retail") {
        "This doesn't look like an unmodified Mario Superstar Baseball (USA) disc.".into()
    } else {
        lines
            .first()
            .map_or("This game could not be checked.".into(), |s| {
                s.replace("preflight: ", "")
            })
    }
}
/// Selected game and per-source preview cache; opening an ISO reads only its small tables.
pub struct Game {
    /// Chosen input path, retained for output naming.
    pub path: PathBuf,
    /// Read-only disc handle.
    pub disc: Disc,
    /// Main DOL, cached for slot checks.
    pub dol: Dol,
    previews: HashMap<usize, SourcePreview>,
    rigs: Rigs,
}
fn gui_disc(path: &Path) -> Result<Disc> {
    if path.is_dir() {
        let root = if !path.join("sys").is_dir() && path.join("DATA/sys").is_dir() {
            path.join("DATA")
        } else {
            path.to_path_buf()
        };
        if !root.join("sys/main.dol").exists() || !root.join("files/ZZZZ.dat").exists() {
            return Err(Error::new("This folder is not an extracted Mario Superstar Baseball disc (it needs the sys and files folders). Choose the .iso instead.".into()));
        }
        return Disc::folder(root);
    }
    if !path.is_file() {
        return Err(Error::new(format!("File not found: {}", path.display())));
    }
    let ext = path
        .extension()
        .map(|s| format!(".{}", s.to_string_lossy().to_lowercase()))
        .unwrap_or_default();
    if [".rvz", ".nkit", ".gcz", ".wbfs", ".ciso", ".wia", ".tgc"].contains(&ext.as_str()) {
        return Err(Error::new(format!(
            "This is a compressed {ext} file. Convert it to .iso first: in Dolphin or Rio, right-click the game → Convert File… → Format: ISO."
        )));
    }
    let mut f = File::open(path)?;
    let mut boot = vec![0; 0x440];
    if f.read_exact(&mut boot).is_err() || u32_at(&boot, 0x1c)? != 0xc2339f3d {
        return Err(Error::new(
            "This file is not a GameCube disc image (.iso).".into(),
        ));
    }
    let disc = Disc::iso(path).map_err(|e| {
        if e.message.contains("truncated") {
            Error::new(
                "This .iso is incomplete (it ends early). Make a fresh copy of the game.".into(),
            )
        } else {
            e
        }
    })?;
    let entries = fst_paths(&disc.system("fst.bin")?)?;
    if ["ZZZZ.dat", "aaaa.dat"]
        .iter()
        .any(|name| !entries.iter().any(|(_, p)| p == name))
    {
        return Err(Error::new(
            "This is a GameCube game, but not Mario Superstar Baseball.".into(),
        ));
    }
    Ok(disc)
}
/// Load and preflight a game, returning a friendly status even when its input is invalid.
pub fn load_game(path: &Path) -> (Option<Game>, OpenGame) {
    let kind = if path.is_file() { "iso" } else { "folder" }.to_owned();
    let result = (|| -> Result<Game> {
        let disc = gui_disc(path)?;
        let dol = Dol::new(disc.system("main.dol")?)?;
        Ok(Game {
            path: path.to_path_buf(),
            disc,
            dol,
            previews: HashMap::new(),
            rigs: Rigs::default(),
        })
    })();
    match result {
        Err(e) => (
            None,
            OpenGame {
                ok: false,
                message: if let Some(io) = &e.io {
                    format!("Couldn't read {}: {}", path.display(), io.reason)
                } else {
                    e.message
                },
                kind,
            },
        ),
        Ok(game) => {
            let mut lines = Vec::new();
            let ok = preflight(&game.disc, &game.dol, &mut |s| lines.push(s.to_owned())).is_ok();
            let message = if ok {
                "Mario Superstar Baseball (USA) — OK".into()
            } else {
                friendly_preflight(&lines)
            };
            (Some(game), OpenGame { ok, message, kind })
        }
    }
}
/// Texture placements and atlas height for the window's before/after previews.
pub fn atlas_layout(g: &Gpc) -> (Vec<(usize, usize, usize)>, usize) {
    let mut order: Vec<_> = g.textures.iter().collect();
    order.sort_by_key(|t| (std::cmp::Reverse(t.h), std::cmp::Reverse(t.w), t.index));
    let (mut x, mut y, mut shelf) = (0, 0, 0);
    let mut out = Vec::new();
    for t in order {
        if x != 0 && x + t.w > 256 {
            x = 0;
            y += shelf + 2;
            shelf = 0;
        }
        out.push((t.index, x, y));
        x += t.w + 2;
        shelf = shelf.max(t.h);
    }
    (out, y + shelf)
}
/// Encode an RGB preview as PNG; decoded pixels match the Python atlas exactly.
pub fn png_bytes(w: usize, h: usize, rgb: &[u8]) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut out, w as u32, h as u32);
        encoder.set_color(png::ColorType::Rgb);
        encoder.set_depth(png::BitDepth::Eight);
        encoder.set_filter(png::Filter::NoFilter);
        let mut writer = encoder
            .write_header()
            .map_err(|e| Error::new(e.to_string()))?;
        writer
            .write_image_data(rgb)
            .map_err(|e| Error::new(e.to_string()))?;
    }
    Ok(out)
}
/// Cached body, regions and atlas images for one source character.
pub struct SourcePreview {
    /// Source charID.
    pub source: usize,
    /// Original in-match body with its retail overlay applied.
    pub body: Gpc,
    /// Recolourable body regions.
    pub regions: Vec<Region>,
    /// Before image sent to the window.
    pub before_png: Vec<u8>,
    layout: Vec<(usize, usize, usize)>,
    height: usize,
    after: HashMap<String, Vec<u8>>,
}
impl SourcePreview {
    /// Build the original atlas and detect regions for a selected source.
    pub fn new(disc: &Disc, dol: &Dol, source: usize) -> Result<Self> {
        check_id(source)?;
        let body = Gpc::new(disc.effective_body(dol, source)?)?;
        let regions = source_regions(disc, dol, source, &body)?;
        let (layout, height) = atlas_layout(&body);
        let mut pv = Self {
            source,
            body,
            regions,
            before_png: Vec::new(),
            layout,
            height,
            after: HashMap::new(),
        };
        pv.before_png = png_bytes(256, height, &pv.atlas(&pv.body)?)?;
        Ok(pv)
    }
    fn atlas(&self, g: &Gpc) -> Result<Vec<u8>> {
        let mut out = [0x10, 0x10, 0x1a].repeat(256 * self.height);
        for (i, x, y) in &self.layout {
            let t = &g.textures[*i];
            let rgb = decode_texture(&t.bytes(&g.data)?, t)?;
            for yy in 0..t.h {
                let o = ((y + yy) * 256 + x) * 3;
                out[o..o + t.w * 3].copy_from_slice(&rgb[yy * t.w * 3..(yy + 1) * t.w * 3]);
            }
        }
        Ok(out)
    }
    /// Return a character body recoloured by the patcher's exact implementation.
    pub fn recoloured(&self, spec: &str) -> Result<Gpc> {
        let mut g = self.body.clone();
        let mut rc = Recolour::new(spec)?;
        rc.resolve(&self.regions)?;
        rc.recolour_gpc(&mut g)?;
        Ok(g)
    }
    /// Return the after atlas, caching recent spec strings for responsive row edits.
    pub fn after_png(&mut self, spec: &str) -> Result<Vec<u8>> {
        if let Some(png) = self.after.get(spec) {
            return Ok(png.clone());
        }
        if self.after.len() > 32 {
            self.after.clear();
        }
        let png = png_bytes(256, self.height, &self.atlas(&self.recoloured(spec)?)?)?;
        self.after.insert(spec.into(), png.clone());
        Ok(png)
    }
}
impl Game {
    /// Return the cached preview for a source, constructing it only once.
    pub fn preview(&mut self, source: usize) -> Result<&mut SourcePreview> {
        if !self.previews.contains_key(&source) {
            self.previews
                .insert(source, SourcePreview::new(&self.disc, &self.dol, source)?);
        }
        Ok(self.previews.get_mut(&source).unwrap())
    }
}
/// One clone row, serialized exactly as the API contract's job clones array.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct CloneRow {
    /// Donor charID.
    pub slot: usize,
    /// Preset name or #rrggbb custom colour.
    pub colour: String,
    /// Folder of painted textures for this clone (`export_textures`), if any.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub textures: Option<PathBuf>,
    /// "Animations from" (#9): the charID whose animations this clone plays; none = the source's.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub anims: Option<usize>,
}
/// Window job arguments from API.md.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Job {
    /// Original folder or ISO.
    pub game: PathBuf,
    /// Final ISO path.
    pub output: PathBuf,
    /// Source charID.
    pub source: usize,
    /// Donor and colour rows.
    pub clones: Vec<CloneRow>,
    /// Brightness preservation strength.
    pub keeplum: f64,
    /// Selected region indices (defaults are converted to an implicit region spec).
    pub regions: Vec<usize>,
    /// Show the source's names, portraits and announcer identity.
    pub ui_source: bool,
    /// Copy source stats and chemistry.
    pub copy_stats: bool,
    /// Keep the donor's roster name-box colour; `false` gives each clone's name box its colour.
    pub keep_colour_word: bool,
    /// Retain the working extraction for inspection.
    pub keep_work: bool,
}
/// Suggest the new ISO filename alongside the selected game.
pub fn default_output(path: &Path) -> PathBuf {
    let base = if path.is_file() {
        path.file_stem()
    } else {
        path.file_name()
    }
    .unwrap_or_default()
    .to_string_lossy();
    path.with_file_name(format!("{base} - Clones.iso"))
}
/// Validate clone rows and output paths, including the Python window's free-space threshold.
pub fn validate_job(job: &Job, free_bytes: Option<u64>) -> Result<()> {
    if job.clones.is_empty() {
        return Err(Error::new("Add at least one clone.".into()));
    }
    check_id(job.source)?;
    for row in &job.clones {
        check_id(row.slot)?;
        if let Some(a) = row.anims {
            check_id(a)?;
        }
    }
    for row in &job.clones {
        if job.clones.iter().filter(|r| r.slot == row.slot).count() > 1 {
            return Err(Error::new(format!(
                "{} is used by two clones. Pick a different slot for one of them.",
                pretty_name(row.slot)
            )));
        }
    }
    if job.clones.iter().any(|r| r.slot == job.source) {
        return Err(Error::new(
            "A character can't be a clone of itself. Pick another slot.".into(),
        ));
    }
    if !job
        .output
        .extension()
        .is_some_and(|s| s.eq_ignore_ascii_case("iso"))
    {
        return Err(Error::new("The output file must end in .iso.".into()));
    }
    let out = absolute_path(&job.output)?;
    let src = absolute_path(&job.game)?;
    if out == src {
        return Err(Error::new(
            "Choose a different name for the new game: it must not replace your original.".into(),
        ));
    }
    if src.is_dir() && out.starts_with(&src) {
        return Err(Error::new(
            "Save the new game outside the game's folder.".into(),
        ));
    }
    let parent = out.parent().unwrap();
    if !parent.is_dir() {
        return Err(Error::new(format!(
            "The folder {} does not exist.",
            parent.display()
        )));
    }
    // Use a unique create_new probe; never truncate an existing file during validation.
    if tempfile::Builder::new()
        .prefix(&format!(".mssb-clone-write-test-{}-", std::process::id()))
        .tempfile_in(parent)
        .is_err()
    {
        return Err(Error::new(format!(
            "Windows won't let this program save in {}. Choose a folder you own, such as Documents or the Desktop.",
            parent.display()
        )));
    }
    let free = match free_bytes {
        Some(n) => n,
        None => fs2::available_space(parent)?,
    };
    if free < 3_300_000_000 {
        return Err(Error::new(format!(
            "Not enough free disk space on the drive of {}: about 3.3 GB is needed while the game is being made, {:.1} GB is free.",
            parent.display(),
            free as f64 / 1e9
        )));
    }
    Ok(())
}
/// Plain API error object, ready to serialize as {error: message}.
#[derive(Debug, Serialize)]
pub struct ApiError {
    /// Friendly user-facing failure message.
    pub error: String,
}
/// Convert patcher messages into the window's friendly dialog wording.
pub fn friendly_error(error: &Error) -> String {
    if error.message == "cancelled" {
        return "Cancelled. Nothing was saved.".into();
    }
    if let Some(io) = &error.io {
        if io.code == Some(28) {
            return "The drive is full. Free up some space (about 3.3 GB) and try again.".into();
        }
        let where_ = io
            .path
            .as_ref()
            .map_or(String::new(), |p| format!(" ({})", p.display()));
        let busy = if matches!(io.code, Some(1 | 13)) || cfg!(windows) && io.code == Some(32) {
            " If that game is open in Dolphin or Rio, close it first."
        } else {
            ""
        };
        return format!(
            "Couldn't read or write a file{where_}: {}.{busy}",
            io.reason
        );
    }
    error
        .message
        .replace(
            "rerun with --ui donor",
            "untick \"Show clones as…\" under Advanced and try again",
        )
        .replace(
            "rerun with --no-gameplay",
            "this combination does not fit; try fewer clones",
        )
}

impl From<Error> for ApiError {
    fn from(e: Error) -> Self {
        Self {
            error: friendly_error(&e),
        }
    }
}
/// Completion payload emitted after the partial ISO is renamed to the requested output.
#[derive(Debug, Serialize)]
pub struct Done {
    /// Final ISO path.
    pub iso: PathBuf,
    /// Kept extracted disc, if requested.
    pub work: Option<PathBuf>,
    /// Explanation of how to select the clones in game.
    pub hint: String,
}
/// Events from the patch worker, with payload fields matching API.md.
#[derive(Debug, Serialize)]
#[serde(tag = "event", content = "payload", rename_all = "snake_case")]
pub enum Event {
    /// A line also appended to the output's .log file.
    Log {
        /// Complete line without a trailing newline.
        line: String,
    },
    /// Overall progress fraction and current game-processing stage.
    Progress {
        /// Progress from zero to one.
        fraction: f64,
        /// One of the four stage labels from API.md.
        label: String,
    },
    /// Completed job locations and selection hint.
    Done(Done),
    /// Friendly failure or cancellation.
    Error {
        /// Friendly error message.
        message: String,
        /// Whether cancellation, rather than a patch failure, stopped the worker.
        cancelled: bool,
    },
}
struct PartialIso(PathBuf);
impl Drop for PartialIso {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}
fn emit_log(log: &mut File, emit: &dyn Fn(Event), line: &str) -> Result<()> {
    writeln!(log, "{line}")?;
    log.flush()?;
    emit(Event::Log { line: line.into() });
    Ok(())
}
/// Execute the window's patch job with durable logs, progress, cancellation and output cleanup.
pub fn run_job(job: &Job, emit: &dyn Fn(Event), cancel: &AtomicBool) -> Result<Done> {
    validate_job(job, None)?;
    let (game, status) = load_game(&job.game);
    if !status.ok {
        return Err(Error::new(status.message));
    }
    let mut game = game.unwrap();
    let selected = region_selection(&game.preview(job.source)?.regions, &job.regions);
    let mut slots = Vec::new();
    for row in &job.clones {
        slots.push(format!(
            "{}:{}",
            row.slot,
            colour_spec(&row.colour, job.keeplum, selected.as_deref())?
        ));
    }
    let log_path = job.output.with_extension("log");
    let mut log = File::create(&log_path).map_err(|e| Error::from(e).at_path(&log_path))?;
    let parent = job
        .output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let work = tempfile::Builder::new()
        .prefix("MSSB-Clone-Maker-work-")
        .tempdir_in(parent)?;
    let partial = job.output.with_file_name(format!(
        "{}.partial.iso",
        job.output.file_stem().unwrap_or_default().to_string_lossy()
    ));
    let _partial_guard = PartialIso(partial.clone());
    let result = (|| -> Result<CloneResult> {
        emit_log(
            &mut log,
            emit,
            &format!(
                "MSSB Clone Maker: {} from {}",
                job.output.display(),
                job.game.display()
            ),
        )?;
        emit_log(
            &mut log,
            emit,
            &format!("working folder: {}", work.path().display()),
        )?;
        let args = CloneArgs {
            disc: game.disc.root.clone(),
            out: work.path().join("disc"),
            source: job.source.to_string(),
            slots,
            iso: Some(partial.clone()),
            ui_source: job.ui_source,
            no_stats: !job.copy_stats,
            colour_word: if job.keep_colour_word {
                ColourWord::Donor
            } else {
                ColourWord::Clone
            },
            force: true,
            textures: job
                .clones
                .iter()
                .filter_map(|r| Some((r.slot, r.textures.clone()?)))
                .collect(),
            anims: job
                .clones
                .iter()
                .filter_map(|r| Some((r.slot, r.anims?)))
                .collect(),
            ..CloneArgs::default()
        };
        let mut log_error = None;
        let mut patch_log = |line: &str| {
            if !line.starts_with("Boot: ")
                && let Err(e) = emit_log(&mut log, emit, line)
            {
                log_error = Some(e);
            }
        };
        let mut progress = |fraction, label: &str| {
            if cancel.load(Ordering::Relaxed) {
                return Err(Error::new("cancelled".into()));
            }
            emit(Event::Progress {
                fraction,
                label: label.into(),
            });
            Ok(())
        };
        let result = crate::clone::clone(&args, &mut patch_log, Some(&mut progress))?;
        if let Some(e) = log_error {
            return Err(e);
        }
        if cancel.load(Ordering::Relaxed) {
            return Err(Error::new("cancelled".into()));
        }
        // std rename replaces a file on Unix; on Windows an existing destination needs removal.
        #[cfg(windows)]
        if job.output.exists() {
            fs::remove_file(&job.output)?;
        }
        fs::rename(&partial, &job.output)?;
        emit_log(&mut log, emit, &format!("saved {}", job.output.display()))?;
        Ok(result)
    })();
    if let Err(e) = &result {
        let line = if e.message == "cancelled" {
            "cancelled".into()
        } else {
            format!("ERROR: {e}")
        };
        emit_log(&mut log, emit, &line)?;
    }
    if partial.exists() {
        let _ = fs::remove_file(&partial);
    }
    let kept = if job.keep_work {
        let path = work.keep().join("disc");
        emit_log(
            &mut log,
            emit,
            &format!("kept the working folder: {}", path.display()),
        )?;
        Some(path)
    } else {
        emit_log(&mut log, emit, "removing the working folder")?;
        drop(work);
        None
    };
    result?;
    let hint = pick_hint(
        &game.dol,
        job.source,
        &job.clones.iter().map(|r| r.slot).collect::<Vec<_>>(),
        job.ui_source,
    )?;
    Ok(Done {
        iso: job.output.clone(),
        work: kept,
        hint,
    })
}
/// Source-region metadata returned to the window.
#[derive(Serialize)]
pub struct RegionInfo {
    /// Region index accepted by preview and create.
    pub index: usize,
    /// Friendly region name.
    pub label: String,
    /// Whether the checkbox starts selected.
    pub default: bool,
    /// RGB hex swatch.
    pub swatch: String,
}
/// Source-specific regions, donor dropdown and initial clone rows.
#[derive(Serialize)]
pub struct SourceInfo {
    /// Available region checkboxes.
    pub regions: Vec<RegionInfo>,
    /// Donors with displayed labels.
    pub slots: Vec<SlotInfo>,
    /// Initial clone rows.
    pub default_rows: Vec<CloneRow>,
}
/// Donor ID and label serialized by source_info.
#[derive(Serialize)]
pub struct SlotInfo {
    /// Donor charID.
    pub id: usize,
    /// Friendly name and selection consequence.
    pub label: String,
}
/// Preview command arguments from the window.
#[derive(Deserialize)]
pub struct PreviewRequest {
    /// Source charID.
    pub source: usize,
    /// Selected donor charID for the caption.
    pub slot: usize,
    /// Preset name or #rrggbb.
    pub colour: String,
    /// Brightness preservation amount.
    pub keeplum: f64,
    /// Checked region indices.
    pub regions: Vec<usize>,
    /// The row's painted texture folder; the after side then shows those instead of the colour.
    #[serde(default)]
    pub textures: Option<PathBuf>,
    /// The row's "Animations from" character, named in the caption when it is not the source.
    #[serde(default)]
    pub anims: Option<usize>,
}
/// Before/after atlas images and the Python window's explanatory caption.
#[derive(Serialize)]
pub struct Preview {
    /// Base64 PNG of original textures.
    pub before_png: String,
    /// Base64 PNG of recoloured textures.
    pub after_png: String,
    /// Description of source, slot, colour and affected regions.
    pub caption: String,
}
/// One entry of a row's "Animations from" dropdown.
#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct AnimChoice {
    /// CharID to put in `clones[].anims` (for a family, its first member).
    pub id: usize,
    /// Displayed name; the source's own is first, marked "(own)".
    pub label: String,
    /// Whether every bone binds as on the animations' own character.
    pub exact: bool,
    /// What will look different, one line each (tooltip); empty when `exact`.
    pub note: String,
}
/// "Animations from" choices for a source's clones: its own, then every other set of
/// animation banks its body can wear (exact bone matches first, then those with warnings),
/// refused sets left out.
/// Characters that share every bank (colour families) are offered once, by family name.
pub fn anim_choices(
    disc: &crate::disc::Disc,
    dol: &Dol,
    rigs: &mut Rigs,
    source: usize,
) -> Result<Vec<AnimChoice>> {
    check_id(source)?;
    let body = rigs.get(disc, dol, source)?;
    let mut seen = vec![body.vector.clone()];
    let mut out = vec![AnimChoice {
        id: source,
        label: format!("{} (own)", pretty_name(source)),
        exact: true,
        note: String::new(),
    }];
    let mut warned = Vec::new();
    for c in 0..54 {
        let rig = rigs.get(disc, dol, c)?;
        if seen.contains(&rig.vector) {
            continue;
        }
        seen.push(rig.vector.clone());
        let fit = anims::fit(&body, &rig);
        if fit.refused() {
            continue;
        }
        let fam = dol.family(c)?[0];
        let name = if fam != 0 {
            family_name(fam, false)
        } else {
            pretty_name(c)
        };
        let notes = anims::warnings(&fit, &pretty_name(source), &name);
        let choice = AnimChoice {
            id: c,
            label: name,
            exact: notes.is_empty(),
            note: notes.join("\n"),
        };
        if choice.exact {
            out.push(choice);
        } else {
            warned.push(choice);
        }
    }
    out.extend(warned);
    Ok(out)
}
/// Where `export_textures` wrote the PNGs, and how many.
#[derive(Debug, Serialize)]
pub struct ExportedTextures {
    /// The new folder holding the PNGs and README.txt.
    pub folder: PathBuf,
    /// Number of PNGs written.
    pub count: usize,
}
/// Stateful command facade for the Tauri window; create runs on its own worker thread.
#[derive(Default)]
pub struct Core {
    game: Mutex<Option<Game>>,
    cancel: Arc<AtomicBool>,
    running: Arc<AtomicBool>,
}
impl Core {
    /// Select and check a game without extracting an ISO.
    pub fn open_game(&self, path: &Path) -> OpenGame {
        let (game, status) = load_game(path);
        *self.game.lock().unwrap() = game;
        status
    }
    /// Return every selectable source, independently of the selected game.
    pub fn sources(&self) -> Vec<Source> {
        sources()
    }
    /// Return regions, compatible donor labels and default rows for a source.
    pub fn source_info(&self, source: usize) -> std::result::Result<SourceInfo, ApiError> {
        let mut lock = self.game.lock().unwrap();
        let game = lock.as_mut().ok_or_else(|| ApiError {
            error: "Choose a game first.".into(),
        })?;
        let choices = donor_choices(&game.dol, source)?;
        let regions = game
            .preview(source)?
            .regions
            .iter()
            .enumerate()
            .map(|(index, r)| RegionInfo {
                index,
                label: capitalize(&r.name),
                default: r.default,
                swatch: r.swatch(),
            })
            .collect();
        Ok(SourceInfo {
            regions,
            default_rows: default_rows(&choices),
            slots: choices
                .into_iter()
                .map(|c| SlotInfo {
                    id: c.id,
                    label: c.label,
                })
                .collect(),
        })
    }
    /// Choose the first unused compatible donor for a new clone row.
    pub fn next_free_slot(
        &self,
        source: usize,
        used: &[usize],
    ) -> std::result::Result<Option<usize>, ApiError> {
        let lock = self.game.lock().unwrap();
        let game = lock.as_ref().ok_or_else(|| ApiError {
            error: "Choose a game first.".into(),
        })?;
        Ok(next_free_slot(&donor_choices(&game.dol, source)?, used))
    }
    /// Render before/after texture atlases using the actual game patch code.
    pub fn preview(&self, request: &PreviewRequest) -> std::result::Result<Preview, ApiError> {
        check_id(request.slot)?;
        let mut lock = self.game.lock().unwrap();
        let game = lock.as_mut().ok_or_else(|| ApiError {
            error: "Choose a game first.".into(),
        })?;
        let moves = match request.anims {
            Some(a) if a != request.source => {
                check_id(a)?;
                format!("; animations from {}", pretty_name(a))
            }
            _ => String::new(),
        };
        let pv = game.preview(request.source)?;
        let before_png = base64::engine::general_purpose::STANDARD.encode(&pv.before_png);
        if let Some(folder) = &request.textures {
            let painted = Painted::load(folder, &pv.body, &pretty_name(request.source))?;
            let mut g = pv.body.clone();
            painted.paint(&pv.body, &pv.body, &mut g)?;
            let after = png_bytes(256, pv.height, &pv.atlas(&g)?)?;
            return Ok(Preview {
                before_png,
                after_png: base64::engine::general_purpose::STANDARD.encode(after),
                caption: format!(
                    "All {} textures of {}, before and as {} with your textures ({} painted){moves}",
                    pv.body.textures.len(),
                    pretty_name(request.source),
                    pretty_name(request.slot),
                    painted.edited.len()
                ),
            });
        }
        let regions = region_selection(&pv.regions, &request.regions);
        let spec = colour_spec(&request.colour, request.keeplum, regions.as_deref())?;
        let mut rc = Recolour::new(&spec)?;
        rc.resolve(&pv.regions)?;
        let after_png = base64::engine::general_purpose::STANDARD.encode(pv.after_png(&spec)?);
        Ok(Preview {
            before_png,
            after_png,
            caption: format!(
                "All {} textures of {}, before and as {} in {} (changes: {}){moves}",
                pv.body.textures.len(),
                pretty_name(request.source),
                pretty_name(request.slot),
                colour_label(&request.colour),
                rc.describe()
            ),
        })
    }
    /// Suggest an output path alongside the selected game.
    pub fn default_output(&self, path: &Path) -> PathBuf {
        default_output(path)
    }
    /// Return null on success or the contract's friendly {error} object; painted texture
    /// folders and "Animations from" skeletons are checked against the source here, before the
    /// long job starts.
    pub fn validate_job(&self, job: &Job) -> Option<ApiError> {
        let folders = || -> Result<()> {
            validate_job(job, None)?;
            let wanted: Vec<_> = job
                .clones
                .iter()
                .filter_map(|r| Some((r.slot, r.anims?)))
                .collect();
            if job.clones.iter().all(|r| r.textures.is_none()) && wanted.is_empty() {
                return Ok(());
            }
            let mut lock = self.game.lock().unwrap();
            let game = lock
                .as_mut()
                .ok_or_else(|| Error::new("Choose a game first.".into()))?;
            let slots: Vec<_> = job.clones.iter().map(|r| r.slot).collect();
            anims::plan(
                &mut game.rigs,
                &game.disc,
                &game.dol,
                job.source,
                &slots,
                &wanted,
            )?;
            let body = &game.preview(job.source)?.body;
            for folder in job.clones.iter().filter_map(|r| r.textures.as_ref()) {
                Painted::load(folder, body, &pretty_name(job.source))?;
            }
            Ok(())
        };
        folders().err().map(Into::into)
    }
    /// "Animations from" choices for the source's clone rows (`anim_choices`); the first call
    /// reads the bone tables of the whole roster (a fraction of a second).
    pub fn anim_choices(&self, source: usize) -> std::result::Result<Vec<AnimChoice>, ApiError> {
        let mut lock = self.game.lock().unwrap();
        let game = lock.as_mut().ok_or_else(|| ApiError {
            error: "Choose a game first.".into(),
        })?;
        Ok(anim_choices(&game.disc, &game.dol, &mut game.rigs, source)?)
    }
    /// Write the source's original body textures as PNGs into `<folder>/<Name> textures`.
    pub fn export_textures(
        &self,
        source: usize,
        folder: &Path,
    ) -> std::result::Result<ExportedTextures, ApiError> {
        check_id(source)?;
        let mut lock = self.game.lock().unwrap();
        let game = lock.as_mut().ok_or_else(|| ApiError {
            error: "Choose a game first.".into(),
        })?;
        let name = pretty_name(source);
        let folder = folder.join(format!("{name} textures"));
        let count = custom_textures::export(&game.preview(source)?.body, &name, &folder)?;
        Ok(ExportedTextures { folder, count })
    }
    /// Start the job, sending progress/log/done/error events through a thread-safe callback.
    pub fn create(
        &self,
        job: Job,
        emit: impl Fn(Event) + Send + Sync + 'static,
    ) -> Result<std::thread::JoinHandle<()>> {
        if self.running.swap(true, Ordering::SeqCst) {
            return Err(Error::new("A clone job is already running.".into()));
        }
        self.cancel.store(false, Ordering::SeqCst);
        let cancel = self.cancel.clone();
        let running = self.running.clone();
        Ok(std::thread::spawn(move || {
            match run_job(&job, &emit, &cancel) {
                Ok(done) => emit(Event::Done(done)),
                Err(e) => emit(Event::Error {
                    cancelled: e.message == "cancelled",
                    message: friendly_error(&e),
                }),
            }
            running.store(false, Ordering::SeqCst);
        }))
    }
    /// Stop the current worker at its next progress step.
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::SeqCst);
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn names_and_custom_specs() {
        assert_eq!(pretty_name(29), "Blue Toad");
        assert_eq!(pretty_name(19), "Bowser Jr.");
        assert_eq!(
            colour_spec("#00ff00", 0.25, None).unwrap(),
            "hue=0.333,keeplum=0.25"
        );
        assert_eq!(
            colour_spec("blue", 1., Some(&[1, 0])).unwrap(),
            "hue=blue,keeplum=1,region=0/1"
        );
    }
    #[test]
    fn friendly_io_messages() {
        let full = Error::from(std::io::Error::from_raw_os_error(28));
        assert_eq!(
            friendly_error(&full),
            "The drive is full. Free up some space (about 3.3 GB) and try again."
        );
        let denied =
            Error::from(std::io::Error::from_raw_os_error(13)).at_path(Path::new("game.iso"));
        assert!(friendly_error(&denied).starts_with("Couldn't read or write a file (game.iso): "));
        assert!(
            friendly_error(&denied)
                .ends_with(" If that game is open in Dolphin or Rio, close it first.")
        );
    }
    #[test]
    fn worker_reports_validation_error_and_can_restart() {
        let core = Core::default();
        let job = Job {
            game: "missing".into(),
            output: "output.iso".into(),
            source: 0,
            clones: vec![],
            keeplum: 0.5,
            regions: vec![],
            ui_source: true,
            copy_stats: true,
            keep_colour_word: false,
            keep_work: false,
        };
        for _ in 0..2 {
            let (send, receive) = std::sync::mpsc::channel();
            core.create(job.clone(), move |event| send.send(event).unwrap())
                .unwrap()
                .join()
                .unwrap();
            assert!(
                matches!(receive.recv().unwrap(),Event::Error{message,cancelled:false} if message=="Add at least one clone.")
            );
        }
    }
    #[test]
    fn api_event_shape() {
        assert_eq!(
            serde_json::to_value(Event::Progress {
                fraction: 0.5,
                label: "Patching".into()
            })
            .unwrap(),
            serde_json::json!({"event":"progress","payload":{"fraction":0.5,"label":"Patching"}})
        );
    }
    #[test]
    fn clone_row_textures_are_optional() {
        let row: CloneRow = serde_json::from_str(r#"{"slot":29,"colour":"blue"}"#).unwrap();
        assert_eq!(row.textures, None);
        assert_eq!(
            serde_json::to_value(&row).unwrap(),
            serde_json::json!({"slot":29,"colour":"blue"})
        );
        let row: CloneRow =
            serde_json::from_str(r#"{"slot":29,"colour":"blue","textures":"art","anims":1}"#)
                .unwrap();
        assert_eq!(row.textures, Some(PathBuf::from("art")));
        assert_eq!(row.anims, Some(1));
    }
}
