//! Player-painted body textures (#37): export a source's textures as PNGs, read a folder of edited
//! PNGs back and encode them into every body copy a clone uses.
//!
//! Files are named `tNN_WxH.png` after the texture's index in the source's body archive. Track 0
//! and the ARAM body hold the same textures by index; the low-detail track 1 sometimes holds
//! smaller versions, which get a shrunk copy of the painted texture when the retail low-detail
//! texture really is a shrunk copy of the body texture (see [`Painted::paint`]).
use crate::texture::{Gpc, Texture};
use crate::texture_encode::{decode_texture_rgba, encode_texture};
use crate::{Error, Result};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

/// A low-detail texture counts as a shrunk copy of a body texture from this PSNR (dB) upwards.
const SHRUNK_COPY_DB: f64 = 25.0;

/// The PNG name of one body texture, e.g. `t03_64x64.png`.
pub fn file_name(t: &Texture) -> String {
    format!("t{:02}_{}x{}.png", t.index, t.w, t.h)
}

/// Parse `tNN_WxH.png` (any case) into index, width and height.
pub fn parse_name(name: &str) -> Option<(usize, usize, usize)> {
    let lower = name.to_ascii_lowercase();
    let (index, size) = lower
        .strip_suffix(".png")?
        .strip_prefix('t')?
        .split_once('_')?;
    let (w, h) = size.split_once('x')?;
    let number = |s: &str| {
        (!s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
            .then(|| s.parse().ok())
            .flatten()
    };
    Some((number(index)?, number(w)?, number(h)?))
}

/// Encode row-major RGBA8 as a PNG file.
pub fn write_png(path: &Path, w: usize, h: usize, rgba: &[u8]) -> Result<()> {
    let mut out = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut out, w as u32, h as u32);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder
            .write_header()
            .map_err(|e| Error::new(e.to_string()))?;
        writer
            .write_image_data(rgba)
            .map_err(|e| Error::new(e.to_string()))?;
    }
    fs::write(path, out).map_err(|e| Error::from(e).at_path(path))
}

/// Read any PNG (grey, palette, RGB, 16-bit, with or without alpha) as width, height and RGBA8.
pub fn read_png(path: &Path) -> Result<(usize, usize, Vec<u8>)> {
    let bytes = fs::read(path).map_err(|e| Error::from(e).at_path(path))?;
    let not_png = || {
        Error::new(format!(
            "{} is not a PNG picture. Save it as PNG (or delete it to keep the original texture).",
            display_name(path)
        ))
    };
    let mut decoder = png::Decoder::new(std::io::Cursor::new(bytes));
    decoder.set_transformations(png::Transformations::normalize_to_color8());
    let mut reader = decoder.read_info().map_err(|_| not_png())?;
    let mut buf = vec![0; reader.output_buffer_size().ok_or_else(not_png)?];
    let info = reader.next_frame(&mut buf).map_err(|_| not_png())?;
    let (w, h) = (info.width as usize, info.height as usize);
    let rows = buf[..info.buffer_size()].chunks_exact(info.line_size);
    let mut rgba = Vec::with_capacity(w * h * 4);
    for row in rows {
        for px in row.chunks_exact(info.color_type.samples()).take(w) {
            rgba.extend_from_slice(&match info.color_type {
                png::ColorType::Grayscale => [px[0], px[0], px[0], 255],
                png::ColorType::GrayscaleAlpha => [px[0], px[0], px[0], px[1]],
                png::ColorType::Rgb => [px[0], px[1], px[2], 255],
                png::ColorType::Rgba => [px[0], px[1], px[2], px[3]],
                png::ColorType::Indexed => return Err(not_png()),
            });
        }
    }
    Ok((w, h, rgba))
}

fn display_name(path: &Path) -> String {
    path.file_name().map_or_else(
        || path.display().to_string(),
        |n| n.to_string_lossy().into(),
    )
}

/// Decode one texture of a body to RGBA8, alpha kept.
fn decode(g: &Gpc, t: &Texture) -> Result<Vec<u8>> {
    decode_texture_rgba(&t.bytes(&g.data)?, t)
}

/// Pixels equal as seen in the game: the colour under fully transparent texels does not count.
fn same_pixels(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len()
        && a.as_chunks::<4>()
            .0
            .iter()
            .zip(b.as_chunks::<4>().0)
            .all(|(p, q)| p == q || p[3] == 0 && q[3] == 0)
}

/// PSNR in dB between two equally sized RGBA8 images (infinite when equal).
pub fn psnr(a: &[u8], b: &[u8]) -> f64 {
    let mse = a
        .iter()
        .zip(b)
        .map(|(&x, &y)| (f64::from(x) - f64::from(y)).powi(2))
        .sum::<f64>()
        / a.len().max(1) as f64;
    if mse == 0.0 {
        f64::INFINITY
    } else {
        10.0 * (255f64.powi(2) / mse).log10()
    }
}

/// Box-filter RGBA8 down by a whole factor; colour is weighted by alpha so edges keep their colour.
pub fn shrink(rgba: &[u8], w: usize, factor: usize) -> Vec<u8> {
    let h = rgba.len() / 4 / w;
    let (nw, nh) = (w / factor, h / factor);
    let mut out = Vec::with_capacity(nw * nh * 4);
    for y in 0..nh {
        for x in 0..nw {
            let mut sum = [0u32; 4];
            for yy in 0..factor {
                for xx in 0..factor {
                    let p = &rgba[((y * factor + yy) * w + x * factor + xx) * 4..][..4];
                    let a = u32::from(p[3]);
                    for c in 0..3 {
                        sum[c] += u32::from(p[c]) * a;
                    }
                    sum[3] += a;
                }
            }
            let n = (factor * factor) as u32;
            let colour = |c: usize| (sum[c] + sum[3] / 2).checked_div(sum[3]).unwrap_or(0) as u8;
            out.extend_from_slice(&[
                colour(0),
                colour(1),
                colour(2),
                ((sum[3] + n / 2) / n) as u8,
            ]);
        }
    }
    out
}

/// The whole-number factor by which `big` shrinks to `small`, if it does.
fn shrink_factor(big: &Texture, small: &Texture) -> Option<usize> {
    let f = big.w / small.w.max(1);
    (f >= 1 && small.w * f == big.w && small.h * f == big.h).then_some(f)
}

/// What the export writes next to the PNGs.
fn readme(source: &str, body: &Gpc) -> String {
    let list: String = body
        .textures
        .iter()
        .map(|t| format!("  {}\n", file_name(t)))
        .collect();
    format!(
        "{source}'s textures, exported by MSSB Clone Maker.\r\n\
         \r\n\
         Paint over these PNGs, then pick this folder with \"Use my textures...\" on a clone row.\r\n\
         \r\n\
         - Keep each picture's size and file name (t03_64x64.png is texture 3, 64 x 64 pixels).\r\n\
         - Transparent stays transparent. Most textures have no half-transparency: a pixel\r\n\
         \x20 there is either see-through (alpha below 128) or solid.\r\n\
         - Delete a file to keep that texture as it was (it is then recoloured like the rest).\r\n\
         - Unchanged files change nothing.\r\n\
         - Your pictures are used as painted: the clone's colour only recolours the textures\r\n\
         \x20 you did not change.\r\n\
         - Fielders and runners far away use a low-detail model. Where its texture is a smaller\r\n\
         \x20 copy of yours, a shrunk copy of your picture is used there; otherwise it keeps the\r\n\
         \x20 original.\r\n\
         \r\n\
         Textures:\r\n{}",
        list.replace('\n', "\r\n")
    )
}

/// Write every body texture of `body` (the source's original, unrecoloured in-match body) into
/// `dir` as `tNN_WxH.png`, plus a README.txt. Returns the number of PNGs written.
pub fn export(body: &Gpc, source: &str, dir: &Path) -> Result<usize> {
    fs::create_dir_all(dir).map_err(|e| Error::from(e).at_path(dir))?;
    for t in &body.textures {
        write_png(&dir.join(file_name(t)), t.w, t.h, &decode(body, t)?)?;
    }
    let path = dir.join("README.txt");
    fs::write(&path, readme(source, body)).map_err(|e| Error::from(e).at_path(&path))?;
    Ok(body.textures.len())
}

/// A folder of painted textures checked against one source: the edited pictures by texture index.
#[derive(Debug, Clone)]
pub struct Painted {
    /// The folder the pictures came from.
    pub folder: PathBuf,
    /// Number of PNGs in the folder (edited or not).
    pub files: usize,
    /// RGBA8 pictures that differ from the source's original, by body texture index.
    pub edited: BTreeMap<usize, Vec<u8>>,
}

impl Painted {
    /// Read and check every `*.png` in `folder` against `body` (the source's original body).
    /// Other files (README.txt, editor files) are ignored. Unedited pictures are dropped here,
    /// so they never touch the clone.
    pub fn load(folder: &Path, body: &Gpc, source: &str) -> Result<Self> {
        let folder_name = display_name(folder);
        let within = |e: Error| Error::new(format!("Your textures in \"{folder_name}\": {e}"));
        if !folder.is_dir() {
            return Err(within(Error::new(format!(
                "the folder {} does not exist.",
                folder.display()
            ))));
        }
        let mut names: Vec<_> = fs::read_dir(folder)
            .map_err(|e| Error::from(e).at_path(folder))?
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.is_file() && p.extension().is_some_and(|x| x.eq_ignore_ascii_case("png")))
            .collect();
        names.sort();
        if names.is_empty() {
            return Err(within(Error::new(format!(
                "there are no texture PNGs in this folder. Export {source}'s textures first, paint them, then pick that folder."
            ))));
        }
        let mut edited = BTreeMap::new();
        let mut seen = BTreeMap::new();
        for path in &names {
            let name = display_name(path);
            let texture =
                parse_name(&name).and_then(|(i, w, h)| body.textures.get(i).map(|t| (t, w, h)));
            let Some((t, w, h)) = texture else {
                return Err(within(Error::new(format!(
                    "{name} is not one of {source}'s texture names (they look like {}). Rename it back, or move it out of the folder.",
                    file_name(&body.textures[0])
                ))));
            };
            if (w, h) != (t.w, t.h) {
                return Err(within(Error::new(format!(
                    "these textures don't fit {source}: {name} is named for {w}x{h}, but {source}'s texture {} is {}x{}. Export {source}'s textures and paint those.",
                    t.index, t.w, t.h
                ))));
            }
            if let Some(other) = seen.insert(t.index, name.clone()) {
                return Err(within(Error::new(format!(
                    "{other} and {name} are both texture {}. Keep only one of them.",
                    t.index
                ))));
            }
            let (pw, ph, rgba) = read_png(path).map_err(within)?;
            if (pw, ph) != (t.w, t.h) {
                return Err(within(Error::new(format!(
                    "{name} is {pw}x{ph} pixels; it has to stay {}x{}. Resize it back to the size it was exported at.",
                    t.w, t.h
                ))));
            }
            if !same_pixels(&rgba, &decode(body, t)?) {
                edited.insert(t.index, rgba);
            }
        }
        Ok(Self {
            folder: folder.to_path_buf(),
            files: names.len(),
            edited,
        })
    }

    /// For each texture of a body copy (`original`, unrecoloured), the body texture it shows:
    /// `(index in the copy, body index, shrink factor)`. A copy's texture shows a body texture
    /// when it holds the same picture (factor 1) or a shrunk copy of it (at least 25 dB after a
    /// box filter); the closest wins, the same index on a tie. Textures with no match are left
    /// out (retail low-detail art that was drawn separately).
    pub fn plan(body: &Gpc, original: &Gpc) -> Result<Vec<(usize, usize, usize)>> {
        let pictures = body
            .textures
            .iter()
            .map(|b| decode(body, b))
            .collect::<Result<Vec<_>>>()?;
        let mut plan = Vec::new();
        for t in &original.textures {
            let ours = decode(original, t)?;
            let mut best: Option<(f64, usize, usize)> = None;
            for b in &body.textures {
                let Some(factor) = shrink_factor(b, t) else {
                    continue;
                };
                let theirs = &pictures[b.index];
                let db = if factor == 1 && same_pixels(theirs, &ours) {
                    f64::INFINITY
                } else {
                    psnr(&shrink(theirs, b.w, factor), &ours)
                };
                let better = best.is_none_or(|(d, i, _)| {
                    db > d || db == d && b.index == t.index && i != t.index
                });
                if db >= SHRUNK_COPY_DB && better {
                    best = Some((db, b.index, factor));
                }
            }
            if let Some((_, source, factor)) = best {
                plan.push((t.index, source, factor));
            }
        }
        Ok(plan)
    }

    /// The RGBA8 picture to encode for body texture `source` shrunk by `factor`, if it was edited.
    pub fn picture(&self, body: &Gpc, source: usize, factor: usize) -> Option<Vec<u8>> {
        let picture = self.edited.get(&source)?;
        Some(if factor == 1 {
            picture.clone()
        } else {
            shrink(picture, body.textures[source].w, factor)
        })
    }

    /// Replace this folder's edited textures in one body copy, after its recolour.
    ///
    /// `body` is the source's original in-match body (the textures the PNGs were exported from),
    /// `original` the unrecoloured source copy that `target` was made from (track 0, track 1 or
    /// the ARAM body). A target texture takes the painted picture when the original holds the
    /// same picture at that size, or a shrunk copy when the original is a shrunk copy of the
    /// body texture (at least 25 dB); otherwise it is left as it was. Returns a log summary.
    pub fn paint(&self, body: &Gpc, original: &Gpc, target: &mut Gpc) -> Result<String> {
        let mut replaced = Vec::new();
        let mut used = std::collections::BTreeSet::new();
        for (index, source, factor) in Self::plan(body, original)? {
            let Some(rgba) = self.picture(body, source, factor) else {
                continue;
            };
            used.insert(source);
            let t = &original.textures[index];
            let (pixels, tlut) = encode_texture(&rgba, t)?;
            let slot = &target.textures[t.index];
            target.data[slot.data..slot.data + slot.size].copy_from_slice(&pixels);
            if let Some(tlut) = tlut {
                let len = 2 * slot.colours;
                if tlut.len() != len {
                    return Err(Error::new(format!(
                        "internal error: texture {} palette is {} B, its slot {len} B",
                        t.index,
                        tlut.len()
                    )));
                }
                target.data[slot.tlut..slot.tlut + len].copy_from_slice(&tlut);
            }
            replaced.push(if factor == 1 && source == t.index {
                format!("t{:02}", t.index)
            } else if factor == 1 {
                format!("t{:02} (as t{source:02})", t.index)
            } else {
                format!("t{:02} (t{source:02} shrunk to {}x{})", t.index, t.w, t.h)
            });
        }
        let kept: Vec<_> = self
            .edited
            .keys()
            .filter(|i| !used.contains(i))
            .map(|i| format!("t{i:02}"))
            .collect();
        let mut line = format!(
            "painted {}",
            if replaced.is_empty() {
                "nothing".into()
            } else {
                replaced.join(", ")
            }
        );
        if !kept.is_empty() {
            line += &format!(
                "; {} not used here (this copy has no matching texture)",
                kept.join(", ")
            );
        }
        Ok(line)
    }
}

/// Load each clone's texture folder (`slot`, `folder`) for the source `c`, checking that every
/// slot is one of the clones. Logs one line per folder.
pub fn load_for_clones(
    folders: &[(usize, PathBuf)],
    body: &Gpc,
    c: usize,
    slots: &[usize],
    log: &mut dyn FnMut(&str),
) -> Result<std::collections::HashMap<usize, Painted>> {
    let source = crate::api::pretty_name(c);
    let mut out = std::collections::HashMap::new();
    for (slot, folder) in folders {
        if !slots.contains(slot) {
            return Err(Error::new(format!(
                "--textures {slot}: slot {slot} ({}) is not one of the clones",
                crate::constants::CHAR_NAMES[*slot]
            )));
        }
        let painted = Painted::load(folder, body, &source)?;
        log(&format!(
            "textures for slot {slot} {}: {} of {} PNGs in {} differ from {source}'s ({})",
            crate::constants::CHAR_NAMES[*slot],
            painted.edited.len(),
            painted.files,
            folder.display(),
            if painted.edited.is_empty() {
                "nothing to paint".to_string()
            } else {
                painted
                    .edited
                    .keys()
                    .map(|i| format!("t{i:02}"))
                    .collect::<Vec<_>>()
                    .join(", ")
            }
        ));
        if out.insert(*slot, painted).is_some() {
            return Err(Error::new(format!("--textures {slot} is given twice")));
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A tiny character archive with the given (w, h, fmt) textures and recognisable pixels.
    pub(crate) fn synthetic_gpc(textures: &[(usize, usize, u8)]) -> Gpc {
        let base = 0x20;
        let mut headers = vec![0u8; 4 + textures.len() * 0x20];
        headers[..2].copy_from_slice(&(textures.len() as u16).to_be_bytes());
        let mut pixels = Vec::new();
        let mut off = headers.len();
        for (i, &(w, h, fmt)) in textures.iter().enumerate() {
            let size = match fmt {
                14 => w * h / 2,
                8 => w.div_ceil(8) * 8 * h.div_ceil(8) * 8 / 2,
                _ => w.div_ceil(8) * 8 * h.div_ceil(4) * 4,
            };
            let colours = match fmt {
                14 => 0,
                8 => 16,
                _ => 256,
            };
            let hdr = &mut headers[4 + i * 0x20..][..0x20];
            hdr[0..4].copy_from_slice(&(off as u32).to_be_bytes());
            hdr[4..8].copy_from_slice(&((off + size) as u32).to_be_bytes());
            hdr[8..10].copy_from_slice(&(h as u16).to_be_bytes());
            hdr[10..12].copy_from_slice(&(w as u16).to_be_bytes());
            hdr[0x17] = fmt;
            hdr[0x18..0x1a].copy_from_slice(&(colours as u16).to_be_bytes());
            hdr[0x1a] = if fmt == 14 { 0 } else { 2 };
            let mut rgba = Vec::new();
            for y in 0..h {
                for x in 0..w {
                    rgba.extend_from_slice(&[
                        (x * 255 / w) as u8,
                        (y * 64 / h) as u8,
                        (90 + 40 * i) as u8,
                        255,
                    ]);
                }
            }
            let t = Texture {
                index: i,
                hdr: 0,
                data: 0,
                tlut: 0,
                w,
                h,
                fmt,
                colours,
                tlut_fmt: if fmt == 14 { 0 } else { 2 },
                size,
            };
            let (data, tlut) = encode_texture(&rgba, &t).unwrap();
            pixels.extend(data);
            pixels.extend(tlut.unwrap_or_default());
            off += size + 2 * colours;
        }
        let layout = base + off;
        let mut data = vec![0u8; layout + 0x40];
        data[0..4].copy_from_slice(&(base as u32).to_be_bytes());
        data[4..8].copy_from_slice(&(layout as u32).to_be_bytes());
        data[8..12].copy_from_slice(&((layout + 0x20) as u32).to_be_bytes());
        data[12..16].copy_from_slice(&((layout + 0x30) as u32).to_be_bytes());
        data[base..base + headers.len()].copy_from_slice(&headers);
        data[base + headers.len()..layout].copy_from_slice(&pixels);
        Gpc::new(data).unwrap()
    }

    #[test]
    fn names() {
        let g = synthetic_gpc(&[(8, 8, 14), (64, 32, 14)]);
        assert_eq!(file_name(&g.textures[1]), "t01_64x32.png");
        assert_eq!(parse_name("t01_64x32.png"), Some((1, 64, 32)));
        assert_eq!(parse_name("T12_8X16.PNG"), Some((12, 8, 16)));
        for bad in [
            "cap.png",
            "t1_64x32.jpg",
            "t_64x32.png",
            "t01_64.png",
            "t01_64x.png",
            "t+1_8x8.png",
        ] {
            assert_eq!(parse_name(bad), None, "{bad}");
        }
    }

    #[test]
    fn export_import_round_trip() {
        let body = synthetic_gpc(&[(8, 8, 14), (16, 16, 14), (16, 8, 8), (8, 8, 9)]);
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(export(&body, "Mario", dir.path()).unwrap(), 4);
        assert!(dir.path().join("README.txt").is_file());
        // unedited: nothing to paint, nothing changes
        let p = Painted::load(dir.path(), &body, "Mario").unwrap();
        assert_eq!((p.files, p.edited.len()), (4, 0));
        let mut target = body.clone();
        p.paint(&body, &body, &mut target).unwrap();
        assert_eq!(target.data, body.data);
        // painted: every format takes the new picture exactly as the encoder makes it
        for t in &body.textures {
            let (w, h, mut rgba) = read_png(&dir.path().join(file_name(t))).unwrap();
            for px in rgba.as_chunks_mut::<4>().0.iter_mut().take(w * h / 2) {
                px.copy_from_slice(&[255, 255, 255, 255]);
            }
            write_png(&dir.path().join(file_name(t)), w, h, &rgba).unwrap();
        }
        let p = Painted::load(dir.path(), &body, "Mario").unwrap();
        assert_eq!(p.edited.len(), 4);
        let mut target = body.clone();
        assert_eq!(
            p.paint(&body, &body, &mut target).unwrap(),
            "painted t00, t01, t02, t03"
        );
        for t in &body.textures {
            let (pixels, tlut) = encode_texture(&p.edited[&t.index], t).unwrap();
            let mut expect = pixels;
            expect.extend(tlut.unwrap_or_default());
            assert_eq!(t.bytes(&target.data).unwrap(), expect);
        }
    }

    #[test]
    fn low_detail_copy_gets_a_shrunk_picture() {
        let body = synthetic_gpc(&[(8, 8, 14), (16, 16, 14)]);
        // the low-detail copy: texture 0 as is, texture 1 a shrunk copy of the body's texture 1
        let mut far = synthetic_gpc(&[(8, 8, 14), (8, 8, 14)]);
        let small = shrink(&decode(&body, &body.textures[1]).unwrap(), 16, 2);
        let (pixels, _) = encode_texture(&small, &far.textures[1]).unwrap();
        let t = far.textures[1].clone();
        far.data[t.data..t.data + t.size].copy_from_slice(&pixels);
        let far = Gpc::new(far.data).unwrap();
        let dir = tempfile::tempdir().unwrap();
        export(&body, "Mario", dir.path()).unwrap();
        let mut white = vec![255u8; 16 * 16 * 4];
        white[..4].copy_from_slice(&[0, 0, 0, 0]);
        write_png(&dir.path().join("t01_16x16.png"), 16, 16, &white).unwrap();
        let p = Painted::load(dir.path(), &body, "Mario").unwrap();
        let mut target = far.clone();
        let line = p.paint(&body, &far, &mut target).unwrap();
        assert_eq!(line, "painted t01 (t01 shrunk to 8x8)");
        let t = &far.textures[1];
        let (pixels, _) = encode_texture(&shrink(&white, 16, 2), t).unwrap();
        assert_eq!(&target.data[t.data..t.data + t.size], &pixels[..]);
        let t0 = &far.textures[0];
        assert_eq!(
            t0.bytes(&target.data).unwrap(),
            t0.bytes(&far.data).unwrap()
        );
    }

    #[test]
    fn friendly_errors() {
        let body = synthetic_gpc(&[(8, 8, 14), (16, 16, 14)]);
        let dir = tempfile::tempdir().unwrap();
        let err = |dir: &Path| Painted::load(dir, &body, "Mario").unwrap_err().message;
        assert!(err(&dir.path().join("missing")).contains("does not exist"));
        assert!(err(dir.path()).contains("no texture PNGs"));
        export(&body, "Mario", dir.path()).unwrap();
        write_png(&dir.path().join("t01_16x16.png"), 8, 8, &[0; 256]).unwrap();
        assert!(err(dir.path()).ends_with(
            "t01_16x16.png is 8x8 pixels; it has to stay 16x16. Resize it back to the size it was exported at."
        ));
        fs::rename(
            dir.path().join("t01_16x16.png"),
            dir.path().join("t01_32x32.png"),
        )
        .unwrap();
        assert!(err(dir.path()).contains("is named for 32x32, but Mario's texture 1 is 16x16"));
        fs::remove_file(dir.path().join("t01_32x32.png")).unwrap();
        fs::write(dir.path().join("t01_16x16.png"), b"not a picture").unwrap();
        assert!(err(dir.path()).contains("t01_16x16.png is not a PNG picture"));
        fs::remove_file(dir.path().join("t01_16x16.png")).unwrap();
        fs::write(dir.path().join("cap.png"), b"").unwrap();
        assert!(err(dir.path()).starts_with("Your textures in \""));
        assert!(err(dir.path()).contains("cap.png is not one of Mario's texture names"));
        fs::remove_file(dir.path().join("cap.png")).unwrap();
        write_png(&dir.path().join("t07_8x8.png"), 8, 8, &[0; 256]).unwrap();
        assert!(err(dir.path()).contains("t07_8x8.png is not one of Mario's texture names"));
    }
}
