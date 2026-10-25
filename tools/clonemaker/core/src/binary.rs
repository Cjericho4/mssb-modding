//! Big-endian DOL, descriptor, FST and REL structures used by the game.
use crate::{Error, Result};

/// Read a big-endian game halfword, rejecting truncated input.
pub fn u16_at(data: &[u8], off: usize) -> Result<u16> {
    Ok(u16::from_be_bytes(
        data.get(off..off + 2)
            .ok_or_else(|| Error::new("truncated binary data".into()))?
            .try_into()
            .unwrap(),
    ))
}
/// Read a big-endian game word, rejecting truncated input.
pub fn u32_at(data: &[u8], off: usize) -> Result<u32> {
    Ok(u32::from_be_bytes(
        data.get(off..off + 4)
            .ok_or_else(|| Error::new("truncated binary data".into()))?
            .try_into()
            .unwrap(),
    ))
}
/// Write a big-endian word without resizing a game structure.
pub fn put_u32(data: &mut [u8], off: usize, value: u32) -> Result<()> {
    data.get_mut(off..off + 4)
        .ok_or_else(|| Error::new("truncated binary data".into()))?
        .copy_from_slice(&value.to_be_bytes());
    Ok(())
}
/// A DiskFileDesc: compression flags, expanded size/flags, disc/blob offset, packed size.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Descriptor(pub [u32; 4]);
impl Descriptor {
    /// Parse the four words of a DOL or REL resource descriptor.
    pub fn parse(data: &[u8], off: usize) -> Result<Self> {
        Ok(Self([
            u32_at(data, off)?,
            u32_at(data, off + 4)?,
            u32_at(data, off + 8)?,
            u32_at(data, off + 12)?,
        ]))
    }
    /// Encode a descriptor for a DOL/REL table.
    pub fn bytes(self) -> Vec<u8> {
        self.0.iter().flat_map(|v| v.to_be_bytes()).collect()
    }
    /// Decode a compressed resource or truncate a raw resource to its expanded size.
    pub fn expand(self, raw: &[u8]) -> Result<Vec<u8>> {
        let size = (self.0[1] & 0x0fff_ffff) as usize;
        if self.0[1] & 0x4000_0000 != 0 {
            Ok(crate::lzss::decompress(raw, size, 11, 4)?.0)
        } else {
            Ok(raw[..size.min(raw.len())].to_vec())
        }
    }
}
/// One initialized text/data section of main.dol.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Section {
    /// File offset of the section.
    pub offset: u32,
    /// Address at which the game loads it.
    pub address: u32,
    /// Number of initialized bytes.
    pub size: u32,
}
/// A mutable DOL image with RAM-addressed access to its sections.
#[derive(Clone)]
pub struct Dol {
    /// The image written back to sys/main.dol after patching.
    pub data: Vec<u8>,
    /// The initialized sections described by the DOL header.
    pub sections: Vec<Section>,
}
impl Dol {
    /// Parse the 7 text and 11 data section entries of a game DOL.
    pub fn new(data: Vec<u8>) -> Result<Self> {
        let mut sections = Vec::new();
        for i in 0..18 {
            let size = u32_at(&data, 0x90 + i * 4)?;
            if size != 0 {
                sections.push(Section {
                    offset: u32_at(&data, i * 4)?,
                    address: u32_at(&data, 0x48 + i * 4)?,
                    size,
                });
            }
        }
        Ok(Self { data, sections })
    }
    /// Translate a RAM range to a file offset, requiring one containing section.
    pub fn off(&self, ram: u32, size: usize) -> Result<usize> {
        for s in &self.sections {
            if ram >= s.address && ram as u64 + size as u64 <= s.address as u64 + s.size as u64 {
                return Ok((s.offset + ram - s.address) as usize);
            }
        }
        Err(Error::new(format!(
            "RAM address 0x{ram:08X} not inside any DOL section"
        )))
    }
    /// Read game bytes by their runtime address.
    pub fn read(&self, ram: u32, size: usize) -> Result<&[u8]> {
        let off = self.off(ram, size)?;
        self.data
            .get(off..off + size)
            .ok_or_else(|| Error::new("truncated binary data".into()))
    }
    /// Replace game bytes at their runtime address.
    pub fn write(&mut self, ram: u32, bytes: &[u8]) -> Result<()> {
        let off = self.off(ram, bytes.len())?;
        self.data
            .get_mut(off..off + bytes.len())
            .ok_or_else(|| Error::new("truncated binary data".into()))?
            .copy_from_slice(bytes);
        Ok(())
    }
    /// Read a byte from a per-character DOL table.
    pub fn u8(&self, ram: u32) -> Result<u8> {
        Ok(self.read(ram, 1)?[0])
    }
    /// Read a halfword from a per-character DOL table.
    pub fn u16(&self, ram: u32) -> Result<u16> {
        u16_at(self.read(ram, 2)?, 0)
    }
    /// Read a word from a per-character DOL table.
    pub fn u32(&self, ram: u32) -> Result<u32> {
        u32_at(self.read(ram, 4)?, 0)
    }
    /// Read a resource descriptor from a DOL table.
    pub fn desc(&self, table: u32, idx: usize) -> Result<Descriptor> {
        Descriptor::parse(self.read(table + idx as u32 * 16, 16)?, 0)
    }
    /// Replace a resource descriptor in a DOL table.
    pub fn set_desc(&mut self, table: u32, idx: usize, d: Descriptor) -> Result<()> {
        self.write(table + idx as u32 * 16, &d.bytes())
    }
    /// Read the ARAM body and optional texture-overlay indices for a character.
    pub fn pair(&self, c: usize) -> Result<(i8, i8)> {
        let b = self.read(0x800f73b8 + c as u32 * 2, 2)?;
        Ok((b[0] as i8, b[1] as i8))
    }
    /// Replace a character's ARAM body/overlay pair.
    pub fn set_pair(&mut self, c: usize, body: i8, overlay: i8) -> Result<()> {
        self.write(0x800f73b8 + c as u32 * 2, &[body as u8, overlay as u8])
    }
    /// Read family, base, unique index, base/captain flags, and colour for a character.
    pub fn family(&self, c: usize) -> Result<[u8; 6]> {
        Ok(self.read(0x800e8558 + c as u32 * 6, 6)?.try_into().unwrap())
    }
    /// Read the ARAM allocation size encoded by lis/addi in fn_800227E4.
    pub fn blob_alloc_size(&self) -> Result<u32> {
        let lis = self.u32(0x800227ec)?;
        let addi = self.u32(0x800227f4)?;
        if lis >> 16 != 0x3c60 || addi >> 16 != 0x3863 {
            return Err(Error::new(
                "fn_800227E4 ARAM allocation instructions not found (patched DOL?)".into(),
            ));
        }
        Ok(((lis & 0xffff) << 16).wrapping_add((addi as i16 as i32) as u32))
    }
    /// Patch the signed-low-halfword ARAM allocation immediates.
    pub fn set_blob_alloc_size(&mut self, size: u32) -> Result<()> {
        let lo = size & 0xffff;
        let hi = ((size >> 16) + u32::from(lo & 0x8000 != 0)) & 0xffff;
        self.write(0x800227ec, &(0x3c600000 | hi).to_be_bytes())?;
        self.write(0x800227f4, &(0x38630000 | lo).to_be_bytes())
    }
    /// Find a body-tier master entry referenced by this donor alone.
    pub fn private_entry(&self, s: usize) -> Result<Option<usize>> {
        let (body, overlay) = self.pair(s)?;
        let entry = if overlay != -1 { overlay } else { body };
        let mut users = Vec::new();
        for c in 0..54 {
            let p = self.pair(c)?;
            if p.0 == entry || p.1 == entry {
                users.push(c);
            }
        }
        Ok(if users == [s] {
            Some(entry as usize)
        } else {
            None
        })
    }
}
/// End of the furthest initialized section in a DOL header.
pub fn dol_size(header: &[u8]) -> Result<usize> {
    let mut end = 0;
    for i in 0..18 {
        let size = u32_at(header, 0x90 + i * 4)?;
        if size != 0 {
            end = end.max(u32_at(header, i * 4)? as usize + size as usize);
        }
    }
    Ok(end)
}
/// A file or directory entry from the GameCube file system table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FstEntry {
    /// Index in the FST's flat preorder table.
    pub index: usize,
    /// Whether this entry names a directory.
    pub directory: bool,
    /// Entry basename, decoded as ASCII with replacement.
    pub name: String,
    /// Disc offset for a file, parent index for a directory.
    pub offset: u32,
    /// Byte length for a file, subtree end index for a directory.
    pub size: u32,
}
/// Parse the FST's entries and string table.
pub fn fst_entries(data: &[u8]) -> Result<Vec<FstEntry>> {
    let count = u32_at(data, 8)? as usize;
    if count > data.len() / 12 {
        return Err(Error::new("truncated binary data".into()));
    }
    let strings = &data[count * 12..];
    let mut out = Vec::new();
    for i in 0..count {
        let word = u32_at(data, i * 12)?;
        let start = (word & 0xffffff) as usize;
        let name = if i == 0 {
            String::new()
        } else {
            let tail = strings
                .get(start..)
                .ok_or_else(|| Error::new("invalid FST string offset".into()))?;
            let end = tail
                .iter()
                .position(|b| *b == 0)
                .ok_or_else(|| Error::new("unterminated FST name".into()))?;
            tail[..end]
                .iter()
                .map(|b| if b.is_ascii() { *b as char } else { '\u{fffd}' })
                .collect()
        };
        out.push(FstEntry {
            index: i,
            directory: word >> 24 == 1,
            name,
            offset: u32_at(data, i * 12 + 4)?,
            size: u32_at(data, i * 12 + 8)?,
        });
    }
    Ok(out)
}
/// Resolve every FST file to its slash-separated path, preserving table order.
pub fn fst_paths(data: &[u8]) -> Result<Vec<(FstEntry, String)>> {
    let mut stack: Vec<(usize, String)> = Vec::new();
    let mut out = Vec::new();
    for e in fst_entries(data)?.into_iter().skip(1) {
        while stack.last().is_some_and(|s| e.index >= s.0) {
            stack.pop();
        }
        if e.directory {
            stack.push((e.size as usize, e.name.clone()));
        } else {
            let mut names: Vec<_> = stack.iter().map(|s| s.1.as_str()).collect();
            names.push(&e.name);
            let path = names.join("/");
            out.push((e, path));
        }
    }
    Ok(out)
}
/// Update a named file's byte length in the FST.
pub fn patch_fst_size(data: &[u8], name: &str, size: u32) -> Result<Vec<u8>> {
    for (e, path) in fst_paths(data)? {
        if path == name {
            let mut out = data.to_vec();
            put_u32(&mut out, e.index * 12 + 8, size)?;
            return Ok(out);
        }
    }
    Err(Error::new(format!("{name} not in fst.bin")))
}
/// Module ID, section locations and import relocation streams of a REL image.
#[derive(Debug)]
pub struct RelHeader {
    /// The game's module number.
    pub module: u32,
    /// File offset and size per section, with the executable bit removed.
    pub sections: Vec<(u32, u32)>,
    /// Module number and relocation stream offset per import.
    pub imports: Vec<(u32, u32)>,
}
/// Parse a Nintendo REL module header.
pub fn rel_header(data: &[u8]) -> Result<RelHeader> {
    let module = u32_at(data, 0)?;
    let mut sections = Vec::new();
    let mut imports = Vec::new();
    let so = u32_at(data, 0x10)? as usize;
    let io = u32_at(data, 0x28)? as usize;
    for i in 0..u32_at(data, 0xc)? as usize {
        sections.push((
            u32_at(data, so + i * 8)? & !1,
            u32_at(data, so + i * 8 + 4)?,
        ));
    }
    for i in 0..u32_at(data, 0x2c)? as usize / 8 {
        imports.push((u32_at(data, io + i * 8)?, u32_at(data, io + i * 8 + 4)?));
    }
    Ok(RelHeader {
        module,
        sections,
        imports,
    })
}
/// A relocation that a REL applies against itself.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Relocation {
    /// File offset of the eight-byte relocation record.
    pub entry: usize,
    /// Section being patched, or none before the first section marker.
    pub section: Option<u8>,
    /// Accumulated offset in that section.
    pub offset: u32,
    /// PowerPC relocation type.
    pub kind: u8,
    /// Section containing the referenced symbol.
    pub target_section: u8,
    /// Referenced symbol's section-relative offset.
    pub addend: u32,
}
/// Walk self-import relocation streams, handling SECTION, NOP and END records.
pub fn rel_self_relocs(data: &[u8]) -> Result<Vec<Relocation>> {
    let hdr = rel_header(data)?;
    let mut out = Vec::new();
    for (m, p) in hdr.imports {
        if m != hdr.module {
            continue;
        }
        let mut p = p as usize;
        let mut section = None;
        let mut offset = 0;
        loop {
            let delta = u16_at(data, p)?;
            let word = u16_at(data, p + 2)?;
            let kind = (word >> 8) as u8;
            let target_section = word as u8;
            let addend = u32_at(data, p + 4)?;
            if kind == 203 {
                break;
            }
            if kind == 202 {
                section = Some(target_section);
                offset = 0;
            } else {
                offset += delta as u32;
                if kind != 201 {
                    out.push(Relocation {
                        entry: p,
                        section,
                        offset,
                        kind,
                        target_section,
                        addend,
                    });
                }
            }
            p += 8;
        }
    }
    Ok(out)
}
/// Locate game.rel's data section by its retail voice-table signature.
pub fn rel_data_section(data: &[u8]) -> Result<(usize, usize)> {
    let so = u32_at(data, 0x10)? as usize;
    for i in 0..u32_at(data, 0xc)? as usize {
        let o = u32_at(data, so + i * 8)? as usize;
        let size = u32_at(data, so + i * 8 + 4)? as usize;
        if size == 0 || o & 1 != 0 || o == 0 {
            continue;
        }
        if o + 0x8170 <= data.len()
            && (0..4)
                .all(|k| u16_at(data, o + 0x8168 + k * 2).ok() == Some([324, 311, 298, 285][k]))
        {
            return Ok((o, size));
        }
    }
    Err(Error::new(
        "game.rel: .data section with the voice table not found (already patched or wrong file?)"
            .into(),
    ))
}
/// Replace every non-overlapping copy of an old descriptor, returning the number replaced.
pub fn replace_desc(data: &mut [u8], old: Descriptor, new: Descriptor) -> usize {
    let a = old.bytes();
    let b = new.bytes();
    let mut n = 0;
    let mut p = 0;
    while p + 16 <= data.len() {
        if data[p..p + 16] == a {
            data[p..p + 16].copy_from_slice(&b);
            n += 1;
            p += 16;
        } else {
            p += 1;
        }
    }
    n
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn section_boundaries() {
        let mut data = vec![0; 0x120];
        put_u32(&mut data, 0, 0x100).unwrap();
        put_u32(&mut data, 0x48, 0x80001000).unwrap();
        put_u32(&mut data, 0x90, 0x20).unwrap();
        let mut dol = Dol::new(data).unwrap();
        assert_eq!(dol.off(0x8000101f, 1).unwrap(), 0x11f);
        assert!(dol.off(0x8000101f, 2).is_err());
        dol.write(0x80001000, &[1, 2, 3, 4]).unwrap();
        assert_eq!(dol.u32(0x80001000).unwrap(), 0x01020304);
    }
    #[test]
    fn nested_fst() {
        let mut fst = vec![0; 48];
        put_u32(&mut fst, 0, 0x1000000).unwrap();
        put_u32(&mut fst, 8, 4).unwrap();
        put_u32(&mut fst, 12, 0x1000001).unwrap();
        put_u32(&mut fst, 20, 3).unwrap();
        put_u32(&mut fst, 24, 3).unwrap();
        put_u32(&mut fst, 36, 5).unwrap();
        fst.extend(b"\0d\0a\0b\0");
        let paths = fst_paths(&fst).unwrap();
        assert_eq!(paths[0].1, "d/a");
        assert_eq!(paths[1].1, "b");
        assert_eq!(
            u32_at(&patch_fst_size(&fst, "d/a", 123).unwrap(), 32).unwrap(),
            123
        );
    }
}
