//! Offline model and material cache for lineup and field portraits.
//! Each validated entry is published atomically so interrupted imports can resume.
use mssb_clone_core::{
    api::{portrait_asset, PortraitAsset},
    binary::Dol,
    constants::CHAR_NAMES,
    disc::Disc,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{ErrorKind, Read, Write},
    path::{Path, PathBuf},
};
use tauri::{AppHandle, Manager};

const CACHE: &str = "GYQE01-portrait-models-v1";
const SOURCE_HINT: &str = "Choose your original USA Mario Superstar Baseball ISO or its extracted game folder to load custom heads.";
// The complete inputs used by Disc::effective_body and gltf::stage_model.
// Check both before decoding any character resources from a selected disc.
const DOL_SHA: &str = "c74e391a4c1c1d3a0781e995befd8060da489477b8291c46d990a66d6c295812";
const ARCHIVE_SHA: &str = "b80d9dd9c432e5e703cc71a36a1f5771086d4a954d526f55d14d226146c6355b";
const ARCHIVE_SIZE: u64 = 450_041_856;
const MAX_ENTRY: u64 = 2 * 1024 * 1024;

fn open_original(path: &Path) -> Result<(Disc, Dol), String> {
    let disc = if path.is_dir() {
        Disc::folder(path)
    } else {
        Disc::iso(path)
    }
    .map_err(|e| format!("{SOURCE_HINT} {e}"))?;
    let image = disc.system("main.dol").map_err(|e| e.to_string())?;
    if format!("{:x}", Sha256::digest(&image)) != DOL_SHA
        || disc.file_size("ZZZZ.dat").map_err(|e| e.to_string())? != ARCHIVE_SIZE
    {
        return Err(SOURCE_HINT.into());
    }
    let mut hash = Sha256::new();
    let mut offset = 0;
    while offset < ARCHIVE_SIZE {
        let size = (ARCHIVE_SIZE - offset).min(8 * 1024 * 1024) as usize;
        let bytes = disc
            .read("ZZZZ.dat", offset, size)
            .map_err(|e| e.to_string())?;
        if bytes.len() != size {
            return Err(SOURCE_HINT.into());
        }
        hash.update(&bytes);
        offset += size as u64;
    }
    if format!("{:x}", hash.finalize()) != ARCHIVE_SHA {
        return Err(SOURCE_HINT.into());
    }
    Ok((disc, Dol::new(image).map_err(|e| e.to_string())?))
}

#[derive(Deserialize, Serialize)]
struct Entry {
    #[serde(flatten)]
    asset: PortraitAsset,
    checksum: String,
}

fn checksum(source: usize, asset: &PortraitAsset) -> String {
    let mut digest = Sha256::new();
    digest.update([source as u8]);
    digest.update(asset.model.as_bytes());
    for texture in &asset.textures {
        digest.update([0]);
        digest.update(texture.as_bytes());
    }
    format!("{:x}", digest.finalize())
}

fn file_name(source: usize) -> String {
    format!("{source:02}.json")
}

fn cached(dir: &Path, source: usize) -> Result<Option<PortraitAsset>, String> {
    if source >= CHAR_NAMES.len() {
        return Err(format!("Character {source} is outside the roster."));
    }
    let file = match File::open(dir.join(file_name(source))) {
        Ok(f) => f,
        Err(e) if e.kind() == ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.to_string()),
    };
    let mut bytes = Vec::new();
    file.take(MAX_ENTRY + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_ENTRY {
        return Ok(None);
    }
    let Ok(entry) = serde_json::from_slice::<Entry>(&bytes) else {
        return Ok(None);
    };
    Ok((entry.checksum == checksum(source, &entry.asset)).then_some(entry.asset))
}

fn prepare(dir: &Path, path: Option<&Path>) -> Result<bool, String> {
    let mut missing = Vec::new();
    for source in 0..CHAR_NAMES.len() {
        if cached(dir, source)?.is_none() {
            if path.is_none() {
                return Ok(false);
            }
            missing.push(source);
        }
    }
    if missing.is_empty() {
        return Ok(true);
    }
    let (disc, dol) = open_original(path.ok_or(SOURCE_HINT)?)?;
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    for source in missing {
        let asset = portrait_asset(&disc, &dol, source)
            .map_err(|e| format!("Could not prepare the {} head. {e}", CHAR_NAMES[source]))?;
        let entry = Entry {
            checksum: checksum(source, &asset),
            asset,
        };
        let mut temp = tempfile::NamedTempFile::new_in(dir).map_err(|e| e.to_string())?;
        let bytes = serde_json::to_vec(&entry).map_err(|e| e.to_string())?;
        if bytes.len() as u64 > MAX_ENTRY {
            return Err(format!(
                "The {} head exceeds the cache size limit.",
                CHAR_NAMES[source]
            ));
        }
        temp.write_all(&bytes).map_err(|e| e.to_string())?;
        temp.persist(dir.join(file_name(source)))
            .map_err(|e| e.to_string())?;
    }
    Ok(true)
}

#[tauri::command]
pub(crate) async fn prepare_custom_portraits(
    app: AppHandle,
    source_path: Option<PathBuf>,
) -> Result<bool, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join(CACHE);
    tauri::async_runtime::spawn_blocking(move || prepare(&dir, source_path.as_deref()))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
pub(crate) async fn load_custom_portrait_asset(
    app: AppHandle,
    source: usize,
) -> Result<Option<PortraitAsset>, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| e.to_string())?
        .join(CACHE);
    tauri::async_runtime::spawn_blocking(move || cached(&dir, source))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;

    #[test]
    fn missing_damaged_and_misplaced_art_can_be_reimported() {
        let temp = tempfile::tempdir().unwrap();
        let dir = temp.path();
        assert!(!prepare(dir, None).unwrap());
        let sample = PortraitAsset {
            model: "model".into(),
            textures: vec!["picture".into()],
        };
        let entry = Entry {
            checksum: checksum(2, &sample),
            asset: sample,
        };
        let mut bytes = serde_json::to_vec(&entry).unwrap();
        fs::write(dir.join("02.json"), &bytes).unwrap();
        assert!(cached(dir, 2).unwrap().is_some());
        fs::write(dir.join("03.json"), &bytes).unwrap();
        assert!(cached(dir, 3).unwrap().is_none());
        let offset = bytes.windows(7).position(|s| s == b"picture").unwrap();
        bytes[offset] = b'P';
        fs::write(dir.join("02.json"), &bytes).unwrap();
        assert!(cached(dir, 2).unwrap().is_none());
        assert!(cached(dir, CHAR_NAMES.len()).is_err());
    }

    #[test]
    #[ignore = "requires the owner's original USA ISO"]
    fn offline_custom_heads_survive_restarts_and_an_extracted_folder_repairs_damage() {
        let iso = PathBuf::from(std::env::var_os("MSSB_RIO_RETAIL_ISO").unwrap());
        let temp = tempfile::tempdir().unwrap();
        let dir = temp.path().join(CACHE);
        assert!(prepare(&dir, Some(&iso)).unwrap());
        assert!(prepare(&dir, None).unwrap());
        for source in 0..CHAR_NAMES.len() {
            let asset = cached(&dir, source).unwrap().unwrap();
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(asset.model)
                .unwrap();
            assert_eq!(&bytes[..4], b"glTF");
            assert!(!asset.textures.is_empty());
            for png in asset.textures {
                let bytes = base64::engine::general_purpose::STANDARD
                    .decode(png)
                    .unwrap();
                assert_eq!(&bytes[..8], b"\x89PNG\r\n\x1a\n");
            }
        }

        let folder = temp.path().join("game");
        fs::create_dir_all(folder.join("sys")).unwrap();
        fs::create_dir_all(folder.join("files")).unwrap();
        let disc = Disc::iso(&iso).unwrap();
        for name in ["fst.bin", "main.dol", "boot.bin"] {
            fs::write(folder.join("sys").join(name), disc.system(name).unwrap()).unwrap();
        }
        let archive = folder.join("files/ZZZZ.dat");
        let mut file = File::create(&archive).unwrap();
        let mut offset = 0;
        while offset < ARCHIVE_SIZE {
            let size = (ARCHIVE_SIZE - offset).min(8 * 1024 * 1024) as usize;
            file.write_all(&disc.read("ZZZZ.dat", offset, size).unwrap())
                .unwrap();
            offset += size as u64;
        }
        drop(file);
        fs::write(dir.join("02.json"), b"damaged").unwrap();
        assert!(!prepare(&dir, None).unwrap());
        assert!(prepare(&dir, Some(&folder)).unwrap());
        assert!(cached(&dir, 2).unwrap().is_some());
        // A modified archive cannot supply a remaining model.
        File::options()
            .write(true)
            .open(&archive)
            .unwrap()
            .write_all(b"BAD!")
            .unwrap();
        fs::remove_file(dir.join("00.json")).unwrap();
        assert!(prepare(&dir, Some(&folder)).is_err());
        assert!(cached(&dir, 2).unwrap().is_some());
    }
}
