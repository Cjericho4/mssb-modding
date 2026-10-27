//! The reference patcher's command-line flags and listing output.
use crate::binary::Dol;
use crate::clone::{CloneArgs, ColourWord, absolute_path, parse_char};
use crate::colour::source_regions;
use crate::constants::*;
use crate::custom_textures::{self, Painted};
use crate::disc::{Disc, open_disc};
use crate::texture::Gpc;
use crate::{Error, Result};
use std::path::{Path, PathBuf};

const OPTIONS: &[&str] = &[
    "--help",
    "--disc",
    "--out",
    "--source",
    "--slot",
    "--iso",
    "--no-aram-overlay",
    "--ui",
    "--colour-word",
    "--relocate-menu-art",
    "--no-stats",
    "--no-gameplay",
    "--list-slots",
    "--list-regions",
    "--force",
    "--dry-run",
    "--textures",
    "--export-textures",
    "--anims",
    "--from",
];
/// One parsed CLI action, sharing CloneArgs with the library patcher.
pub enum Command {
    /// Display the reference's help text.
    Help,
    /// Display the `export-gltf` help text.
    GltfHelp,
    /// Print the donor slot table or source region list.
    List {
        /// Disc folder or ISO.
        disc: PathBuf,
        /// Source name or charID for a region listing.
        source: String,
        /// Whether to print slots rather than regions.
        slots: bool,
    },
    /// Write the source's body textures as PNGs into a folder.
    ExportTextures {
        /// Disc folder or ISO.
        disc: PathBuf,
        /// Source name or charID.
        source: String,
        /// Destination folder.
        dir: PathBuf,
    },
    /// Execute a clone patch.
    Clone(CloneArgs),
    /// `export-gltf`: write one character as a glTF file for Blender.
    ExportGltf {
        /// Disc folder or ISO.
        disc: PathBuf,
        /// Character name or charID.
        character: String,
        /// Every animation of the normal-match banks instead of the swing only.
        all_animations: bool,
        /// Folder of painted `tNN_WxH.png` textures.
        textures: Option<PathBuf>,
        /// The .gltf file to write.
        out: PathBuf,
    },
}
/// Usage of the `export-gltf` command.
pub const GLTF_USAGE: &str =
    "usage: mssb-clone export-gltf --game GAME --character NAME [--all-animations]
                              [--textures FOLDER] --out FILE.gltf

Export a character's textured body, skeleton and animations to a .gltf file that
Blender opens (File > Import > glTF 2.0).

  --game GAME         the game: a .iso file or an extracted disc folder (or --disc)
  --character NAME    name or id, e.g. mario, dk, babymario, kingboo, 0
  --all-animations    every batting, running, fielding, pitching and extra
                      animation (default: only the normal swing)
  --textures FOLDER   use your painted tNN_WxH.png pictures from Export textures
  --out FILE.gltf     the file to write
";
const GLTF_PREFIX: &str = "export-gltf: ";
/// Parse `export-gltf` flags (argv after the command word).
fn parse_gltf(argv: &[String]) -> Result<Command> {
    let usage = |m: String| Error::new(format!("{GLTF_PREFIX}{m}"));
    let (mut disc, mut character, mut textures, mut out) = (None, None, None, None);
    let mut all_animations = false;
    let mut i = 0;
    while i < argv.len() {
        let (key, explicit) = argv[i]
            .split_once('=')
            .map_or((argv[i].as_str(), None), |(k, v)| (k, Some(v.to_owned())));
        match key {
            "-h" | "--help" => return Ok(Command::GltfHelp),
            "--all-animations" => all_animations = true,
            "--game" | "--disc" | "--character" | "--textures" | "--out" => {
                let value = match explicit {
                    Some(v) => v,
                    None => {
                        i += 1;
                        match argv.get(i) {
                            Some(v) if !v.starts_with("--") => v.clone(),
                            _ => {
                                return Err(usage(format!(
                                    "argument {key}: expected one argument"
                                )));
                            }
                        }
                    }
                };
                let path = || Some(crate::normalize_path(Path::new(&value)));
                match key {
                    "--character" => character = Some(value.clone()),
                    "--textures" => textures = path(),
                    "--out" => out = path(),
                    _ => disc = path(),
                }
            }
            _ => return Err(usage(format!("unrecognized arguments: {}", argv[i]))),
        }
        i += 1;
    }
    let missing: Vec<_> = [
        ("--game", disc.is_none()),
        ("--character", character.is_none()),
        ("--out", out.is_none()),
    ]
    .iter()
    .filter(|(_, m)| *m)
    .map(|(k, _)| *k)
    .collect();
    if !missing.is_empty() {
        return Err(usage(format!(
            "the following arguments are required: {}",
            missing.join(", ")
        )));
    }
    Ok(Command::ExportGltf {
        disc: disc.unwrap(),
        character: character.unwrap(),
        all_animations,
        textures,
        out: out.unwrap(),
    })
}
/// Parse CLI arguments using the Python reference's defaults, choices and required flags.
pub fn parse(argv: &[String]) -> Result<Command> {
    if argv.first().is_some_and(|a| a == "export-gltf") {
        return parse_gltf(&argv[1..]);
    }
    let mut args = CloneArgs::default();
    let mut disc = false;
    let mut out = false;
    let mut list_slots = false;
    let mut list_regions = false;
    let mut export_textures = None;
    let mut unknown = Vec::new();
    let mut i = 0;
    while i < argv.len() {
        let arg = &argv[i];
        let (key, explicit) = arg
            .split_once('=')
            .map_or((arg.as_str(), None), |(k, v)| (k, Some(v)));
        let key = if key == "-h" {
            "--help"
        } else if OPTIONS.contains(&key) {
            key
        } else {
            let opts: Vec<_> = OPTIONS.iter().filter(|s| s.starts_with(key)).collect();
            if key.starts_with("--") && opts.len() == 1 {
                *opts[0]
            } else if key.starts_with("--") && opts.len() > 1 {
                return Err(Error::new(format!(
                    "ambiguous option: {key} could match {}",
                    opts.iter().map(|s| **s).collect::<Vec<_>>().join(", ")
                )));
            } else {
                unknown.push(arg.clone());
                i += 1;
                continue;
            }
        };
        if key == "--help" {
            if let Some(v) = explicit {
                return Err(Error::new(format!(
                    "argument -h/--help: ignored explicit argument '{v}'"
                )));
            }
            return Ok(Command::Help);
        }
        if [
            "--disc",
            "--out",
            "--source",
            "--slot",
            "--iso",
            "--ui",
            "--colour-word",
            "--textures",
            "--export-textures",
            "--anims",
            "--from",
        ]
        .contains(&key)
        {
            let value = if let Some(v) = explicit {
                v.to_owned()
            } else {
                i += 1;
                if i >= argv.len() || argv[i].starts_with("--") {
                    return Err(Error::new(format!("argument {key}: expected one argument")));
                }
                argv[i].clone()
            };
            match key {
                "--disc" => {
                    args.disc = crate::normalize_path(Path::new(&value));
                    disc = true;
                }
                "--out" => {
                    args.out = crate::normalize_path(Path::new(&value));
                    out = true;
                }
                "--source" => args.source = value,
                "--slot" => args.slots.push(value),
                "--iso" => args.iso = Some(crate::normalize_path(Path::new(&value))),
                "--ui" => {
                    if !["source", "donor"].contains(&value.as_str()) {
                        return Err(Error::new(format!(
                            "argument --ui: invalid choice: '{value}' (choose from 'source', 'donor')"
                        )));
                    }
                    args.ui_source = value == "source";
                }
                "--colour-word" => {
                    args.colour_word = match value.as_str() {
                        "clone" => ColourWord::Clone,
                        "donor" => ColourWord::Donor,
                        "none" => ColourWord::None,
                        _ => {
                            return Err(Error::new(format!(
                                "argument --colour-word: invalid choice: '{value}' (choose from 'clone', 'donor', 'none')"
                            )));
                        }
                    };
                }
                "--textures" => {
                    let (slot, dir) = value.split_once(':').ok_or_else(|| {
                        Error::new(format!(
                            "argument --textures: expected SLOT:FOLDER, got '{value}'"
                        ))
                    })?;
                    args.textures
                        .push((parse_char(slot)?, crate::normalize_path(Path::new(dir))));
                }
                "--anims" => {
                    let (slot, from) = value.split_once(':').ok_or_else(|| {
                        Error::new(format!(
                            "argument --anims: expected SLOT:CHARACTER, got '{value}'"
                        ))
                    })?;
                    args.anims.push((parse_char(slot)?, parse_char(from)?));
                }
                "--from" => {
                    let (slot, from) = value.split_once(':').ok_or_else(|| {
                        Error::new(format!(
                            "argument --from: expected SLOT:CHARACTER, got '{value}'"
                        ))
                    })?;
                    args.from.push((parse_char(slot)?, parse_char(from)?));
                }
                "--export-textures" => {
                    export_textures = Some(crate::normalize_path(Path::new(&value)));
                }
                _ => unreachable!(),
            }
        } else {
            if let Some(v) = explicit {
                return Err(Error::new(format!(
                    "argument {key}: ignored explicit argument '{v}'"
                )));
            }
            match key {
                "--no-aram-overlay" => args.no_aram_overlay = true,
                "--relocate-menu-art" => args.relocate_menu_art = true,
                "--no-stats" => args.no_stats = true,
                "--no-gameplay" => args.no_gameplay = true,
                "--force" => args.force = true,
                "--dry-run" => args.dry_run = true,
                "--list-slots" => list_slots = true,
                "--list-regions" => list_regions = true,
                _ => unreachable!(),
            }
        }
        i += 1;
    }
    if !disc {
        return Err(Error::new(
            "the following arguments are required: --disc".into(),
        ));
    }
    if !unknown.is_empty() {
        return Err(Error::new(format!(
            "unrecognized arguments: {}",
            unknown.join(" ")
        )));
    }
    if let Some(dir) = export_textures {
        return Ok(Command::ExportTextures {
            disc: args.disc,
            source: args.source,
            dir,
        });
    }
    if list_slots || list_regions {
        return Ok(Command::List {
            disc: args.disc,
            source: args.source,
            slots: list_slots,
        });
    }
    if !out && !args.dry_run {
        return Err(Error::new(
            "--out is required (a new folder; the input is never modified)".into(),
        ));
    }
    if out && absolute_path(&args.out)? == absolute_path(&args.disc)? {
        return Err(Error::new(
            "--out must be a different folder from --disc".into(),
        ));
    }
    if !out {
        args.out = args.disc.parent().unwrap_or(Path::new(".")).join("dry-run");
    }
    Ok(Command::Clone(args))
}
/// Produce the slot table, including private-entry and setup-switch restrictions.
pub fn list_slots(disc: &Disc, log: &mut dyn FnMut(&str)) -> Result<()> {
    let dol = Dol::new(disc.system("main.dol")?)?;
    let default_case = dol.u32(ADDR_JUMPTABLE)?;
    log(&format!(
        "{:>3} {:<16} {:<10} {:<5} {:<10} {:<8} {:<8} {}",
        "id", "name", "family", "base", "pair", "private", "setup", "notes"
    ));
    for (c, name) in CHAR_NAMES.iter().enumerate() {
        let fam = dol.family(c)?;
        let (b, o) = dol.pair(c)?;
        let pe = dol.private_entry(c)?;
        let case = if c >= 48 || dol.u32(ADDR_JUMPTABLE + c as u32 * 4)? == default_case {
            "-"
        } else {
            "yes"
        };
        let mut notes = Vec::new();
        if pe.is_none() {
            notes.push(
                "body shared with its colour variants: donor only with --no-aram-overlay".into(),
            );
        }
        if fam[0] != 0 {
            let family = FAMILY_NAMES[fam[0] as usize];
            notes.push(format!(
                "colour {} of {family} (auto-assigned when several {family} are picked)",
                fam[5] + 1
            ));
        }
        log(&format!(
            "{c:>3} {name:<16} {:<10} {:<5} {:<10} {:<8} {case:<8} {}",
            FAMILY_NAMES[fam[0] as usize],
            if fam[3] != 0 { "yes" } else { "" },
            format!("{b},{o}"),
            pe.map_or(String::new(), |v| v.to_string()),
            notes.join("; ")
        ));
    }
    Ok(())
}
/// Produce a source's detailed detected-region listing.
pub fn list_regions(disc: &Disc, c: usize, log: &mut dyn FnMut(&str)) -> Result<()> {
    let dol = Dol::new(disc.system("main.dol")?)?;
    let g = Gpc::new(disc.effective_body(&dol, c)?)?;
    log(&format!(
        "colour regions of {c} {} (use region=N in a recolour spec; default = the ones marked *):",
        CHAR_NAMES[c]
    ));
    for (i, r) in source_regions(disc, &dol, c, &g)?.into_iter().enumerate() {
        log(&format!(
            "  {}{i}  {:<22} {}  {:5.1}% of the body texels   from={:.3} width={:.3} minsat={:.2} minval={:.2} maxval={:.2}",
            if r.default { "*" } else { " " },
            r.name,
            r.swatch(),
            100. * r.weight,
            r.hue,
            r.width,
            r.minsat,
            r.minval,
            r.maxval
        ));
    }
    Ok(())
}
/// Execute a parsed CLI action, keeping temporary ISO extractions alive for listings.
pub fn execute(command: Command, log: &mut dyn FnMut(&str)) -> Result<()> {
    match command {
        Command::Help => log(include_str!("help.txt")),
        Command::GltfHelp => log(GLTF_USAGE),
        Command::ExportGltf {
            disc,
            character,
            all_animations,
            textures,
            out,
        } => {
            let c = parse_char(&character)?;
            if let Some(dir) = &textures
                && !dir.is_dir()
            {
                return Err(Error::new(format!(
                    "--textures must name an existing folder: {}",
                    dir.display()
                )));
            }
            let disc = read_only_disc(&disc)?;
            let dol = Dol::new(disc.system("main.dol")?)?;
            let e = crate::gltf::export(&disc, &dol, c, all_animations, textures.as_deref())?;
            std::fs::write(&out, &e.json).map_err(|err| Error::from(err).at_path(&out))?;
            log(&format!(
                "exported {}: {} bones, {} animation{} -> {}",
                crate::api::pretty_name(c),
                e.bones,
                e.animations,
                if e.animations == 1 { "" } else { "s" },
                out.display()
            ));
        }
        Command::Clone(args) => {
            // ponytail: with --from the early checks would need every source; clone() still checks.
            if !args.textures.is_empty() && args.from.is_empty() {
                // check the painted folders before the long copy of the game
                let (_, body) = read_only_body(&args.disc, &args.source)?;
                let source = crate::api::pretty_name(parse_char(&args.source)?);
                for (_, folder) in &args.textures {
                    Painted::load(folder, &body, &source)?;
                }
            }
            if !args.anims.is_empty() && args.from.is_empty() {
                // check the skeletons before the long copy of the game
                let disc = read_only_disc(&args.disc)?;
                let dol = Dol::new(disc.system("main.dol")?)?;
                let slots = crate::clone::donor_slots(&args.slots)?;
                let source = parse_char(&args.source)?;
                crate::anims::plan(
                    &mut Default::default(),
                    &disc,
                    &dol,
                    source,
                    &slots,
                    &args.anims,
                )?;
            }
            crate::clone::clone(&args, log, None)?;
        }
        Command::ExportTextures { disc, source, dir } => {
            let (c, body) = read_only_body(&disc, &source)?;
            let n = custom_textures::export(&body, &crate::api::pretty_name(c), &dir)?;
            log(&format!(
                "exported {n} textures of {} to {}",
                CHAR_NAMES[c],
                dir.display()
            ));
        }
        Command::List {
            disc,
            source,
            slots,
        } => {
            let temp = tempfile::tempdir()?;
            let d = open_disc(&disc, &temp.path().join("disc"), log, None)?;
            if slots {
                list_slots(&d, log)?;
            } else {
                list_regions(&d, parse_char(&source)?, log)?;
            }
        }
    }
    Ok(())
}
/// The source's original in-match body, read straight from a folder or ISO (nothing extracted).
fn read_only_body(disc: &Path, source: &str) -> Result<(usize, Gpc)> {
    let c = parse_char(source)?;
    let disc = read_only_disc(disc)?;
    let dol = Dol::new(disc.system("main.dol")?)?;
    Ok((c, Gpc::new(disc.effective_body(&dol, c)?)?))
}
/// A folder or ISO opened for reading only (an ISO is not extracted).
fn read_only_disc(disc: &Path) -> Result<Disc> {
    if disc.is_file() {
        Disc::iso(disc)
    } else {
        Disc::folder(disc)
    }
}
/// Render an argparse-style usage error for the CLI's stderr.
pub fn usage_error(error: &Error) -> String {
    if let Some(message) = error.message.strip_prefix(GLTF_PREFIX) {
        return format!("{GLTF_USAGE}\nmssb-clone export-gltf: error: {message}");
    }
    format!(
        "{}clone_character.py: error: {}",
        include_str!("usage.txt"),
        error
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn argparse_defaults_and_choices() {
        assert!(matches!(parse(&["--help".into()]).unwrap(), Command::Help));
        assert_eq!(
            parse(&[]).err().unwrap().message,
            "the following arguments are required: --disc"
        );
        assert_eq!(parse_char(" Baby Mario ").unwrap(), 7);
        assert_eq!(parse_char("53").unwrap(), 53);
    }
    #[test]
    fn export_gltf_flags() {
        let argv = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        let Command::ExportGltf {
            disc,
            character,
            all_animations,
            textures,
            out,
        } = parse(&argv(
            "export-gltf --game g.iso --character dk --all-animations --out=dk.gltf",
        ))
        .unwrap()
        else {
            panic!()
        };
        assert_eq!(
            (disc, character.as_str(), all_animations, textures, out),
            (
                PathBuf::from("g.iso"),
                "dk",
                true,
                None,
                PathBuf::from("dk.gltf")
            )
        );
        assert!(matches!(
            parse(&argv("export-gltf --help")).unwrap(),
            Command::GltfHelp
        ));
        let error = parse(&argv("export-gltf --disc g")).err().unwrap();
        assert!(
            usage_error(&error).ends_with(
                "export-gltf: error: the following arguments are required: --character, --out"
            ),
            "{}",
            usage_error(&error)
        );
    }
    #[test]
    fn texture_flags() {
        let argv = |s: &str| s.split(' ').map(String::from).collect::<Vec<_>>();
        let Command::Clone(args) = parse(&argv(
            "--disc d --out o --slot 29 --textures 29:C:\\art --textures BlueToad:x",
        ))
        .unwrap() else {
            panic!()
        };
        assert_eq!(
            args.textures,
            vec![(29, PathBuf::from("C:\\art")), (29, PathBuf::from("x"))]
        );
        assert!(matches!(
            parse(&argv("--disc d --source luigi --export-textures out")).unwrap(),
            Command::ExportTextures { source, .. } if source == "luigi"
        ));
        let Command::Clone(args) = parse(&argv(
            "--disc d --out o --slot 29 --anims 29:luigi --anims=BlueToad:0",
        ))
        .unwrap() else {
            panic!()
        };
        assert_eq!(args.anims, vec![(29, 1), (29, 0)]);
        for (value, mode) in [
            ("clone", ColourWord::Clone),
            ("donor", ColourWord::Donor),
            ("none", ColourWord::None),
        ] {
            let Command::Clone(args) =
                parse(&argv(&format!("--disc d --out o --colour-word {value}"))).unwrap()
            else {
                panic!()
            };
            assert_eq!(args.colour_word, mode);
        }
        let Command::Clone(args) = parse(&argv("--disc d --out o")).unwrap() else {
            panic!()
        };
        assert_eq!(args.colour_word, ColourWord::Clone);
        assert!(
            parse(&argv("--disc d --out o --colour-word word"))
                .err()
                .unwrap()
                .message
                .contains("choose from 'clone', 'donor', 'none'")
        );
        assert!(
            parse(&argv("--disc d --out o --anims luigi"))
                .err()
                .unwrap()
                .message
                .contains("expected SLOT:CHARACTER")
        );
        assert!(
            parse(&argv("--disc d --out o --textures 29"))
                .err()
                .unwrap()
                .message
                .contains("expected SLOT:FOLDER")
        );
    }
}
