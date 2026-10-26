//! "Animations from" (#9): a clone wears its source's body but plays another character's ANIM
//! banks. Only descriptors are redirected (`lbl_800F1D78[slot*19 + 2..=18]` and the ARAM copy of
//! track 15, `master[432 + slot]`); no shared file is rewritten.
//!
//! The game binds an animation to a body by **bone ID**, not by array position or bone count:
//! `ACTSetAnimation` looks up each runtime bone's ID in the sequence's tracks
//! (`ANIMGetTrackFromSequence`, mssb-dtk `.agent/animations.md` §3). So the check here compares
//! the IDs in both body variants (disc tracks 0 and 1) with the IDs each bank drives.
//! Assumption (the playback decomp of `ACTSetBoneTrack` may refine it): a bone without a track
//! in the playing sequence is not moved by it.
use crate::binary::{Descriptor, Dol, u16_at, u32_at};
use crate::constants::*;
use crate::disc::Disc;
use crate::{Error, Result, lzss};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::ops::Range;

/// Disc subfile tracks that hold ANIM banks (0 and 1 are the bodies).
pub const ANIM_TRACKS: Range<usize> = 2..19;
/// Banks with no identified request path (animations.md §1): checked, but a mismatch there only
/// warns. If the playback decomp finds a caller, move them out of this list.
pub const UNUSED_TRACKS: [usize; 2] = [7, 8];
/// The ID several unrelated bones share (Luigi's body has three); it can't bind meaningfully.
const NO_BONE: u16 = 0xFFFF;
/// Version word of both ANIM banks and actor layouts.
const VERSION: u32 = 0x007B_7960;

/// Bone ID → parent bone ID (`None` for a root) of one body's actor layout.
pub type Skeleton = BTreeMap<u16, Option<u16>>;
/// Bone ID → the disc tracks it is reported for.
pub type BoneTracks = BTreeMap<u16, BTreeSet<usize>>;

/// Read the bone IDs and parent IDs of a body (`.gpc`): the actor layout at body+4, its
/// 0x1C-byte bones at layout+0x20 with the parent link at +0xC and the bone ID at +0x16.
pub fn skeleton(gpc: &[u8]) -> Result<Skeleton> {
    let bad = || Error::new("body: the bone list does not look right".into());
    let layout = u32_at(gpc, 4)? as usize;
    if u32_at(gpc, layout)? != VERSION {
        return Err(bad());
    }
    let count = u16_at(gpc, layout + 6)? as usize;
    let bone = |i: usize| layout + 0x20 + i * 0x1C;
    let ids = (0..count)
        .map(|i| u16_at(gpc, bone(i) + 0x16))
        .collect::<Result<Vec<_>>>()?;
    let mut out = Skeleton::new();
    for (i, &id) in ids.iter().enumerate() {
        if id == NO_BONE {
            continue;
        }
        // Links are relative to the layout and name a bone record (LoadActorLayout).
        let parent = match u32_at(gpc, bone(i) + 0xC)? as usize {
            0 => None,
            link => {
                let rel = link.checked_sub(0x20).ok_or_else(bad)?;
                if rel % 0x1C != 0 || rel / 0x1C >= count {
                    return Err(bad());
                }
                Some(ids[rel / 0x1C])
            }
        };
        out.entry(id).or_insert(parent);
    }
    Ok(out)
}

/// Bytes at the start of a bank up to the end of its track table.
fn table_size(header: &[u8]) -> Result<usize> {
    if u32_at(header, 0)? != VERSION || u32_at(header, 4)? != 0x18 {
        return Err(Error::new(
            "animation bank: unknown header (not a retail ANIM bank)".into(),
        ));
    }
    Ok(0x18 + u16_at(header, 0xA)? as usize * 12 + u16_at(header, 0xC)? as usize * 16)
}

/// Bone IDs an ANIM bank has tracks for: the 0x10-byte track records follow the 0x18-byte header
/// and the 0xC-byte sequences; the bone ID is at track+0xA.
pub fn bank_bones(bank: &[u8]) -> Result<BTreeSet<u16>> {
    let size = table_size(bank)?;
    let first = 0x18 + u16_at(bank, 0xA)? as usize * 12;
    (first..size)
        .step_by(16)
        .map(|t| u16_at(bank, t + 0xA))
        .collect()
}

/// Expand the first `size` bytes of a ZZZZ.dat resource, reading only what they need.
fn load_prefix(disc: &Disc, d: Descriptor, size: usize) -> Result<Vec<u8>> {
    let size = size.min((d.0[1] & 0x0fff_ffff) as usize);
    let packed = d.0[3] as usize;
    if d.0[1] & 0x4000_0000 == 0 {
        return disc.read("ZZZZ.dat", d.0[2] as u64, size.min(packed));
    }
    let raw = disc.read(
        "ZZZZ.dat",
        d.0[2] as u64,
        lzss::prefix_bound(size).min(packed),
    )?;
    lzss::decompress_prefix(&raw, size, 11, 4)
}

/// What the check needs of one character: both body skeletons and the bones of each bank.
#[derive(Clone, Debug)]
pub struct Rig {
    /// Skeletons of disc tracks 0 (high detail) and 1 (low detail).
    pub bodies: [Skeleton; 2],
    /// Bone IDs driven by disc tracks 2..=18, in that order.
    pub banks: Vec<BTreeSet<u16>>,
    /// Descriptors of tracks 2..=18; characters with equal vectors share every bank.
    pub vector: Vec<Descriptor>,
}

/// Rigs read from one clean game, shared banks read once.
#[derive(Default)]
pub struct Rigs {
    banks: HashMap<[u32; 4], BTreeSet<u16>>,
    rigs: HashMap<usize, Rig>,
}
impl Rigs {
    /// The rig of charID `c` from a clean game's descriptors (read before patching).
    pub fn get(&mut self, disc: &Disc, dol: &Dol, c: usize) -> Result<Rig> {
        if let Some(rig) = self.rigs.get(&c) {
            return Ok(rig.clone());
        }
        let bodies = [
            skeleton(&disc.load_subfile(dol, c * 19)?)?,
            skeleton(&disc.load_subfile(dol, c * 19 + 1)?)?,
        ];
        let mut banks = Vec::new();
        let mut vector = Vec::new();
        for t in ANIM_TRACKS {
            let d = dol.desc(ADDR_SUBFILES, c * 19 + t)?;
            let bones = match self.banks.entry(d.0) {
                std::collections::hash_map::Entry::Occupied(e) => e.into_mut(),
                std::collections::hash_map::Entry::Vacant(e) => {
                    let size = table_size(&load_prefix(disc, d, 0x18)?)?;
                    e.insert(bank_bones(&load_prefix(disc, d, size)?).map_err(|e| {
                        Error::new(format!("{} track {t}: {}", CHAR_NAMES[c], e.message))
                    })?)
                }
            };
            banks.push(bones.clone());
            vector.push(d);
        }
        let rig = Rig {
            bodies,
            banks,
            vector,
        };
        self.rigs.insert(c, rig.clone());
        Ok(rig)
    }
}

/// How well one character's animations fit another's body.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Fit {
    /// Bones the animations drive in tracks the game uses that the body lacks: refused.
    pub missing: BoneTracks,
    /// The same for tracks 7 and 8, which nothing is known to request.
    pub missing_unused: BoneTracks,
    /// Bones the body's own animations move that the new ones leave alone.
    pub still: BoneTracks,
    /// Driven bones whose parent bone differs between the two skeletons.
    pub reparented: BoneTracks,
}
impl Fit {
    /// Whether the animations need bones the body does not have.
    pub fn refused(&self) -> bool {
        !self.missing.is_empty()
    }
    /// Whether the animations bind exactly as on their own character.
    pub fn exact(&self) -> bool {
        *self == Fit::default()
    }
}

fn add(map: &mut BoneTracks, bone: u16, track: usize) {
    map.entry(bone).or_default().insert(track);
}

/// Compare `anims`' banks with `body`'s skeletons (both variants, per track). A bone counts as
/// driven by a bank only if the bank's own character has it: banks also carry tracks for IDs
/// their body lacks (Luigi's track 3 has 60 and 61), which the game never binds there.
pub fn fit(body: &Rig, anims: &Rig) -> Fit {
    let mut out = Fit::default();
    for v in 0..2 {
        let (own, theirs) = (&body.bodies[v], &anims.bodies[v]);
        for (i, t) in ANIM_TRACKS.enumerate() {
            for &b in &anims.banks[i] {
                let Some(parent) = theirs.get(&b) else {
                    continue;
                };
                match own.get(&b) {
                    None if UNUSED_TRACKS.contains(&t) => add(&mut out.missing_unused, b, t),
                    None => add(&mut out.missing, b, t),
                    Some(p) if p != parent => add(&mut out.reparented, b, t),
                    Some(_) => {}
                }
            }
            for &b in &body.banks[i] {
                if own.contains_key(&b) && !anims.banks[i].contains(&b) {
                    add(&mut out.still, b, t);
                }
            }
        }
    }
    out
}

/// "2-6, 9-18" for a set of tracks.
pub fn track_ranges(tracks: &BTreeSet<usize>) -> String {
    let mut parts: Vec<(usize, usize)> = Vec::new();
    for &t in tracks {
        match parts.last_mut() {
            Some(last) if last.1 + 1 == t => last.1 = t,
            _ => parts.push((t, t)),
        }
    }
    parts
        .iter()
        .map(|&(a, b)| {
            if a == b {
                a.to_string()
            } else {
                format!("{a}-{b}")
            }
        })
        .collect::<Vec<_>>()
        .join(", ")
}

/// "36, 70 (tracks 7, 8, 17); 4 (tracks 2-6)": bones grouped by the tracks they appear in.
pub fn describe(map: &BoneTracks) -> String {
    let mut groups: BTreeMap<&BTreeSet<usize>, Vec<u16>> = BTreeMap::new();
    for (bone, tracks) in map {
        groups.entry(tracks).or_default().push(*bone);
    }
    groups
        .iter()
        .map(|(tracks, bones)| {
            format!(
                "{} (track{} {})",
                bones
                    .iter()
                    .map(u16::to_string)
                    .collect::<Vec<_>>()
                    .join(", "),
                if tracks.len() == 1 { "" } else { "s" },
                track_ranges(tracks)
            )
        })
        .collect::<Vec<_>>()
        .join("; ")
}

/// The refusal for animations that need bones the body lacks (`None` if they fit).
pub fn refusal(fit: &Fit, body: &str, anims: &str) -> Option<String> {
    fit.refused().then(|| {
        format!(
            "{anims}'s animations move bones that {body}'s body does not have: bone IDs {}. A {body} clone can't use them; pick another \"Animations from\" character.",
            describe(&fit.missing)
        )
    })
}

/// One line per thing that will look different from the animations' own character.
pub fn warnings(fit: &Fit, body: &str, anims: &str) -> Vec<String> {
    let mut out = Vec::new();
    if !fit.still.is_empty() {
        out.push(format!(
            "{body} bones that {anims}'s animations do not move (they stay put): {}",
            describe(&fit.still)
        ));
    }
    if !fit.reparented.is_empty() {
        out.push(format!(
            "bones attached to a different parent in {anims}'s skeleton (may bend oddly): {}",
            describe(&fit.reparented)
        ));
    }
    if !fit.missing_unused.is_empty() {
        out.push(format!(
            "bones {body} lacks, only in banks the game is not known to use: {}",
            describe(&fit.missing_unused)
        ));
    }
    out
}

/// The animations one clone ends up with.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Choice {
    /// Donor slot of the clone.
    pub slot: usize,
    /// CharID whose banks it plays; the source when nothing (or an equal bank set) was chosen.
    pub anims: usize,
    /// The character that was asked for, when its banks equal the source's.
    pub same_as_source: Option<usize>,
    /// Skeleton warnings for the log (empty when the bones match exactly).
    pub warnings: Vec<String>,
}

/// Resolve and check each clone's "Animations from" (`wanted`: slot → charID) against the
/// source's body, which every clone wears. Reads the clean game's descriptors, so call it before
/// patching. Refuses animations that need bones the body lacks.
pub fn plan(
    rigs: &mut Rigs,
    disc: &Disc,
    dol: &Dol,
    source: usize,
    slots: &[usize],
    wanted: &[(usize, usize)],
) -> Result<Vec<Choice>> {
    let name = crate::api::pretty_name;
    for (i, (slot, _)) in wanted.iter().enumerate() {
        if !slots.contains(slot) {
            return Err(Error::new(format!(
                "animations for {} were given, but it is not one of the clone slots",
                name(*slot)
            )));
        }
        if wanted[..i].iter().any(|(s, _)| s == slot) {
            return Err(Error::new(format!(
                "animations for {} were given twice",
                name(*slot)
            )));
        }
    }
    let mut out = Vec::new();
    for &slot in slots {
        let mut choice = Choice {
            slot,
            anims: source,
            same_as_source: None,
            warnings: Vec::new(),
        };
        if let Some(&(_, a)) = wanted.iter().find(|(s, _)| *s == slot)
            && a != source
        {
            let (body, anims) = (rigs.get(disc, dol, source)?, rigs.get(disc, dol, a)?);
            if anims.vector == body.vector {
                choice.same_as_source = Some(a);
            } else {
                let fit = fit(&body, &anims);
                if let Some(message) = refusal(&fit, &name(source), &name(a)) {
                    return Err(Error::new(message));
                }
                choice.anims = a;
                choice.warnings = warnings(&fit, &name(source), &name(a));
            }
        }
        out.push(choice);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A body: header word, layout offset, then the layout with `(id, parent index)` bones.
    pub(crate) fn body(bones: &[(u16, Option<usize>)]) -> Vec<u8> {
        let layout = 0x10;
        let mut b = vec![0; layout + 0x20 + bones.len() * 0x1C];
        b[4..8].copy_from_slice(&(layout as u32).to_be_bytes());
        b[layout..layout + 4].copy_from_slice(&VERSION.to_be_bytes());
        b[layout + 6..layout + 8].copy_from_slice(&(bones.len() as u16).to_be_bytes());
        for (i, (id, parent)) in bones.iter().enumerate() {
            let r = layout + 0x20 + i * 0x1C;
            let link = parent.map_or(0, |p| 0x20 + p as u32 * 0x1C);
            b[r + 0xC..r + 0x10].copy_from_slice(&link.to_be_bytes());
            b[r + 0x16..r + 0x18].copy_from_slice(&id.to_be_bytes());
        }
        b
    }
    /// A bank with one sequence per entry of `seqs`, each a list of bone IDs.
    pub(crate) fn bank(seqs: &[&[u16]]) -> Vec<u8> {
        let tracks: usize = seqs.iter().map(|s| s.len()).sum();
        let mut b = vec![0; 0x18 + seqs.len() * 12 + tracks * 16];
        b[0..4].copy_from_slice(&VERSION.to_be_bytes());
        b[4..8].copy_from_slice(&0x18u32.to_be_bytes());
        b[0xA..0xC].copy_from_slice(&(seqs.len() as u16).to_be_bytes());
        b[0xC..0xE].copy_from_slice(&(tracks as u16).to_be_bytes());
        let mut t = 0x18 + seqs.len() * 12;
        for s in seqs {
            for id in *s {
                b[t + 0xA..t + 0xC].copy_from_slice(&id.to_be_bytes());
                t += 16;
            }
        }
        b
    }
    fn rig(bodies: [&[(u16, Option<usize>)]; 2], banks: &[&[u16]]) -> Rig {
        Rig {
            bodies: bodies.map(|b| skeleton(&body(b)).unwrap()),
            banks: ANIM_TRACKS
                .map(|t| {
                    banks
                        .get(t - 2)
                        .copied()
                        .unwrap_or(&[])
                        .iter()
                        .copied()
                        .collect()
                })
                .collect(),
            vector: Vec::new(),
        }
    }

    #[test]
    fn reads_bone_ids_and_parent_ids_not_positions() {
        // root 71 last, like Mario's low body; 0xFFFF bones are skipped
        let s = skeleton(&body(&[
            (0, Some(3)),
            (4, Some(0)),
            (NO_BONE, Some(1)),
            (71, None),
        ]))
        .unwrap();
        assert_eq!(s, Skeleton::from([(0, Some(71)), (4, Some(0)), (71, None)]));
        assert!(skeleton(&body(&[(0, Some(9))])).is_err());
        assert_eq!(
            bank_bones(&bank(&[&[4, 5], &[5, 9]])).unwrap(),
            BTreeSet::from([4, 5, 9])
        );
        let mut wrong = bank(&[&[1]]);
        wrong[3] ^= 1;
        assert!(bank_bones(&wrong).is_err());
    }

    #[test]
    fn same_count_different_ids_is_refused() {
        let a: &[(u16, Option<usize>)] = &[(0, None), (4, Some(0)), (5, Some(1))];
        let b: &[(u16, Option<usize>)] = &[(0, None), (4, Some(0)), (6, Some(1))];
        let body = rig([a, a], &[&[4, 5]]);
        let anims = rig([b, b], &[&[4, 6]]);
        let f = fit(&body, &anims);
        assert!(f.refused());
        assert_eq!(f.missing, BoneTracks::from([(6, BTreeSet::from([2]))]));
        assert_eq!(f.still, BoneTracks::from([(5, BTreeSet::from([2]))]));
        let message = refusal(&f, "Mario", "Boo").unwrap();
        assert!(message.contains("bone IDs 6 (track 2). "), "{message}");
    }

    #[test]
    fn partial_overlap_and_unused_tracks_only_warn() {
        let a: &[(u16, Option<usize>)] = &[(0, None), (4, Some(0)), (36, Some(0))];
        let b: &[(u16, Option<usize>)] = &[(0, None), (4, Some(0)), (70, Some(1))];
        // track 2: both move 4; track 7: the body's own bank moves 36, the new one 70
        let body = rig([a, a], &[&[4], &[], &[], &[], &[], &[36]]);
        let anims = rig([b, b], &[&[4, 60], &[], &[], &[], &[], &[70]]);
        let f = fit(&body, &anims);
        assert!(!f.refused() && !f.exact());
        // 60 is ignored: the animations' own body has no bone 60
        assert_eq!(
            f.missing_unused,
            BoneTracks::from([(70, BTreeSet::from([7]))])
        );
        let w = warnings(&f, "Mario", "Luigi");
        assert_eq!(w.len(), 2, "{w:?}");
        assert!(w[0].ends_with("36 (track 7)"), "{w:?}");
    }

    #[test]
    fn reparented_bones_warn() {
        let a: &[(u16, Option<usize>)] = &[(0, None), (90, Some(0)), (4, Some(0))];
        let b: &[(u16, Option<usize>)] = &[(0, None), (90, Some(0)), (4, Some(1))];
        let f = fit(&rig([a, a], &[&[4]]), &rig([b, b], &[&[4]]));
        assert!(!f.refused());
        assert_eq!(f.reparented, BoneTracks::from([(4, BTreeSet::from([2]))]));
        assert!(fit(&rig([a, a], &[&[4]]), &rig([a, a], &[&[4]])).exact());
    }

    #[test]
    fn groups_bones_by_track_ranges() {
        let map = BoneTracks::from([
            (36, BTreeSet::from([7, 8, 17])),
            (70, BTreeSet::from([7, 8, 17])),
            (4, BTreeSet::from([2, 3, 4, 5, 6, 9, 10])),
        ]);
        assert_eq!(
            describe(&map),
            "4 (tracks 2-6, 9-10); 36, 70 (tracks 7-8, 17)"
        );
    }
}
