//! Character model texture archives, GameCube pixel formats and exact preview decoding.
use crate::binary::{u16_at, u32_at};
use crate::{Error, Result, align};

/// One texture header in a character or menu archive.
#[derive(Debug, Clone)]
pub struct Texture {
    /// Index within the archive.
    pub index: usize,
    /// File offset of this texture header.
    pub hdr: usize,
    /// File offset of the tiled pixel bytes.
    pub data: usize,
    /// File offset of the palette, for indexed textures.
    pub tlut: usize,
    /// Texture width in texels.
    pub w: usize,
    /// Texture height in texels.
    pub h: usize,
    /// GX pixel format (8 CI4, 9 CI8, 14 CMPR).
    pub fmt: u8,
    /// Palette entries, zero for CMPR.
    pub colours: usize,
    /// GX palette format (1 RGB565, 2 RGB5A3).
    pub tlut_fmt: u8,
    /// Pixel buffer size excluding the palette.
    pub size: usize,
}
impl Texture {
    /// Read a texture header relative to its archive base.
    pub fn parse(data: &[u8], archive: usize, index: usize) -> Result<Self> {
        let hdr = archive + 4 + index * 0x20;
        let fmt = *data
            .get(hdr + 0x17)
            .ok_or_else(|| Error::new("truncated texture header".into()))?;
        let w = u16_at(data, hdr + 10)? as usize;
        let h = u16_at(data, hdr + 8)? as usize;
        let size = match fmt {
            14 => w * h / 2,
            8 => align(w, 8) * align(h, 8) / 2,
            9 => align(w, 8) * align(h, 4),
            _ => 0,
        };
        Ok(Self {
            index,
            hdr,
            data: archive + u32_at(data, hdr)? as usize,
            tlut: archive + u32_at(data, hdr + 4)? as usize,
            w,
            h,
            fmt,
            colours: if fmt == 14 {
                0
            } else {
                u16_at(data, hdr + 0x18)? as usize
            },
            tlut_fmt: data[hdr + 0x1a],
            size,
        })
    }
    /// Copy pixels and (for CI) palette in the order used by the reference.
    pub fn bytes(&self, data: &[u8]) -> Result<Vec<u8>> {
        let mut out = data
            .get(self.data..self.data + self.size)
            .ok_or_else(|| Error::new("texture runs past the archive".into()))?
            .to_vec();
        if self.colours != 0 {
            out.extend(
                data.get(self.tlut..self.tlut + 2 * self.colours)
                    .ok_or_else(|| Error::new("palette runs past the archive".into()))?,
            );
        }
        Ok(out)
    }
}
/// A mutable character GPC with texture and geometry section locations.
#[derive(Clone)]
pub struct Gpc {
    /// Entire decompressed character resource.
    pub data: Vec<u8>,
    /// Start of its texture archive.
    pub texture: usize,
    /// Start of the actor layout, also the end of its texture archive.
    pub layout: usize,
    /// Start of geometry descriptors.
    pub geo: usize,
    /// Start of skinning data.
    pub skn: usize,
    /// Parsed texture headers in archive order.
    pub textures: Vec<Texture>,
}
impl Gpc {
    /// Parse and validate supported character texture headers.
    pub fn new(data: Vec<u8>) -> Result<Self> {
        let texture = u32_at(&data, 0)? as usize;
        let layout = u32_at(&data, 4)? as usize;
        if texture == 0 || layout == 0 || layout <= texture {
            return Err(Error::new(format!(
                "not a character .gpc (root header {texture:08X} {layout:08X})"
            )));
        }
        let mut textures = Vec::new();
        for i in 0..u16_at(&data, texture)? as usize {
            let t = Texture::parse(&data, texture, i)?;
            if t.fmt != 14 {
                if ![8, 9].contains(&t.fmt) || ![1, 2].contains(&t.tlut_fmt) {
                    return Err(Error::new(format!(
                        "texture {i} is format {} (palette format {}); only CMPR, CI4 and CI8 are supported",
                        t.fmt, t.tlut_fmt
                    )));
                }
                if t.colours == 0
                    || t.colours > if t.fmt == 8 { 16 } else { 256 }
                    || t.tlut + 2 * t.colours > layout
                {
                    return Err(Error::new(format!(
                        "texture {i}: bad palette ({} colours at 0x{:X})",
                        t.colours,
                        t.tlut - texture
                    )));
                }
            }
            if t.data + t.size > layout {
                return Err(Error::new(format!("texture {i} runs past the archive")));
            }
            textures.push(t);
        }
        Ok(Self {
            geo: u32_at(&data, 8)? as usize,
            skn: u32_at(&data, 12)? as usize,
            data,
            texture,
            layout,
            textures,
        })
    }
    /// Borrow the texture archive applied as an ARAM overlay.
    pub fn archive_bytes(&self) -> &[u8] {
        &self.data[self.texture..self.layout]
    }
    /// Check root ordering and the layout skin descriptor index, avoiding the known skin crash.
    pub fn check(&self) -> Result<()> {
        let skin = u16_at(&self.data, self.layout + 20)? as usize;
        let num = u32_at(&self.data, self.geo + 12)? as usize;
        if skin != 0xffff && skin >= num {
            return Err(Error::new(format!(
                "layout skinFileID {skin} >= numDescriptors {num} (this is the 0x800233A0 crash)"
            )));
        }
        if !(self.texture < self.layout
            && self.layout < self.geo
            && self.geo < self.skn
            && self.skn <= self.data.len())
        {
            return Err(Error::new("root header offsets are not ordered".into()));
        }
        Ok(())
    }
}
/// Expand RGB565 endpoints using GX bit replication.
pub fn rgb565(c: u16) -> [u8; 3] {
    let r = (c >> 11) as u8;
    let g = ((c >> 5) & 63) as u8;
    let b = (c & 31) as u8;
    [
        (r << 3) | (r >> 2),
        (g << 2) | (g >> 4),
        (b << 3) | (b >> 2),
    ]
}
/// Quantize an RGB colour to a CMPR endpoint without dithering.
pub fn to_rgb565(c: [u8; 3]) -> u16 {
    ((c[0] as u16 >> 3) << 11) | ((c[1] as u16 >> 2) << 5) | (c[2] as u16 >> 3)
}
/// Four CMPR colours, with None representing the transparent palette entry.
pub fn cmpr_palette(c0: u16, c1: u16) -> [Option<[u8; 3]>; 4] {
    let a = rgb565(c0);
    let b = rgb565(c1);
    let mix = |x: u16, y: u16, d: u16| {
        std::array::from_fn(|i| ((x * a[i] as u16 + y * b[i] as u16) / d) as u8)
    };
    if c0 > c1 {
        [Some(a), Some(b), Some(mix(2, 1, 3)), Some(mix(1, 2, 3))]
    } else {
        [Some(a), Some(b), Some(mix(1, 1, 2)), None]
    }
}
/// Count the uses of each palette entry in a CMPR block's MSB-first index word.
pub fn index_counts(idx: u32) -> [usize; 4] {
    let mut n = [0; 4];
    for i in 0..16 {
        n[((idx >> (2 * i)) & 3) as usize] += 1;
    }
    n
}
/// Decode an RGB565 or RGB5A3 palette to exact RGBA bytes.
pub fn tlut_rgba(data: &[u8], fmt: u8) -> Result<Vec<[u8; 4]>> {
    let mut out = Vec::new();
    for p in (0..data.len()).step_by(2) {
        let v = u16_at(data, p)?;
        out.push(if fmt == 1 {
            let c = rgb565(v);
            [c[0], c[1], c[2], 255]
        } else if v & 0x8000 != 0 {
            let c = [
                ((v >> 10) & 31) as u8,
                ((v >> 5) & 31) as u8,
                (v & 31) as u8,
            ];
            [
                (c[0] << 3) | (c[0] >> 2),
                (c[1] << 3) | (c[1] >> 2),
                (c[2] << 3) | (c[2] >> 2),
                255,
            ]
        } else {
            [
                (((v >> 8) & 15) * 17) as u8,
                (((v >> 4) & 15) * 17) as u8,
                ((v & 15) * 17) as u8,
                (((v >> 12) & 7) * 255 / 7) as u8,
            ]
        });
    }
    Ok(out)
}
/// Unswizzle CI4 or CI8 pixels to a row-major array of palette indices.
pub fn ci_indices(data: &[u8], t: &Texture) -> Result<Vec<u8>> {
    let th = if t.fmt == 8 { 8 } else { 4 };
    let tiles_x = align(t.w, 8) / 8;
    let mut out = Vec::with_capacity(t.w * t.h);
    for y in 0..t.h {
        for x in 0..t.w {
            let k = ((y / th) * tiles_x + x / 8) * 8 * th + (y % th) * 8 + x % 8;
            let off = if t.fmt == 8 { k / 2 } else { k };
            let v = *data
                .get(off)
                .ok_or_else(|| Error::new("truncated CI pixels".into()))?;
            out.push(if t.fmt == 8 {
                (v >> (4 * (1 - k % 2))) & 15
            } else {
                v
            });
        }
    }
    Ok(out)
}
/// Decode texture_bytes to row-major RGB pixels, compositing transparency over a grey checker.
pub fn decode_texture(data: &[u8], t: &Texture) -> Result<Vec<u8>> {
    let checker = [[0x99; 3], [0xcc; 3]];
    let mut out = vec![0; t.w * t.h * 3];
    if t.fmt == 14 {
        let mut o = 0;
        for ty in (0..t.h).step_by(8) {
            for tx in (0..t.w).step_by(8) {
                for (by, bx) in [(0, 0), (0, 4), (4, 0), (4, 4)] {
                    let pal = cmpr_palette(u16_at(data, o)?, u16_at(data, o + 2)?);
                    let idx = u32_at(data, o + 4)?;
                    o += 8;
                    let x0 = tx + bx;
                    let y0 = ty + by;
                    if x0 >= t.w || y0 >= t.h {
                        continue;
                    }
                    for r in 0..4.min(t.h - y0) {
                        for c in 0..4.min(t.w - x0) {
                            let rgb = pal[((idx >> (30 - 8 * r - 2 * c)) & 3) as usize]
                                .unwrap_or(checker[((x0 >> 3) ^ (y0 >> 3)) & 1]);
                            let p = ((y0 + r) * t.w + x0 + c) * 3;
                            out[p..p + 3].copy_from_slice(&rgb);
                        }
                    }
                }
            }
        }
        return Ok(out);
    }
    let pal = tlut_rgba(
        data.get(t.size..t.size + 2 * t.colours)
            .ok_or_else(|| Error::new("truncated palette".into()))?,
        t.tlut_fmt,
    )?;
    for (i, k) in ci_indices(data, t)?.into_iter().enumerate() {
        let rgba = pal
            .get(k as usize)
            .ok_or_else(|| Error::new("invalid palette index".into()))?;
        let bg = checker[(((i % t.w) >> 3) ^ ((i / t.w) >> 3)) & 1];
        let a = rgba[3] as u32;
        for c in 0..3 {
            out[i * 3 + c] = ((rgba[c] as u32 * a + bg[c] as u32 * (255 - a)) / 255) as u8;
        }
    }
    Ok(out)
}
/// A small insertion-ordered histogram, preserving Python dict order for region floating-point sums.
#[derive(Default)]
pub struct ColourCounts {
    /// RGB colours and their visible texel counts, in first-use order.
    pub entries: Vec<([u8; 3], usize)>,
    positions: std::collections::HashMap<[u8; 3], usize>,
}
impl ColourCounts {
    fn add(&mut self, c: [u8; 3], n: usize) {
        if n == 0 {
            return;
        }
        if let Some(i) = self.positions.get(&c) {
            self.entries[*i].1 += n;
        } else {
            self.positions.insert(c, self.entries.len());
            self.entries.push((c, n));
        }
    }
    /// Accumulate visible texels using the reference's palette order.
    pub fn texture(&mut self, data: &[u8], t: &Texture) -> Result<()> {
        if t.fmt == 14 {
            for o in (0..data.len()).step_by(8) {
                let pal = cmpr_palette(u16_at(data, o)?, u16_at(data, o + 2)?);
                let n = index_counts(u32_at(data, o + 4)?);
                for i in 0..4 {
                    if let Some(c) = pal[i] {
                        self.add(c, n[i]);
                    }
                }
            }
        } else {
            let pal = tlut_rgba(&data[t.size..t.size + 2 * t.colours], t.tlut_fmt)?;
            let mut n = vec![0; pal.len()];
            for k in ci_indices(data, t)? {
                n[k as usize] += 1;
            }
            for (c, n) in pal.into_iter().zip(n) {
                if c[3] >= 128 {
                    self.add([c[0], c[1], c[2]], n);
                }
            }
        }
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn endpoint_modes() {
        assert_eq!(rgb565(0xffff), [255; 3]);
        assert_eq!(cmpr_palette(0, 0xffff)[3], None);
        assert_eq!(cmpr_palette(0xffff, 0)[2], Some([170; 3]));
    }
    #[test]
    fn transparent_palette() {
        assert_eq!(
            tlut_rgba(&[0, 0, 0xff, 0xff], 2).unwrap(),
            vec![[0, 0, 0, 0], [255; 4]]
        );
    }
}
