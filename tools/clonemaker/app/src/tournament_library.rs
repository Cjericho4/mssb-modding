//! Team sheets saved by this player.
use crate::{
    selftest,
    tournament::{self, TeamSheet},
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{self, Read, Write},
    path::{Path, PathBuf},
};
use tauri::{AppHandle, Manager};

#[derive(Serialize)]
pub(crate) struct Entry {
    id: String,
    label: String,
    kind: &'static str,
}

fn library(app: &AppHandle) -> Result<PathBuf, String> {
    if let Some(scratch) = app.state::<selftest::Options>().scratch_dir()? {
        return Ok(scratch.join("Tournament teams"));
    }
    Ok(app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join("Tournament teams"))
}

// Keep filenames portable, within filesystem byte limits, and recognizable in the picker.
fn safe_name(name: &str) -> String {
    let mut result = String::new();
    for c in name.trim().chars() {
        if result.len() + c.len_utf8() > 90 {
            break;
        }
        if c.is_control() || r#"<>:"/\|?*"#.contains(c) {
            result.push('_');
        } else {
            result.push(c);
        }
    }
    let result = result.trim_matches([' ', '.']);
    if result.is_empty() {
        "Tournament team".into()
    } else {
        result.into()
    }
}

pub(crate) fn suggested_filename(player: &str) -> String {
    format!("{}.team.json", safe_name(player))
}

pub(crate) fn matchup_filename(away: &str, home: &str) -> String {
    let side = |player: &str, fallback: &str| {
        if player.trim().is_empty() {
            fallback.to_owned()
        } else {
            safe_name(player)
        }
    };
    format!(
        "{} vs {}.matchup.json",
        side(away, "Away"),
        side(home, "Home")
    )
}

fn file_label(path: &Path) -> String {
    let stem = path
        .file_stem()
        .and_then(|v| v.to_str())
        .unwrap_or("Tournament team");
    safe_name(stem.strip_suffix(".team").unwrap_or(stem))
}

fn store_entry(folder: &Path, stem: &str, json: &[u8]) -> io::Result<()> {
    fs::create_dir_all(folder)?;
    let mut tmp = tempfile::NamedTempFile::new_in(folder)?;
    tmp.write_all(json)?;
    tmp.as_file().sync_all()?;
    tmp.persist(folder.join(format!("{stem}.json")))?;
    Ok(())
}

fn remember(folder: &Path, export: &Path, json: &[u8]) -> io::Result<()> {
    // Saving the same portable file updates its entry. Two folders can contain different teams with the same name.
    let id = Sha256::digest(export.as_os_str().as_encoded_bytes());
    store_entry(folder, &format!("{}--{id:x}", file_label(export)), json)
}

pub(crate) fn remember_export(app: &AppHandle, export: &str, json: &[u8]) -> Result<(), String> {
    remember(&library(app)?, Path::new(export), json).map_err(|e| e.to_string())
}

fn remember_import(folder: &Path, team: &TeamSheet, filename: &str) -> Result<String, String> {
    // Content identity keeps different teams sent as "Away.team.json" independent.
    let json = serde_json::to_vec(team).map_err(|e| e.to_string())?;
    let digest = Sha256::digest(&json);
    let label = file_label(Path::new(filename));
    let stem = format!("{label}--{digest:x}");
    store_entry(folder, &stem, &json).map_err(|e| e.to_string())?;
    Ok(format!("saved/{stem}"))
}

#[derive(Serialize)]
pub(crate) struct ImportedTeam {
    team: TeamSheet,
    id: Option<String>,
    warning: Option<String>,
}

#[tauri::command]
pub(crate) async fn remember_imported_team(
    app: AppHandle,
    team: TeamSheet,
    filename: String,
) -> Result<ImportedTeam, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let team = tournament::portable_sheet(team)?;
        let saved = library(&app).and_then(|dir| remember_import(&dir, &team, &filename));
        let (id, warning) = match saved {
            Ok(id) => (Some(id), None),
            Err(reason) => (
                None,
                Some(format!("Could not save a Team library copy: {reason}")),
            ),
        };
        Ok(ImportedTeam { team, id, warning })
    })
    .await
    .map_err(|e| e.to_string())?
}

fn saved_entries(folder: &Path) -> Result<Vec<Entry>, String> {
    let mut entries = Vec::new();
    let files = match fs::read_dir(folder) {
        Ok(files) => files,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(entries),
        Err(e) => return Err(e.to_string()),
    };
    for file in files {
        let file = file.map_err(|e| e.to_string())?;
        if !file.file_type().map_err(|e| e.to_string())?.is_file() {
            continue;
        }
        let name = file.file_name();
        let Some(stem) = name.to_str().and_then(|v| v.strip_suffix(".json")) else {
            continue;
        };
        let Some((label, hash)) = stem.rsplit_once("--") else {
            continue;
        };
        if hash.len() == 64 && hash.bytes().all(|b| b.is_ascii_hexdigit()) {
            entries.push(Entry {
                id: format!("saved/{stem}"),
                label: label.to_owned(),
                kind: "saved",
            });
        }
    }
    Ok(entries)
}

fn entries(folder: &Path) -> Result<Vec<Entry>, String> {
    let mut all = saved_entries(folder)?;
    all.sort_by_cached_key(|r| (r.kind, r.label.to_lowercase(), r.id.clone()));
    Ok(all)
}

fn load(folder: &Path, id: &str) -> Result<TeamSheet, String> {
    // Resolve only entries the library lists. A client cannot provide a path to read.
    let entry = saved_entries(folder)?
        .into_iter()
        .find(|e| e.id == id)
        .ok_or("This team is no longer in your tournament library.")?;
    let path = folder.join(format!("{}.json", &entry.id["saved/".len()..]));
    let file = fs::File::open(path).map_err(|e| e.to_string())?;
    let team: TeamSheet =
        serde_json::from_reader(file.take(24 * 1024 * 1024 + 1)).map_err(|e| e.to_string())?;
    tournament::portable_sheet(team)
}

#[tauri::command]
pub(crate) async fn list_tournament_teams(app: AppHandle) -> Result<Vec<Entry>, String> {
    tauri::async_runtime::spawn_blocking(move || entries(&library(&app)?))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub(crate) async fn load_tournament_team(app: AppHandle, id: String) -> Result<TeamSheet, String> {
    tauri::async_runtime::spawn_blocking(move || load(&library(&app)?, &id))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn matchup_names_follow_the_two_sheets() {
        assert_eq!(matchup_filename("", " "), "Away vs Home.matchup.json");
        assert_eq!(
            matchup_filename("Comets", ""),
            "Comets vs Home.matchup.json"
        );
        assert_eq!(
            matchup_filename(" Away/Side ", "Comets"),
            "Away_Side vs Comets.matchup.json"
        );
    }

    #[test]
    fn new_library_is_empty() {
        let tmp = tempfile::tempdir().unwrap();
        assert!(entries(tmp.path()).unwrap().is_empty());
        assert!(load(tmp.path(), "preset/example").is_err());
    }
    #[test]
    fn saved_team_updates_and_loads_even_if_the_export_moves() {
        let tmp = tempfile::tempdir().unwrap();
        let folder = tmp.path().join("library");
        let path = tmp.path().join("My home nine.team.json");
        let mut team: TeamSheet = serde_json::from_value(serde_json::json!({
            "format":"mssb-tournament-team-v1", "player":"",
            "rows":(0..9).map(|pos| serde_json::json!({
                "source":if pos == 0 { Some(2) } else {None}, "pos":pos, "captain":false, "star":false
            })).collect::<Vec<_>>()
        })).unwrap();
        remember(&folder, &path, &serde_json::to_vec(&team).unwrap()).unwrap();
        team.rows[0].stats.insert("speed".into(), 0);
        remember(&folder, &path, &serde_json::to_vec(&team).unwrap()).unwrap();
        let saved = saved_entries(&folder).unwrap();
        assert_eq!(saved.len(), 1);
        assert_eq!(saved[0].label, "My home nine");
        assert_eq!(
            load(&folder, &saved[0].id).unwrap().rows[0].stats["speed"],
            0
        );
        let loaded = load(&folder, &saved[0].id).unwrap();
        assert_eq!(loaded.rows[0].source, Some(2));
        assert!(loaded.rows[1].source.is_none());
        assert!(loaded.player.is_empty());
        assert!(load(&folder, "saved/../../Settings").is_err());
    }

    #[test]
    fn received_files_are_reusable_without_an_original_file_or_game() {
        let tmp = tempfile::tempdir().unwrap();
        let mut team: TeamSheet = serde_json::from_value(serde_json::json!({
            "format":"mssb-tournament-team-v1", "player":"Coach",
            "rows":(0..9).map(|pos| serde_json::json!({
                "source":if pos == 0 { Some(2) } else {None}, "pos":pos, "captain":false, "star":false
            })).collect::<Vec<_>>()
        })).unwrap();
        team.rows[0].label = "Blue DK".into();
        let id = remember_import(tmp.path(), &team, "Moon Comets.team.json").unwrap();
        assert_eq!(
            id,
            remember_import(tmp.path(), &team, "Moon Comets.team.json").unwrap()
        );
        assert_eq!(entries(tmp.path()).unwrap()[0].label, "Moon Comets");
        assert_eq!(load(tmp.path(), &id).unwrap().rows[0].label, "Blue DK");
        team.rows[0].label = "Green DK".into();
        let other = remember_import(tmp.path(), &team, "Moon Comets.team.json").unwrap();
        assert_ne!(id, other);
        assert_eq!(entries(tmp.path()).unwrap().len(), 2);
        assert_eq!(load(tmp.path(), &id).unwrap().rows[0].label, "Blue DK");
        assert_eq!(load(tmp.path(), &other).unwrap().rows[0].label, "Green DK");
    }
}
