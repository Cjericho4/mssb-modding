//! Read-only retail format inventory and encoder quality probe (no assets are written).
use mssb_clone_core::Result;
use mssb_clone_core::binary::Dol;
use mssb_clone_core::constants::{ADDR_MENU_COMMON_DESC, ADDR_MENU_GRID_DESC};
use mssb_clone_core::disc::Disc;
use mssb_clone_core::menu::{TextureFile, frame_textures, portrait_desc};
use mssb_clone_core::texture::{Gpc, Texture, decode_texture};
use mssb_clone_core::texture_encode::{decode_texture_rgba, encode_texture};
use std::collections::BTreeMap;

#[derive(Default)]
struct Stats {
    count: usize,
    exact: usize,
    errors: Vec<f64>,
}
fn quality(
    stats: &mut BTreeMap<(String, u8), Stats>,
    label: &str,
    t: &Texture,
    bytes: &[u8],
) -> Result<()> {
    let rgba = decode_texture_rgba(bytes, t)?;
    // Cross-check against the established preview decoder where it supports
    // the TLUT. Its legacy palette decoder does not handle IA8 menu names.
    if t.fmt == 14 || t.tlut_fmt != 0 {
        let preview = decode_texture(bytes, t)?;
        for (i, p) in rgba.as_chunks::<4>().0.iter().enumerate() {
            let bg = if (((i % t.w) >> 3) ^ ((i / t.w) >> 3)) & 1 == 0 {
                0x99
            } else {
                0xcc
            };
            for k in 0..3 {
                assert_eq!(
                    preview[i * 3 + k],
                    ((u32::from(p[k]) * u32::from(p[3]) + bg * (255 - u32::from(p[3]))) / 255)
                        as u8
                );
            }
        }
    }
    let (mut encoded, palette) = encode_texture(&rgba, t)?;
    if let Some(palette) = palette {
        encoded.extend(palette);
    }
    let actual = decode_texture_rgba(&encoded, t)?;
    let entry = stats.entry((label.into(), t.fmt)).or_default();
    entry.count += 1;
    if actual == rgba {
        entry.exact += 1;
        entry.errors.push(f64::INFINITY);
    } else {
        let mse = rgba
            .iter()
            .zip(&actual)
            .map(|(&a, &b)| (f64::from(a) - f64::from(b)).powi(2))
            .sum::<f64>()
            / rgba.len() as f64;
        entry.errors.push(10. * (255f64.powi(2) / mse).log10());
    }
    Ok(())
}

fn count(counts: &mut BTreeMap<(String, u8, u8), usize>, label: &str, t: &Texture) {
    *counts
        .entry((
            label.into(),
            t.fmt,
            if t.colours == 0 { 255 } else { t.tlut_fmt },
        ))
        .or_default() += 1;
}
fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let root = args
        .iter()
        .find(|arg| arg.as_str() != "--inventory-only")
        .map_or("orig/GYQE01", String::as_str);
    let inventory = args.iter().any(|arg| arg == "--inventory-only");
    let disc = Disc::folder(root)?;
    let dol = Dol::new(disc.system("main.dol")?)?;
    let mut counts = BTreeMap::new();
    let mut stats = BTreeMap::new();
    for c in 0..54 {
        let gpc = Gpc::new(disc.effective_body(&dol, c)?)?;
        for t in &gpc.textures {
            count(&mut counts, "ARAM body", t);
            if !inventory {
                quality(&mut stats, "ARAM body", t, &t.bytes(&gpc.data)?)?;
            }
        }
        for track in 0..2 {
            let gpc = Gpc::new(disc.load_subfile(&dol, c * 19 + track)?)?;
            for t in &gpc.textures {
                count(&mut counts, &format!("track {track}"), t);
                if !inventory {
                    quality(
                        &mut stats,
                        &format!("track {track}"),
                        t,
                        &t.bytes(&gpc.data)?,
                    )?;
                }
            }
        }
    }
    for (addr, seqs, label) in [
        (ADDR_MENU_COMMON_DESC, vec![0x82, 0x83], "menu names/mini"),
        (ADDR_MENU_GRID_DESC, vec![0x19], "menu grid"),
    ] {
        let tf = TextureFile::new(disc.load("ZZZZ.dat", dol.desc(addr, 0)?, 0)?)?;
        for seq in seqs {
            for i in frame_textures(&tf.data, seq)?.into_iter().take(54) {
                count(&mut counts, label, &tf.textures[i]);
                if !inventory {
                    quality(
                        &mut stats,
                        label,
                        &tf.textures[i],
                        &tf.textures[i].bytes(&tf.data)?,
                    )?;
                }
            }
        }
    }
    let tf = TextureFile::new(disc.load("ZZZZ.dat", portrait_desc(&dol)?, 0)?)?;
    for i in tf.portraits()? {
        count(&mut counts, "HUD", &tf.textures[i]);
        if !inventory {
            quality(
                &mut stats,
                "HUD",
                &tf.textures[i],
                &tf.textures[i].bytes(&tf.data)?,
            )?;
        }
    }
    println!("resource\tGX format\tTLUT (255 = none)\tcount");
    for ((label, fmt, tlut), n) in counts {
        println!("{label}\t{fmt}\t{tlut}\t{n}");
    }
    println!("quality\tresource\tGX format\tcount\tpixel-identical\tmean PSNR dB\tworst PSNR dB");
    for ((label, fmt), s) in stats {
        let mean = if s.errors.is_empty() {
            f64::INFINITY
        } else {
            s.errors.iter().sum::<f64>() / s.errors.len() as f64
        };
        let worst = s.errors.iter().copied().fold(f64::INFINITY, f64::min);
        println!(
            "quality\t{label}\t{fmt}\t{}\t{}\t{mean:.4}\t{worst:.4}",
            s.count, s.exact
        );
    }
    Ok(())
}
