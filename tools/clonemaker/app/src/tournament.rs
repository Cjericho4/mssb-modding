//! Build a two-player Rio matchup from portable batting orders and the players' painted PNGs.
use base64::{engine::general_purpose::STANDARD, Engine};
use mssb_clone_core::{
    api::{self, CloneRow, Job},
    binary::Dol,
    custom_textures,
    disc::Disc,
    stats,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{Read, Seek, Write},
    path::{Path, PathBuf},
    sync::{atomic::AtomicBool, LazyLock},
};

const FORMAT: &str = "mssb-tournament-team-v1";
const BASE_SHA256: &str = "e3cd4dc92dd85f72b3dd10a95f2e075f612c197862b94c86466c64970f4efae9";
const CAPTAINS: [usize; 12] = [0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 17, 19];
const MAX_PNG: usize = 2 * 1024 * 1024;
const MAX_TEAM_PNG: usize = 16 * 1024 * 1024;
const MAX_TEAM_PIXELS: usize = 16 * 1024 * 1024;

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Team<S = usize> {
    pub(crate) format: String,
    pub(crate) player: String,
    pub(crate) rows: Vec<Batter<S>>,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Batter<S = usize> {
    pub(crate) source: S,
    pub(crate) pos: u8,
    pub(crate) captain: bool,
    pub(crate) star: bool,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub(crate) label: String,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub(crate) textures: BTreeMap<String, String>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub(crate) stats: stats::Overrides,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct BuildSummary {
    pub(crate) iso_path: PathBuf,
    pub(crate) hud_path: PathBuf,
    pub(crate) sha256: String,
    pub(crate) hud_sha256: String,
    pub(crate) clone_count: usize,
}

fn message(e: impl std::fmt::Display) -> String {
    e.to_string()
}

// Use the filename shape produced by Clone Maker, including the index padding. A submission
// supplies file contents, never a local filename to open or a relative directory to traverse.
fn texture_data(name: &str, encoded: &str) -> Result<(Vec<u8>, usize), String> {
    let (i, w, h) = custom_textures::parse_name(name)
        .ok_or_else(|| format!("Invalid texture filename: {name}"))?;
    if name != format!("t{i:02}_{w}x{h}.png")
        || i >= 256
        || !(1..=1024).contains(&w)
        || !(1..=1024).contains(&h)
    {
        return Err(format!("Invalid texture index, size, or filename: {name}"));
    }
    if encoded.len() > MAX_PNG.div_ceil(3) * 4 {
        return Err(format!("{name} exceeds the 2 MiB texture limit."));
    }
    let bytes = STANDARD
        .decode(encoded)
        .map_err(|_| format!("{name} is not base64."))?;
    if bytes.len() < 33
        || bytes.len() > MAX_PNG
        || &bytes[..16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        || bytes[16..20] != (w as u32).to_be_bytes()
        || bytes[20..24] != (h as u32).to_be_bytes()
    {
        return Err(format!(
            "{name} must be a PNG matching its stated dimensions."
        ));
    }
    Ok((bytes, w * h))
}

// A portable sheet can have open slots. The builder accepts only teams with concrete source IDs.
pub(crate) type TeamSheet = Team<Option<usize>>;

pub(crate) fn validate_sheet(team: &TeamSheet) -> Result<(), String> {
    validate_roster(team, false)
}

// Each fingerprint identifies the visible RGBA pixels of an original Studio texture.
static STOCK_TEXTURES: LazyLock<BTreeMap<(usize, &'static str), &'static str>> =
    LazyLock::new(|| {
        include_str!("stock-team-textures.tsv")
            .lines()
            .map(|line| {
                let (source, rest) = line.split_once('\t').expect("stock texture source");
                let (name, digest) = rest.split_once('\t').expect("stock texture fingerprint");
                ((source.parse().expect("stock character ID"), name), digest)
            })
            .collect()
    });

// All portable files carry the same explicit team fields. Rebuild each user-painted
// PNG from its pixels so a selected file cannot bundle metadata or trailing file data.
pub(crate) fn portable_sheet(mut team: TeamSheet) -> Result<TeamSheet, String> {
    validate_sheet(&team)?;
    let mut total = 0;
    for row in &mut team.rows {
        let Some(source) = row.source else { continue };
        for (name, encoded) in std::mem::take(&mut row.textures) {
            let expected = STOCK_TEXTURES.get(&(source, name.as_str())).ok_or_else(|| {
                format!("{name} does not match a texture for this character. Use a changed PNG from this character’s Studio export.")
            })?;
            let (bytes, pixel_count) = texture_data(&name, &encoded)?;
            let (w, h, mut rgba) = custom_textures::decode_png(&bytes, &name).map_err(message)?;
            if w * h != pixel_count {
                return Err(format!("{name} must contain a full-size texture."));
            }
            for pixel in rgba.as_chunks_mut::<4>().0 {
                if pixel[3] == 0 {
                    pixel[..3].fill(0);
                }
            }
            let digest = format!("{:x}", Sha256::digest(&rgba));
            if digest == *expected {
                continue;
            }
            if STOCK_TEXTURES.values().any(|stock| *stock == digest) {
                return Err(format!("{name} contains unchanged artwork from another original game texture. Choose your edited texture."));
            }
            let png = custom_textures::rgba_png(w, h, &rgba).map_err(message)?;
            total += png.len();
            if png.len() > MAX_PNG || total > MAX_TEAM_PNG {
                return Err(format!("The exported texture pixels exceed the 2 MiB per picture or 16 MiB per team limit at {name}."));
            }
            row.textures.insert(name, STANDARD.encode(png));
        }
    }
    Ok(team)
}

#[tauri::command]
pub(crate) async fn prepare_team_sheet(team: TeamSheet) -> Result<TeamSheet, String> {
    tauri::async_runtime::spawn_blocking(move || portable_sheet(team))
        .await
        .map_err(message)?
}

pub(crate) fn validate(team: &Team) -> Result<(), String> {
    validate_roster(team, true)
}

fn validate_roster<S: Copy + Into<Option<usize>>>(
    team: &Team<S>,
    for_match: bool,
) -> Result<(), String> {
    if team.format != FORMAT {
        return Err("Choose a version 1 tournament team submission.".into());
    }
    let player = team.player.trim();
    if (for_match && (player.is_empty() || ["CPU", "No Player Selected"].contains(&player)))
        || team.player.encode_utf16().count() > 30
        || team.player.chars().any(char::is_control)
    {
        return Err("Enter the human player's Rio lobby name (up to 30 characters).".into());
    }
    if team.rows.len() != 9 {
        return Err("Submit exactly nine batters.".into());
    }
    let mut positions = BTreeSet::new();
    let mut byte_count = 0;
    let mut pixel_count = 0;
    for (i, row) in team.rows.iter().enumerate() {
        let source = row.source.into();
        if source.is_some_and(|id| id >= 54) || row.pos >= 9 || !positions.insert(row.pos) {
            return Err(format!(
                "Check the source and unique field position of batter {}.",
                i + 1
            ));
        }
        if row.label.encode_utf16().count() > 64 || row.label.chars().any(char::is_control) {
            return Err(format!(
                "Use a player label of up to 64 characters for batter {}.",
                i + 1
            ));
        }
        if source.is_none()
            && (row.captain
                || row.star
                || !row.label.is_empty()
                || !row.stats.is_empty()
                || !row.textures.is_empty())
        {
            return Err(format!(
                "Empty batting spot {} has player details. Choose a character first.",
                i + 1
            ));
        }
        if row.captain && !source.is_some_and(|id| CAPTAINS.contains(&id)) {
            return Err(format!(
                "Batter {} must be a captain-eligible character.",
                i + 1
            ));
        }
        stats::validate(&row.stats).map_err(message)?;
        if row.textures.len() > 64 {
            return Err(format!("Batter {} exceeds 64 texture files.", i + 1));
        }
        for (name, encoded) in &row.textures {
            let (bytes, pixels) = texture_data(name, encoded)?;
            byte_count += bytes.len();
            pixel_count += pixels;
            if byte_count > MAX_TEAM_PNG || pixel_count > MAX_TEAM_PIXELS {
                return Err(
                    "The team's textures exceed 16 MiB of PNGs or 16 million pixels.".into(),
                );
            }
        }
    }
    let captains = team.rows.iter().filter(|r| r.captain).count();
    if captains > 1 || (for_match && captains != 1) {
        return Err("Choose exactly one captain on each team.".into());
    }
    Ok(())
}

// An augmenting path can free a scarce compatible donor without violating any character's
// requirements. Visit free slots first so existing choices stay intact whenever possible.
fn assign(
    row: usize,
    choices: &[Vec<usize>],
    owners: &mut [Option<usize>],
    seen: &mut [bool],
) -> bool {
    for &slot in &choices[row] {
        if !seen[slot] && owners[slot].is_none() {
            owners[slot] = Some(row);
            return true;
        }
    }
    for &slot in &choices[row] {
        if seen[slot] {
            continue;
        }
        seen[slot] = true;
        if let Some(previous) = owners[slot] {
            if assign(previous, choices, owners, seen) {
                owners[slot] = Some(row);
                return true;
            }
        }
    }
    false
}

// Slot decisions depend only on v1 inputs, in Away then Home batting order, and the USA disc.
fn plan(rows: &[&Batter], donors: &BTreeMap<usize, Vec<usize>>) -> Result<Vec<usize>, String> {
    let sources: BTreeSet<_> = rows.iter().map(|r| r.source).collect();
    let mut owners = vec![None; 54];
    for &source in &sources {
        let stock = rows
            .iter()
            .enumerate()
            .filter(|(_, r)| r.source == source && r.stats.is_empty() && r.textures.is_empty())
            .min_by_key(|(i, r)| (!r.captain, *i));
        if let Some((i, _)) = stock {
            owners[source] = Some(i);
        }
    }
    let mut choices = vec![Vec::new(); rows.len()];
    let mut needed = Vec::new();
    for (i, row) in rows.iter().enumerate() {
        if owners[row.source] == Some(i) {
            continue;
        }
        choices[i] = donors[&row.source]
            .iter()
            .copied()
            .filter(|d| !sources.contains(d) && (!row.captain || CAPTAINS.contains(d)))
            .collect();
        // Leave eligible captain slots available when an ordinary variant fits.
        if !row.captain {
            choices[i].sort_by_key(|d| CAPTAINS.contains(d));
        }
        needed.push(i);
    }
    needed.sort_by_key(|&i| (!rows[i].captain, choices[i].len(), i));
    for i in needed {
        if !assign(i, &choices, &mut owners, &mut [false; 54]) {
            return Err(format!(
                "No compatible {}slot for {} in this matchup. Adjust one of the submitted players.",
                if rows[i].captain { "captain " } else { "" },
                api::pretty_name(rows[i].source)
            ));
        }
    }
    let mut slots = vec![0; rows.len()];
    for (slot, owner) in owners.into_iter().enumerate() {
        if let Some(i) = owner {
            slots[i] = slot;
        }
    }
    Ok(slots)
}

fn hash(file: &mut File) -> Result<String, String> {
    file.rewind().map_err(message)?;
    let mut sha = Sha256::new();
    let mut buf = vec![0u8; 1024 * 1024];
    loop {
        let n = file.read(&mut buf).map_err(message)?;
        if n == 0 {
            break;
        }
        sha.update(&buf[..n]);
    }
    Ok(format!("{:x}", sha.finalize()))
}

fn hud(away: &Team, home: &Team, slots: &[usize], hands: &[[u8; 2]], sha: &str) -> Value {
    let mut doc = json!({"Clone Maker SHA256":sha, "TagSetID":-1, "Inning":1, "Half Inning":0,
        "Away Batter Roster Loc":0, "Home Batter Roster Loc":0});
    for (j, (side, team)) in [("Away", away), ("Home", home)].into_iter().enumerate() {
        doc[format!("{side} Player")] = json!(team.player.trim());
        for (i, row) in team.rows.iter().enumerate() {
            let slot = slots[j * 9 + i];
            let mut entry = json!({"CharID":slot, "Fielding Position":row.pos,
                "Batting Hand":hands[slot][0], "Fielding Hand":hands[slot][1],
                "Captain":u8::from(row.captain), "Superstar":u8::from(row.star), "Defensive Stats":{}});
            if !row.label.trim().is_empty() {
                entry["Clone Maker Label"] = json!(row.label.trim());
            }
            doc[format!("{side} Roster {i}")] = entry;
        }
    }
    doc
}

fn hud_bytes(mut doc: Value) -> Result<Vec<u8>, String> {
    doc.sort_all_objects();
    let mut bytes = serde_json::to_vec_pretty(&doc).map_err(message)?;
    bytes.push(b'\n');
    Ok(bytes)
}

// Publish both files together. A simultaneous build or a user-created empty directory
// at the destination must win without being replaced.
#[cfg(any(target_os = "macos", target_os = "linux"))]
fn publish(stage: &Path, output: &Path) -> Result<(), String> {
    use rustix::fs::{renameat_with, RenameFlags, CWD};
    renameat_with(CWD, stage, CWD, output, RenameFlags::NOREPLACE).map_err(message)
}
#[cfg(windows)]
fn publish(stage: &Path, output: &Path) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::MoveFileW;

    fn wide_path(path: &Path) -> Result<Vec<u16>, String> {
        let path = std::path::absolute(path).map_err(message)?;
        let (Some(parent), Some(name)) = (path.parent(), path.file_name()) else {
            return Err("Expected a matchup folder name.".into());
        };
        // Canonicalize the existing parent for Win32 long-path support. The destination
        // itself need not exist, and a link at its name must count as a collision.
        let parent = fs::canonicalize(parent).map_err(message)?;
        let mut wide: Vec<u16> = parent.join(name).as_os_str().encode_wide().collect();
        if wide.contains(&0) {
            return Err("A matchup folder path contains a null character.".into());
        }
        wide.push(0);
        Ok(wide)
    }

    let from = wide_path(stage)?;
    let to = wide_path(output)?;
    // MoveFileW renames a directory only if the new name is free. Both UTF-16
    // buffers are NUL-terminated and remain valid for the duration of the call.
    if unsafe { MoveFileW(from.as_ptr(), to.as_ptr()) } == 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    Ok(())
}

#[cfg(not(any(windows, target_os = "macos", target_os = "linux")))]
fn publish(stage: &Path, output: &Path) -> Result<(), String> {
    fs::rename(stage, output).map_err(message)
}

pub(crate) fn build(
    away: &Team,
    home: &Team,
    base: &Path,
    output: &Path,
    progress: impl Fn(&str),
) -> Result<BuildSummary, String> {
    validate(away)?;
    validate(home)?;
    if away.player.trim() == home.player.trim() {
        return Err("Choose two different human Rio lobby names.".into());
    }
    let parent = output.parent().ok_or("Choose a new matchup folder.")?;
    if output.try_exists().map_err(message)? {
        return Err("That matchup folder already exists. Choose a new output folder.".into());
    }
    progress("Verifying the clean USA game image");
    let mut original = File::open(base).map_err(message)?;
    let initial = original.metadata().map_err(message)?;
    if hash(&mut original)? != BASE_SHA256 {
        return Err(
            "Start with a clean Mario Superstar Baseball USA disc image (GYQE01 revision 0)."
                .into(),
        );
    }
    let disc = Disc::iso(base).map_err(message)?;
    let dol = Dol::new(disc.system("main.dol").map_err(message)?).map_err(message)?;
    let rows: Vec<_> = away.rows.iter().chain(&home.rows).collect();
    let mut available = BTreeMap::new();
    for source in rows.iter().map(|r| r.source).collect::<BTreeSet<_>>() {
        let donors = api::donor_choices(&dol, source).map_err(message)?;
        available.insert(source, donors.into_iter().map(|d| d.id).collect());
    }
    let slots = plan(&rows, &available)?;
    let stage = tempfile::Builder::new()
        .prefix(".mssb-match-")
        .tempdir_in(parent)
        .map_err(message)?;
    let assets = tempfile::Builder::new()
        .prefix("textures-")
        .tempdir_in(stage.path())
        .map_err(message)?;
    let mut clones = Vec::new();
    for (&slot, row) in slots.iter().zip(rows.iter()) {
        if slot == row.source {
            continue;
        }
        let mut textures = None;
        if !row.textures.is_empty() {
            let dir = assets.path().join(slot.to_string());
            fs::create_dir(&dir).map_err(message)?;
            for (name, encoded) in &row.textures {
                let (bytes, _) = texture_data(name, encoded)?;
                fs::write(dir.join(name), bytes).map_err(message)?;
            }
            textures = Some(dir);
        }
        clones.push(CloneRow {
            slot,
            source: Some(row.source),
            colour: "original".into(),
            textures,
            stats: row.stats.clone(),
            anims: None,
            regions: None,
        });
    }
    let iso = stage.path().join("matchup.iso");
    let clone_count = clones.len();
    if clones.is_empty() {
        progress("Copying the clean image for this matchup");
        original.rewind().map_err(message)?;
        let mut out = File::create(&iso).map_err(message)?;
        std::io::copy(&mut original, &mut out).map_err(message)?;
        out.sync_all().map_err(message)?;
    } else {
        let job = Job {
            game: base.to_owned(),
            output: iso.clone(),
            source: clones[0].source.unwrap(),
            clones,
            keeplum: 0.0,
            regions: vec![],
            ui_source: true,
            copy_stats: true,
            keep_colour_word: false,
            keep_work: false,
        };
        let stage_name = std::cell::RefCell::new(String::new());
        api::run_job(
            &job,
            &|event| {
                if let api::Event::Progress { label, .. } = event {
                    let mut previous = stage_name.borrow_mut();
                    if *previous != label {
                        progress(&label);
                        *previous = label;
                    }
                }
            },
            &AtomicBool::new(false),
        )
        .map_err(message)?;
        fs::remove_file(iso.with_extension("log")).map_err(message)?;
    }
    assets.close().map_err(message)?;
    let current = fs::metadata(base).map_err(message)?;
    if initial.len() != current.len() || initial.modified().ok() != current.modified().ok() {
        return Err(
            "The base ISO changed during the tournament build. Choose the clean ISO again.".into(),
        );
    }
    progress("Checking the finished ISO and writing both lineups");
    let mut image = File::open(&iso).map_err(message)?;
    let hands = stats::roster_hands(&mut image, &iso).map_err(message)?;
    let sha256 = hash(&mut image)?;
    if clone_count == 0 && sha256 != BASE_SHA256 {
        return Err("The copied base image changed.".into());
    }
    let mut hud_file = File::create(stage.path().join("hud.json")).map_err(message)?;
    let bytes = hud_bytes(hud(away, home, &slots, &hands, &sha256))?;
    let hud_sha256 = format!("{:x}", Sha256::digest(&bytes));
    hud_file.write_all(&bytes).map_err(message)?;
    hud_file.sync_all().map_err(message)?;
    drop(hud_file);
    drop(image);
    publish(stage.path(), output)?;
    Ok(BuildSummary {
        iso_path: output.join("matchup.iso"),
        hud_path: output.join("hud.json"),
        sha256,
        hud_sha256,
        clone_count,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn team(name: &str, sources: [usize; 9]) -> Team {
        Team {
            format: FORMAT.into(),
            player: name.into(),
            rows: sources
                .into_iter()
                .enumerate()
                .map(|(i, source)| Batter {
                    source,
                    pos: ((i + 2) % 9) as u8,
                    captain: i == 0,
                    star: i == 3,
                    label: String::new(),
                    textures: BTreeMap::new(),
                    stats: BTreeMap::new(),
                })
                .collect(),
        }
    }

    #[test]
    fn duplicate_captains_and_independent_stats_retain_disjoint_slots_and_order() {
        let mut away = team("Away", [2, 2, 2, 4, 5, 6, 7, 8, 10]);
        away.rows[1].label = "Bench press".into();
        away.rows[1].stats.insert("speed".into(), 91);
        let home = team("Home", [2, 5, 5, 1, 3, 11, 12, 13, 14]);
        let rows: Vec<_> = away.rows.iter().chain(&home.rows).collect();
        let candidates = rows.iter().map(|r| (r.source, (0..54).collect())).collect();
        let slots = plan(&rows, &candidates).unwrap();
        assert_eq!(slots, plan(&rows, &candidates).unwrap());
        assert_eq!(slots.iter().collect::<BTreeSet<_>>().len(), 18);
        assert_eq!(slots[0], 2);
        assert!(CAPTAINS.contains(&slots[9]));
        assert!(!rows.iter().any(|r| r.source == slots[1]));
        let hands = vec![[0, 1]; 54];
        let doc = hud(&away, &home, &slots, &hands, BASE_SHA256);
        assert_eq!(doc["Away Roster 1"]["Clone Maker Label"], "Bench press");
        for (i, row) in rows.iter().enumerate() {
            let key = format!("{} Roster {}", if i < 9 { "Away" } else { "Home" }, i % 9);
            assert_eq!(doc[&key]["CharID"], slots[i]);
            assert_eq!(doc[&key]["Fielding Position"], row.pos);
        }
    }

    #[test]
    fn matchup_hash_includes_defense_even_when_iso_and_batting_slots_match() {
        let mut away = team("Away", [0, 1, 2, 3, 4, 5, 6, 7, 8]);
        let home = team("Home", [9, 10, 11, 12, 13, 14, 15, 16, 17]);
        let slots: Vec<_> = (0..18).collect();
        let hands = vec![[0, 0]; 54];
        let first = hud_bytes(hud(&away, &home, &slots, &hands, BASE_SHA256)).unwrap();
        assert_eq!(first.last(), Some(&b'\n'));
        assert_eq!(
            first,
            hud_bytes(hud(&away, &home, &slots, &hands, BASE_SHA256)).unwrap()
        );
        (away.rows[0].pos, away.rows[1].pos) = (away.rows[1].pos, away.rows[0].pos);
        let second = hud_bytes(hud(&away, &home, &slots, &hands, BASE_SHA256)).unwrap();
        assert_ne!(Sha256::digest(first), Sha256::digest(second));
    }

    #[test]
    fn a_completed_build_cannot_overwrite_another_output() {
        let tmp = tempfile::tempdir().unwrap();
        let stage = tmp.path().join("stage");
        let target = tmp.path().join("matchup");
        fs::create_dir(&stage).unwrap();
        fs::write(stage.join("hud.json"), "new").unwrap();
        fs::create_dir(&target).unwrap();
        assert!(publish(&stage, &target).is_err());
        assert_eq!(target.read_dir().unwrap().count(), 0);
        fs::write(target.join("hud.json"), "previous").unwrap();
        assert!(publish(&stage, &target).is_err());
        assert_eq!(fs::read(target.join("hud.json")).unwrap(), b"previous");
        publish(&stage, &tmp.path().join("other")).unwrap();
    }

    #[test]
    fn eligible_slots_can_be_reassigned_to_fit_a_more_constrained_clone() {
        let choices = vec![vec![29, 30], vec![30, 31], vec![29, 30]];
        let mut owners = vec![None; 54];
        for i in 0..3 {
            assert!(assign(i, &choices, &mut owners, &mut [false; 54]));
        }
        let assigned: BTreeSet<_> = owners.into_iter().flatten().collect();
        assert_eq!(assigned, BTreeSet::from([0, 1, 2]));
    }

    #[test]
    fn portable_team_keeps_painted_pixels_and_drops_nonimage_data() {
        // A 16x16 RGBA PNG with a tEXt chunk and unrelated bytes after IEND.
        let encoded = "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAG3RFWHRDb21tZW50AHByaXZhdGUtZWRpdG9yLW5vdGVgVxP0AAAAGUlEQVR4nGOwqTzwnxLMMGrAqAGjBgwXAwCmtnQfE7bFwQAAAABJRU5ErkJggnByaXZhdGUtdHJhaWxlcg==";
        let name = "t00_16x16.png";
        let rows = team("Visitor", [0, 1, 2, 3, 4, 5, 6, 7, 8]);
        let mut sheet: TeamSheet =
            serde_json::from_value(serde_json::to_value(rows).unwrap()).unwrap();
        sheet.rows[0].textures.insert(name.into(), encoded.into());
        let clean = portable_sheet(sheet.clone()).unwrap();
        let bytes = STANDARD.decode(&clean.rows[0].textures[name]).unwrap();
        assert!(!bytes.windows(7).any(|part| part == b"private"));
        let (w, h, rgba) = custom_textures::decode_png(&bytes, name).unwrap();
        assert_eq!((w, h), (16, 16));
        assert!(rgba.chunks_exact(4).all(|p| p == [0x3c, 0x79, 0xc0, 0xff]));
        assert_eq!(
            portable_sheet(clean.clone()).unwrap().rows[0].textures,
            clean.rows[0].textures
        );
        sheet.rows[0]
            .textures
            .insert(name.into(), STANDARD.encode(&bytes[..33]));
        assert!(portable_sheet(sheet).is_err());
    }

    #[test]
    #[ignore = "uses the owner's original USA disc to verify stock fingerprints"]
    fn sharing_omits_original_art_and_rejects_stock_art_from_another_slot() {
        let path = PathBuf::from(std::env::var_os("MSSB_RIO_RETAIL_ISO").unwrap());
        let disc = Disc::iso(&path).unwrap();
        let dol = Dol::new(disc.system("main.dol").unwrap()).unwrap();
        let mario = api::portrait_asset(&disc, &dol, 0).unwrap();
        let rows = team("Visitor", [0, 1, 2, 3, 4, 5, 6, 7, 8]);
        let mut sheet: TeamSheet =
            serde_json::from_value(serde_json::to_value(rows).unwrap()).unwrap();
        for (index, png) in mario.textures.into_iter().enumerate() {
            let bytes = STANDARD.decode(&png).unwrap();
            let (w, h, _) = custom_textures::decode_png(&bytes, "texture").unwrap();
            sheet.rows[0]
                .textures
                .insert(format!("t{index:02}_{w}x{h}.png"), png);
        }
        assert!(!sheet.rows[0].textures.is_empty());
        assert!(portable_sheet(sheet.clone()).unwrap().rows[0]
            .textures
            .is_empty());
        // Luigi has the same named material at a number of slots but different original art.
        let name = sheet.rows[0]
            .textures
            .keys()
            .find(|n| {
                STOCK_TEXTURES
                    .get(&(1, n.as_str()))
                    .is_some_and(|d| *d != STOCK_TEXTURES[&(0, n.as_str())])
            })
            .unwrap()
            .clone();
        let original = sheet.rows[0].textures.remove(&name).unwrap();
        sheet.rows[1].textures.insert(name, original);
        assert!(portable_sheet(sheet)
            .err()
            .unwrap()
            .contains("original game texture"));
    }

    #[test]
    fn submissions_reject_ambiguous_lineups_bad_stats_and_nonportable_textures() {
        let mut good = team("Human", [2, 2, 2, 4, 5, 6, 7, 8, 9]);
        validate(&good).unwrap();
        good.rows[1].pos = good.rows[0].pos;
        assert!(validate(&good).is_err());
        good.rows[1].pos = 3;
        good.rows[1].stats.insert("throwing_hand".into(), 2);
        assert!(validate(&good).is_err());
        good.rows[1].stats.clear();
        let png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAOam8i8AAAAASUVORK5CYII=";
        good.rows[1]
            .textures
            .insert("t00_1x1.png".into(), png.into());
        validate(&good).unwrap();
        for name in [
            "../t00_1x1.png",
            "/tmp/t00_1x1.png",
            "t0_1x1.png",
            "t00_2x2.png",
            "t00_8192x1.png",
        ] {
            assert!(texture_data(name, png).is_err(), "{name}");
        }
    }

    #[test]
    #[ignore = "uses the owner's retail disc and a directory of tournament submissions"]
    fn verify_local_league_matrix() {
        let root =
            PathBuf::from(std::env::var_os("MSSB_TOURNAMENT_TEAMS").expect("team directory"));
        let base = PathBuf::from(std::env::var_os("MSSB_TOURNAMENT_BASE").expect("retail ISO"));
        assert_eq!(hash(&mut File::open(&base).unwrap()).unwrap(), BASE_SHA256);
        let disc = Disc::iso(&base).unwrap();
        let dol = Dol::new(disc.system("main.dol").unwrap()).unwrap();
        let mut original = File::open(&base).unwrap();
        let retail_hands = stats::roster_hands(&mut original, &base).unwrap();
        let donors = (0..54)
            .map(|id| {
                (
                    id,
                    api::donor_choices(&dol, id)
                        .unwrap()
                        .into_iter()
                        .map(|d| d.id)
                        .collect(),
                )
            })
            .collect();
        let mut files: Vec<_> = fs::read_dir(root)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .filter(|p| {
                p.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .ends_with(".team.json")
            })
            .collect();
        files.sort();
        assert!(files.len() >= 2, "include at least two *.team.json files");
        let teams: Vec<Team> = files
            .iter()
            .map(|file| {
                let team: Team = serde_json::from_reader(File::open(file).unwrap()).unwrap();
                validate(&team).unwrap_or_else(|e| panic!("{}: {e}", file.display()));
                team
            })
            .collect();
        let mut pairs = 0;
        for j in 0..teams.len() {
            for k in 0..teams.len() {
                if k == j {
                    continue;
                }
                let rows: Vec<_> = teams[j].rows.iter().chain(&teams[k].rows).collect();
                let slots = plan(&rows, &donors).unwrap_or_else(|e| {
                    panic!("{} vs {}: {e}", files[j].display(), files[k].display())
                });
                assert_eq!(slots, plan(&rows, &donors).unwrap());
                assert_eq!(slots.iter().collect::<BTreeSet<_>>().len(), 18);
                let mut final_hands = retail_hands.clone();
                for (&slot, r) in slots.iter().zip(&rows) {
                    if slot != r.source {
                        assert!(!rows.iter().any(|other| other.source == slot));
                    }
                    if r.captain {
                        assert!(CAPTAINS.contains(&slot));
                    }
                    final_hands[slot] = [
                        *r.stats
                            .get("batting_hand")
                            .unwrap_or(&retail_hands[r.source][0]),
                        *r.stats
                            .get("throwing_hand")
                            .unwrap_or(&retail_hands[r.source][1]),
                    ];
                }
                let doc = hud(&teams[j], &teams[k], &slots, &final_hands, BASE_SHA256);
                for (i, r) in rows.iter().enumerate() {
                    let side = if i < 9 { "Away" } else { "Home" };
                    let entry = &doc[format!("{side} Roster {}", i % 9)];
                    assert_eq!(entry["CharID"], slots[i]);
                    assert_eq!(entry["Fielding Position"], r.pos);
                    assert_eq!(entry["Captain"], u8::from(r.captain));
                }
                pairs += 1;
            }
        }
        println!(
            "{} submissions, {pairs} planned matchups validated",
            teams.len()
        );
    }

    #[test]
    #[ignore = "uses the owner's clean disc and two portable tournament submissions"]
    fn build_local_fixture() {
        let env = |key: &str| PathBuf::from(std::env::var_os(key).expect(key));
        let read =
            |key: &str| serde_json::from_reader::<_, Team>(File::open(env(key)).unwrap()).unwrap();
        let away = read("MSSB_TOURNAMENT_AWAY");
        let home = read("MSSB_TOURNAMENT_HOME");
        let base = env("MSSB_TOURNAMENT_BASE");
        let result = build(&away, &home, &base, &env("MSSB_TOURNAMENT_OUTPUT"), |s| {
            println!("{s}")
        })
        .unwrap();

        // Verify the actual texture bytes written on disc, before any game renderer or HUD.
        let original = Disc::iso(&base).unwrap();
        let patched = Disc::iso(&result.iso_path).unwrap();
        let orig_dol = Dol::new(original.system("main.dol").unwrap()).unwrap();
        let patch_dol = Dol::new(patched.system("main.dol").unwrap()).unwrap();
        let doc: Value = serde_json::from_reader(File::open(&result.hud_path).unwrap()).unwrap();
        for (side, team) in [("Away", &away), ("Home", &home)] {
            for (i, row) in team.rows.iter().enumerate() {
                let slot = doc[format!("{side} Roster {i}")]["CharID"]
                    .as_u64()
                    .unwrap() as usize;
                if slot == row.source || !row.textures.is_empty() {
                    continue;
                }
                let copies = |disc: &Disc, dol: &Dol, id: usize| {
                    [
                        disc.load_subfile(dol, id * 19).unwrap(),
                        disc.load_subfile(dol, id * 19 + 1).unwrap(),
                        disc.effective_body(dol, id).unwrap(),
                    ]
                };
                for (orig, actual) in copies(&original, &orig_dol, row.source)
                    .into_iter()
                    .zip(copies(&patched, &patch_dol, slot))
                {
                    let orig = mssb_clone_core::texture::Gpc::new(orig).unwrap();
                    let actual = mssb_clone_core::texture::Gpc::new(actual).unwrap();
                    assert_eq!(orig.textures.len(), actual.textures.len());
                    for (a, b) in orig.textures.iter().zip(&actual.textures) {
                        assert!(
                            a.bytes(&orig.data).unwrap() == b.bytes(&actual.data).unwrap(),
                            "{side} batter {} slot {slot} texture {} differs from source {}",
                            i + 1,
                            b.index,
                            row.source
                        );
                    }
                }
            }
        }
        println!(
            "{} clones; ISO SHA-256 {}",
            result.clone_count, result.sha256
        );
    }
}
