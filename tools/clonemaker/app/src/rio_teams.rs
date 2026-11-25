//! Native file operations for the two-player Project Rio roster editor.
use mssb_clone_core::stats;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::{Read, Seek, Write},
    path::Path,
};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

#[derive(Serialize)]
pub struct TeamImage {
    name: String,
    sha256: String,
    hands: Vec<[u8; 2]>,
}

fn inspect(path: &Path) -> Result<TeamImage, String> {
    let mut input = File::open(path).map_err(|e| e.to_string())?;
    let before = input.metadata().map_err(|e| e.to_string())?;
    let hands = stats::roster_hands(&mut input, path).map_err(|e| e.to_string())?;
    input.rewind().map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    let mut buf = vec![0u8; 1024 * 1024];
    loop {
        let n = input.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hash.update(&buf[..n]);
    }
    let current = std::fs::metadata(path).map_err(|e| e.to_string())?;
    if before.len() != current.len() || before.modified().ok() != current.modified().ok() {
        return Err("The selected ISO changed while it was being read. Choose it again.".into());
    }
    Ok(TeamImage {
        name: path
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned(),
        sha256: format!("{:x}", hash.finalize()),
        hands,
    })
}

#[tauri::command]
pub async fn rio_team_image(app: AppHandle) -> Result<Option<TeamImage>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let Some(file) = app
            .dialog()
            .file()
            .add_filter("GameCube ISO", &["iso"])
            .blocking_pick_file()
        else {
            return Ok(None);
        };
        inspect(&file.into_path().map_err(|e| e.to_string())?).map(Some)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// The native save dialog owns the destination. Replace only after the complete JSON is on disk.
#[tauri::command]
pub async fn save_rio_roster(
    app: AppHandle,
    json: String,
    single: bool,
) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if json.len() > 96 * 1024 {
            return Err("Roster file exceeds 96 KiB.".into());
        }
        let _: serde_json::Map<String, serde_json::Value> =
            serde_json::from_str(&json).map_err(|e| e.to_string())?;
        let suggested = if single { "team.json" } else { "hud.json" };
        let Some(file) = app
            .dialog()
            .file()
            .add_filter("Roster JSON", &["json"])
            .set_file_name(suggested)
            .blocking_save_file()
        else {
            return Ok(None);
        };
        let path = file.into_path().map_err(|e| e.to_string())?;
        if !path
            .extension()
            .and_then(|v| v.to_str())
            .is_some_and(|v| v.eq_ignore_ascii_case("json"))
        {
            return Err("Choose a .json filename.".into());
        }
        let parent = path.parent().ok_or("Choose a folder for the roster.")?;
        let mut tmp = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
        tmp.write_all(json.as_bytes())
            .and_then(|_| tmp.as_file().sync_all())
            .map_err(|e| e.to_string())?;
        tmp.persist(&path).map_err(|e| e.to_string())?;
        Ok(Some(path.display().to_string()))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Stage the backup and replacement in the chosen directory. A failed write leaves the old HUD readable.
fn install(folder: &Path, json: &[u8]) -> Result<String, String> {
    if !folder
        .file_name()
        .and_then(|s| s.to_str())
        .is_some_and(|s| s.eq_ignore_ascii_case("HudFiles"))
    {
        return Err("Choose the HudFiles directory inside your Project Rio user folder.".into());
    }
    let hud = folder.join("hud.json");
    let backup = match std::fs::symlink_metadata(&hud) {
        Ok(info) if info.is_file() => {
            let mut previous = File::open(&hud).map_err(|e| e.to_string())?;
            let mut file = tempfile::Builder::new()
                .prefix("before-clone-maker-")
                .suffix(".json")
                .tempfile_in(folder)
                .map_err(|e| e.to_string())?;
            std::io::copy(&mut previous, &mut file).map_err(|e| e.to_string())?;
            file.as_file().sync_all().map_err(|e| e.to_string())?;
            Some(file.keep().map_err(|e| e.to_string())?.1)
        }
        Ok(_) => return Err("The existing hud.json must be a regular file.".into()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => return Err(e.to_string()),
    };
    let mut file = tempfile::NamedTempFile::new_in(folder).map_err(|e| e.to_string())?;
    file.write_all(json)
        .and_then(|_| file.as_file().sync_all())
        .map_err(|e| e.to_string())?;
    file.persist(&hud).map_err(|e| e.to_string())?;
    Ok(match backup {
        Some(path) => format!("Saved the previous HUD as {}.", path.display()),
        None => format!("Installed in {}.", folder.display()),
    })
}

#[tauri::command]
pub async fn install_rio_matchup(app: AppHandle, json: String) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if json.len() > 96 * 1024 {
            return Err("Matchup exceeds 96 KiB.".into());
        }
        let doc: serde_json::Value = serde_json::from_str(&json).map_err(|e| e.to_string())?;
        if !doc.get("Clone Maker SHA256").is_some_and(|h| h.is_string())
            || !["Away", "Home"]
                .iter()
                .all(|side| (0..9).all(|i| doc.get(format!("{side} Roster {i}")).is_some()))
        {
            return Err("A verified two-player roster is required.".into());
        }
        let Some(folder) = app
            .dialog()
            .file()
            .set_title("Select Project Rio's HudFiles folder")
            .blocking_pick_folder()
        else {
            return Ok(None);
        };
        install(
            &folder.into_path().map_err(|e| e.to_string())?,
            json.as_bytes(),
        )
        .map(Some)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    /// Expected values come from sha256sum on the retail disc and the independent
    /// tools/datamap/stats.py decoder on its extracted ZZZZ.dat (batting hand first).
    #[test]
    #[ignore = "set MSSB_RIO_RETAIL_ISO to the full USA revision 0 retail image"]
    fn inspect_retail_iso_matches_reference() {
        let path = std::env::var_os("MSSB_RIO_RETAIL_ISO").expect("set MSSB_RIO_RETAIL_ISO");
        let image = inspect(Path::new(&path)).unwrap();
        assert_eq!(
            image.sha256,
            "e3cd4dc92dd85f72b3dd10a95f2e075f612c197862b94c86466c64970f4efae9"
        );

        let expected = concat!(
            "RR LR LR LR RR LR LR RR LR LR RR LL LR RR LL RR LL RR ",
            "LR RL LR RR RR RR LR LR LR RR RR RR RR RR RR LR LR LR ",
            "LR RR LL LR RR RR LR LR LL LL LL LL LL LL LL LL RR RR"
        );
        let pairs = expected.split_whitespace().collect::<Vec<_>>();
        assert_eq!(pairs.len(), 54);
        assert_eq!(image.hands.len(), pairs.len());
        for (slot, (pair, actual)) in pairs.iter().zip(&image.hands).enumerate() {
            let hands = pair.as_bytes();
            assert_eq!(
                *actual,
                [u8::from(hands[0] == b'L'), u8::from(hands[1] == b'L')],
                "batting/throwing hand mismatch in slot {slot}"
            );
        }
    }

    #[test]
    fn installing_preserves_existing_resume_and_rejects_other_folders() {
        let tmp = tempfile::tempdir().unwrap();
        assert!(install(tmp.path(), b"{}").is_err());
        let folder = tmp.path().join("HudFiles");
        std::fs::create_dir(&folder).unwrap();
        let hud = folder.join("hud.json");
        std::fs::write(&hud, b"old resume").unwrap();
        install(&folder, b"new matchup").unwrap();
        assert_eq!(std::fs::read(&hud).unwrap(), b"new matchup");
        let backup = folder
            .read_dir()
            .unwrap()
            .map(|e| e.unwrap().path())
            .find(|p| p != &hud)
            .unwrap();
        assert_eq!(std::fs::read(backup).unwrap(), b"old resume");
    }
}
