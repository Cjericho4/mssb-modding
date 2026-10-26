//! Read-only "Animations from" compatibility matrix of the whole roster (#9): for every source body,
//! which other characters' animations fit exactly, fit with warnings, or are refused. Writes no
//! game data. `--check` also expands every bank completely and compares its bone IDs with the
//! table-prefix reader the window uses; `--choices SOURCE` prints that source's dropdown and
//! `BODY:ANIMS` the check's messages for one pair.
use mssb_clone_core::anims::{self, ANIM_TRACKS, Rigs};
use mssb_clone_core::binary::Dol;
use mssb_clone_core::constants::{ADDR_SUBFILES, CHAR_NAMES};
use mssb_clone_core::disc::Disc;
use mssb_clone_core::{Error, Result};

fn main() -> Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let path = args
        .first()
        .ok_or_else(|| Error::new("usage: anim_matrix DISC [--check]".into()))?;
    let disc = if std::path::Path::new(path).is_file() {
        Disc::iso(path)?
    } else {
        Disc::folder(path)?
    };
    let dol = Dol::new(disc.system("main.dol")?)?;
    let start = std::time::Instant::now();
    let mut rigs = Rigs::default();
    let all = (0..54)
        .map(|c| rigs.get(&disc, &dol, c))
        .collect::<Result<Vec<_>>>()?;
    println!("read 54 rigs in {:.2?}", start.elapsed());
    if args.iter().any(|a| a == "--check") {
        let mut seen = std::collections::HashSet::new();
        for (c, rig) in all.iter().enumerate() {
            for (i, t) in ANIM_TRACKS.enumerate() {
                let d = dol.desc(ADDR_SUBFILES, c * 19 + t)?;
                if seen.insert(d.0) {
                    let full = anims::bank_bones(&disc.load("ZZZZ.dat", d, 0)?)?;
                    assert_eq!(full, rig.banks[i], "{c} track {t}");
                }
            }
        }
        println!(
            "checked {} distinct banks against full expansion",
            seen.len()
        );
    }
    // one column per distinct animation vector, named by its lowest charID
    let mut reps: Vec<usize> = Vec::new();
    for c in 0..54 {
        if !reps.iter().any(|&r| all[r].vector == all[c].vector) {
            reps.push(c);
        }
    }
    let (mut exact, mut warned, mut refused) = (0, 0, 0);
    for body in 0..54 {
        let mut cells = (Vec::new(), Vec::new(), 0);
        for &a in &reps {
            if all[a].vector == all[body].vector {
                continue;
            }
            let fit = anims::fit(&all[body], &all[a]);
            if fit.refused() {
                cells.2 += 1;
            } else if fit.exact() {
                cells.0.push(CHAR_NAMES[a].to_string());
            } else {
                let kinds: Vec<_> = [
                    (!fit.still.is_empty(), "still"),
                    (!fit.reparented.is_empty(), "parent"),
                    (!fit.missing_unused.is_empty(), "7/8"),
                    (!fit.foreign.is_empty(), "foreign"),
                ]
                .iter()
                .filter(|k| k.0)
                .map(|k| k.1)
                .collect();
                cells
                    .1
                    .push(format!("{} [{}]", CHAR_NAMES[a], kinds.join(",")));
            }
        }
        exact += cells.0.len();
        warned += cells.1.len();
        refused += cells.2;
        println!(
            "{body:>2} {:<15} exact: {}\n                   warn: {}\n                   refused: {}",
            CHAR_NAMES[body],
            cells.0.join(", "),
            cells.1.join(", "),
            cells.2
        );
    }
    println!(
        "{} animation sets; body x other-set pairs: {exact} exact, {warned} with warnings, {refused} refused",
        reps.len()
    );
    if let Some(i) = args.iter().position(|a| a == "--choices") {
        let source = mssb_clone_core::clone::parse_char(&args[i + 1])?;
        for choice in mssb_clone_core::api::anim_choices(&disc, &dol, &mut rigs, source)? {
            println!(
                "{:>2} {}  {}",
                choice.id,
                choice.label,
                choice.note.replace('\n', " | ")
            );
        }
    }
    if let Some(pair) = args.iter().find(|a| a.contains(':')) {
        let (b, a) = pair.split_once(':').unwrap();
        let (b, a) = (
            mssb_clone_core::clone::parse_char(b)?,
            mssb_clone_core::clone::parse_char(a)?,
        );
        let fit = anims::fit(&all[b], &all[a]);
        let (bn, an) = (CHAR_NAMES[b], CHAR_NAMES[a]);
        if let Some(r) = anims::refusal(&fit, bn, an) {
            println!("{r}");
        }
        for w in anims::warnings(&fit, bn, an) {
            println!("warning: {w}");
        }
    }
    Ok(())
}
