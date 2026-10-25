//! Namco's word-oriented LZSS codec, with the reference encoder's tie breaking.
use crate::{Error, Result, align};

#[derive(Default)]
struct BitWriter {
    out: Vec<u8>,
    cur: u32,
    n: u32,
}
impl BitWriter {
    fn word(&mut self) {
        self.out.extend(self.cur.to_be_bytes());
        self.cur = 0;
        self.n = 0;
    }
    fn write(&mut self, value: usize, cnt: u32) {
        let value = value as u32 & ((1 << cnt) - 1);
        let space = 32 - self.n;
        if cnt <= space {
            self.cur |= value << self.n;
            self.n += cnt;
            if self.n == 32 {
                self.word();
            }
        } else {
            let low = cnt - space;
            self.cur |= (value >> low) << self.n;
            self.word();
            self.cur = value & ((1 << low) - 1);
            self.n = low;
        }
    }
    fn finish(mut self) -> Vec<u8> {
        if self.n != 0 {
            self.word();
        }
        self.out.resize(align(self.out.len(), 32), 0);
        self.out
    }
}
struct BitReader<'a> {
    data: &'a [u8],
    pos: usize,
    buf: u32,
    n: u32,
}
impl BitReader<'_> {
    fn read(&mut self, cnt: u32) -> Result<usize> {
        if self.n >= cnt {
            let v = self.buf & ((1 << cnt) - 1);
            self.buf >>= cnt;
            self.n -= cnt;
            return Ok(v as usize);
        }
        let need = cnt - self.n;
        let v = self.buf << need;
        let bytes = self
            .data
            .get(self.pos..self.pos + 4)
            .ok_or_else(|| Error::new("compressed stream exhausted".into()))?;
        let w = u32::from_be_bytes(bytes.try_into().unwrap());
        self.pos += 4;
        self.n = 32 - need;
        self.buf = w >> need;
        Ok((v | (w & ((1 << need) - 1))) as usize)
    }
}
fn validate_bits(lb: u32, rp: u32) -> Result<()> {
    if !(1..=16).contains(&lb) || !(1..=16).contains(&rp) {
        return Err(Error::new("invalid LZSS bit widths".into()));
    }
    Ok(())
}
/// Expand exactly `size` game bytes and return the number of compressed bytes consumed.
pub fn decompress(data: &[u8], size: usize, lb: u32, rp: u32) -> Result<(Vec<u8>, usize)> {
    validate_bits(lb, rp)?;
    let mut reader = BitReader {
        data,
        pos: 0,
        buf: 0,
        n: 0,
    };
    let mut out = Vec::with_capacity(size);
    while out.len() < size {
        if reader.read(1)? != 0 {
            out.push(reader.read(8)? as u8);
        } else {
            let back = reader.read(lb)? + 1;
            let count = reader.read(rp)? + 2;
            if back > out.len() || count > size - out.len() {
                return Err(Error::new("bad back-reference".into()));
            }
            for _ in 0..count {
                out.push(out[out.len() - back]);
            }
        }
    }
    Ok((out, reader.pos))
}
fn best(
    data: &[u8],
    pos: usize,
    chain: &[usize],
    lb: u32,
    rp: u32,
    max_chain: usize,
) -> (usize, usize) {
    let limit = ((1 << rp) + 1).min(data.len() - pos);
    let mut best = (0, 0);
    for &cand in chain.iter().rev().take(max_chain) {
        if pos - cand > 1 << lb {
            break;
        }
        let mut len = 2;
        while len < limit && data[cand + len] == data[pos + len] {
            len += 1;
        }
        if len > best.0 {
            best = (len, pos - cand);
            if len == limit {
                break;
            }
        }
    }
    best
}
fn key(data: &[u8], p: usize) -> usize {
    ((data[p] as usize) << 8) | data[p + 1] as usize
}
/// Greedily encode model/overlay bytes, including the loader's 32-byte padding.
pub fn compress(data: &[u8], lb: u32, rp: u32, max_chain: usize) -> Result<Vec<u8>> {
    validate_bits(lb, rp)?;
    let mut heads = vec![Vec::new(); 65536];
    let mut bw = BitWriter::default();
    let mut pos = 0;
    while pos < data.len() {
        let (len, back) = if pos + 1 < data.len() {
            best(data, pos, &heads[key(data, pos)], lb, rp, max_chain)
        } else {
            (0, 0)
        };
        let step = if len >= 2 {
            bw.write(0, 1);
            bw.write(back - 1, lb);
            bw.write(len - 2, rp);
            len
        } else {
            bw.write(1, 1);
            bw.write(data[pos] as usize, 8);
            1
        };
        for p in pos..pos + step {
            if p + 1 < data.len() {
                let chain = &mut heads[key(data, p)];
                chain.push(p);
                if chain.len() > 256 {
                    chain.drain(..128);
                }
            }
        }
        pos += step;
    }
    Ok(bw.finish())
}
/// Optimally parse REL/UI bytes; literals win cost ties and shorter matches win match ties.
pub fn compress_optimal(data: &[u8], lb: u32, rp: u32, max_chain: usize) -> Result<Vec<u8>> {
    validate_bits(lb, rp)?;
    let n = data.len();
    let mut heads = vec![Vec::new(); 65536];
    let mut longest = vec![(0, 0); n];
    for pos in 0..n.saturating_sub(1) {
        let chain = &mut heads[key(data, pos)];
        longest[pos] = best(data, pos, chain, lb, rp, max_chain);
        chain.push(pos);
        if chain.len() > 2 * max_chain {
            chain.drain(..max_chain);
        }
    }
    let mut cost = vec![0usize; n + 1];
    let mut steps = vec![1; n];
    for pos in (0..n).rev() {
        let mut best = 9 + cost[pos + 1];
        for len in 2..=longest[pos].0 {
            let v = 1 + lb as usize + rp as usize + cost[pos + len];
            if v < best {
                best = v;
                steps[pos] = len;
            }
        }
        cost[pos] = best;
    }
    let mut bw = BitWriter::default();
    let mut pos = 0;
    while pos < n {
        let len = steps[pos];
        if len == 1 {
            bw.write(1, 1);
            bw.write(data[pos] as usize, 8);
        } else {
            bw.write(0, 1);
            bw.write(longest[pos].1 - 1, lb);
            bw.write(len - 2, rp);
        }
        pos += len;
    }
    Ok(bw.finish())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn roundtrips() {
        let mut data = Vec::new();
        for i in 0..9000 {
            data.push(((i * 123 + i / 17) % 256) as u8);
        }
        for n in [0, 1, 2, 3, 31, 32, 33, 200, 9000] {
            for bits in [(11, 4), (3, 2)] {
                for encoded in [
                    compress(&data[..n], bits.0, bits.1, 48).unwrap(),
                    compress_optimal(&data[..n], bits.0, bits.1, 256).unwrap(),
                ] {
                    assert_eq!(encoded.len() % 32, 0);
                    let (out, used) = decompress(&encoded, n, bits.0, bits.1).unwrap();
                    assert_eq!(out, data[..n]);
                    assert!(used <= encoded.len());
                }
            }
        }
    }
    #[test]
    fn invalid_streams() {
        assert_eq!(
            decompress(&[], 1, 11, 4).unwrap_err().message,
            "compressed stream exhausted"
        );
        assert_eq!(
            decompress(&[0; 4], 2, 11, 4).unwrap_err().message,
            "bad back-reference"
        );
    }
    #[test]
    fn reference_word_boundary() {
        let data: Vec<u8> = (0..20).collect();
        let bytes = compress(&data, 11, 4, 48).unwrap();
        assert_eq!(
            &bytes[..8],
            &[0x08, 0x14, 0x06, 0x01, 0x83, 0x41, 0x60, 0x93]
        );
    }
}
