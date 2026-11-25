//! Import a Studio texture folder into an offline planner row.
use crate::tournament::{portable_texture, MAX_PNG, MAX_TEAM_PIXELS, MAX_TEAM_PNG};
use base64::{engine::general_purpose::STANDARD, Engine};
use mssb_clone_core::{constants::CHAR_NAMES, custom_textures};
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::Read,
    path::Path,
};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

#[derive(Serialize)]
pub(crate) struct TextureFolder {
    name: String,
    textures: BTreeMap<String, String>,
    skipped: usize,
}

fn load_folder(folder: &Path, source: usize) -> Result<TextureFolder, String> {
    let mut textures = BTreeMap::new();
    let mut slots = BTreeSet::new();
    let (mut input_bytes, mut output_bytes, mut pixels) = (0, 0, 0);
    for entry in fs::read_dir(folder).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if !path
            .extension()
            .is_some_and(|ext| ext.eq_ignore_ascii_case("png"))
        {
            continue;
        }
        let filename = entry.file_name();
        let filename = filename.to_string_lossy();
        if filename.starts_with("._") {
            continue;
        } // Finder sidecar
        if !entry.file_type().map_err(|e| e.to_string())?.is_file() {
            return Err(format!(
                "{filename} must be a regular PNG file in the selected folder."
            ));
        }
        let (id, w, h) = custom_textures::parse_name(&filename).ok_or_else(|| {
            format!("{filename} is not a Studio texture name. Keep the names from Export Textures.")
        })?;
        if id >= 256 || !(1..=1024).contains(&w) || !(1..=1024).contains(&h) {
            return Err(format!(
                "{filename} has an unsupported texture index or size."
            ));
        }
        if !slots.insert(id) {
            return Err(format!(
                "Two pictures use texture {id}. Keep one PNG per slot."
            ));
        }
        pixels += w * h;
        if slots.len() > 64 || pixels > MAX_TEAM_PIXELS {
            return Err("Choose a folder with up to 64 PNGs and 16 megapixels.".into());
        }
        let mut bytes = Vec::new();
        File::open(&path)
            .and_then(|f| f.take(MAX_PNG as u64 + 1).read_to_end(&mut bytes))
            .map_err(|e| format!("Could not read {filename}: {e}"))?;
        input_bytes += bytes.len();
        if bytes.len() > MAX_PNG || input_bytes > MAX_TEAM_PNG {
            return Err("Use PNGs under 2 MiB each and 16 MiB per folder.".into());
        }
        let name = format!("t{id:02}_{w}x{h}.png");
        if let Some(png) = portable_texture(source, &name, &STANDARD.encode(bytes))? {
            output_bytes += png.len();
            if output_bytes > MAX_TEAM_PNG {
                return Err("The painted textures exceed 16 MiB.".into());
            }
            textures.insert(name, STANDARD.encode(png));
        }
    }
    if slots.is_empty() {
        return Err(
            "No texture PNGs found. Choose the folder containing your painted Studio PNGs.".into(),
        );
    }
    Ok(TextureFolder {
        name: folder
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned(),
        skipped: slots.len() - textures.len(),
        textures,
    })
}

/// The webview supplies a character ID. Only the native picker supplies the directory.
#[tauri::command]
pub(crate) async fn pick_tournament_texture_folder(
    app: AppHandle,
    source: usize,
) -> Result<Option<TextureFolder>, String> {
    if source >= CHAR_NAMES.len() {
        return Err("Choose a character before adding textures.".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let Some(folder) = app
            .dialog()
            .file()
            .set_title("Choose the folder with your painted textures")
            .blocking_pick_folder()
        else {
            return Ok(None);
        };
        let path = folder.into_path().map_err(|e| e.to_string())?;
        load_folder(&path, source).map(Some)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use mssb_clone_core::custom_textures::rgba_png;

    #[test]
    fn reads_only_complete_packs_for_the_selected_character() {
        let temp = tempfile::tempdir().unwrap();
        let folder = temp.path().join("Silverback DK");
        fs::create_dir(&folder).unwrap();
        fs::write(folder.join("README.txt"), "Studio export").unwrap();
        assert!(load_folder(&folder, 2).is_err());

        let pixels = [27, 181, 87, 255].repeat(64 * 64);
        let png = rgba_png(64, 64, &pixels).unwrap();
        fs::write(folder.join("T05_64x64.PNG"), &png).unwrap();
        let pack = load_folder(&folder, 2).unwrap();
        assert_eq!(pack.name, "Silverback DK");
        assert_eq!(pack.skipped, 0);
        assert_eq!(pack.textures.len(), 1);
        let encoded = STANDARD.decode(&pack.textures["t05_64x64.png"]).unwrap();
        assert_eq!(
            custom_textures::decode_png(&encoded, "tile").unwrap().2,
            pixels
        );

        fs::write(folder.join("t07_64x64.png"), b"broken").unwrap();
        assert!(load_folder(&folder, 2).is_err());
        fs::remove_file(folder.join("t07_64x64.png")).unwrap();
        fs::write(folder.join("t255_64x64.png"), &png).unwrap();
        assert!(load_folder(&folder, 2).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlinked_pngs() {
        let temp = tempfile::tempdir().unwrap();
        let folder = temp.path().join("textures");
        fs::create_dir(&folder).unwrap();
        let external = temp.path().join("outside.png");
        fs::write(&external, rgba_png(64, 64, &[255; 64 * 64 * 4]).unwrap()).unwrap();
        std::os::unix::fs::symlink(external, folder.join("t05_64x64.png")).unwrap();
        assert!(load_folder(&folder, 2)
            .err()
            .unwrap()
            .contains("regular PNG"));
    }
}
