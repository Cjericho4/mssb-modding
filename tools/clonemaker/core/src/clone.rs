//! Assemble a complete clone patch using the reference's resource ordering and log messages.
use crate::binary::*;
use crate::colour::{Recolour, source_regions};
use crate::constants::*;
use crate::disc::{Disc, open_disc, preflight, write_iso};
use crate::lzss;
use crate::menu::*;
use crate::texture::Gpc;
use crate::{Error, Result, align};
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// CLI patch options; the window's job adapter uses the same patch path.
#[derive(Clone, Debug)]
pub struct CloneArgs {
    /// Original clean extracted game or ISO.
    pub disc: PathBuf,
    /// Destination extraction to create.
    pub out: PathBuf,
    /// Character name or numeric charID.
    pub source: String,
    /// Repeatable SLOT[:RECOLOUR] specifications, empty for the four Toad defaults.
    pub slots: Vec<String>,
    /// Optional ISO destination.
    pub iso: Option<PathBuf>,
    /// Keep uncoloured source bodies in matches.
    pub no_aram_overlay: bool,
    /// Copy source menu art, HUD and announcer identity.
    pub ui_source: bool,
    /// Retain the donor's roster colour word.
    pub keep_colour_word: bool,
    /// Force the appendix path for menu resources (differential test option).
    pub relocate_menu_art: bool,
    /// Preserve donor stats.
    pub no_stats: bool,
    /// Preserve donor gameplay tables.
    pub no_gameplay: bool,
    /// Replace an existing output extraction.
    pub force: bool,
    /// Compute and log edits without writing.
    pub dry_run: bool,
}
impl Default for CloneArgs {
    fn default() -> Self {
        Self {
            disc: PathBuf::new(),
            out: PathBuf::new(),
            source: "mario".into(),
            slots: Vec::new(),
            iso: None,
            no_aram_overlay: false,
            ui_source: true,
            keep_colour_word: false,
            relocate_menu_art: false,
            no_stats: false,
            no_gameplay: false,
            force: false,
            dry_run: false,
        }
    }
}
/// A completed patch's selected characters and output locations.
#[derive(Debug, serde::Serialize)]
pub struct CloneResult {
    /// Source charID.
    pub source: usize,
    /// Donor charIDs and their original recolour spec strings.
    pub slots: Vec<(usize, String)>,
    /// Destination extracted disc folder.
    pub out: PathBuf,
    /// Written ISO, or none for a dry run or folder-only patch.
    pub iso: Option<PathBuf>,
    /// Whether files were deliberately left unwritten.
    pub dry_run: bool,
}
/// A cancellable overall progress callback for the window.
pub type PatchProgress<'a> = dyn FnMut(f64, &str) -> Result<()> + 'a;
fn stage(progress: &mut Option<&mut PatchProgress<'_>>, fraction: f64, label: &str) -> Result<()> {
    if let Some(step) = progress {
        step(fraction, label)?;
    }
    Ok(())
}
fn norm_name(s: &str) -> String {
    s.to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric())
        .collect()
}
/// Parse the patcher's character IDs and punctuation-insensitive character names.
pub fn parse_char(s: &str) -> Result<usize> {
    let s = s.trim();
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_digit()) {
        let v = s
            .parse::<usize>()
            .map_err(|_| Error::new(format!("character id out of range: {s}")))?;
        if v >= 54 {
            return Err(Error::new(format!("character id out of range: {v}")));
        }
        return Ok(v);
    }
    let key = norm_name(s);
    CHAR_NAMES
        .iter()
        .position(|n| norm_name(n) == key)
        .ok_or_else(|| Error::new(format!("unknown character '{s}' (use --list-slots)")))
}
fn parse_slot(s: &str) -> Result<(usize, Recolour)> {
    let (slot, rc) = s.split_once(':').unwrap_or((s, ""));
    Ok((
        parse_char(slot)?,
        Recolour::new(if rc.is_empty() { "hue=blue" } else { rc })?,
    ))
}
fn names(clones: &[(usize, Recolour)]) -> String {
    clones
        .iter()
        .map(|(s, _)| CHAR_NAMES[*s])
        .collect::<Vec<_>>()
        .join(", ")
}
fn ids(clones: &[(usize, Recolour)]) -> Vec<usize> {
    clones.iter().map(|p| p.0).collect()
}
fn copy_tree(
    src: &Path,
    dst: &Path,
    done: &mut u64,
    total: u64,
    progress: &mut Option<&mut PatchProgress<'_>>,
) -> Result<()> {
    fs::create_dir(dst)?;
    for item in fs::read_dir(src)? {
        let item = item?;
        let from = item.path();
        let to = dst.join(item.file_name());
        if from.is_dir() {
            copy_tree(&from, &to, done, total, progress)?;
        } else {
            fs::copy(&from, &to)?;
            *done += fs::metadata(&to)?.len();
            stage(
                progress,
                0.45 * *done as f64 / total.max(1) as f64,
                "Copying the game",
            )?;
        }
    }
    Ok(())
}
fn tree_size(src: &Path) -> Result<u64> {
    let mut n = 0;
    for item in fs::read_dir(src)? {
        let p = item?.path();
        n += if p.is_dir() {
            tree_size(&p)?
        } else {
            fs::metadata(p)?.len()
        };
    }
    Ok(n)
}
fn prepare_output(out: &Path, force: bool) -> Result<()> {
    if out.exists() {
        if !force {
            return Err(Error::new(format!(
                "{} exists; use --force to overwrite it (or delete it to start over)",
                out.display()
            )));
        }
        fs::remove_dir_all(out)?;
    }
    Ok(())
}
struct Appendix {
    start: usize,
    cursor: usize,
    chunks: Vec<(usize, Vec<u8>)>,
}
impl Appendix {
    fn place(&mut self, b: Vec<u8>, al: usize) -> usize {
        self.cursor = align(self.cursor, al);
        let off = self.cursor;
        self.cursor += b.len();
        self.chunks.push((off, b));
        off
    }
}
fn compress_checked(data: &[u8], optimal: bool, label: &str) -> Result<Vec<u8>> {
    let comp = if optimal {
        lzss::compress_optimal(data, 11, 4, 256)?
    } else {
        lzss::compress(data, 11, 4, 48)?
    };
    if lzss::decompress(&comp, data.len(), 11, 4)?.0 != data {
        return Err(Error::new(format!(
            "internal error: LZSS round trip failed{label}"
        )));
    }
    Ok(comp)
}
fn load_menu(disc: &Disc, dol: &Dol, addr: u32) -> Result<(Vec<u8>, Descriptor)> {
    let d = dol.desc(addr, 0)?;
    if d.0[0] != 0x40b || d.0[1] & 0x40000000 == 0 {
        return Err(Error::new(format!(
            "main.dol: descriptor at {addr:08X} is not an LZSS file"
        )));
    }
    Ok((disc.load("ZZZZ.dat", d, 0)?, d))
}
struct Move {
    label: &'static str,
    counts: [usize; 3],
    old: Descriptor,
    new: Descriptor,
}
fn output_result(args: &CloneArgs, c: usize, clones: &[(usize, Recolour)]) -> CloneResult {
    CloneResult {
        source: c,
        slots: clones.iter().map(|(s, rc)| (*s, rc.spec.clone())).collect(),
        out: args.out.clone(),
        iso: if args.dry_run { None } else { args.iso.clone() },
        dry_run: args.dry_run,
    }
}
/// Patch a clean game into a new extraction, optionally writing an ISO; never mutate the input.
pub fn clone(
    args: &CloneArgs,
    log: &mut dyn FnMut(&str),
    mut progress: Option<&mut PatchProgress<'_>>,
) -> Result<CloneResult> {
    // Protect both aliases and output paths nested inside the original extraction.
    let input = absolute_path(&args.disc)?;
    let output = absolute_path(&args.out)?;
    if input.starts_with(&output) || (input.is_dir() && output.starts_with(&input)) {
        return Err(Error::new(
            "--out must be a different folder from --disc".into(),
        ));
    }
    if let Some(iso) = &args.iso {
        let iso = absolute_path(iso)?;
        if iso == input || (input.is_dir() && iso.starts_with(&input)) {
            return Err(Error::new(
                "--iso must not replace or modify the original disc".into(),
            ));
        }
    }
    let from_iso = args.disc.is_file();
    let mut temp = None;
    let src_disc = if from_iso && args.dry_run {
        let t = tempfile::tempdir()?;
        let d = open_disc(&args.disc, &t.path().join("disc"), log, None)?;
        temp = Some(t);
        d
    } else if from_iso {
        prepare_output(&args.out, args.force)?;
        let mut step = |done: u64, total: u64| {
            stage(
                &mut progress,
                0.45 * done as f64 / total.max(1) as f64,
                "Unpacking the game",
            )
        };
        open_disc(&args.disc, &args.out, log, Some(&mut step))?
    } else {
        Disc::folder(&args.disc)?
    };
    preflight(&src_disc, &Dol::new(src_disc.system("main.dol")?)?, log)?;
    let disc = if args.dry_run {
        log("DRY RUN: nothing will be written");
        src_disc
    } else if from_iso {
        src_disc
    } else {
        prepare_output(&args.out, args.force)?;
        log(&format!(
            "copying {} -> {} (this includes the 450 MB ZZZZ.dat)",
            src_disc.root.display(),
            args.out.display()
        ));
        let total = tree_size(&src_disc.root)?;
        copy_tree(&src_disc.root, &args.out, &mut 0, total, &mut progress)?;
        Disc::folder(&args.out)?
    };
    let mut dol = Dol::new(disc.system("main.dol")?)?;
    let c = parse_char(&args.source)?;
    let mut clones = if args.slots.is_empty() {
        [
            (29, "hue=blue"),
            (30, "hue=green"),
            (31, "hue=purple"),
            (32, "hue=yellow"),
        ]
        .iter()
        .map(|(s, rc)| Ok((*s, Recolour::new(rc)?)))
        .collect::<Result<Vec<_>>>()?
    } else {
        args.slots
            .iter()
            .map(|s| parse_slot(s))
            .collect::<Result<_>>()?
    };
    let slots = ids(&clones);
    if slots.iter().collect::<std::collections::HashSet<_>>().len() != slots.len() {
        return Err(Error::new("duplicate donor slot".into()));
    }
    if slots.contains(&c) {
        return Err(Error::new(
            "the source cannot be one of its own donors".into(),
        ));
    }
    let use_overlay = !args.no_aram_overlay;
    let default_case = dol.u32(ADDR_JUMPTABLE)?;
    let src_case = if c < 48 {
        dol.u32(ADDR_JUMPTABLE + c as u32 * 4)?
    } else {
        default_case
    };
    for (s, _) in &clones {
        if use_overlay && dol.private_entry(*s)?.is_none() {
            return Err(Error::new(format!(
                "slot {s} ({}) shares its ARAM body with other slots; pick a colour variant (Toads 29-32, Shy Guys 44-47, ...) or a stand-alone character, or use --no-aram-overlay",
                CHAR_NAMES[*s]
            )));
        }
        if *s >= 48 && src_case != default_case {
            return Err(Error::new(format!(
                "slot {s} is outside the per-character setup switch (0..47) but {} needs its own case",
                CHAR_NAMES[c]
            )));
        }
    }
    stage(&mut progress, 0.45, "Patching")?;
    log(&format!("source: {c} {}", CHAR_NAMES[c]));
    let src_tracks = [
        Gpc::new(disc.load_subfile(&dol, c * 19)?)?,
        Gpc::new(disc.load_subfile(&dol, c * 19 + 1)?)?,
    ];
    let src_body = Gpc::new(disc.effective_body(&dol, c)?)?;
    for g in [&src_tracks[0], &src_tracks[1], &src_body] {
        g.check()?;
    }
    log(&format!(
        "  track0 {} B, track1 {} B, ARAM body {} B, texture archive {} B ({} textures)",
        src_tracks[0].data.len(),
        src_tracks[1].data.len(),
        src_body.data.len(),
        src_body.archive_bytes().len(),
        src_body.textures.len()
    ));
    let regions = source_regions(&disc, &dol, c, &src_body)?;
    log(&format!(
        "  colour regions: {}",
        regions
            .iter()
            .enumerate()
            .map(|(i, r)| format!(
                "{i} {}{}",
                r.name,
                if r.default { " (default)" } else { "" }
            ))
            .collect::<Vec<_>>()
            .join("; ")
    ));
    for (_, rc) in &mut clones {
        rc.resolve(&regions)?;
    }
    let start = align(disc.file_size("ZZZZ.dat")? as usize, 0x800);
    let mut appendix = Appendix {
        start,
        cursor: start,
        chunks: Vec::new(),
    };
    let blob_desc = dol.desc(ADDR_MASTER, 515)?;
    let mut blob = if use_overlay {
        disc.read("ZZZZ.dat", blob_desc.0[2] as u64, blob_desc.0[3] as usize)?
    } else {
        Vec::new()
    };
    let mut inplace = Vec::new();
    let mut moves = Vec::new();
    let mut colour_word = false;
    if args.ui_source {
        let unique = dol.u8(ADDR_FAMILY + c as u32 * 6 + 2)?;
        for (s, _) in &clones {
            dol.write(ADDR_FAMILY + *s as u32 * 6 + 2, &[unique])?;
        }
        dol.write(ADDR_FINDCHAR, FINDCHAR_PATCH)?;
        log(&format!(
            "ui: unique index of {} -> {unique} ({}'s); fn_800698F8 rewritten to read lbl_800E8558[charID].uniqueIdx",
            names(&clones),
            CHAR_NAMES[c]
        ));
        let pdesc = portrait_desc(&dol)?;
        let mut pf = TextureFile::new(disc.load("ZZZZ.dat", pdesc, 0)?)?;
        for (s, rc) in &clones {
            pf.copy_portrait(*s, c, rc)?;
        }
        let mut comp = compress_checked(&pf.data, true, " (portraits)")?;
        let packed = comp.len();
        if packed > pdesc.0[3] as usize {
            return Err(Error::new(format!(
                "the re-packed HUD portrait file is {packed} B but only {} B fit at its disc offset; rerun with --ui donor",
                pdesc.0[3]
            )));
        }
        comp.resize(pdesc.0[3] as usize, 0);
        inplace.push((pdesc.0[2] as usize, comp));
        log(&format!(
            "ui: HUD portraits {} := {}'s (palette recoloured); file re-packed in place at ZZZZ.dat+0x{:08X} ({packed} of {} B)",
            names(&clones),
            CHAR_NAMES[c],
            pdesc.0[2],
            pdesc.0[3]
        ));
        let (common, cd) = load_menu(&disc, &dol, ADDR_MENU_COMMON_DESC)?;
        let (grid, gd) = load_menu(&disc, &dol, ADDR_MENU_GRID_DESC)?;
        let (ce, ge) = patch_menu_art(common, grid, c, &clones)?;
        log(&format!(
            "ui: menus: preview name, mini picture and grid portrait of {} := {}'s (pictures recoloured)",
            names(&clones),
            CHAR_NAMES[c]
        ));
        for (label, desc, ed, counts) in [
            ("common menu file", cd, ce, [0, 5, 1]),
            ("grid portrait file", gd, ge, [9, 5, 1]),
        ] {
            let data = ed.tf.data;
            let mut comp = compress_checked(&data, true, &format!(" ({label})"))?;
            let packed = comp.len();
            if data.len() == (desc.0[1] & 0x0fffffff) as usize
                && packed <= desc.0[3] as usize
                && !args.relocate_menu_art
            {
                comp.resize(desc.0[3] as usize, 0);
                inplace.push((desc.0[2] as usize, comp));
                log(&format!(
                    "ui: {label} re-packed in place at ZZZZ.dat+0x{:08X} ({packed} of {} B, {} B to spare)",
                    desc.0[2],
                    desc.0[3],
                    desc.0[3] as usize - packed
                ));
            } else {
                let off = appendix.place(comp, 0x800);
                let new = Descriptor([
                    desc.0[0],
                    0x40000000 | data.len() as u32,
                    off as u32,
                    packed as u32,
                ]);
                let n = replace_desc(&mut dol.data, desc, new);
                if n != 2 {
                    return Err(Error::new(format!(
                        "main.dol: expected 2 copies of the {label} descriptor, found {n}"
                    )));
                }
                moves.push(Move {
                    label,
                    counts,
                    old: desc,
                    new,
                });
                log(&format!(
                    "ui: {label} ({} -> {packed} B{}) moved to ZZZZ.dat+0x{off:08X}; {n} DOL descriptor copies updated, REL copies below",
                    data.len(),
                    if ed.grown != 0 {
                        format!(", grew {} B", ed.grown)
                    } else {
                        String::new()
                    }
                ));
            }
        }
        if !args.keep_colour_word {
            colour_word = true;
        } else {
            log("ui: colour word: the donors keep theirs (--colour-word donor)");
        }
    } else {
        log("ui: donor (menus, HUD and announcer keep the donor's name/portrait/call-out)");
    }
    for (s, rc) in &mut clones {
        log(&format!(
            "clone -> slot {s} {}  ({}; recolours {})",
            CHAR_NAMES[*s],
            rc.spec,
            rc.describe()
        ));
        for t in 2..19 {
            let d = dol.desc(ADDR_SUBFILES, c * 19 + t)?;
            dol.set_desc(ADDR_SUBFILES, *s * 19 + t, d)?;
        }
        for (t, src_track) in src_tracks.iter().enumerate() {
            let mut g = src_track.clone();
            rc.recolour_gpc(&mut g)?;
            g.check()?;
            let comp = compress_checked(&g.data, false, "")?;
            let packed = comp.len();
            let off = appendix.place(comp, 0x20);
            dol.set_desc(
                ADDR_SUBFILES,
                *s * 19 + t,
                Descriptor([
                    0x40b,
                    0x40000000 | g.data.len() as u32,
                    off as u32,
                    packed as u32,
                ]),
            )?;
            log(&format!(
                "  track {t} -> ZZZZ.dat+0x{off:08X} ({} -> {packed} B)",
                g.data.len()
            ));
        }
        for k in 0..7 {
            let d = dol.desc(ADDR_MASTER, 54 + c * 7 + k)?;
            dol.set_desc(ADDR_MASTER, 54 + *s * 7 + k, d)?;
        }
        let d = dol.desc(ADDR_MASTER, 432 + c)?;
        dol.set_desc(ADDR_MASTER, 432 + *s, d)?;
        let src_body_entry = dol.pair(c)?.0;
        if use_overlay {
            let o = dol.private_entry(*s)?.unwrap();
            let mut g = src_body.clone();
            rc.recolour_gpc(&mut g)?;
            let archive = g.archive_bytes();
            let comp = compress_checked(archive, false, "")?;
            let packed = comp.len();
            let off = align(blob.len(), 0x20);
            blob.resize(off, 0);
            blob.extend(comp);
            dol.set_desc(
                ADDR_MASTER,
                o,
                Descriptor([
                    0x40b,
                    0x40000000 | archive.len() as u32,
                    off as u32,
                    packed as u32,
                ]),
            )?;
            dol.set_pair(*s, src_body_entry, o as i8)?;
            log(&format!(
                "  ARAM overlay -> master[{o}] blob+0x{off:08X} ({} -> {packed} B); pair = {{{src_body_entry}, {o}}}",
                archive.len()
            ));
        } else {
            let (b, o) = dol.pair(c)?;
            dol.set_pair(*s, b, o)?;
            log("  ARAM body = source's (no recolour in the ARAM path)");
        }
        for (addr, rs) in [(ADDR_GLOVE, 2), (ADDR_MODELMAP, 2), (ADDR_ROWKIND, 1)] {
            let b = dol.read(addr + c as u32 * rs, rs as usize)?.to_vec();
            dol.write(addr + *s as u32 * rs, &b)?;
        }
        for k in 0..6 {
            let d = dol.desc(ADDR_SUBITEM_FILES, c * 6 + k)?;
            dol.set_desc(ADDR_SUBITEM_FILES, *s * 6 + k, d)?;
        }
        if *s < 48 {
            dol.write(ADDR_JUMPTABLE + *s as u32 * 4, &src_case.to_be_bytes())?;
        }
        if !args.no_gameplay {
            for k in 0..15 {
                let b = dol
                    .read(ADDR_ANIMPARAMS + ((k * 54 + c) * 2) as u32, 2)?
                    .to_vec();
                dol.write(ADDR_ANIMPARAMS + ((k * 54 + *s) * 2) as u32, &b)?;
            }
        }
    }
    stage(&mut progress, 0.50, "Patching")?;
    if use_overlay {
        blob.resize(align(blob.len(), 0x20), 0);
        let size = blob.len();
        let off = appendix.place(blob, 0x800);
        dol.set_desc(
            ADDR_MASTER,
            515,
            Descriptor([blob_desc.0[0], size as u32, off as u32, size as u32]),
        )?;
        let old_alloc = dol.blob_alloc_size()?;
        if old_alloc != blob_desc.0[3] {
            return Err(Error::new(format!(
                "ARAM allocation (0x{old_alloc:X}) does not match the blob descriptor (0x{:X})",
                blob_desc.0[3]
            )));
        }
        dol.set_blob_alloc_size(size as u32)?;
        log(&format!(
            "ARAM blob moved to ZZZZ.dat+0x{off:08X}, size 0x{:X} -> 0x{size:X} (fn_800227E4 immediates patched)",
            blob_desc.0[3]
        ));
    }
    let rel_addrs = [0x800e8aa8, 0x800e8ab8, 0x800e8ac8];
    let rel_names = ["menus.rel", "game.rel", "challenge.rel"];
    let mut rel_ends = Vec::new();
    for a in rel_addrs {
        rel_ends.push(dol.desc(a, 0)?.0[2] as u64);
    }
    rel_ends.push(disc.file_size("aaaa.dat")?);
    rel_ends.sort();
    let mut rel_patches = Vec::new();
    for i in 0..3 {
        let edits: Vec<_> = moves.iter().filter(|m| m.counts[i] != 0).collect();
        if !(i == 0 && colour_word || i == 1 && !args.no_gameplay || !edits.is_empty()) {
            continue;
        }
        let old = dol.desc(rel_addrs[i], 0)?;
        let mut rel = disc.load_rel(&dol, i)?;
        let mut lines = Vec::new();
        if i == 0 && colour_word {
            let offsets = patch_colour_word(&mut rel, &slots)?;
            lines.push(format!(
                "colour word: label 0 for charIDs {slots:?} (relocation addends at {})",
                if offsets.is_empty() {
                    "none needed".into()
                } else {
                    offsets
                        .iter()
                        .map(|o| format!("0x{o:X}"))
                        .collect::<Vec<_>>()
                        .join(", ")
                }
            ));
        }
        if i == 1 && !args.no_gameplay {
            let (base, size) = rel_data_section(&rel)?;
            for (off, rs) in [
                (0x8168, 2),
                (0x6ef8, 0x24),
                (0x7690, 2),
                (0x7870, 6),
                (0x7e34, 4),
                (0x17000, 4),
            ] {
                if off + 54 * rs > size {
                    return Err(Error::new(format!(
                        "game.rel table at 0x{off:X} is outside .data"
                    )));
                }
                for s in &slots {
                    rel.copy_within(
                        base + off + c * rs..base + off + (c + 1) * rs,
                        base + off + s * rs,
                    );
                }
            }
            lines.push("copied voice/hitbox/timing tables".into());
        }
        for m in edits {
            let n = replace_desc(&mut rel, m.old, m.new);
            if n != m.counts[i] {
                return Err(Error::new(format!(
                    "{}: expected {} copies of the {} descriptor, found {n}",
                    rel_names[i], m.counts[i], m.label
                )));
            }
            lines.push(format!("{n} {} descriptor copies updated", m.label));
        }
        let mut comp = compress_checked(&rel, true, &format!(" ({})", rel_names[i]))?;
        let packed = comp.len();
        let room = rel_ends.iter().find(|end| **end > old.0[2] as u64).unwrap() - old.0[2] as u64;
        if packed as u64 > room {
            return Err(Error::new(format!(
                "patched {} packs to {packed} B but only {room} B fit in aaaa.dat; rerun with {}",
                rel_names[i],
                if i == 1 {
                    "--no-gameplay"
                } else {
                    "--ui donor"
                }
            )));
        }
        comp.resize(packed.max(old.0[3] as usize), 0);
        rel_patches.push((old.0[2] as usize, comp));
        dol.set_desc(
            rel_addrs[i],
            0,
            Descriptor([
                old.0[0],
                0x40000000 | rel.len() as u32,
                old.0[2],
                packed as u32,
            ]),
        )?;
        log(&format!(
            "{}: {}; re-packed in place at aaaa.dat+0x{:08X} ({packed} B, room {room} B)",
            rel_names[i],
            lines.join("; "),
            old.0[2]
        ));
    }
    if args.dry_run {
        log(&format!(
            "DRY RUN summary: {} B in {} chunks appended to ZZZZ.dat from 0x{:08X}; {} in-place rewrite(s); main.dol/stats/game.rel rows for slots {slots:?}",
            appendix.cursor - appendix.start,
            appendix.chunks.len(),
            appendix.start,
            inplace.len()
        ));
        log("DRY RUN: no files written");
        return Ok(output_result(args, c, &clones));
    }
    stage(&mut progress, 0.525, "Patching")?;
    let zz_path = disc.root.join("files/ZZZZ.dat");
    let mut zz = OpenOptions::new().read(true).write(true).open(&zz_path)?;
    for (off, bytes) in inplace.into_iter().chain(appendix.chunks) {
        zz.seek(SeekFrom::Start(off as u64))?;
        zz.write_all(&bytes)?;
    }
    let end = zz.seek(SeekFrom::End(0))? as usize;
    let new_size = align(end, 0x8000);
    zz.write_all(&vec![0; new_size - end])?;
    log(&format!("ZZZZ.dat: {start} -> {new_size} bytes"));
    if !args.no_stats {
        zz.seek(SeekFrom::Start(0x6cf8800))?;
        let mut stats = vec![0; 54 * 0xa0];
        zz.read_exact(&mut stats)?;
        let stat_ids: Vec<_> = (0..54)
            .map(|i| u16_at(&stats, i * 0xa0 + 0x24).map(|v| v as i16))
            .collect::<Result<_>>()?;
        if stat_ids != (0..54).collect::<Vec<_>>() {
            return Err(Error::new(format!(
                "stat block at ZZZZ.dat+0x6CF8800 does not look right (CharIDs {:?}...)",
                &stat_ids[..5]
            )));
        }
        let src = stats[c * 0xa0..(c + 1) * 0xa0].to_vec();
        for s in &slots {
            stats[*s * 0xa0..(*s + 1) * 0xa0].copy_from_slice(&src);
            stats[*s * 0xa0 + 0x24..*s * 0xa0 + 0x26].copy_from_slice(&(*s as u16).to_be_bytes());
        }
        for s in &slots {
            for j in 0..54 {
                stats[j * 0xa0 + 0x3b + *s] = stats[j * 0xa0 + 0x3b + c];
            }
        }
        zz.seek(SeekFrom::Start(0x6cf8800))?;
        zz.write_all(&stats)?;
        log(&format!(
            "stats: copied {}'s row and chemistry to {}",
            CHAR_NAMES[c],
            names(&clones)
        ));
    }
    if !rel_patches.is_empty() {
        let mut aa = OpenOptions::new()
            .write(true)
            .open(disc.root.join("files/aaaa.dat"))?;
        for (off, bytes) in rel_patches {
            aa.seek(SeekFrom::Start(off as u64))?;
            aa.write_all(&bytes)?;
        }
    }
    fs::write(
        disc.root.join("sys/fst.bin"),
        patch_fst_size(&disc.system("fst.bin")?, "ZZZZ.dat", new_size as u32)?,
    )?;
    fs::write(disc.root.join("sys/main.dol"), &dol.data)?;
    log("main.dol and fst.bin written");
    if let Some(iso) = &args.iso {
        let mut step = |done: u64, total: u64| {
            stage(
                &mut progress,
                0.55 + (1.0 - 0.55) * done as f64 / total.max(1) as f64,
                "Writing the new ISO",
            )
        };
        write_iso(&disc, iso, log, Some(&mut step))?;
    }
    log("");
    log(&format!(
        "Done.  In the game, pick: {}",
        clones
            .iter()
            .map(|(s, rc)| format!("{} (slot {s}) -> {}", CHAR_NAMES[*s], rc.spec))
            .collect::<Vec<_>>()
            .join(", ")
    ));
    if args.ui_source {
        log(&format!(
            "Menus (preview name, mini picture, grid portrait), HUD and the announcer show/say {} for these slots{}.",
            CHAR_NAMES[c],
            if args.keep_colour_word {
                "; the donors keep their colour word"
            } else {
                ""
            }
        ));
    }
    log(&format!(
        "Boot: Dolphin -> File -> Open -> {}",
        args.out.join("sys/main.dol").display()
    ));
    drop(temp);
    Ok(output_result(args, c, &clones))
}
/// Resolve an output path even if its final components do not yet exist.
pub fn absolute_path(path: &Path) -> Result<PathBuf> {
    let path = if path.as_os_str().is_empty() {
        Path::new(".")
    } else {
        path
    };
    let absolute = std::path::absolute(path)?;
    let mut resolved = PathBuf::new();
    for component in absolute.components() {
        match component {
            std::path::Component::ParentDir => {
                resolved.pop();
            }
            std::path::Component::CurDir => {}
            other => {
                resolved.push(other.as_os_str());
                if resolved.exists() {
                    resolved = canonical(&resolved)?;
                }
            }
        }
    }
    Ok(resolved)
}

/// `fs::canonicalize` without Windows' `\\?\` prefix on drive-letter paths, so messages show paths
/// as the user knows them (like Python's `Path.resolve()`).
fn canonical(path: &Path) -> Result<PathBuf> {
    let full = fs::canonicalize(path)?;
    if cfg!(windows)
        && let Some(plain) = full.to_str().and_then(|s| s.strip_prefix(r"\\?\"))
        && plain.as_bytes().get(1) == Some(&b':')
    {
        return Ok(PathBuf::from(plain));
    }
    Ok(full)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn resolves_missing_components_and_parents() {
        let temp = tempfile::tempdir().unwrap();
        assert_eq!(
            absolute_path(&temp.path().join("missing/../output.iso")).unwrap(),
            canonical(temp.path()).unwrap().join("output.iso")
        );
        assert_eq!(
            absolute_path(Path::new("")).unwrap(),
            std::env::current_dir().unwrap()
        );
    }
}
