//! HUD portrait palettes, sprite frame maps and safe menu texture retargeting.
use crate::binary::{Descriptor, Dol, put_u32, rel_header, rel_self_relocs, u16_at, u32_at};
use crate::colour::Recolour;
use crate::texture::Texture;
use crate::{Error, Result, align};

/// A decompressed UI resource with a root-relative texture archive.
#[derive(Clone)]
pub struct TextureFile {
    /// Entire decompressed resource including sprite banks.
    pub data: Vec<u8>,
    /// Texture archive's file offset.
    pub archive: usize,
    /// Texture headers in archive order.
    pub textures: Vec<Texture>,
}
impl TextureFile {
    /// Parse the root texture archive and all its headers.
    pub fn new(data: Vec<u8>) -> Result<Self> {
        let archive = u32_at(&data, 0)? as usize;
        let textures = (0..u16_at(&data, archive)? as usize)
            .map(|i| Texture::parse(&data, archive, i))
            .collect::<Result<_>>()?;
        Ok(Self {
            data,
            archive,
            textures,
        })
    }
    /// Borrow a texture's palette.
    pub fn tlut(&self, i: usize) -> &[u8] {
        let t = &self.textures[i];
        &self.data[t.tlut..t.tlut + t.colours * 2]
    }
    /// Find the 54 charID-ordered 50x50 CI8 HUD portraits and validate their palettes.
    pub fn portraits(&self) -> Result<Vec<usize>> {
        let out: Vec<_> = self
            .textures
            .iter()
            .filter(|t| (t.w, t.h, t.fmt) == (50, 50, 9))
            .map(|t| t.index)
            .collect();
        if out.len() != 54 {
            return Err(Error::new(format!(
                "portrait file: expected 54 50x50 CI8 textures, found {}",
                out.len()
            )));
        }
        for i in &out {
            let t = &self.textures[*i];
            if t.colours != 256 || t.tlut_fmt != 2 {
                return Err(Error::new(format!(
                    "portrait {} is not CI8 with a 256-entry RGB5A3 palette",
                    t.index
                )));
            }
        }
        Ok(out)
    }
    /// Replace a donor's HUD portrait pixels and recolour the source's palette.
    pub fn copy_portrait(&mut self, s: usize, c: usize, rc: &Recolour) -> Result<()> {
        let por = self.portraits()?;
        let src = self.textures[por[c]].clone();
        let dst = self.textures[por[s]].clone();
        if src.size != dst.size {
            return Err(Error::new("portrait sizes differ".into()));
        }
        self.data
            .copy_within(src.data..src.data + src.size, dst.data);
        let pal = rc.for_palette().recolour_tlut(self.tlut(por[c]))?;
        self.data[dst.tlut..dst.tlut + 512].copy_from_slice(&pal);
        Ok(())
    }
}
/// Check that both DOL portrait descriptors agree and reference an LZSS resource.
pub fn portrait_desc(dol: &Dol) -> Result<Descriptor> {
    let d = dol.desc(crate::constants::ADDR_PORTRAIT_DESC, 0)?;
    if dol.desc(crate::constants::ADDR_PORTRAIT_DESC2, 0)? != d {
        return Err(Error::new(
            "main.dol: the two portrait-file descriptor rows disagree (patched DOL?)".into(),
        ));
    }
    if d.0[0] != 0x40b || d.0[1] & 0x40000000 == 0 {
        return Err(Error::new(
            "main.dol: portrait file descriptor is not an LZSS file".into(),
        ));
    }
    Ok(d)
}
/// Expand the type-0 templates and type-2 delta records of a menu sprite-bank sequence.
pub fn frame_textures(data: &[u8], sequence: usize) -> Result<Vec<usize>> {
    let bank = u32_at(data, 4)? as usize;
    let table = bank + u32_at(data, bank + 8)? as usize;
    if sequence >= u32_at(data, table)? as usize {
        return Err(Error::new("sequence outside sprite bank".into()));
    }
    let seq = table + u32_at(data, table + 8 + sequence * 4)? as usize;
    if u32_at(data, seq)? & 0x7fffffff != 2 {
        return Err(Error::new(
            "expected header list plus one image list".into(),
        ));
    }
    let group = seq + u32_at(data, seq + 12)? as usize;
    let count = u16_at(data, group)? as usize;
    let stride = u16_at(data, group + 2)? as usize;
    let mut pos = group + 4;
    let template = data
        .get(pos..pos + stride)
        .ok_or_else(|| Error::new("truncated sprite template".into()))?;
    let mut frames = vec![None; count];
    for _ in 0..count {
        let kind = u16_at(data, pos)? & 0x7fff;
        let frame = u16_at(data, pos + 2)? as usize;
        let mut record = template.to_vec();
        let size = match kind {
            0 => stride,
            2 => {
                record[..4].copy_from_slice(&data[pos..pos + 4]);
                let changes = u16_at(data, pos + 4)? as usize;
                let size = u16_at(data, pos + 6)? as usize * 4;
                let values = pos + size - changes * 4;
                for i in 0..changes {
                    let dst = 4 + u16_at(data, pos + 8 + i * 2)? as usize * 4;
                    if dst + 4 > stride {
                        return Err(Error::new("delta outside expanded record".into()));
                    }
                    record[dst..dst + 4].copy_from_slice(&data[values + i * 4..values + i * 4 + 4]);
                }
                size
            }
            _ => {
                return Err(Error::new(format!(
                    "unexpected record type {kind} in a character list"
                )));
            }
        };
        if frame >= count {
            return Err(Error::new("character frames are not contiguous".into()));
        }
        if frames[frame].is_some() {
            return Err(Error::new("duplicate frame".into()));
        }
        frames[frame] = Some(u16_at(&record, 6)? as usize);
        pos += size;
    }
    frames
        .into_iter()
        .map(|f| f.ok_or_else(|| Error::new("character frames are not contiguous".into())))
        .collect()
}
/// A UI texture editor that allocates private palettes only from unreferenced buffers.
pub struct MenuTextures {
    /// Editable texture resource.
    pub tf: TextureFile,
    /// Palette bytes appended when no freed buffer fits.
    pub grown: usize,
    free: Vec<(usize, usize)>,
}
impl MenuTextures {
    /// Parse a CI menu resource and reject unsupported pixel formats.
    pub fn new(data: Vec<u8>) -> Result<Self> {
        let tf = TextureFile::new(data)?;
        for t in &tf.textures {
            if ![8, 9].contains(&t.fmt) {
                return Err(Error::new(format!(
                    "menu art: texture {} has unsupported format {}",
                    t.index, t.fmt
                )));
            }
        }
        Ok(Self {
            tf,
            grown: 0,
            free: Vec::new(),
        })
    }
    fn referenced(&self, start: usize, end: usize) -> bool {
        self.tf.textures.iter().any(|t| {
            [(t.data, t.size), (t.tlut, t.colours * 2)]
                .iter()
                .any(|(s, n)| *s < end && start < s + n)
        })
    }
    fn set(&mut self, i: usize, data: Option<usize>, tlut: Option<usize>) -> Result<()> {
        let t = &mut self.tf.textures[i];
        if let Some(d) = data {
            put_u32(&mut self.tf.data, t.hdr, (d - self.tf.archive) as u32)?;
            t.data = d;
        }
        if let Some(p) = tlut {
            put_u32(&mut self.tf.data, t.hdr + 4, (p - self.tf.archive) as u32)?;
            t.tlut = p;
        }
        Ok(())
    }
    fn alloc(&mut self, n: usize) -> usize {
        for (k, (s, e)) in self.free.iter().copied().enumerate() {
            if e.saturating_sub(s) >= n && !self.referenced(s, e) {
                self.free[k] = (align(s + n, 0x20), e);
                return s;
            }
        }
        let off = align(self.tf.data.len(), 0x20);
        self.tf.data.resize(off + n, 0);
        self.grown += n;
        off
    }
    /// Retarget a donor's image and optionally install a private recoloured palette.
    pub fn retarget(&mut self, dst: usize, src: usize, tlut: Option<&[u8]>) -> Result<()> {
        let d = self.tf.textures[dst].clone();
        let s = self.tf.textures[src].clone();
        if (d.w, d.h, d.fmt, d.colours, d.tlut_fmt) != (s.w, s.h, s.fmt, s.colours, s.tlut_fmt) {
            return Err(Error::new(format!(
                "menu art: textures {dst} and {src} differ in size/format"
            )));
        }
        self.set(dst, Some(s.data), Some(s.tlut))?;
        self.free.splice(
            0..0,
            [(d.tlut, d.tlut + d.colours * 2), (d.data, d.data + d.size)],
        );
        let Some(tlut) = tlut else {
            return Ok(());
        };
        if tlut == self.tf.tlut(src) {
            return Ok(());
        }
        if tlut.len() != s.colours * 2 {
            return Err(Error::new("palette size".into()));
        }
        let off = self.alloc(tlut.len());
        self.tf.data[off..off + tlut.len()].copy_from_slice(tlut);
        self.set(dst, None, Some(off))
    }
}
/// Copy names and recoloured mini/grid portraits using the resources' validated frame mappings.
pub fn patch_menu_art(
    common: Vec<u8>,
    grid: Vec<u8>,
    c: usize,
    clones: &[(usize, Recolour)],
) -> Result<(MenuTextures, MenuTextures)> {
    let names = frame_textures(&common, 0x82)?;
    let mini = frame_textures(&common, 0x83)?;
    let portraits = frame_textures(&grid, 0x19)?;
    if names.len() < 54
        || mini.len() < 54
        || portraits.len() < 54
        || (0..54).any(|c| {
            names[c] != if c < 29 { c } else { c + 28 }
                || mini[c] != 153 + c
                || portraits[c] != 4 + c
        })
    {
        return Err(Error::new("menu art: the name/picture/portrait frame lists are not the retail ones (patched disc?)".into()));
    }
    let mut ce = MenuTextures::new(common)?;
    let mut ge = MenuTextures::new(grid)?;
    for (s, _) in clones {
        ce.retarget(names[*s], names[c], None)?;
    }
    for (s, rc) in clones {
        let pal = rc.for_palette();
        let cp = pal.recolour_tlut(ce.tf.tlut(mini[c]))?;
        let gp = pal.recolour_tlut(ge.tf.tlut(portraits[c]))?;
        ce.retarget(mini[*s], mini[c], Some(&cp))?;
        ge.retarget(portraits[*s], portraits[c], Some(&gp))?;
    }
    Ok((ce, ge))
}
fn colour_label(c: usize) -> usize {
    for (lab, ids) in [
        (1, &[13, 16, 20, 22, 25, 34, 42, 50, 52][..]),
        (2, &[21, 24, 29, 33, 44, 51, 53]),
        (3, &[23, 30, 36, 45]),
        (4, &[12, 26, 27, 31, 35, 43, 46, 49]),
        (5, &[32]),
        (6, &[47]),
    ] {
        if ids.contains(&c) {
            return lab;
        }
    }
    0
}
/// Redirect donor colour-word switch entries to the empty label and return edited addend offsets.
pub fn patch_colour_word(rel: &mut [u8], donors: &[usize]) -> Result<Vec<usize>> {
    let cases = [
        0x7559c, 0x7556c, 0x75574, 0x7557c, 0x75584, 0x7558c, 0x75594,
    ];
    let hdr = rel_header(rel)?;
    let text = hdr.sections[1].0 as usize;
    for (lab, case) in cases.iter().enumerate() {
        if u32_at(rel, text + case)? != 0x38000000 | lab as u32 {
            return Err(Error::new(format!(
                "menus.rel: fn_2_74F40's colour-word case {lab} is not li r0, {lab}"
            )));
        }
    }
    let mut found = std::collections::BTreeMap::new();
    for r in rel_self_relocs(rel)? {
        if r.section == Some(5) && (0x2d844..0x2d844 + 4 * 42).contains(&r.offset) {
            let c = 12 + (r.offset as usize - 0x2d844) / 4;
            if r.kind != 1 || r.target_section != 1 || r.addend != cases[colour_label(c)] as u32 {
                return Err(Error::new(format!(
                    "menus.rel: colour-word jump-table entry for charID {c} is not retail"
                )));
            }
            found.insert(c, r.entry);
        }
    }
    if found.keys().copied().collect::<Vec<_>>() != (12..54).collect::<Vec<_>>() {
        return Err(Error::new(
            "menus.rel: colour-word jump table not found".into(),
        ));
    }
    let mut out = Vec::new();
    for s in donors {
        if colour_label(*s) != 0 {
            let p = found[s] + 4;
            put_u32(rel, p, cases[0] as u32)?;
            out.push(p);
        }
    }
    Ok(out)
}
