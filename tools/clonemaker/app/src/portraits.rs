//! Keep the original game's HUD faces available when planning without a disc.
use mssb_clone_core::{
    api::roster_portraits_from_resource,
    binary::{Descriptor, Dol},
    disc::Disc,
    menu::portrait_desc,
};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{ErrorKind, Read, Write},
    path::{Path, PathBuf},
};
use tauri::{AppHandle, Manager};

// USA revision 0's packed HUD resource in ZZZZ.dat. Hashing this small resource
// works for both Dolphin extractions and ISOs and keeps clone donor art out of source slots.
const PACKED_SIZE: usize = 0x21420;
const EXPANDED_SIZE: u32 = 0x3e3dc;
const RETAIL_SHA256: &str = "27ece0663beaa98051105df035d43a85d18085787dd1904491d8e6a08d4c2023";
const CACHE_NAME: &str = "GYQE01-retail-faces.lz";
const SOURCE_HINT: &str = "Choose your original USA Mario Superstar Baseball ISO or its extracted game folder to load character portraits.";

fn original_resource(bytes: &[u8]) -> bool {
    bytes.len() == PACKED_SIZE && format!("{:x}", Sha256::digest(bytes)) == RETAIL_SHA256
}

fn from_source(path: &Path) -> Result<Vec<u8>, String> {
    let disc = if path.is_dir() {
        Disc::folder(path)
    } else {
        Disc::iso(path)
    }
    .map_err(|e| format!("{SOURCE_HINT} {e}"))?;
    let dol =
        Dol::new(disc.system("main.dol").map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    let desc = portrait_desc(&dol).map_err(|_| SOURCE_HINT.to_owned())?;
    if desc.0[3] as usize != PACKED_SIZE || desc.0[1] & 0x0fff_ffff != EXPANDED_SIZE {
        return Err(SOURCE_HINT.into());
    }
    let bytes = disc
        .read("ZZZZ.dat", desc.0[2].into(), PACKED_SIZE)
        .map_err(|e| e.to_string())?;
    if !original_resource(&bytes) {
        return Err(SOURCE_HINT.into());
    }
    Ok(bytes)
}

fn read_cache(cache: &Path) -> Result<Option<Vec<u8>>, String> {
    let file = match File::open(cache) {
        Ok(file) => file,
        Err(e) if e.kind() == ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.to_string()),
    };
    let mut bytes = Vec::with_capacity(PACKED_SIZE);
    file.take(PACKED_SIZE as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    Ok(original_resource(&bytes).then_some(bytes))
}

fn catalog(cache_dir: &Path, source: Option<&Path>) -> Result<Vec<String>, String> {
    let cache = cache_dir.join(CACHE_NAME);
    let bytes = if let Some(source) = source {
        let bytes = from_source(source)?;
        fs::create_dir_all(cache_dir).map_err(|e| e.to_string())?;
        let mut file = tempfile::NamedTempFile::new_in(cache_dir).map_err(|e| e.to_string())?;
        file.write_all(&bytes)
            .and_then(|_| file.as_file().sync_all())
            .map_err(|e| e.to_string())?;
        file.persist(&cache).map_err(|e| e.to_string())?;
        bytes
    } else if let Some(bytes) = read_cache(&cache)? {
        bytes
    } else {
        return Ok(Vec::new());
    };
    let desc = Descriptor([0x40b, 0x4000_0000 | EXPANDED_SIZE, 0, PACKED_SIZE as u32]);
    let data = desc.expand(&bytes).map_err(|e| e.to_string())?;
    roster_portraits_from_resource(data).map_err(|e| e.to_string())
}

#[tauri::command]
pub(crate) async fn load_roster_portraits(
    app: AppHandle,
    source_path: Option<PathBuf>,
) -> Result<Vec<String>, String> {
    let cache_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    tauri::async_runtime::spawn_blocking(move || catalog(&cache_dir, source_path.as_deref()))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Seek, SeekFrom};

    #[test]
    fn missing_or_damaged_local_cache_leaves_planning_available() {
        let temp = tempfile::tempdir().unwrap();
        assert!(catalog(temp.path(), None).unwrap().is_empty());
        fs::write(temp.path().join(CACHE_NAME), vec![0; PACKED_SIZE]).unwrap();
        assert!(catalog(temp.path(), None).unwrap().is_empty());
    }

    #[test]
    #[ignore = "uses the owner's local retail ISO to verify original portraits"]
    fn cache_round_trips_real_iso_and_extracted_folder() {
        let path = PathBuf::from(std::env::var_os("MSSB_RIO_RETAIL_ISO").unwrap());
        let temp = tempfile::tempdir().unwrap();
        let cache = temp.path().join("art");
        let expected = catalog(&cache, Some(&path)).unwrap();
        assert_eq!(expected.len(), 54);
        assert_eq!(catalog(&cache, None).unwrap(), expected);

        // Only the resource containing the portraits is needed in an extracted game.
        let root = temp.path().join("extracted");
        let disc = Disc::iso(&path).unwrap();
        fs::create_dir_all(root.join("sys")).unwrap();
        fs::create_dir_all(root.join("files")).unwrap();
        for name in ["boot.bin", "fst.bin", "main.dol"] {
            fs::write(root.join("sys").join(name), disc.system(name).unwrap()).unwrap();
        }
        let dol = Dol::new(disc.system("main.dol").unwrap()).unwrap();
        let desc = portrait_desc(&dol).unwrap();
        let mut archive = File::create(root.join("files/ZZZZ.dat")).unwrap();
        archive.seek(SeekFrom::Start(desc.0[2].into())).unwrap();
        archive.write_all(&from_source(&path).unwrap()).unwrap();
        assert_eq!(catalog(&cache, Some(&root)).unwrap(), expected);
        archive.seek(SeekFrom::Start(desc.0[2].into())).unwrap();
        archive.write_all(&[0, 0, 0, 0]).unwrap();
        assert!(catalog(&cache, Some(&root)).is_err());
        assert_eq!(catalog(&cache, None).unwrap(), expected);
    }
}
