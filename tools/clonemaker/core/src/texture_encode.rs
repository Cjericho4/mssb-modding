//! Standalone RGBA8 encoding for the CMPR and indexed GX formats used by Clone Maker.
//!
//! Buffers use GX tile order and big-endian words. Cloning uses it for painted textures
//! (`custom_textures`).
use crate::binary::{u16_at, u32_at};
use crate::texture::{Texture, ci_indices, cmpr_palette, rgb565};
use crate::{Error, Result};
use std::collections::{BTreeMap, HashMap};

type Pixel = [u8; 4];
/// Tiled pixel bytes and, for indexed textures, a separate big-endian palette.
pub type EncodedTexture = (Vec<u8>, Option<Vec<u8>>);

fn invalid(message: &str) -> Error {
    Error::new(message.into())
}
fn layout(t: &Texture) -> Result<(usize, usize, usize)> {
    if t.w == 0 || t.h == 0 || t.w > u16::MAX as usize || t.h > u16::MAX as usize {
        return Err(invalid("texture dimensions must be in 1..=65535"));
    }
    let (tw, th) = match t.fmt {
        8 | 14 => (8, 8),
        9 => (8, 4),
        _ => return Err(invalid("encoder supports GX CI4, CI8 and CMPR")),
    };
    let size = t.w.div_ceil(tw) * t.h.div_ceil(th) * 32;
    if t.size != size {
        return Err(Error::new(format!(
            "texture slot has {} bytes; {}x{} format {} requires {size}",
            t.size, t.w, t.h, t.fmt
        )));
    }
    if t.fmt != 14
        && (t.colours == 0 || t.colours > if t.fmt == 8 { 16 } else { 256 } || t.tlut_fmt > 2)
    {
        return Err(invalid(
            "invalid CI palette size or TLUT format (expected IA8, RGB565 or RGB5A3)",
        ));
    }
    Ok((tw, th, size))
}

/// Encode row-major RGBA8 into exactly the target's pixel and palette slot sizes.
///
/// `rgba` must contain `target.w * target.h * 4` bytes. Edge tiles are padded by
/// repeating the last texel. CMPR alpha below 128 becomes transparent black;
/// other alpha becomes 255. Indexed alpha follows the target TLUT's precision.
/// No offsets in `target` are dereferenced, and no existing data is modified.
pub fn encode_texture(rgba: &[u8], target: &Texture) -> Result<EncodedTexture> {
    let (tw, th, size) = layout(target)?;
    let expected = target
        .w
        .checked_mul(target.h)
        .and_then(|n| n.checked_mul(4))
        .ok_or_else(|| invalid("RGBA dimensions overflow"))?;
    if rgba.len() != expected {
        return Err(Error::new(format!(
            "expected {}x{} RGBA8 ({expected} bytes), got {} bytes",
            target.w,
            target.h,
            rgba.len()
        )));
    }
    let pixels = rgba.as_chunks::<4>().0;
    let at = |x: usize, y: usize| pixels[y.min(target.h - 1) * target.w + x.min(target.w - 1)];
    let mut data = Vec::with_capacity(size);
    if target.fmt == 14 {
        let mut cache = HashMap::new();
        for ty in (0..target.h).step_by(8) {
            for tx in (0..target.w).step_by(8) {
                for (by, bx) in [(0, 0), (0, 4), (4, 0), (4, 4)] {
                    let block = std::array::from_fn(|i| at(tx + bx + i % 4, ty + by + i / 4));
                    data.extend_from_slice(
                        cache.entry(block).or_insert_with(|| cmpr_block(&block)),
                    );
                }
            }
        }
        return Ok((data, None));
    }
    let palette = quantize(pixels, target.colours, target.tlut_fmt);
    let decoded: Vec<_> = palette
        .iter()
        .map(|&v| palette_pixel(v, target.tlut_fmt))
        .collect();
    let mut indices = HashMap::new();
    for &p in pixels {
        indices
            .entry(p)
            .or_insert_with(|| nearest(p, &decoded) as u8);
    }
    for ty in (0..target.h).step_by(th) {
        for tx in (0..target.w).step_by(tw) {
            for y in 0..th {
                for x in (0..tw).step_by(if target.fmt == 8 { 2 } else { 1 }) {
                    let k = indices[&at(tx + x, ty + y)];
                    data.push(if target.fmt == 8 {
                        (k << 4) | indices[&at(tx + x + 1, ty + y)]
                    } else {
                        k
                    });
                }
            }
        }
    }
    Ok((
        data,
        Some(palette.into_iter().flat_map(u16::to_be_bytes).collect()),
    ))
}

/// Decode uncomposited row-major RGBA8 for encoding, export and quality measurements.
///
/// Unlike the preview decoder, this preserves palette alpha and returns transparent
/// black for CMPR's transparent index. Input is pixels followed immediately by TLUT.
pub fn decode_texture_rgba(data: &[u8], t: &Texture) -> Result<Vec<u8>> {
    layout(t)?;
    let required = t.size + if t.fmt == 14 { 0 } else { 2 * t.colours };
    if data.len() < required {
        return Err(invalid("truncated texture pixels or palette"));
    }
    let mut out = vec![0; t.w * t.h * 4];
    if t.fmt == 14 {
        let mut o = 0;
        for ty in (0..t.h).step_by(8) {
            for tx in (0..t.w).step_by(8) {
                for (by, bx) in [(0, 0), (0, 4), (4, 0), (4, 4)] {
                    let pal = cmpr_palette(u16_at(data, o)?, u16_at(data, o + 2)?);
                    let indices = u32_at(data, o + 4)?;
                    o += 8;
                    for i in 0..16 {
                        let (x, y) = (tx + bx + i % 4, ty + by + i / 4);
                        if x < t.w && y < t.h {
                            let p = (y * t.w + x) * 4;
                            let c = pal[((indices >> (30 - 2 * i)) & 3) as usize]
                                .map_or([0; 4], |[r, g, b]| [r, g, b, 255]);
                            out[p..p + 4].copy_from_slice(&c);
                        }
                    }
                }
            }
        }
    } else {
        let pal: Vec<_> = data[t.size..required]
            .as_chunks::<2>()
            .0
            .iter()
            .map(|v| palette_pixel(u16::from_be_bytes([v[0], v[1]]), t.tlut_fmt))
            .collect();
        for (p, k) in out
            .as_chunks_mut::<4>()
            .0
            .iter_mut()
            .zip(ci_indices(data, t)?)
        {
            p.copy_from_slice(
                pal.get(k as usize)
                    .ok_or_else(|| invalid("invalid palette index"))?,
            );
        }
    }
    Ok(out)
}

fn distance(a: Pixel, b: Pixel) -> u64 {
    (0..4)
        .map(|k| (i64::from(a[k]) - i64::from(b[k])).pow(2) as u64)
        .sum()
}
fn nearest(p: Pixel, palette: &[Pixel]) -> usize {
    (0..palette.len())
        .min_by_key(|&i| distance(p, palette[i]))
        .unwrap_or(0)
}
fn quant(v: u8, max: u16) -> u16 {
    (u16::from(v) * max + 127) / 255
}
fn endpoint(p: [u8; 3]) -> u16 {
    (quant(p[0], 31) << 11) | (quant(p[1], 63) << 5) | quant(p[2], 31)
}
fn palette_pixel(v: u16, fmt: u8) -> Pixel {
    match fmt {
        0 => [v as u8, v as u8, v as u8, (v >> 8) as u8],
        1 => {
            let [r, g, b] = rgb565(v);
            [r, g, b, 255]
        }
        _ if v & 0x8000 != 0 => {
            let expand = |v: u16| {
                let v = (v & 31) as u8;
                (v << 3) | (v >> 2)
            };
            [expand(v >> 10), expand(v >> 5), expand(v), 255]
        }
        _ => [
            ((v >> 8) & 15) as u8 * 17,
            ((v >> 4) & 15) as u8 * 17,
            (v & 15) as u8 * 17,
            (((v >> 12) & 7) * 255 / 7) as u8,
        ],
    }
}
fn palette_word(p: Pixel, fmt: u8) -> u16 {
    match fmt {
        0 => {
            (u16::from(p[3]) << 8)
                | ((299 * u32::from(p[0]) + 587 * u32::from(p[1]) + 114 * u32::from(p[2]) + 500)
                    / 1000) as u16
        }
        1 => endpoint([p[0], p[1], p[2]]),
        _ => {
            let opaque =
                0x8000 | (quant(p[0], 31) << 10) | (quant(p[1], 31) << 5) | quant(p[2], 31);
            let alpha = (quant(p[3], 7) << 12)
                | (quant(p[0], 15) << 8)
                | (quant(p[1], 15) << 4)
                | quant(p[2], 15);
            if distance(p, palette_pixel(opaque, 2)) <= distance(p, palette_pixel(alpha, 2)) {
                opaque
            } else {
                alpha
            }
        }
    }
}

fn quantize(pixels: &[Pixel], n: usize, fmt: u8) -> Vec<u16> {
    // Quantize into the actual TLUT colour space first. An already decoded CI
    // image then retains its exact representable colours, including both RGB5A3 modes.
    let mut histogram = BTreeMap::<u16, usize>::new();
    for &p in pixels {
        *histogram.entry(palette_word(p, fmt)).or_default() += 1;
    }
    let entries: Vec<_> = histogram
        .into_iter()
        .map(|(v, w)| (v, palette_pixel(v, fmt), w))
        .collect();
    if entries.len() <= n {
        let mut pal: Vec<_> = entries.iter().map(|e| e.0).collect();
        pal.resize(n, 0);
        return pal;
    }
    // Weighted median cut in RGBA, followed by Lloyd refinement.
    let feature = |p: Pixel, k: usize| p[k];
    let mut boxes = vec![(0..entries.len()).collect::<Vec<_>>()];
    while boxes.len() < n {
        let mut choice = (0, 0, 0u64);
        for (i, b) in boxes.iter().enumerate().filter(|(_, b)| b.len() > 1) {
            let weight = b.iter().map(|&j| entries[j].2 as u64).sum::<u64>();
            for k in 0..4 {
                let lo = b.iter().map(|&j| feature(entries[j].1, k)).min().unwrap();
                let hi = b.iter().map(|&j| feature(entries[j].1, k)).max().unwrap();
                let score = u64::from(hi - lo).pow(2) * weight;
                if score > choice.2 {
                    choice = (i, k, score);
                }
            }
        }
        if choice.2 == 0 {
            break;
        }
        let mut b = boxes.swap_remove(choice.0);
        b.sort_by_key(|&j| (feature(entries[j].1, choice.1), entries[j].0));
        let total = b.iter().map(|&j| entries[j].2).sum::<usize>();
        let mut acc = 0;
        let mut split = 1;
        for (i, &j) in b.iter().take(b.len() - 1).enumerate() {
            acc += entries[j].2;
            split = i + 1;
            if acc * 2 >= total {
                break;
            }
        }
        let right = b.split_off(split);
        boxes.push(b);
        boxes.push(right);
    }
    let centroid = |members: &[usize]| {
        let mut sums = [0u64; 4];
        let mut weight = 0;
        for &i in members {
            let (_, p, w) = entries[i];
            weight += w as u64;
            for (k, sum) in sums.iter_mut().enumerate() {
                *sum += u64::from(feature(p, k)) * w as u64;
            }
        }
        let p = std::array::from_fn(|k| ((sums[k] + weight / 2) / weight) as u8);
        palette_word(p, fmt)
    };
    let mut pal: Vec<_> = boxes.iter().map(|b| centroid(b)).collect();
    for _ in 0..8 {
        let decoded: Vec<_> = pal.iter().map(|&v| palette_pixel(v, fmt)).collect();
        let mut groups = vec![Vec::new(); pal.len()];
        for (i, (_, p, _)) in entries.iter().enumerate() {
            groups[nearest(*p, &decoded)].push(i);
        }
        let next: Vec<_> = groups
            .iter()
            .enumerate()
            .map(|(i, b)| if b.is_empty() { pal[i] } else { centroid(b) })
            .collect();
        let error = |palette: &[u16]| {
            let decoded: Vec<_> = palette.iter().map(|&v| palette_pixel(v, fmt)).collect();
            entries
                .iter()
                .map(|(_, p, w)| distance(*p, decoded[nearest(*p, &decoded)]) * (*w as u64))
                .sum::<u64>()
        };
        if next == pal || error(&next) >= error(&pal) {
            break;
        }
        pal = next;
    }
    pal.resize(n, 0);
    pal
}

#[derive(Clone, Copy)]
struct Fit {
    error: u64,
    a: u16,
    b: u16,
    indices: u32,
}
fn evaluate(block: &[Pixel; 16], a: u16, b: u16) -> Fit {
    let pal = cmpr_palette(a, b);
    let mut fit = Fit {
        error: 0,
        a,
        b,
        indices: 0,
    };
    for &p in block {
        let (idx, error) = if p[3] < 128 {
            (3, if a <= b { 0 } else { u64::MAX / 32 })
        } else {
            (0..if a > b { 4 } else { 3 })
                .map(|i| {
                    let c = pal[i].unwrap();
                    (
                        i,
                        (0..3)
                            .map(|k| (i64::from(p[k]) - i64::from(c[k])).pow(2) as u64)
                            .sum(),
                    )
                })
                .min_by_key(|v| v.1)
                .unwrap()
        };
        fit.error += error;
        fit.indices = (fit.indices << 2) | idx as u32;
    }
    fit
}
fn ordered(a: u16, b: u16, three: bool) -> (u16, u16) {
    if (a <= b) == three { (a, b) } else { (b, a) }
}
fn improve(block: &[Pixel; 16], best: &mut Fit, a: u16, b: u16, three: bool) {
    let (a, b) = ordered(a, b, three);
    if !three && a == b {
        return;
    }
    let fit = evaluate(block, a, b);
    if fit.error < best.error {
        *best = fit;
    }
}
fn refine(block: &[Pixel; 16], best: &mut Fit, three: bool) {
    for _ in 0..8 {
        let before = best.error;
        let weights = if three {
            [1., 0., 0.5, 0.]
        } else {
            [1., 0., 2. / 3., 1. / 3.]
        };
        let (mut aa, mut ab, mut bb) = (0., 0., 0.);
        let (mut ax, mut bx) = ([0.; 3], [0.; 3]);
        for (i, p) in block.iter().enumerate().filter(|(_, p)| p[3] >= 128) {
            let u = weights[((best.indices >> (30 - 2 * i)) & 3) as usize];
            let v = 1. - u;
            aa += u * u;
            ab += u * v;
            bb += v * v;
            for k in 0..3 {
                ax[k] += u * f64::from(p[k]);
                bx[k] += v * f64::from(p[k]);
            }
        }
        let det = aa * bb - ab * ab;
        if det > 1e-9 {
            let a = std::array::from_fn(|k| {
                ((ax[k] * bb - bx[k] * ab) / det).round().clamp(0., 255.) as u8
            });
            let b = std::array::from_fn(|k| {
                ((bx[k] * aa - ax[k] * ab) / det).round().clamp(0., 255.) as u8
            });
            improve(block, best, endpoint(a), endpoint(b), three);
        }
        // Discrete coordinate descent measures the decoder's integer palette,
        // rather than trusting continuous least squares after RGB565 rounding.
        for end in 0..2 {
            for (shift, max) in [(11, 31), (5, 63), (0, 31)] {
                let v = if end == 0 { best.a } else { best.b };
                let c = (v >> shift) & max;
                for delta in [-1i32, 1] {
                    let next = i32::from(c) + delta;
                    if !(0..=i32::from(max)).contains(&next) {
                        continue;
                    }
                    let v = (v & !(max << shift)) | ((next as u16) << shift);
                    let (a, b) = if end == 0 { (v, best.b) } else { (best.a, v) };
                    improve(block, best, a, b, three);
                }
            }
        }
        if best.error == 0 || best.error == before {
            break;
        }
    }
}
// Enumerate contiguous clusters along the principal axis. Prefix sums make
// each least-squares endpoint solve independent of the number of texels.
fn cluster_fit(block: &[Pixel; 16], axis: [f64; 3], best: &mut Fit, three: bool) {
    let mut pixels: Vec<_> = block.iter().filter(|p| p[3] >= 128).collect();
    let dot = |p: &Pixel| (0..3).map(|k| f64::from(p[k]) * axis[k]).sum::<f64>();
    pixels.sort_by(|a, b| dot(a).total_cmp(&dot(b)));
    let n = pixels.len();
    let mut prefix = vec![[0.; 3]; n + 1];
    for (i, p) in pixels.iter().enumerate() {
        prefix[i + 1] = std::array::from_fn(|k| prefix[i][k] + f64::from(p[k]));
    }
    let mut solve = |cuts: &[usize], weights: &[f64]| {
        let (mut aa, mut ab, mut bb) = (0., 0., 0.);
        let (mut ax, mut bx) = ([0.; 3], [0.; 3]);
        for (group, &u) in weights.iter().enumerate() {
            let v = 1. - u;
            let count = (cuts[group + 1] - cuts[group]) as f64;
            aa += count * u * u;
            ab += count * u * v;
            bb += count * v * v;
            for k in 0..3 {
                let sum = prefix[cuts[group + 1]][k] - prefix[cuts[group]][k];
                ax[k] += u * sum;
                bx[k] += v * sum;
            }
        }
        let det = aa * bb - ab * ab;
        if det > 1e-9 {
            let a = std::array::from_fn(|k| {
                ((ax[k] * bb - bx[k] * ab) / det).round().clamp(0., 255.) as u8
            });
            let b = std::array::from_fn(|k| {
                ((bx[k] * aa - ax[k] * ab) / det).round().clamp(0., 255.) as u8
            });
            improve(block, best, endpoint(a), endpoint(b), three);
        }
    };
    for i in 0..=n {
        for j in i..=n {
            if three {
                solve(&[0, i, j, n], &[0., 0.5, 1.]);
            } else {
                for k in j..=n {
                    solve(&[0, i, j, k, n], &[0., 1. / 3., 2. / 3., 1.]);
                }
            }
        }
    }
}

// An already decoded CMPR block has at most four unique opaque colours.
// Assign those colours to distinct palette slots, then solve the integer GX
// interpolation constraints independently for R, G and B. This also recovers
// blocks that never reference either endpoint, without a lossy approximation.
fn recover_palette(block: &[Pixel; 16], colours: &[[u8; 3]], three: bool) -> Option<Fit> {
    let weights: &[(i32, i32, i32)] = if three {
        &[(2, 0, 2), (0, 2, 2), (1, 1, 2)]
    } else {
        &[(3, 0, 3), (0, 3, 3), (2, 1, 3), (1, 2, 3)]
    };
    let slots = weights.len();
    for code in 0..slots.pow(colours.len() as u32) {
        let mut code = code;
        let mut assigned = Vec::new();
        for _ in colours {
            assigned.push(code % slots);
            code /= slots;
        }
        if (0..assigned.len()).any(|i| assigned[..i].contains(&assigned[i])) {
            continue;
        }
        let channel = |k: usize, bits: u8| {
            let levels = 1i32 << bits;
            let expanded: Vec<i32> = (0..levels)
                .map(|v| {
                    if bits == 5 {
                        (v << 3) | (v >> 2)
                    } else {
                        (v << 2) | (v >> 4)
                    }
                })
                .collect();
            let mut pairs = Vec::new();
            let (u, v, d) = weights[assigned[0]];
            let target = i32::from(colours[0][k]);
            for (a, &x) in expanded.iter().enumerate() {
                let (lo, hi) = if v == 0 {
                    if u * x / d != target {
                        continue;
                    }
                    (0, expanded.len())
                } else {
                    let low = target * d - u * x;
                    let high = (target + 1) * d - 1 - u * x;
                    (
                        expanded.partition_point(|&y| v * y < low),
                        expanded.partition_point(|&y| v * y <= high),
                    )
                };
                for (b, &y) in expanded.iter().enumerate().take(hi).skip(lo) {
                    if colours.iter().zip(&assigned).all(|(c, &slot)| {
                        let (u, v, d) = weights[slot];
                        (u * x + v * y) / d == i32::from(c[k])
                    }) {
                        pairs.push((a as u16, b as u16));
                    }
                }
            }
            pairs
        };
        let red = channel(0, 5);
        if red.is_empty() {
            continue;
        }
        let green = channel(1, 6);
        if green.is_empty() {
            continue;
        }
        let blue = channel(2, 5);
        for &(ra, rb) in &red {
            for &(ga, gb) in &green {
                for &(ba, bb) in &blue {
                    let a = (ra << 11) | (ga << 5) | ba;
                    let b = (rb << 11) | (gb << 5) | bb;
                    if (a <= b) == three {
                        return Some(evaluate(block, a, b));
                    }
                }
            }
        }
    }
    None
}

fn cmpr_block(block: &[Pixel; 16]) -> [u8; 8] {
    let mut colours = Vec::new();
    for p in block.iter().filter(|p| p[3] >= 128) {
        let c = [p[0], p[1], p[2]];
        if !colours.contains(&c) {
            colours.push(c);
        }
    }
    if colours.is_empty() {
        return [0, 0, 0, 0, 255, 255, 255, 255];
    }
    let alpha = block.iter().any(|p| p[3] < 128);
    let mut best = Fit {
        error: u64::MAX,
        a: 0,
        b: 0,
        indices: 0,
    };
    for three in [false, true] {
        if alpha && !three {
            continue;
        }
        let mut fit = Fit {
            error: u64::MAX,
            a: 0,
            b: 0,
            indices: 0,
        };
        if colours.len() <= if three { 3 } else { 4 }
            && let Some(exact) = recover_palette(block, &colours, three)
        {
            fit = exact;
        }
        // Recover retail palettes before fitting arbitrary images: endpoints may
        // be unused, so also invert each possible pair of interpolation weights.
        if fit.error != 0 && colours.len() <= 4 {
            let weights = if three {
                vec![1., 0., 0.5]
            } else {
                vec![1., 0., 2. / 3., 1. / 3.]
            };
            for x in &colours {
                for y in &colours {
                    for &u in &weights {
                        for &v in &weights {
                            if u == v {
                                continue;
                            }
                            let a = std::array::from_fn(|k| {
                                ((f64::from(x[k]) * (1. - v) - f64::from(y[k]) * (1. - u))
                                    / (u - v))
                                    .round()
                                    .clamp(0., 255.) as u8
                            });
                            let b = std::array::from_fn(|k| {
                                ((f64::from(y[k]) * u - f64::from(x[k]) * v) / (u - v))
                                    .round()
                                    .clamp(0., 255.) as u8
                            });
                            improve(block, &mut fit, endpoint(a), endpoint(b), three);
                            if fit.error == 0 {
                                break;
                            }
                        }
                        if fit.error == 0 {
                            break;
                        }
                    }
                    if fit.error == 0 {
                        break;
                    }
                }
                if fit.error == 0 {
                    break;
                }
            }
        }
        if fit.error != 0 {
            // Principal axis of the visible RGB covariance gives a stable initial
            // ordering even when the texture's main axis is not a colour channel.
            let visible: Vec<_> = block.iter().filter(|p| p[3] >= 128).collect();
            let mean: [f64; 3] = std::array::from_fn(|k| {
                visible.iter().map(|p| f64::from(p[k])).sum::<f64>() / visible.len() as f64
            });
            let mut cov = [[0.; 3]; 3];
            for p in &visible {
                for i in 0..3 {
                    for j in 0..3 {
                        cov[i][j] += (f64::from(p[i]) - mean[i]) * (f64::from(p[j]) - mean[j]);
                    }
                }
            }
            let channel = (0..3)
                .max_by(|&i, &j| cov[i][i].total_cmp(&cov[j][j]))
                .unwrap();
            let mut axis = [0.; 3];
            axis[channel] = 1.;
            for _ in 0..8 {
                let next: [f64; 3] =
                    std::array::from_fn(|i| (0..3).map(|j| cov[i][j] * axis[j]).sum());
                let norm = next.iter().map(|x| x * x).sum::<f64>().sqrt();
                if norm < 1e-9 {
                    break;
                }
                axis = next.map(|v| v / norm);
            }
            colours.sort_by(|a, b| {
                let dot = |p: &[u8; 3]| (0..3).map(|k| f64::from(p[k]) * axis[k]).sum::<f64>();
                dot(a).total_cmp(&dot(b))
            });
            improve(
                block,
                &mut fit,
                endpoint(colours[0]),
                endpoint(*colours.last().unwrap()),
                three,
            );
            cluster_fit(block, axis, &mut fit, three);
            refine(block, &mut fit, three);
        }
        if fit.error < best.error {
            best = fit;
        }
        if best.error == 0 {
            break;
        }
    }
    let mut out = [0; 8];
    out[..2].copy_from_slice(&best.a.to_be_bytes());
    out[2..4].copy_from_slice(&best.b.to_be_bytes());
    out[4..].copy_from_slice(&best.indices.to_be_bytes());
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn texture(w: usize, h: usize, fmt: u8, tlut_fmt: u8, colours: usize) -> Texture {
        Texture {
            index: 0,
            hdr: 0,
            data: 0,
            tlut: 0,
            w,
            h,
            fmt,
            colours,
            tlut_fmt,
            size: w.div_ceil(8) * h.div_ceil(if fmt == 9 { 4 } else { 8 }) * 32,
        }
    }
    fn roundtrip(pixels: &[u8], t: &Texture) -> Vec<u8> {
        let (mut data, pal) = encode_texture(pixels, t).unwrap();
        assert_eq!(data.len(), t.size);
        match pal {
            Some(pal) => {
                assert_eq!(pal.len(), 2 * t.colours);
                data.extend(pal);
            }
            None => assert_eq!(t.fmt, 14),
        }
        decode_texture_rgba(&data, t).unwrap()
    }
    fn random(state: &mut u32) -> u32 {
        *state = state.wrapping_mul(1664525).wrapping_add(1013904223);
        *state
    }

    #[test]
    fn ci_roundtrips_and_padding_all_tluts() {
        for fmt in [8, 9] {
            for tlut_fmt in 0..=2 {
                let t = texture(13, 7, fmt, tlut_fmt, if fmt == 8 { 16 } else { 256 });
                let words = match tlut_fmt {
                    0 => vec![0x00ff, 0x8040, 0xff00, 0xffff],
                    1 => vec![0, 0xf800, 0x07e0, 0x001f],
                    _ => vec![0x0123, 0x3456, 0x7abc, 0xffff],
                };
                let rgba: Vec<_> = (0..t.w * t.h)
                    .flat_map(|i| palette_pixel(words[(i + i / t.w) % words.len()], tlut_fmt))
                    .collect();
                assert_eq!(roundtrip(&rgba, &t), rgba, "fmt {fmt} TLUT {tlut_fmt}");
            }
        }
    }
    #[test]
    fn gx_byte_order_and_index_order() {
        let t = texture(8, 8, 14, 0, 0);
        let mut data = Vec::new();
        for indices in [0u32, 0x55555555, 0xaaaaaaaa, 0xffffffff] {
            data.extend([0xf8, 0, 0, 0x1f]);
            data.extend(indices.to_be_bytes());
        }
        let rgba = decode_texture_rgba(&data, &t).unwrap();
        for (x, y, want) in [
            (0, 0, [255, 0, 0, 255]),
            (4, 0, [0, 0, 255, 255]),
            (0, 4, [170, 0, 85, 255]),
            (4, 4, [85, 0, 170, 255]),
        ] {
            assert_eq!(&rgba[(y * 8 + x) * 4..(y * 8 + x + 1) * 4], &want);
        }
        let t = texture(8, 8, 8, 0, 2);
        let mut data = vec![0x01; 32];
        data.extend([0xff, 0, 0x80, 0xff]);
        let rgba = decode_texture_rgba(&data, &t).unwrap();
        assert_eq!(&rgba[..8], &[0, 0, 0, 255, 255, 255, 255, 128]);
        let (data, pal) = encode_texture(&rgba, &t).unwrap();
        assert_eq!(pal.unwrap(), [0x80, 0xff, 0xff, 0]);
        assert_eq!(data, vec![0x10; 32]);
    }
    #[test]
    fn cmpr_decoded_blocks_are_exact_even_with_unused_endpoints() {
        let mut state = 41;
        for i in 0..512 {
            let a = random(&mut state) as u16;
            let b = random(&mut state) as u16;
            let indices = match i % 4 {
                0 => 0xaaaaaaaa,
                1 => 0xffffffff,
                2 => random(&mut state) & 0xaaaaaaaa,
                _ => random(&mut state),
            };
            let pal = cmpr_palette(a, b);
            let block: [Pixel; 16] = std::array::from_fn(|i| {
                pal[((indices >> (30 - 2 * i)) & 3) as usize]
                    .map_or([0; 4], |[r, g, b]| [r, g, b, 255])
            });
            let encoded = cmpr_block(&block);
            let t = texture(8, 8, 14, 0, 0);
            let bytes = encoded.repeat(4);
            let actual = decode_texture_rgba(&bytes, &t).unwrap();
            for (j, p) in block.iter().enumerate() {
                let off = ((j / 4) * 8 + j % 4) * 4;
                assert_eq!(
                    &actual[off..off + 4],
                    p,
                    "block {i}, endpoints {a:04x} {b:04x}"
                );
            }
        }
    }
    #[test]
    fn cmpr_alpha_threshold_and_odd_dimensions() {
        let t = texture(9, 5, 14, 0, 0);
        let rgba: Vec<_> = (0..t.w * t.h)
            .flat_map(|i| [255, 0, 0, if i % 2 == 0 { 127 } else { 128 }])
            .collect();
        let actual = roundtrip(&rgba, &t);
        for (i, p) in actual.as_chunks::<4>().0.iter().enumerate() {
            assert_eq!(
                p,
                if i % 2 == 0 {
                    &[0; 4]
                } else {
                    &[255, 0, 0, 255]
                }
            );
        }
        let black = vec![0; 8 * 8 * 4];
        assert_eq!(roundtrip(&black, &texture(8, 8, 14, 0, 0)), black);
        let opaque: Vec<_> = (0..64).flat_map(|_| [0, 0, 0, 255]).collect();
        assert_eq!(roundtrip(&opaque, &texture(8, 8, 14, 0, 0)), opaque);
    }
    #[test]
    fn rgb5a3_uses_both_encodings() {
        assert_eq!(palette_word([255; 4], 2), 0xffff);
        assert_eq!(palette_word([17, 34, 51, 109], 2), 0x3123);
        for fmt in 0..=2 {
            for v in 0..=u16::MAX {
                let p = palette_pixel(v, fmt);
                assert_eq!(
                    palette_pixel(palette_word(p, fmt), fmt),
                    p,
                    "TLUT {fmt} word {v:04x}"
                );
            }
        }
    }
    #[test]
    fn quantizer_reduces_rich_rgba_and_respects_palette_capacity() {
        let mut state = 11;
        let rgba: Vec<_> = (0..256)
            .flat_map(|_| random(&mut state).to_be_bytes())
            .collect();
        for fmt in [8, 9] {
            for tlut_fmt in 0..=2 {
                let t = texture(16, 16, fmt, tlut_fmt, if fmt == 8 { 16 } else { 64 });
                let actual = roundtrip(&rgba, &t);
                assert_eq!(actual.len(), rgba.len());
                assert_eq!(roundtrip(&actual, &t), actual);
            }
        }
    }
    #[test]
    fn cmpr_fit_quality_on_edited_opaque_image() {
        let t = texture(16, 16, 14, 0, 0);
        let rgba: Vec<_> = (0..256)
            .flat_map(|i| {
                [
                    (i % 16 * 17) as u8,
                    (i / 16 * 17) as u8,
                    ((i % 16 + i / 16) * 8) as u8,
                    255,
                ]
            })
            .collect();
        let actual = roundtrip(&rgba, &t);
        let mse = rgba
            .iter()
            .zip(&actual)
            .map(|(&a, &b)| (f64::from(a) - f64::from(b)).powi(2))
            .sum::<f64>()
            / rgba.len() as f64;
        let psnr = 10. * (255f64.powi(2) / mse).log10();
        assert!(psnr > 28., "PSNR {psnr}");
        assert!(actual.as_chunks::<4>().0.iter().all(|p| p[3] == 255));
    }
    #[test]
    fn ci8_uses_all_256_indices() {
        let t = texture(16, 16, 9, 0, 256);
        let rgba: Vec<_> = (0..256)
            .flat_map(|i| [i as u8, i as u8, i as u8, 255])
            .collect();
        let (data, pal) = encode_texture(&rgba, &t).unwrap();
        assert_eq!(
            ci_indices(&data, &t).unwrap(),
            (0..256).map(|i| i as u8).collect::<Vec<_>>()
        );
        assert_eq!(pal.unwrap().len(), 512);
        assert_eq!(roundtrip(&rgba, &t), rgba);
    }
    #[test]
    fn cmpr_selects_lower_error_mode_without_transparent_opaque_texels() {
        for (a, b, three) in [(0, 0xffff, true), (0xffff, 0, false)] {
            let pal = cmpr_palette(a, b);
            let block = std::array::from_fn(|i| {
                let [r, g, b] = pal[i % if three { 3 } else { 4 }].unwrap();
                [r, g, b, 255]
            });
            let encoded = cmpr_block(&block);
            let a = u16::from_be_bytes([encoded[0], encoded[1]]);
            let b = u16::from_be_bytes([encoded[2], encoded[3]]);
            assert_eq!(a <= b, three);
            let fit = evaluate(&block, a, b);
            assert_eq!(fit.error, 0);
            if three {
                let indices = u32::from_be_bytes(encoded[4..].try_into().unwrap());
                assert_eq!(crate::texture::index_counts(indices)[3], 0);
            }
        }
    }
    #[test]
    fn malformed_inputs_return_errors() {
        for fmt in [8, 9, 14] {
            let mut t = texture(8, 8, fmt, 2, 16);
            for n in [0, 1, 255, 257] {
                assert!(encode_texture(&vec![0; n], &t).is_err());
            }
            assert!(decode_texture_rgba(&[], &t).is_err());
            t.size = 0;
            assert!(encode_texture(&[0; 256], &t).is_err());
            t.w = usize::MAX;
            assert!(encode_texture(&[], &t).is_err());
            t.w = 0;
            assert!(encode_texture(&[], &t).is_err());
        }
        let mut t = texture(8, 8, 8, 2, 0);
        assert!(encode_texture(&[0; 256], &t).is_err());
        t.colours = 17;
        assert!(encode_texture(&[0; 256], &t).is_err());
        t.colours = 16;
        t.tlut_fmt = 3;
        assert!(encode_texture(&[0; 256], &t).is_err());
        t.fmt = 99;
        assert!(encode_texture(&[0; 256], &t).is_err());
        t = texture(8, 8, 8, 2, 1);
        let mut data = vec![0xff; 32];
        data.extend([0xff, 0xff]);
        assert!(decode_texture_rgba(&data, &t).is_err());
    }
}
