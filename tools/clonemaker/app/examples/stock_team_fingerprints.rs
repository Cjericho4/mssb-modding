//! Regenerate the hash-only catalog used by portable team exports.
//! From app/, run `cargo run --release --example stock_team_fingerprints -- <original-USA.iso>`.
//! Its standard output is the content of src/stock-team-textures.tsv.
use mssb_clone_core::{
    api, binary::Dol, custom_textures, disc::Disc, texture::Gpc, texture_encode,
};
use sha2::{Digest, Sha256};
use std::{env, fs::File, io, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = PathBuf::from(
        env::args_os()
            .nth(1)
            .ok_or("Pass an original USA ISO path.")?,
    );
    let mut hash = Sha256::new();
    io::copy(&mut File::open(&path)?, &mut hash)?;
    if format!("{:x}", hash.finalize())
        != "e3cd4dc92dd85f72b3dd10a95f2e075f612c197862b94c86466c64970f4efae9"
    {
        return Err("Expected the original USA revision 0 disc.".into());
    }
    let disc = Disc::iso(&path)?;
    let dol = Dol::new(disc.system("main.dol")?)?;
    for source in api::sources() {
        let body = Gpc::new(disc.effective_body(&dol, source.id)?)?;
        for texture in &body.textures {
            let mut pixels =
                texture_encode::decode_texture_rgba(&texture.bytes(&body.data)?, texture)?;
            for pixel in pixels.chunks_exact_mut(4) {
                if pixel[3] == 0 {
                    pixel[..3].fill(0);
                }
            }
            println!(
                "{}\t{}\t{:x}",
                source.id,
                custom_textures::file_name(texture),
                Sha256::digest(&pixels)
            );
        }
    }
    Ok(())
}
