//! Read-only check of a clone made with painted textures (#37), against the same clone made
//! without them. Every body copy of the clone's slot (track 0, track 1, the ARAM body) must equal
//! the plain clone's copy with the folder painted over it, and every painted texture must hold
//! exactly the encoder's output for its picture. Prints per-texture PSNR (picture vs. game).
//!
//! `custom_texture_check ORIG_DISC PLAIN.iso PAINTED.iso SOURCE SLOT FOLDER`
use mssb_clone_core::binary::Dol;
use mssb_clone_core::clone::parse_char;
use mssb_clone_core::custom_textures::{Painted, psnr};
use mssb_clone_core::disc::Disc;
use mssb_clone_core::texture::Gpc;
use mssb_clone_core::texture_encode::{decode_texture_rgba, encode_texture};
use mssb_clone_core::{Error, Result};

fn copies(disc: &Disc, c: usize) -> Result<[Gpc; 3]> {
    let dol = Dol::new(disc.system("main.dol")?)?;
    Ok([
        Gpc::new(disc.load_subfile(&dol, c * 19)?)?,
        Gpc::new(disc.load_subfile(&dol, c * 19 + 1)?)?,
        Gpc::new(disc.effective_body(&dol, c)?)?,
    ])
}

/// The colour the game shows, RGB only: transparent texels keep no colour, so they count as black.
fn seen(rgba: &[u8]) -> Vec<u8> {
    rgba.chunks(4)
        .flat_map(|p| {
            if p[3] == 0 {
                [0; 3]
            } else {
                [p[0], p[1], p[2]]
            }
        })
        .collect()
}

fn main() -> Result<()> {
    let a: Vec<String> = std::env::args().skip(1).collect();
    if a.len() != 6 {
        return Err(Error::new(
            "usage: custom_texture_check ORIG_DISC PLAIN.iso PAINTED.iso SOURCE SLOT FOLDER".into(),
        ));
    }
    let (c, slot) = (parse_char(&a[3])?, parse_char(&a[4])?);
    let orig = copies(&Disc::folder(&a[0])?, c)?;
    let plain = copies(&Disc::iso(&a[1])?, slot)?;
    let painted = copies(&Disc::iso(&a[2])?, slot)?;
    let body = &orig[2];
    let folder = Painted::load(a[5].as_ref(), body, "source")?;
    let mut ok = true;
    for (k, name) in ["track 0", "track 1", "ARAM body"].iter().enumerate() {
        let mut expect = plain[k].clone();
        let line = folder.paint(body, &orig[k], &mut expect)?;
        let same = expect.data == painted[k].data;
        ok &= same;
        println!(
            "{name}: {} ({line})",
            if same {
                "OK, = plain clone + painting"
            } else {
                "MISMATCH"
            }
        );
        for (index, source, factor) in Painted::plan(body, &orig[k])? {
            let Some(picture) = folder.picture(body, source, factor) else {
                continue;
            };
            let t = &painted[k].textures[index];
            let bytes = t.bytes(&painted[k].data)?;
            let (mut encoded, tlut) = encode_texture(&picture, t)?;
            encoded.extend(tlut.unwrap_or_default());
            let exact = encoded == bytes;
            ok &= exact;
            let game = decode_texture_rgba(&bytes, t)?;
            let alpha = picture
                .chunks(4)
                .zip(game.chunks(4))
                .filter(|(p, q)| (p[3] >= 128) == (q[3] >= 128))
                .count();
            println!(
                "  t{index:02} {}x{} from t{source:02}{}: encoder output {}, RGB PSNR {:.2} dB, alpha agrees on {:.2}% of texels",
                t.w,
                t.h,
                if factor > 1 {
                    format!(" shrunk {factor}x")
                } else {
                    String::new()
                },
                if exact { "exact" } else { "DIFFERS" },
                psnr(&seen(&picture), &seen(&game)),
                100.0 * alpha as f64 / (t.w * t.h) as f64
            );
        }
    }
    println!("{}", if ok { "ALL OK" } else { "FAILED" });
    if !ok {
        std::process::exit(1);
    }
    Ok(())
}
