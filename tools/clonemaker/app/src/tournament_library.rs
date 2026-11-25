//! Bundled ASSBLAST rosters and the player's saved tournament teams.
use crate::tournament::{self, Team};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::OnceLock,
};
use tauri::{AppHandle, Manager};

#[derive(Deserialize)]
struct Preset {
    id: String,
    label: String,
    team: Team,
}

#[derive(Serialize)]
pub(crate) struct Entry {
    id: String,
    label: String,
    kind: &'static str,
}

fn presets() -> &'static [Preset] {
    static PRESETS: OnceLock<Vec<Preset>> = OnceLock::new();
    PRESETS.get_or_init(|| {
        serde_json::from_str(include_str!("tournament-teams/assblast.json"))
            .expect("bundled tournament teams must be valid")
    })
}

fn library(app: &AppHandle) -> Result<PathBuf, String> {
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

fn remember(folder: &Path, export: &Path, json: &[u8]) -> io::Result<()> {
    fs::create_dir_all(folder)?;
    let stem = export
        .file_stem()
        .and_then(|v| v.to_str())
        .unwrap_or("Tournament team");
    let label = safe_name(stem.strip_suffix(".team").unwrap_or(stem));
    // Saving the same portable file updates its entry. Two folders can contain different teams with the same name.
    let id = Sha256::digest(export.as_os_str().as_encoded_bytes());
    let mut tmp = tempfile::NamedTempFile::new_in(folder)?;
    tmp.write_all(json)?;
    tmp.as_file().sync_all()?;
    tmp.persist(folder.join(format!("{label}--{id:x}.json")))?;
    Ok(())
}

pub(crate) fn remember_export(app: &AppHandle, export: &str, json: &[u8]) -> Result<(), String> {
    remember(&library(app)?, Path::new(export), json).map_err(|e| e.to_string())
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
    all.extend(presets().iter().map(|p| Entry {
        id: p.id.clone(),
        label: p.label.clone(),
        kind: "preset",
    }));
    all.sort_by_cached_key(|r| (r.kind, r.label.to_lowercase(), r.id.clone()));
    Ok(all)
}

fn load(folder: &Path, id: &str) -> Result<Team, String> {
    if let Some(preset) = presets().iter().find(|p| p.id == id) {
        return Ok(preset.team.clone());
    }
    // Resolve only entries the library lists. A client cannot provide a path to read.
    let entry = saved_entries(folder)?
        .into_iter()
        .find(|e| e.id == id)
        .ok_or("This team is no longer in your tournament library.")?;
    let path = folder.join(format!("{}.json", &entry.id["saved/".len()..]));
    let file = fs::File::open(path).map_err(|e| e.to_string())?;
    let team: Team =
        serde_json::from_reader(file.take(24 * 1024 * 1024 + 1)).map_err(|e| e.to_string())?;
    tournament::validate(&team)?;
    Ok(team)
}

#[tauri::command]
pub(crate) async fn list_tournament_teams(app: AppHandle) -> Result<Vec<Entry>, String> {
    tauri::async_runtime::spawn_blocking(move || entries(&library(&app)?))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub(crate) async fn load_tournament_team(app: AppHandle, id: String) -> Result<Team, String> {
    tauri::async_runtime::spawn_blocking(move || load(&library(&app)?, &id))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bundled_teams_load_distinct_historical_rosters_without_a_disc() {
        let tmp = tempfile::tempdir().unwrap();
        let all = entries(tmp.path()).unwrap();
        assert_eq!(all.len(), 14);
        for entry in &all {
            tournament::validate(&load(tmp.path(), &entry.id).unwrap()).unwrap();
        }
        let dk = load(tmp.path(), "preset/grandpoobear").unwrap();
        assert_eq!(
            (
                dk.rows[0].source,
                dk.rows[0].pos,
                dk.rows[1].source,
                dk.rows[1].pos
            ),
            (2, 3, 2, 5)
        );
    }
    #[test]
    fn saved_team_updates_and_loads_even_if_the_export_moves() {
        let tmp = tempfile::tempdir().unwrap();
        let folder = tmp.path().join("library");
        let path = tmp.path().join("My home nine.team.json");
        let mut team = presets()[0].team.clone();
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
        assert!(load(&folder, "saved/../../Settings").is_err());
    }
}
