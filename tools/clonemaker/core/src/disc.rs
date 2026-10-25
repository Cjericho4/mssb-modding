//! Extracted discs, ISO streaming and retail validation. Original inputs are opened read-only.
use crate::binary::{Descriptor, Dol, dol_size, fst_paths, u32_at};
use crate::{Error, Result, align};
use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// A byte-count callback; returning an error cancels at the next streaming step.
pub type Progress<'a> = dyn FnMut(u64, u64) -> Result<()> + 'a;
/// A disc's extracted root, or its ISO and parsed file locations for read-only preview access.
#[derive(Clone)]
pub struct Disc {
    /// Chosen game path (folder root or ISO).
    pub root: PathBuf,
    iso_files: Option<Vec<(crate::binary::FstEntry, String)>>,
    system: Vec<(String, Vec<u8>)>,
}
impl Disc {
    /// Open a Dolphin extraction without changing it.
    pub fn folder(root: impl AsRef<Path>) -> Result<Self> {
        let root = crate::normalize_path(root.as_ref());
        for p in [
            root.join("sys/main.dol"),
            root.join("files/ZZZZ.dat"),
            root.join("sys/fst.bin"),
        ] {
            if !p.exists() {
                return Err(Error::new(format!(
                    "not an extracted GameCube disc folder (missing {})",
                    p.display()
                )));
            }
        }
        Ok(Self {
            root,
            iso_files: None,
            system: Vec::new(),
        })
    }
    /// Open an ISO's system tables for previews without extracting its large files.
    pub fn iso(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let mut f = File::open(path)?;
        let (boot, _, _, dol, fst) = read_system(&mut f, path)?;
        let iso_files = fst_paths(&fst)?;
        Ok(Self {
            root: path.to_path_buf(),
            iso_files: Some(iso_files),
            system: vec![
                ("boot.bin".into(), boot),
                ("main.dol".into(), dol),
                ("fst.bin".into(), fst),
            ],
        })
    }
    /// Whether this handle reads directly from an ISO.
    pub fn is_iso(&self) -> bool {
        self.iso_files.is_some()
    }
    /// Read one small system file, from the extraction or the ISO's cached tables.
    pub fn system(&self, name: &str) -> Result<Vec<u8>> {
        if self.is_iso() {
            self.system
                .iter()
                .find(|(n, _)| n == name)
                .map(|(_, b)| b.clone())
                .ok_or_else(|| Error::new(format!("missing sys/{name}")))
        } else {
            Ok(fs::read(self.root.join("sys").join(name))?)
        }
    }
    /// Read a range of a resource file without loading the whole archive.
    pub fn read(&self, name: &str, off: u64, size: usize) -> Result<Vec<u8>> {
        let (path, base) = if let Some(entries) = &self.iso_files {
            let (e, _) = entries
                .iter()
                .find(|(_, p)| p == name)
                .ok_or_else(|| Error::new(format!("{name} not in fst.bin")))?;
            (self.root.clone(), e.offset as u64)
        } else {
            (self.root.join("files").join(name), 0)
        };
        let mut f = File::open(path)?;
        f.seek(SeekFrom::Start(base + off))?;
        let mut bytes = Vec::with_capacity(size);
        f.take(size as u64).read_to_end(&mut bytes)?;
        Ok(bytes)
    }
    /// Return a resource file's length from the FST or filesystem.
    pub fn file_size(&self, name: &str) -> Result<u64> {
        if let Some(entries) = &self.iso_files {
            entries
                .iter()
                .find(|(_, p)| p == name)
                .map(|(e, _)| e.size as u64)
                .ok_or_else(|| Error::new(format!("{name} not in fst.bin")))
        } else {
            Ok(fs::metadata(self.root.join("files").join(name))?.len())
        }
    }
    /// Load and expand a resource described in ZZZZ.dat or aaaa.dat.
    pub fn load(&self, name: &str, desc: Descriptor, base: u32) -> Result<Vec<u8>> {
        desc.expand(&self.read(name, base as u64 + desc.0[2] as u64, desc.0[3] as usize)?)
    }
    /// Load one of the character's 19 disc-path resources.
    pub fn load_subfile(&self, dol: &Dol, idx: usize) -> Result<Vec<u8>> {
        self.load("ZZZZ.dat", dol.desc(0x800f1d78, idx)?, 0)
    }
    /// Load menus, game or challenge REL from aaaa.dat in the game's order.
    pub fn load_rel(&self, dol: &Dol, idx: usize) -> Result<Vec<u8>> {
        self.load(
            "aaaa.dat",
            dol.desc([0x800e8aa8, 0x800e8ab8, 0x800e8ac8][idx], 0)?,
            0,
        )
    }
    /// Load an ARAM resource from inside the master blob.
    pub fn load_master(&self, dol: &Dol, idx: usize) -> Result<Vec<u8>> {
        self.load(
            "ZZZZ.dat",
            dol.desc(0x800efd38, idx)?,
            dol.desc(0x800efd38, 515)?.0[2],
        )
    }
    /// Assemble the actual in-match body, including its retail texture overlay.
    pub fn effective_body(&self, dol: &Dol, c: usize) -> Result<Vec<u8>> {
        let (body, overlay) = dol.pair(c)?;
        let mut bytes = self.load_master(dol, body as usize)?;
        if overlay != -1 {
            let ov = self.load_master(dol, overlay as usize)?;
            let off = u32_at(&bytes, 0)? as usize;
            let target = bytes
                .get_mut(off..off + ov.len())
                .ok_or_else(|| Error::new("overlay outside body".into()))?;
            target.copy_from_slice(&ov);
        }
        Ok(bytes)
    }
}
fn read_at(f: &mut File, off: u64, size: usize, path: &Path) -> Result<Vec<u8>> {
    f.seek(SeekFrom::Start(off))?;
    let mut b = vec![0; size];
    f.read_exact(&mut b).map_err(|_| {
        Error::new(format!(
            "{} is truncated or not a GameCube disc image",
            path.display()
        ))
    })?;
    Ok(b)
}
type SystemFiles = (Vec<u8>, Vec<u8>, Vec<u8>, Vec<u8>, Vec<u8>);
fn read_system(f: &mut File, path: &Path) -> Result<SystemFiles> {
    let boot = read_at(f, 0, 0x440, path)?;
    if u32_at(&boot, 0x1c)? != 0xc2339f3d {
        return Err(Error::new(format!(
            "{} is not a GameCube disc image (no disc magic); .rvz/.nkit images must be converted to .iso first (Dolphin: right-click the game -> Convert File -> ISO)",
            path.display()
        )));
    }
    let dol_off = u32_at(&boot, 0x420)? as u64;
    let fst_off = u32_at(&boot, 0x424)? as u64;
    let fst_size = u32_at(&boot, 0x428)? as usize;
    let ah = read_at(f, 0x2440, 0x20, path)?;
    let apl_size = 0x20 + u32_at(&ah, 0x14)? as usize + u32_at(&ah, 0x18)? as usize;
    let fst = read_at(f, fst_off, fst_size, path)?;
    let dh = read_at(f, dol_off, 0x100, path)?;
    let dol = read_at(f, dol_off, dol_size(&dh)?, path)?;
    Ok((
        boot,
        read_at(f, 0x440, 0x2000, path)?,
        read_at(f, 0x2440, apl_size, path)?,
        dol,
        fst,
    ))
}
fn safe_relative(path: &str) -> Result<&Path> {
    let p = Path::new(path);
    if p.components()
        .any(|c| !matches!(c, std::path::Component::Normal(_)))
        || path.contains('\\')
    {
        return Err(Error::new("invalid FST path".into()));
    }
    Ok(p)
}
/// Extract a GameCube ISO into Dolphin layout, reporting bytes and allowing cancellation.
pub fn extract_iso(
    path: &Path,
    dest: &Path,
    log: &mut dyn FnMut(&str),
    mut progress: Option<&mut Progress<'_>>,
) -> Result<()> {
    let mut f = File::open(path)?;
    let (boot, bi2, apl, dol, fst) = read_system(&mut f, path)?;
    fs::create_dir_all(dest.join("sys"))?;
    fs::create_dir(dest.join("files"))?;
    for (name, b) in [
        ("boot.bin", boot),
        ("bi2.bin", bi2),
        ("apploader.img", apl),
        ("main.dol", dol),
        ("fst.bin", fst.clone()),
    ] {
        fs::write(dest.join("sys").join(name), b)?;
    }
    log(&format!(
        "extracting {} -> {}",
        path.display(),
        dest.display()
    ));
    let entries = fst_paths(&fst)?;
    let total = entries.iter().map(|(e, _)| e.size as u64).sum();
    let mut done = 0;
    let mut buf = vec![0; 1 << 20];
    for (e, name) in entries {
        let out = dest.join("files").join(safe_relative(&name)?);
        fs::create_dir_all(out.parent().unwrap())?;
        let mut g = File::create(out)?;
        f.seek(SeekFrom::Start(e.offset as u64))?;
        let mut left = e.size as usize;
        while left != 0 {
            let n = f.read(&mut buf[..left.min(1 << 20)])?;
            if n == 0 {
                return Err(Error::new(format!(
                    "{} is truncated ({name})",
                    path.display()
                )));
            }
            g.write_all(&buf[..n])?;
            left -= n;
            done += n as u64;
            if let Some(step) = progress.as_mut() {
                step(done, total)?;
            }
        }
    }
    Ok(())
}
/// Open a folder or extract an ISO into a caller-owned scratch directory.
pub fn open_disc(
    path: &Path,
    scratch: &Path,
    log: &mut dyn FnMut(&str),
    progress: Option<&mut Progress<'_>>,
) -> Result<Disc> {
    if path.is_file() {
        extract_iso(path, scratch, log, progress)?;
        Disc::folder(scratch)
    } else {
        Disc::folder(path)
    }
}
/// Stream an extracted disc to a sparse, aligned ISO with the Python reference's exact layout.
pub fn write_iso(
    disc: &Disc,
    path: &Path,
    log: &mut dyn FnMut(&str),
    mut progress: Option<&mut Progress<'_>>,
) -> Result<()> {
    let boot = disc.system("boot.bin")?;
    let fst = disc.system("fst.bin")?;
    let mut f = File::create(path)?;
    for (off, bytes) in [
        (0, boot.clone()),
        (0x440, disc.system("bi2.bin")?),
        (0x2440, disc.system("apploader.img")?),
        (u32_at(&boot, 0x420)? as u64, disc.system("main.dol")?),
        (u32_at(&boot, 0x424)? as u64, fst.clone()),
    ] {
        f.seek(SeekFrom::Start(off))?;
        f.write_all(&bytes)?;
    }
    let entries = fst_paths(&fst)?;
    let total = entries.iter().map(|(e, _)| e.size as u64).sum();
    let mut done = 0;
    let mut end = 0;
    let mut buf = vec![0; 1 << 20];
    for (e, name) in entries {
        let src = disc.root.join("files").join(safe_relative(&name)?);
        let size = fs::metadata(&src)?.len();
        if size != e.size as u64 {
            return Err(Error::new(format!(
                "fst.bin says {name} is {} bytes but the file is {size}",
                e.size
            )));
        }
        f.seek(SeekFrom::Start(e.offset as u64))?;
        let mut g = File::open(src)?;
        loop {
            let n = g.read(&mut buf)?;
            if n == 0 {
                break;
            }
            f.write_all(&buf[..n])?;
            done += n as u64;
            if let Some(step) = progress.as_mut() {
                step(done, total)?;
            }
        }
        end = end.max(e.offset as u64 + size);
    }
    end = align(end as usize, 0x8000) as u64;
    f.set_len(end)?;
    log(&format!("wrote {} ({end} bytes)", path.display()));
    Ok(())
}
/// Check retail sizes, descriptor tables and the original findCharacterID instructions.
pub fn preflight(disc: &Disc, dol: &Dol, log: &mut dyn FnMut(&str)) -> Result<()> {
    let mut problems = Vec::new();
    let ds = disc.system("main.dol")?.len();
    if ds != 1139968 {
        problems.push(format!("sys/main.dol is {ds} bytes, retail GYQE01 is 1139968 (wrong game/region or modified DOL)"));
    }
    let zz = disc.file_size("ZZZZ.dat")?;
    if zz != 450041856 {
        problems.push(format!(
            "files/ZZZZ.dat is {zz} bytes, retail is 450041856 ({})",
            if zz > 450041856 {
                "already patched: point --disc at the clean extraction"
            } else {
                "truncated extraction"
            }
        ));
    }
    let checks = (|| -> Result<()> {
        if dol.desc(0x800efd38, 515)?.0 != [0, 0x4d6600, 0x1a15e800, 0x4d6600] {
            problems.push("main.dol blob descriptor is not retail (already patched DOL?)".into());
        }
        if dol.blob_alloc_size()? != 0x4d6600 {
            problems.push("main.dol ARAM allocation already patched".into());
        }
        if dol.desc(0x800f1d78, 0)?.0[2] != 0x9439000 || dol.pair(0)? != (0, -1) {
            problems.push("main.dol character tables do not look like retail GYQE01".into());
        }
        if dol.desc(0x800e8bf8, 0)?.0 != [0x40b, 0x40106568, 0x1a9fe800, 0x68c10]
            || dol.desc(0x800e8cd8, 0)?.0 != [0x40b, 0x400adffc, 0x18aee800, 0x4bab0]
        {
            problems
                .push("main.dol menu art descriptors are not retail (already patched DOL?)".into());
        }
        if dol.read(0x800698f8, FINDCHAR_RETAIL.len())? != FINDCHAR_RETAIL {
            problems.push(
                "main.dol fn_800698F8 (findCharacterID) is not retail (already patched DOL?)"
                    .into(),
            );
        }
        Ok(())
    })();
    if let Err(e) = checks {
        problems.push(e.message);
    }
    if let Ok(boot) = disc.system("boot.bin")
        && !boot.starts_with(b"GYQE01")
    {
        problems.push(format!(
            "sys/boot.bin game id is {}, expected GYQE01 (USA)",
            python_bytes(&boot[..boot.len().min(6)])
        ));
    }
    if !problems.is_empty() {
        for p in problems {
            log(&format!("preflight: {p}"));
        }
        return Err(Error::new(
            "--disc must be a clean, complete Dolphin extraction of the USA disc (GYQE01)".into(),
        ));
    }
    log("preflight: clean retail GYQE01 dump");
    Ok(())
}

fn python_bytes(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') {
        b'"'
    } else {
        b'\''
    };
    let mut out = format!("b{}", quote as char);
    for &b in bytes {
        if b == quote || b == b'\\' {
            out.push('\\');
            out.push(b as char);
        } else {
            match b {
                b'\n' => out.push_str("\\n"),
                b'\r' => out.push_str("\\r"),
                b'\t' => out.push_str("\\t"),
                32..=126 => out.push(b as char),
                _ => out += &format!("\\x{b:02x}"),
            }
        }
    }
    out.push(quote as char);
    out
}

const FINDCHAR_RETAIL: &[u8] = &[
    148, 33, 255, 240, 124, 8, 2, 166, 60, 128, 128, 17, 60, 160, 128, 14, 144, 1, 0, 20, 56, 0, 0,
    32, 56, 132, 141, 184, 147, 225, 0, 12, 59, 229, 105, 184, 56, 160, 0, 0, 124, 9, 3, 166, 136,
    4, 0, 0, 124, 0, 24, 0, 64, 130, 0, 60, 44, 5, 0, 17, 64, 130, 0, 28,
];

#[cfg(test)]
mod tests {
    use super::*;
    use crate::binary::put_u32;
    #[test]
    fn iso_progress_cancel_and_roundtrip() {
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path().join("disc");
        fs::create_dir_all(root.join("sys")).unwrap();
        fs::create_dir(root.join("files")).unwrap();
        let mut boot = vec![0; 0x440];
        boot[..6].copy_from_slice(b"GYQE01");
        put_u32(&mut boot, 0x1c, 0xc2339f3d).unwrap();
        put_u32(&mut boot, 0x420, 0x3000).unwrap();
        put_u32(&mut boot, 0x424, 0x4000).unwrap();
        put_u32(&mut boot, 0x428, 27).unwrap();
        let mut dol = vec![0; 0x104];
        put_u32(&mut dol, 0, 0x100).unwrap();
        put_u32(&mut dol, 0x90, 4).unwrap();
        dol[0x100..].copy_from_slice(b"DOL!");
        let mut fst = vec![0; 24];
        put_u32(&mut fst, 0, 0x1000000).unwrap();
        put_u32(&mut fst, 8, 2).unwrap();
        put_u32(&mut fst, 12, 1).unwrap();
        put_u32(&mut fst, 16, 0x8000).unwrap();
        put_u32(&mut fst, 20, 5).unwrap();
        fst.extend(b"\0x\0");
        for (n, b) in [
            ("boot.bin", boot),
            ("main.dol", dol),
            ("fst.bin", fst),
            ("bi2.bin", vec![0; 0x2000]),
            ("apploader.img", vec![0; 0x20]),
        ] {
            fs::write(root.join("sys").join(n), b).unwrap();
        }
        fs::write(root.join("files/x"), b"hello").unwrap();
        // Disc::folder requires retail resource names; the synthetic handle needs none of those.
        let disc = Disc {
            root: root.clone(),
            iso_files: None,
            system: Vec::new(),
        };
        let iso = temp.path().join("game.iso");
        let mut calls = Vec::new();
        write_iso(
            &disc,
            &iso,
            &mut |_| {},
            Some(&mut |done, total| {
                calls.push((done, total));
                Ok(())
            }),
        )
        .unwrap();
        assert_eq!(calls, [(5, 5)]);
        let extracted = temp.path().join("extracted");
        extract_iso(&iso, &extracted, &mut |_| {}, None).unwrap();
        assert_eq!(fs::read(extracted.join("files/x")).unwrap(), b"hello");
        let second = temp.path().join("second.iso");
        write_iso(
            &Disc {
                root: extracted,
                iso_files: None,
                system: Vec::new(),
            },
            &second,
            &mut |_| {},
            None,
        )
        .unwrap();
        assert_eq!(fs::read(&iso).unwrap(), fs::read(second).unwrap());
        let err = extract_iso(
            &iso,
            &temp.path().join("cancelled"),
            &mut |_| {},
            Some(&mut |_, _| Err(Error::new("cancelled".into()))),
        )
        .unwrap_err();
        assert_eq!(err.message, "cancelled");
        assert_eq!(fs::read(root.join("files/x")).unwrap(), b"hello");
    }
}
