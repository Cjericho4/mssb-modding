//! MusyX sound-group files (`sound.N`) and the merged voice bank that keeps a clone's voice (#29).
//!
//! A group file is stored (not LZSS). `fn_80021518` hands its four chunks to
//! `sndPushGroup(proj, gid, samp, sdir, pool)` (src/Musyx/s_data.c):
//!
//! ```text
//! 0x00  {u32 offset, u32 size} x4      proj, sdir, pool, samp; each chunk 0x20-aligned
//! proj  GROUP_DATA 0x28 {nextOff, u16 id, u16 type (1 = SFX), macroOff, sampleOff, curveOff,
//!       keymapOff, layerOff, fxTableOff, 0, 0}, five id lists (u16, 0x8000|first,last = range,
//!       0xFFFF ends), FX_DATA {u16 n, u16 0, FX_TAB[n] 10 B, sorted by id}; 0xFFFFFFFF ends
//! sdir  SDIR_DATA 0x20 {u16 id, u16 refCount, u32 samp offset, u32 addr, info, length, loop
//!       start, loop length, u32 extra}, 0xFFFFFFFF ends, then SNDADPCMinfo 0x28 at sdir+extra
//! pool  POOL_DATA {macroOff, curveOff, keymapOff, layerOff} (0 = none) + MEM_DATA lists
//!       {u32 nextOff (to the next entry), u16 id, u16 0, data}, each ended by 0xFFFFFFFF
//! samp  DSP-ADPCM data, each sample 0x20-aligned in sdir order
//! ```
//!
//! Ids are global across groups and the voice banks share none, so a source's sounds can join a
//! donor's bank under their retail ids. The audio library looks an SFX up by binary search of
//! each pushed group's FX table (`dataGetFX`), takes the first pushed sdir entry with a sample
//! id (`dataAddSampleReference`), and finds pool objects by a linear walk (`GetPoolAddr`).
use crate::binary::{u16_at, u32_at};
use crate::{Error, Result, align};
use std::collections::{BTreeMap, BTreeSet};

/// Sound-group row of the voice bank of unique index 0 (`fn_3_90F48` loads `sound.(5 + u)`).
pub const VOICE_ROW0: usize = 5;
/// Voice lines per character (`fn_3_90220` plays `lbl_3_data_8168[charID] + line`).
pub const VOICE_LINES: u16 = 13;
const GROUP_DATA: usize = 0x28;
const SDIR_ENTRY: usize = 0x20;
const ADPCM_INFO: usize = 0x28;
const FX_ENTRY: usize = 10;
const END: u32 = 0xFFFF_FFFF;
/// proj id lists in GROUP_DATA order.
const LISTS: [&str; 5] = ["macro", "sample", "curve", "keymap", "layer"];
/// Pool lists in POOL_DATA order, with the id bit `InsertData` adds before looking them up.
const POOL: [(&str, usize, u16); 4] = [
    ("macro", 0, 0),
    ("curve", 2, 0),
    ("keymap", 3, 0x4000),
    ("layer", 4, 0x8000),
];

fn bad(what: &str) -> Error {
    Error::new(format!("sound group: {what}"))
}
fn get(data: &[u8], off: usize, len: usize) -> Result<&[u8]> {
    data.get(off..off + len)
        .ok_or_else(|| bad("structure runs past its chunk"))
}
/// Bytes a sample occupies in ARAM (`convert_length` in hardware.c, then 0x20 alignment).
pub fn aram_bytes(length_word: u32) -> usize {
    let n = (length_word & 0x00FF_FFFF) as usize;
    let bytes = match length_word >> 24 {
        0 | 1 | 4 | 5 => n.div_ceil(14) * 8,
        2 | 6 => n * 2,
        _ => n,
    };
    align(bytes, 0x20)
}
/// `ScanIDList`: plain ids, or `0x8000|first, last` ranges, ended by 0xFFFF.
fn id_list(proj: &[u8], mut off: usize) -> Result<Vec<u16>> {
    let mut ids = Vec::new();
    loop {
        let v = u16_at(proj, off)?;
        if v == 0xFFFF {
            return Ok(ids);
        }
        if v & 0x8000 != 0 {
            ids.extend((v & 0x3FFF)..=u16_at(proj, off + 2)?);
            off += 4;
        } else {
            ids.push(v);
            off += 2;
        }
    }
}
/// The retail id-list encoding: runs of three or more as ranges, single ids otherwise.
fn encode_ids(ids: &[u16]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < ids.len() {
        let mut j = i;
        while j + 1 < ids.len() && ids[j + 1] == ids[j] + 1 {
            j += 1;
        }
        if j - i >= 2 {
            out.extend((0x8000 | ids[i]).to_be_bytes());
            out.extend(ids[j].to_be_bytes());
            i = j + 1;
        } else {
            out.extend(ids[i].to_be_bytes());
            i += 1;
        }
    }
    out.extend([0xFF, 0xFF]);
    out
}
fn chunks(file: &[u8]) -> Result<[&[u8]; 4]> {
    if u32_at(file, 0)? != 0x20 {
        return Err(bad("not a MusyX group file (header word 0 must be 0x20)"));
    }
    let mut out = [&file[..0]; 4];
    for (i, chunk) in out.iter_mut().enumerate() {
        let off = u32_at(file, i * 8)? as usize;
        let size = u32_at(file, i * 8 + 4)? as usize;
        *chunk = get(file, off, size)?;
    }
    Ok(out)
}

/// One SDIR entry with its ADPCM info and sample data.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Sample {
    id: u16,
    /// The 0x20-byte entry; its samp offset and info pointer are rewritten on output.
    entry: Vec<u8>,
    info: Option<Vec<u8>>,
    data: Vec<u8>,
}
impl Sample {
    /// The entry without the two file-layout fields (samp offset, info pointer).
    fn fields(&self) -> (&[u8], &[u8]) {
        (&self.entry[..4], &self.entry[8..0x1C])
    }
}
/// One MEM_DATA object of the pool: its id and the bytes after `nextOff`.
#[derive(Clone, Debug, PartialEq, Eq)]
struct PoolObject {
    id: u16,
    body: Vec<u8>,
}

/// A one-group SFX file (every voice bank), parsed into id-keyed parts.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Group {
    /// The group id pushed for this file (`lbl_3_data_8148[u]` for a voice bank).
    pub gid: u16,
    lists: [Vec<u16>; 5],
    fx: Vec<[u8; FX_ENTRY]>,
    samples: Vec<Sample>,
    pool: [Vec<PoolObject>; 4],
}
impl Group {
    /// Parse a stored group file with exactly one SFX group.
    pub fn parse(file: &[u8]) -> Result<Self> {
        let [proj, sdir, pool, samp] = chunks(file)?;
        if u32_at(proj, 0)? == END {
            return Err(bad("no group in proj"));
        }
        let next = u32_at(proj, 0)? as usize;
        if u16_at(proj, 6)? != 1 || u32_at(proj, next)? != END {
            return Err(bad("expected exactly one SFX group (a voice bank)"));
        }
        let mut lists: [Vec<u16>; 5] = Default::default();
        for (k, list) in lists.iter_mut().enumerate() {
            *list = id_list(proj, u32_at(proj, 8 + k * 4)? as usize)?;
        }
        let table = u32_at(proj, 0x1C)? as usize;
        let fx = (0..u16_at(proj, table)? as usize)
            .map(|k| {
                Ok(get(proj, table + 4 + k * FX_ENTRY, FX_ENTRY)?
                    .try_into()
                    .unwrap())
            })
            .collect::<Result<_>>()?;
        let mut samples = Vec::new();
        let mut off = 0;
        while u16_at(sdir, off)? != 0xFFFF {
            let entry = get(sdir, off, SDIR_ENTRY)?.to_vec();
            let extra = u32_at(&entry, 0x1C)? as usize;
            let start = u32_at(&entry, 4)? as usize;
            let len = aram_bytes(u32_at(&entry, 0x10)?).min(samp.len().saturating_sub(start));
            samples.push(Sample {
                id: u16_at(&entry, 0)?,
                info: (extra != 0)
                    .then(|| get(sdir, extra, ADPCM_INFO).map(<[u8]>::to_vec))
                    .transpose()?,
                data: get(samp, start, len)?.to_vec(),
                entry,
            });
            off += SDIR_ENTRY;
        }
        let mut objects: [Vec<PoolObject>; 4] = Default::default();
        for (k, list) in objects.iter_mut().enumerate() {
            let mut off = u32_at(pool, k * 4)? as usize;
            if off == 0 {
                continue;
            }
            loop {
                let next = u32_at(pool, off)?;
                if next == END {
                    break;
                }
                if next < 8 {
                    return Err(bad("pool entry with a bad nextOff"));
                }
                list.push(PoolObject {
                    id: u16_at(pool, off + 4)?,
                    body: get(pool, off + 4, next as usize - 4)?.to_vec(),
                });
                off += next as usize;
            }
        }
        Ok(Self {
            gid: u16_at(proj, 4)?,
            lists,
            fx,
            samples,
            pool: objects,
        })
    }
    /// FX (SFX) ids in table order.
    pub fn fx_ids(&self) -> Vec<u16> {
        self.fx
            .iter()
            .map(|f| u16::from_be_bytes([f[0], f[1]]))
            .collect()
    }
    /// ARAM bytes of the samples this group uploads when pushed alone.
    pub fn aram_bytes(&self) -> usize {
        self.samples
            .iter()
            .filter(|s| self.lists[1].contains(&s.id))
            .map(|s| aram_bytes(u32_at(&s.entry, 0x10).unwrap_or(0)))
            .sum()
    }
    /// Add `other`'s sounds (FX, id lists, samples, pool objects) under their own ids. Anything
    /// this group already has with the same id must be identical and is not added twice, so
    /// merging the same source again changes nothing. Returns the number of FX added.
    pub fn merge(&mut self, other: &Group) -> Result<usize> {
        let mut added = 0;
        for f in &other.fx {
            match self.fx.iter().find(|g| g[..2] == f[..2]) {
                Some(g) if g == f => {}
                Some(_) => {
                    return Err(bad(&format!(
                        "SFX 0x{:X} exists with other data in both groups",
                        u16::from_be_bytes([f[0], f[1]])
                    )));
                }
                None => {
                    self.fx.push(*f);
                    added += 1;
                }
            }
        }
        // dataGetFX binary-searches each group's FX table.
        self.fx.sort_by_key(|f| u16::from_be_bytes([f[0], f[1]]));
        for (mine, theirs) in self.lists.iter_mut().zip(&other.lists) {
            let ids: BTreeSet<u16> = mine.iter().chain(theirs).copied().collect();
            *mine = ids.into_iter().collect();
        }
        for s in &other.samples {
            match self.samples.iter().find(|t| t.id == s.id) {
                Some(t) if t.fields() == s.fields() && t.info == s.info && t.data == s.data => {}
                Some(_) => {
                    return Err(bad(&format!(
                        "sample 0x{:X} exists with other data in both groups",
                        s.id
                    )));
                }
                None => self.samples.push(s.clone()),
            }
        }
        self.samples.sort_by_key(|s| s.id);
        for (k, (mine, theirs)) in self.pool.iter_mut().zip(&other.pool).enumerate() {
            for o in theirs {
                match mine.iter().find(|m| m.id == o.id) {
                    Some(m) if m == o => {}
                    Some(_) => {
                        return Err(bad(&format!(
                            "{} 0x{:X} exists with other data in both groups",
                            POOL[k].0, o.id
                        )));
                    }
                    None => mine.push(o.clone()),
                }
            }
        }
        Ok(added)
    }
    /// The group file in the retail layout (a parsed retail voice bank rebuilds byte-identically).
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut body = Vec::new();
        let mut offs = [0u32; 5];
        for (k, list) in self.lists.iter().enumerate() {
            offs[k] = (GROUP_DATA + body.len()) as u32;
            body.extend(encode_ids(list));
        }
        body.resize(align(GROUP_DATA + body.len(), 4) - GROUP_DATA, 0);
        let table = (GROUP_DATA + body.len()) as u32;
        body.extend((self.fx.len() as u16).to_be_bytes());
        body.extend([0, 0]);
        for f in &self.fx {
            body.extend(f);
        }
        body.resize(align(GROUP_DATA + body.len(), 4) - GROUP_DATA, 0);
        let mut proj = Vec::new();
        proj.extend(((GROUP_DATA + body.len()) as u32).to_be_bytes());
        proj.extend(self.gid.to_be_bytes());
        proj.extend(1u16.to_be_bytes());
        for v in offs.into_iter().chain([table, 0, 0]) {
            proj.extend(v.to_be_bytes());
        }
        proj.extend(body);
        proj.extend(END.to_be_bytes());

        let head = self.samples.len() * SDIR_ENTRY + 4;
        let (mut sdir, mut infos, mut samp) = (Vec::new(), Vec::<u8>::new(), Vec::new());
        for s in &self.samples {
            let mut e = s.entry.clone();
            e[4..8].copy_from_slice(&(samp.len() as u32).to_be_bytes());
            let extra = match &s.info {
                Some(info) => {
                    let at = head + infos.len();
                    infos.extend(info);
                    at as u32
                }
                None => 0,
            };
            e[0x1C..0x20].copy_from_slice(&extra.to_be_bytes());
            sdir.extend(e);
            samp.extend(&s.data);
            samp.resize(align(samp.len(), 0x20), 0);
        }
        sdir.extend(END.to_be_bytes());
        sdir.extend(infos);

        let mut pool = vec![0; 0x10];
        for (k, list) in self.pool.iter().enumerate() {
            if list.is_empty() {
                continue;
            }
            let at = pool.len() as u32;
            pool[k * 4..k * 4 + 4].copy_from_slice(&at.to_be_bytes());
            for o in list {
                pool.extend((o.body.len() as u32 + 4).to_be_bytes());
                pool.extend(&o.body);
            }
            pool.extend(END.to_be_bytes());
        }

        let mut out = vec![0; 0x20];
        for (i, chunk) in [proj, sdir, pool, samp].into_iter().enumerate() {
            out.resize(align(out.len(), 0x20), 0);
            let at = out.len() as u32;
            out[i * 8..i * 8 + 4].copy_from_slice(&at.to_be_bytes());
            out[i * 8 + 4..i * 8 + 8].copy_from_slice(&(chunk.len() as u32).to_be_bytes());
            out.extend(chunk);
        }
        out
    }
}

/// MusyX's sample heap in ARAM: from 0x4500 (`ARGetBaseAddress` 0x4000 + the 0x500 zero buffer)
/// to `aramTop` 0x804000 (`sndInit` with 8 MB). `aramStoreData` uploads nothing past it, so a
/// sample that does not fit stays silent. The game's own ARAM heap starts at 0x804000.
pub const SAMPLE_HEAP: usize = 0x80_4000 - 0x4500;
/// Rows pushed for every exhibition match besides the 18 voice banks: boot SFX (row 0, never
/// popped), in-match SFX and songs (row 1, `fn_3_910AC`); the stadium row `0x27 + stadium` comes
/// on top (`fn_3_90798`). Menu groups are popped before game.rel starts (`fn_80021410`).
pub const MATCH_ROWS: [usize; 2] = [0, 1];
/// Exhibition stadium rows (`0x27 + stadium`, stadiums 0..5).
pub const STADIUM_ROWS: std::ops::Range<usize> = 39..45;
/// Players whose voice banks a match pushes (`fn_3_5A28C` loops roster slots 0..17).
pub const MATCH_VOICES: usize = 18;

/// Sample id -> ARAM bytes for every sample the file's groups upload when pushed.
pub fn pushed_samples(file: &[u8]) -> Result<BTreeMap<u16, usize>> {
    let [proj, sdir, _, _] = chunks(file)?;
    let mut ids = BTreeSet::new();
    let mut off = 0;
    while u32_at(proj, off)? != END {
        ids.extend(id_list(proj, u32_at(proj, off + 0xC)? as usize)?);
        let next = u32_at(proj, off)? as usize;
        if next <= off {
            return Err(bad("proj nextOff does not advance"));
        }
        off = next;
    }
    let mut out = BTreeMap::new();
    let mut off = 0;
    while u16_at(sdir, off)? != 0xFFFF {
        let id = u16_at(sdir, off)?;
        if ids.contains(&id) {
            out.entry(id)
                .or_insert(aram_bytes(u32_at(sdir, off + 0x10)?));
        }
        off += SDIR_ENTRY;
    }
    Ok(out)
}

/// The most ARAM a match can use: the `fixed` groups plus the [`MATCH_VOICES`] voice banks that
/// add the most. A sample id already uploaded costs nothing again (`dataAddSampleReference`), so
/// samples found in several banks (a source merged into donor banks, and its own bank) count once.
pub fn worst_match_aram(fixed: &BTreeMap<u16, usize>, banks: &[BTreeMap<u16, usize>]) -> usize {
    let mut seen = BTreeMap::new();
    for bank in banks {
        for id in bank.keys() {
            *seen.entry(*id).or_insert(0) += 1;
        }
    }
    let shared = |id: &u16| seen[id] > 1;
    let shared_cost: usize = banks
        .iter()
        .flatten()
        .filter(|(id, _)| shared(id) && !fixed.contains_key(id))
        .map(|(id, size)| (*id, *size))
        .collect::<BTreeMap<_, _>>()
        .values()
        .sum();
    // Each bank's own cost, and whether it brings the shared samples.
    let mut own: Vec<(usize, bool)> = banks
        .iter()
        .map(|b| {
            let cost = b
                .iter()
                .filter(|(id, _)| !shared(id) && !fixed.contains_key(id))
                .map(|(_, s)| s)
                .sum();
            (cost, b.keys().any(shared))
        })
        .collect();
    own.sort_by_key(|b| std::cmp::Reverse(b.0));
    let top = &own[..MATCH_VOICES.min(own.len())];
    let mut voices: usize = top.iter().map(|b| b.0).sum();
    if top.iter().any(|b| b.1) {
        voices += shared_cost;
    } else if let Some(best) = own[top.len()..].iter().find(|b| b.1) {
        // Swapping the cheapest pick for a bank that brings the shared samples may cost more.
        let swapped = voices - top.last().map_or(0, |b| b.0) + best.0 + shared_cost;
        voices = voices.max(swapped);
    }
    fixed.values().sum::<usize>() + voices
}

/// A group file read the way the audio library reads it, straight from the bytes (independent of
/// [`Group`]): proj walked by `nextOff`, FX by binary search, pool objects by the `GetPoolAddr`
/// walk, samples by the sdir scan.
struct Pushed<'a> {
    chunks: [&'a [u8]; 4],
    gid: u16,
    lists: Vec<BTreeSet<u16>>,
    table: usize,
    fx_count: usize,
}
impl<'a> Pushed<'a> {
    fn new(file: &'a [u8]) -> Result<Self> {
        let chunks = chunks(file)?;
        let proj = chunks[0];
        let mut off = 0;
        let mut found = None;
        while u32_at(proj, off)? != END {
            if u16_at(proj, off + 6)? == 1 {
                found = found.or(Some(off));
            }
            let next = u32_at(proj, off)? as usize;
            if next <= off {
                return Err(bad("proj nextOff does not advance"));
            }
            off = next;
        }
        let g = found.ok_or_else(|| bad("no SFX group"))?;
        let lists = (0..5)
            .map(|k| {
                Ok(id_list(proj, u32_at(proj, g + 8 + k * 4)? as usize)?
                    .into_iter()
                    .collect())
            })
            .collect::<Result<_>>()?;
        let table = u32_at(proj, g + 0x1C)? as usize;
        Ok(Self {
            chunks,
            gid: u16_at(proj, g + 4)?,
            lists,
            table: table + 4,
            fx_count: u16_at(proj, table)? as usize,
        })
    }
    fn fx_at(&self, k: usize) -> Result<&'a [u8]> {
        get(self.chunks[0], self.table + k * FX_ENTRY, FX_ENTRY)
    }
    /// `dataGetFX` for this group: binary search of the FX table.
    fn fx(&self, id: u16) -> Result<Option<&'a [u8]>> {
        let (mut lo, mut hi) = (0, self.fx_count);
        while lo < hi {
            let mid = (lo + hi) / 2;
            let entry = self.fx_at(mid)?;
            match u16_at(entry, 0)?.cmp(&id) {
                std::cmp::Ordering::Equal => return Ok(Some(entry)),
                std::cmp::Ordering::Less => lo = mid + 1,
                std::cmp::Ordering::Greater => hi = mid,
            }
        }
        Ok(None)
    }
    /// `GetPoolAddr`: the object's bytes after nextOff, if the group lists and holds it.
    fn object(&self, kind: usize, id: u16) -> Result<Option<&'a [u8]>> {
        let (_, list, bit) = POOL[kind];
        if !self.lists[list].contains(&id) {
            return Ok(None);
        }
        let pool = self.chunks[2];
        let mut off = u32_at(pool, kind * 4)? as usize;
        if off == 0 {
            return Ok(None);
        }
        loop {
            let next = u32_at(pool, off)?;
            if next == END {
                return Ok(None);
            }
            if u16_at(pool, off + 4)? == id | bit {
                return get(pool, off + 4, next as usize - 4).map(Some);
            }
            if next < 8 {
                return Err(bad("pool entry with a bad nextOff"));
            }
            off += next as usize;
        }
    }
    /// The sdir entry's playback fields, ADPCM info and the sample bytes uploaded to ARAM.
    #[allow(clippy::type_complexity)]
    fn sample(&self, id: u16) -> Result<Option<(Vec<u8>, &'a [u8], &'a [u8])>> {
        if !self.lists[1].contains(&id) {
            return Ok(None);
        }
        let [_, sdir, _, samp] = self.chunks;
        let mut off = 0;
        while u16_at(sdir, off)? != 0xFFFF {
            if u16_at(sdir, off)? == id {
                let e = get(sdir, off, SDIR_ENTRY)?;
                let extra = u32_at(e, 0x1C)? as usize;
                let info = if extra != 0 {
                    get(sdir, extra, ADPCM_INFO)?
                } else {
                    &[]
                };
                let len = aram_bytes(u32_at(e, 0x10)?);
                let start = u32_at(e, 4)? as usize;
                let data = get(samp, start, len.min(samp.len().saturating_sub(start)))?;
                let fields = [&e[..4], &e[8..0x1C]].concat();
                return Ok(Some((fields, info, data)));
            }
            off += SDIR_ENTRY;
        }
        Ok(None)
    }
}

/// Check a merged voice bank against the files it was made from: the donor's group id, every
/// FX, macro, curve, keymap, layer and sample of the donor and of each source resolves in the
/// merged file to exactly the original data (so every SFX id the game can ask for plays the
/// same thing), the 13 voice lines of each source's SFX base are present, the FX table is
/// sorted for the binary search, and the merged file holds nothing else.
pub fn verify_merged(merged: &[u8], donor: &[u8], sources: &[(&[u8], u16)]) -> Result<()> {
    let m = Pushed::new(merged)?;
    let d = Pushed::new(donor)?;
    if m.gid != d.gid {
        return Err(bad("merged bank changed the donor's group id"));
    }
    let ids: Vec<u16> = (0..m.fx_count)
        .map(|k| u16_at(m.fx_at(k)?, 0))
        .collect::<Result<_>>()?;
    if ids.windows(2).any(|w| w[0] >= w[1]) {
        return Err(bad("merged FX table is not sorted by id"));
    }
    let originals: Vec<Pushed> = std::iter::once(Ok(d))
        .chain(sources.iter().map(|(f, _)| Pushed::new(f)))
        .collect::<Result<_>>()?;
    let mut expected: Vec<BTreeSet<u16>> = vec![BTreeSet::new(); 5];
    let mut fx_ids = BTreeSet::new();
    for o in &originals {
        for k in 0..o.fx_count {
            let f = o.fx_at(k)?;
            let id = u16_at(f, 0)?;
            fx_ids.insert(id);
            if m.fx(id)? != Some(f) {
                return Err(bad(&format!(
                    "SFX 0x{id:X} does not resolve to its original entry"
                )));
            }
        }
        for (k, list) in o.lists.iter().enumerate() {
            expected[k].extend(list);
        }
        for (kind, (name, list, _)) in POOL.iter().enumerate() {
            for &id in &o.lists[*list] {
                if m.object(kind, id)? != o.object(kind, id)? {
                    return Err(bad(&format!("{name} 0x{id:X} differs from the original")));
                }
            }
        }
        for &id in &o.lists[1] {
            if m.sample(id)? != o.sample(id)? {
                return Err(bad(&format!("sample 0x{id:X} differs from the original")));
            }
        }
    }
    for (_, base) in sources {
        for line in 0..VOICE_LINES {
            if m.fx(base + line)?.is_none() {
                return Err(bad(&format!(
                    "voice line {line} (SFX 0x{:X}) missing",
                    base + line
                )));
            }
        }
    }
    if ids.len() != fx_ids.len() || m.lists != expected {
        return Err(bad(&format!(
            "merged bank holds ids that none of its inputs has ({} lists)",
            LISTS.join("/")
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A synthetic voice bank: `n` FX from `fx0`, one macro and one sample per FX from `id0`,
    /// an optional curve, the layout of a retail bank.
    fn bank(gid: u16, fx0: u16, id0: u16, n: u16, curve: Option<u16>) -> Vec<u8> {
        let mut lists: [Vec<u16>; 5] = Default::default();
        let mut fx = Vec::new();
        let mut samples = Vec::new();
        let mut macros = Vec::new();
        for k in 0..n {
            let (id, mac, smp) = (fx0 + k, id0 + k, id0 + 0x100 + k);
            let mut f = [0u8; FX_ENTRY];
            f[..2].copy_from_slice(&id.to_be_bytes());
            f[2..4].copy_from_slice(&mac.to_be_bytes());
            f[4..].copy_from_slice(&[1, 0x40, 0x7F, 0x40, 0x3C, 0]);
            fx.push(f);
            lists[0].push(mac);
            lists[1].push(smp);
            // StartSample (0x10) with the sample id, then End.
            let mut body = mac.to_be_bytes().to_vec();
            body.extend([0, 0]);
            body.extend(((u32::from(smp) << 8) | 0x10).to_be_bytes());
            body.extend([0, 0, 0, k as u8, 0, 0, 0, 0, 0, 0, 0, 0]);
            macros.push(PoolObject { id: mac, body });
            let len = 14 * (3 + u32::from(k));
            let mut entry = vec![0; SDIR_ENTRY];
            entry[..2].copy_from_slice(&smp.to_be_bytes());
            entry[0xC..0x10].copy_from_slice(&(0x3C00_7D00u32).to_be_bytes());
            entry[0x10..0x14].copy_from_slice(&len.to_be_bytes());
            samples.push(Sample {
                id: smp,
                entry,
                info: Some((0..ADPCM_INFO).map(|b| (b as u16 + smp) as u8).collect()),
                data: (0..aram_bytes(len))
                    .map(|b| (b as u16 ^ smp) as u8)
                    .collect(),
            });
        }
        let mut pool: [Vec<PoolObject>; 4] = Default::default();
        pool[0] = macros;
        if let Some(c) = curve {
            lists[2].push(c);
            pool[1].push(PoolObject {
                id: c,
                body: [c.to_be_bytes().to_vec(), vec![0, 0, 1, 2, 3, 4, 5, 6, 7, 8]].concat(),
            });
        }
        Group {
            gid,
            lists,
            fx,
            samples,
            pool,
        }
        .to_bytes()
    }

    #[test]
    fn parse_rebuilds_identically() {
        for curve in [None, Some(0x81)] {
            let file = bank(14, 0x9B, 0x13B, 13, curve);
            let g = Group::parse(&file).unwrap();
            assert_eq!(g.to_bytes(), file);
            assert_eq!(g.fx_ids(), (0x9B..0x9B + 13).collect::<Vec<_>>());
        }
    }

    #[test]
    fn merged_bank_resolves_every_original_id() {
        let donor = bank(14, 0x9B, 0x13B, 13, None);
        let mario = bank(27, 0x144, 0x2F, 13, None);
        let pianta = bank(8, 0x201, 0x200, 13, Some(0x81));
        let mut g = Group::parse(&donor).unwrap();
        assert_eq!(g.merge(&Group::parse(&mario).unwrap()).unwrap(), 13);
        let one = g.to_bytes();
        verify_merged(&one, &donor, &[(&mario, 0x144)]).unwrap();
        // A second source onto the same donor bank (another family's clones).
        assert_eq!(g.merge(&Group::parse(&pianta).unwrap()).unwrap(), 13);
        let two = g.to_bytes();
        verify_merged(&two, &donor, &[(&mario, 0x144), (&pianta, 0x201)]).unwrap();
        // The same source again adds nothing.
        assert_eq!(g.merge(&Group::parse(&mario).unwrap()).unwrap(), 0);
        assert_eq!(g.to_bytes(), two);
        let parsed = Group::parse(&two).unwrap();
        assert_eq!(parsed.gid, 14);
        assert_eq!(parsed.fx_ids().len(), 39);
        assert_eq!(parsed.to_bytes(), two);
        // Merging into an already merged file is the same as merging once more.
        let mut again = parsed;
        assert_eq!(again.merge(&Group::parse(&pianta).unwrap()).unwrap(), 0);
        assert_eq!(again.to_bytes(), two);
    }

    #[test]
    fn verify_rejects_wrong_merges() {
        let donor = bank(14, 0x9B, 0x13B, 13, None);
        let mario = bank(27, 0x144, 0x2F, 13, None);
        // The donor alone is missing every source line.
        assert!(verify_merged(&donor, &donor, &[(&mario, 0x144)]).is_err());
        let mut g = Group::parse(&donor).unwrap();
        g.merge(&Group::parse(&mario).unwrap()).unwrap();
        let good = g.to_bytes();
        // One sample byte changed.
        let mut bad_sample = good.clone();
        let samp = u32_at(&good, 24).unwrap() as usize;
        bad_sample[samp + 5] ^= 1;
        assert!(verify_merged(&bad_sample, &donor, &[(&mario, 0x144)]).is_err());
        // Unsorted FX table (bsearch would miss ids).
        let mut unsorted = g.clone();
        unsorted.fx.swap(0, 20);
        assert!(verify_merged(&unsorted.to_bytes(), &donor, &[(&mario, 0x144)]).is_err());
        // Group id changed.
        let mut regid = g.clone();
        regid.gid = 27;
        assert!(verify_merged(&regid.to_bytes(), &donor, &[(&mario, 0x144)]).is_err());
        // Conflicting data under one id is refused by the merge itself.
        let clash = bank(27, 0x9B, 0x2F, 13, None);
        assert!(
            Group::parse(&donor)
                .unwrap()
                .merge(&Group::parse(&clash).unwrap())
                .is_err()
        );
    }

    #[test]
    fn worst_match_counts_shared_samples_once() {
        let bank = |ids: std::ops::Range<u16>, size: usize| -> BTreeMap<u16, usize> {
            ids.map(|i| (i, size)).collect()
        };
        let fixed = bank(0..2, 100);
        // 20 disjoint banks of 10 samples; the 18 largest count.
        let mut banks: Vec<_> = (0..20u16)
            .map(|k| bank(100 + k * 10..110 + k * 10, 1 + k as usize))
            .collect();
        let retail = worst_match_aram(&fixed, &banks);
        assert_eq!(retail, 200 + (3..=20).map(|k| 10 * k).sum::<usize>());
        // The smallest bank (the source, 10 B) merged into the largest donor bank: its samples
        // now ride along with that bank; counted once even if the source's bank also plays.
        let source = banks[0].clone();
        banks[19].extend(source.clone());
        assert_eq!(worst_match_aram(&fixed, &banks), retail + 10);
        banks[18].extend(source);
        assert_eq!(worst_match_aram(&fixed, &banks), retail + 10);
    }

    #[test]
    fn aram_sizes_follow_the_library() {
        assert_eq!(aram_bytes(14), 0x20);
        assert_eq!(aram_bytes(14 * 5), 0x40);
        assert_eq!(aram_bytes(0x0200_0010), 0x20);
        let g = Group::parse(&bank(14, 0x9B, 0x13B, 2, None)).unwrap();
        assert_eq!(g.aram_bytes(), aram_bytes(42) + aram_bytes(56));
    }
}
