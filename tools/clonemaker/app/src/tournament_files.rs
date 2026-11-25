//! Native file pickers for sharing team submissions and building both sides of a tournament game.
use crate::rio_teams::save_json_dialog;
use crate::tournament::{self, Team};
use serde::Serialize;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Emitter};
use tauri_plugin_dialog::DialogExt;

#[tauri::command]
pub(crate) async fn save_tournament_team(
    app: AppHandle,
    team: Team,
) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        tournament::validate(&team)?;
        let mut json = serde_json::to_vec_pretty(&team).map_err(|e| e.to_string())?;
        json.push(b'\n');
        let filename = crate::tournament_library::suggested_filename(&team.player);
        let saved = save_json_dialog(&app, &json, &filename)?;
        if let Some(path) = &saved {
            crate::tournament_library::remember_export(&app, path, &json).map_err(|error| {
                format!("Your team was exported to {path}. The team picker could not save a library copy: {error}")
            })?;
        }
        Ok(saved)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[derive(Serialize)]
pub(crate) struct BuildLocation {
    folder: PathBuf,
    sha256: String,
    #[serde(rename = "hudSha256")]
    hud_sha256: String,
}

// Each build gets a new directory. A retry leaves a previous completed build available for play.
fn next_output(parent: &Path) -> Result<PathBuf, String> {
    for n in 1..=9999 {
        let name = if n == 1 {
            "Tournament matchup".into()
        } else {
            format!("Tournament matchup {n}")
        };
        let path = parent.join(name);
        match std::fs::symlink_metadata(&path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(path),
            Err(e) => return Err(e.to_string()),
            Ok(_) => continue,
        }
    }
    Err("Choose an output folder with room for another tournament matchup.".into())
}

#[tauri::command]
pub(crate) async fn build_tournament_matchup(
    app: AppHandle,
    away: Team,
    home: Team,
) -> Result<Option<BuildLocation>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        tournament::validate(&away)?;
        tournament::validate(&home)?;
        let Some(base) = app
            .dialog()
            .file()
            .set_title("Choose the original USA Mario Superstar Baseball ISO")
            .add_filter("GameCube ISO", &["iso"])
            .blocking_pick_file()
        else {
            return Ok(None);
        };
        let Some(parent) = app
            .dialog()
            .file()
            .set_title("Choose a folder for the tournament game and Rio lineup")
            .blocking_pick_folder()
        else {
            return Ok(None);
        };
        let base = base.into_path().map_err(|e| e.to_string())?;
        let output = next_output(&parent.into_path().map_err(|e| e.to_string())?)?;
        let result = tournament::build(&away, &home, &base, &output, |step| {
            let _ = app.emit("tournament-progress", step);
        })?;
        Ok(Some(BuildLocation {
            folder: output,
            sha256: result.sha256,
            hud_sha256: result.hud_sha256,
        }))
    })
    .await
    .map_err(|e| e.to_string())?
}
