//! Python-colorsys compatible f64 colour arithmetic and per-source region detection.
use crate::binary::Dol;
use crate::disc::Disc;
use crate::texture::{ColourCounts, Gpc, cmpr_palette, index_counts, rgb565, to_rgb565};
use crate::{Error, Result};
use std::collections::{HashMap, HashSet};

/// Convert normalized RGB to HSV in the operation order used by Python colorsys.
pub fn rgb_to_hsv([r, g, b]: [f64; 3]) -> [f64; 3] {
    let maxc = r.max(g).max(b);
    let minc = r.min(g).min(b);
    let rangec = maxc - minc;
    if minc == maxc {
        return [0., 0., maxc];
    }
    let s = rangec / maxc;
    let rc = (maxc - r) / rangec;
    let gc = (maxc - g) / rangec;
    let bc = (maxc - b) / rangec;
    let h = if r == maxc {
        bc - gc
    } else if g == maxc {
        2. + rc - bc
    } else {
        4. + gc - rc
    };
    [(h / 6.).rem_euclid(1.), s, maxc]
}
/// Convert normalized HSV to RGB, retaining Python's evaluation order.
pub fn hsv_to_rgb([h, s, v]: [f64; 3]) -> [f64; 3] {
    if s == 0. {
        return [v; 3];
    }
    let i = (h * 6.) as i64;
    let f = h * 6. - i as f64;
    let p = v * (1. - s);
    let q = v * (1. - s * f);
    let t = v * (1. - s * (1. - f));
    match i.rem_euclid(6) {
        0 => [v, t, p],
        1 => [q, v, p],
        2 => [p, v, t],
        3 => [p, q, v],
        4 => [t, p, v],
        _ => [v, p, q],
    }
}
fn hsv(c: [u8; 3]) -> [f64; 3] {
    rgb_to_hsv(c.map(|x| x as f64 / 255.))
}
/// Distance between two hues (0..1 turns) on the colour circle, 0..0.5.
pub fn hue_distance(a: f64, b: f64) -> f64 {
    ((a - b + 0.5).rem_euclid(1.) - 0.5).abs()
}
/// Recognize the face/hand skin range excluded from detected clothing regions.
pub fn is_skin(h: f64, s: f64, v: f64) -> bool {
    h <= 0.13 && (0.08..=0.62).contains(&s) && v >= 0.5
}
/// An HSV box that the game can recolour, with settings for shading and protected textures.
#[derive(Clone, Debug, serde::Serialize)]
pub struct Region {
    /// Human-readable colour and body-part name.
    pub name: String,
    /// Centre of the circular hue interval.
    pub hue: f64,
    /// Half-width of the hue interval, or at least 0.5 for any hue.
    pub width: f64,
    /// Lower saturation bound.
    pub minsat: f64,
    /// Upper saturation bound.
    pub maxsat: f64,
    /// Lower brightness bound.
    pub minval: f64,
    /// Upper brightness bound.
    pub maxval: f64,
    /// Skin protection: 0 none, 1 skin, 2 skin and lips.
    pub keepskin: u8,
    /// Texture indices to preserve (gloves and family-shared details).
    pub skip: HashSet<usize>,
    /// Fraction of visible body texels inside this region.
    pub weight: f64,
    /// Representative RGB swatch.
    pub rgb: [u8; 3],
    /// Typical saturation of this region's texels.
    pub sat: f64,
    /// Typical brightness of this region's texels.
    pub val: f64,
    /// Whether a bare preset selects this region.
    pub default: bool,
    /// Whether CMPR decisions use visible texels rather than endpoints.
    pub blockwise: bool,
    /// Saturation used to colour a neutral region.
    pub tint: Option<f64>,
    /// Extra saturation for pale clothing.
    pub boost: f64,
    /// Multiplier on brightness preservation.
    pub lumk: f64,
}
impl Region {
    /// Test whether a texel falls inside this region, including skin exclusions.
    pub fn contains(&self, h: f64, s: f64, v: f64) -> bool {
        if s < self.minsat
            || s > self.maxsat
            || v < self.minval
            || v > self.maxval
            || (self.width < 0.5 && hue_distance(h, self.hue) > self.width)
        {
            return false;
        }
        !(self.keepskin != 0
            && (is_skin(h, s, v)
                || (self.keepskin == 2 && (h >= 0.93 || h <= 0.02) && s <= 0.5 && v >= 0.8)))
    }
    /// Squared distance to the region's typical colour, with hue weighted four times.
    pub fn distance(&self, h: f64, s: f64, v: f64) -> f64 {
        let dh = if self.width < 0.5 {
            hue_distance(h, self.hue) * 4.
        } else {
            0.
        };
        dh * dh + (s - self.sat).powi(2) + (v - self.val).powi(2)
    }
    /// A lowercase RGB hex colour for the window's swatch.
    pub fn swatch(&self) -> String {
        format!("#{:02x}{:02x}{:02x}", self.rgb[0], self.rgb[1], self.rgb[2])
    }
}
include!("region_tables.rs");
fn percentile(mut pairs: Vec<(f64, usize)>, q: f64) -> f64 {
    pairs.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
    let total = pairs.iter().map(|p| p.1).sum::<usize>() as f64;
    let mut acc = 0.;
    for (v, w) in &pairs {
        acc += *w as f64;
        if acc >= q * total {
            return *v;
        }
    }
    pairs.last().map_or(0., |p| p.0)
}
fn hue_name(h: f64, s: f64, v: f64) -> String {
    let deg = h.rem_euclid(1.) * 360.;
    let mut name = "red";
    for (top, n) in [
        (15., "red"),
        (40., "orange"),
        (70., "yellow"),
        (165., "green"),
        (195., "cyan"),
        (255., "blue"),
        (320., "purple"),
        (345., "pink"),
        (360., "red"),
    ] {
        if deg < top {
            name = n;
            break;
        }
    }
    if ["orange", "yellow"].contains(&name) && v < 0.6 {
        "brown".into()
    } else if name == "red" && v < 0.45 {
        "dark red".into()
    } else if s < 0.5 && v > 0.7 {
        format!("light {name}")
    } else {
        name.into()
    }
}
struct Candidate {
    bin: usize,
    h: f64,
    s: f64,
    v: f64,
    n: usize,
    rgb: [u8; 3],
}
fn bin_stats(cand: &[Candidate], bins: &[usize]) -> (f64, f64) {
    let members: Vec<_> = cand.iter().filter(|c| bins.contains(&c.bin)).collect();
    (
        percentile(members.iter().map(|c| (c.s, c.n)).collect(), 0.5),
        percentile(members.iter().map(|c| (c.v, c.n)).collect(), 0.5),
    )
}
/// Detect all clothing/body colour regions, preserving Python's cluster and summation order.
pub fn detect_regions(gpc: &Gpc, char_id: usize, keep: HashSet<usize>) -> Result<Vec<Region>> {
    let mut skip = keep;
    skip.extend(keep_textures(char_id));
    for t in &gpc.textures {
        let hash = format!("{:x}", md5::compute(t.bytes(&gpc.data)?));
        if GLOVE_HASHES.contains(&&hash[..12]) {
            skip.insert(t.index);
        }
    }
    let mut counts = ColourCounts::default();
    for t in &gpc.textures {
        if !skip.contains(&t.index) {
            counts.texture(&t.bytes(&gpc.data)?, t)?;
        }
    }
    let total = (counts.entries.iter().map(|p| p.1).sum::<usize>() as f64).max(1.);
    let mut cand = Vec::new();
    let mut hist = [0.; 72];
    for &(rgb, n) in &counts.entries {
        let [h, s, v] = hsv(rgb);
        if s < 0.2 || v < 0.15 || is_skin(h, s, v) {
            continue;
        }
        let bin = (h * 72.) as usize % 72;
        hist[bin] += n as f64;
        cand.push(Candidate {
            bin,
            h,
            s,
            v,
            n,
            rgb,
        });
    }
    let on = hist.map(|x| x >= 0.0015 * total);
    let mut runs: Vec<Vec<usize>> = Vec::new();
    if on.iter().all(|x| *x) {
        runs.push((0..72).collect());
    } else if let Some(start) = on.iter().position(|x| !*x) {
        let mut cur = Vec::new();
        for k in 1..=72 {
            let i = (start + k) % 72;
            if on[i] {
                cur.push(i);
            } else if !cur.is_empty() {
                runs.push(std::mem::take(&mut cur));
            }
        }
        if !cur.is_empty() {
            runs.push(cur);
        }
    }
    let mut clusters = Vec::new();
    for run in runs {
        let mut parts = Vec::new();
        let mut cur = vec![run[0]];
        for j in 1..run.len().saturating_sub(1) {
            let i = run[j];
            let left = cur
                .iter()
                .map(|k| hist[*k])
                .fold(f64::NEG_INFINITY, f64::max);
            let right = run[j + 1..]
                .iter()
                .map(|k| hist[*k])
                .fold(f64::NEG_INFINITY, f64::max);
            if hist[i] < hist[run[j - 1]]
                && hist[i] <= hist[run[j + 1]]
                && hist[i] < 0.5 * left.min(right)
            {
                parts.push(std::mem::take(&mut cur));
            }
            cur.push(i);
        }
        if run.len() > 1 {
            cur.push(*run.last().unwrap());
        }
        parts.push(cur);
        let mut merged = vec![parts.remove(0)];
        for part in parts {
            let a = bin_stats(&cand, merged.last().unwrap());
            let b = bin_stats(&cand, &part);
            let gap = (merged.last().unwrap().len() + part.len()) as f64 / 2. / 72.;
            if (a.0 - b.0).abs() < 0.22 && (a.1 - b.1).abs() < 0.22 && gap < 0.07 {
                merged.last_mut().unwrap().extend(part);
            } else {
                merged.push(part);
            }
        }
        clusters.extend(merged);
    }
    let mut regions = Vec::new();
    for bins in clusters {
        let members: Vec<_> = cand.iter().filter(|c| bins.contains(&c.bin)).collect();
        let weight = members.iter().map(|c| c.n).sum::<usize>();
        if (weight as f64) < 0.015 * total {
            continue;
        }
        let sx = members
            .iter()
            .map(|c| (2. * std::f64::consts::PI * c.h).cos() * c.n as f64)
            .sum::<f64>();
        let sy = members
            .iter()
            .map(|c| (2. * std::f64::consts::PI * c.h).sin() * c.n as f64)
            .sum::<f64>();
        let hue = (sy.atan2(sx) / (2. * std::f64::consts::PI)).rem_euclid(1.);
        let width = percentile(
            members
                .iter()
                .map(|c| (hue_distance(c.h, hue), c.n))
                .collect(),
            0.99,
        ) + 0.015;
        let s_lo = percentile(members.iter().map(|c| (c.s, c.n)).collect(), 0.01) - 0.04;
        let v_lo = percentile(members.iter().map(|c| (c.v, c.n)).collect(), 0.01) - 0.04;
        let s_med = percentile(members.iter().map(|c| (c.s, c.n)).collect(), 0.5);
        let v_med = percentile(members.iter().map(|c| (c.v, c.n)).collect(), 0.5);
        let v_hi = percentile(members.iter().map(|c| (c.v, c.n)).collect(), 0.99);
        let rgb = std::array::from_fn(|k| {
            (members
                .iter()
                .map(|c| c.rgb[k] as usize * c.n)
                .sum::<usize>() as f64
                / weight as f64) as u8
        });
        regions.push(Region {
            name: hue_name(hue, s_med, v_med),
            hue,
            width,
            minsat: 0.2f64.max(s_lo - 0.04),
            maxsat: 1.,
            minval: 0.1f64.max(v_lo - 0.08),
            maxval: 1f64.min(v_hi + 0.08),
            keepskin: 1,
            skip: skip.clone(),
            weight: weight as f64 / total,
            rgb,
            sat: s_med,
            val: v_med,
            default: false,
            blockwise: true,
            tint: None,
            boost: 1.,
            lumk: 1.,
        });
    }
    regions.sort_by(|a, b| b.weight.total_cmp(&a.weight));
    let mut seen = HashMap::new();
    for r in &mut regions {
        let n = seen.entry(r.name.clone()).or_insert(0);
        *n += 1;
        if *n > 1 {
            r.name = format!("{} {}", r.name, n);
        }
    }
    let mut overrides = region_overrides(char_id);
    if !overrides.is_empty() {
        for o in &mut overrides {
            if o.keepskin != 0 || o.tint.is_some() {
                o.skip.extend(&skip);
            }
            o.weight = counts
                .entries
                .iter()
                .filter(|(rgb, _)| {
                    let [h, s, v] = hsv(*rgb);
                    o.contains(h, s, v)
                })
                .map(|(_, n)| *n)
                .sum::<usize>() as f64
                / total;
        }
        regions.retain(|r| {
            !overrides
                .iter()
                .any(|o| o.width < 0.5 && hue_distance(r.hue, o.hue) <= r.width + o.width)
        });
        overrides.extend(regions);
        regions = overrides;
    }
    let mut picked = Vec::new();
    for prefix in region_defaults(char_id) {
        if let Some(i) = regions.iter().enumerate().position(|(i, r)| {
            !picked.contains(&i) && prefix.split('|').any(|w| r.name.contains(w))
        }) {
            picked.push(i);
        }
    }
    if picked.is_empty() && !regions.is_empty() {
        picked.push(0);
    }
    for (i, r) in regions.iter_mut().enumerate() {
        r.default = picked.contains(&i);
        r.name = region_name(char_id, &r.name).to_owned();
    }
    let mut out = Vec::new();
    for i in picked {
        out.push(regions[i].clone());
    }
    out.extend(regions.into_iter().filter(|r| !r.default));
    Ok(out)
}
/// Detect regions while protecting texture indices shared by every member of a colour family.
pub fn source_regions(disc: &Disc, dol: &Dol, c: usize, gpc: &Gpc) -> Result<Vec<Region>> {
    let body = dol.pair(c)?.0;
    let mut members = Vec::new();
    for m in 0..54 {
        if dol.pair(m)?.0 == body {
            members.push(m);
        }
    }
    let mut keep = HashSet::new();
    if members.len() >= 2 {
        let bodies: Vec<_> = members
            .into_iter()
            .map(|m| Gpc::new(disc.effective_body(dol, m)?))
            .collect::<Result<_>>()?;
        if bodies
            .iter()
            .all(|b| b.textures.len() == bodies[0].textures.len())
        {
            for i in 0..bodies[0].textures.len() {
                let first = bodies[0].textures[i].bytes(&bodies[0].data)?;
                if bodies
                    .iter()
                    .all(|b| b.textures[i].bytes(&b.data).is_ok_and(|v| v == first))
                {
                    keep.insert(i);
                }
            }
        }
    }
    detect_regions(gpc, c, keep)
}

fn luma([r, g, b]: [f64; 3]) -> f64 {
    0.299 * r + 0.587 * g + 0.114 * b
}
fn match_luma(rgb: [f64; 3], target: f64) -> [f64; 3] {
    let y = luma(rgb);
    if y <= 0. {
        return [target; 3];
    }
    let mut out = rgb.map(|x| x * target / y);
    if out.iter().any(|x| *x > 1.) {
        let top = out.map(|x| x.min(1.));
        let yt = luma(top);
        let t = if yt < 1. {
            (target - yt) / (1. - yt)
        } else {
            0.
        };
        out = top.map(|x| x + (1. - x) * t);
    }
    out.map(|x| x.clamp(0., 1.))
}
fn parse_float(v: &str) -> Result<f64> {
    v.parse()
        .map_err(|_| Error::new(format!("could not convert string to float: {v:?}")))
}
fn parse_indices(v: &str) -> Result<Vec<usize>> {
    v.split('/')
        .filter(|s| !s.is_empty())
        .map(|s| {
            s.parse()
                .map_err(|_| Error::new(format!("invalid literal for int() with base 10: {s:?}")))
        })
        .collect()
}
fn parse_hex(v: &str) -> Result<u32> {
    u32::from_str_radix(v, 16)
        .map_err(|_| Error::new(format!("invalid literal for int() with base 16: {v:?}")))
}
/// A parsed recolour spec, resolved to this source's regions before patching its textures.
#[derive(Clone)]
pub struct Recolour {
    /// Original CLI spec, retained for log parity.
    pub spec: String,
    hue: Option<f64>,
    band_from: f64,
    band_width: f64,
    minsat: f64,
    maxsat: f64,
    minval: f64,
    maxval: f64,
    keepskin: bool,
    explicit_band: bool,
    region: Option<Vec<usize>>,
    bands: Option<Vec<usize>>,
    regions: Vec<Region>,
    skip_tex: HashSet<usize>,
    all: bool,
    sat: f64,
    val: f64,
    keeplum: f64,
    tint: Option<u32>,
    tint_strength: f64,
    solid: Option<u32>,
    tex: Option<Vec<usize>>,
    cache: HashMap<u32, u16>,
    texel_cache: HashMap<[u8; 3], bool>,
}
impl Recolour {
    /// Parse the reference's comma-separated keys and bare hue presets.
    pub fn new(spec: &str) -> Result<Self> {
        let mut rc = Self {
            spec: spec.into(),
            hue: None,
            band_from: 0.,
            band_width: 0.035,
            minsat: 0.5,
            maxsat: 1.,
            minval: 0.,
            maxval: 1.,
            keepskin: false,
            explicit_band: false,
            region: None,
            bands: None,
            regions: Vec::new(),
            skip_tex: HashSet::new(),
            all: false,
            sat: 1.,
            val: 1.,
            keeplum: 0.5,
            tint: None,
            tint_strength: 0.5,
            solid: None,
            tex: None,
            cache: HashMap::new(),
            texel_cache: HashMap::new(),
        };
        for part in spec.split(',').map(str::trim).filter(|s| !s.is_empty()) {
            let Some((k, v)) = part.split_once('=') else {
                rc.hue = Some(
                    crate::constants::PRESETS
                        .iter()
                        .find(|p| p.0 == part.to_lowercase())
                        .ok_or_else(|| Error::new(format!("bad recolour token '{part}'")))?
                        .1,
                );
                continue;
            };
            let k = k.trim().to_lowercase();
            let v = v.trim();
            if [
                "from", "width", "minsat", "maxsat", "minval", "maxval", "keepskin",
            ]
            .contains(&k.as_str())
            {
                rc.explicit_band = true;
            }
            match k.as_str() {
                "hue" => {
                    rc.hue = Some(
                        if let Some(p) = crate::constants::PRESETS
                            .iter()
                            .find(|p| p.0 == v.to_lowercase())
                        {
                            p.1
                        } else {
                            parse_float(v)?
                        },
                    )
                }
                "from" => rc.band_from = parse_float(v)?,
                "width" => rc.band_width = parse_float(v)?,
                "minsat" => rc.minsat = parse_float(v)?,
                "maxsat" => rc.maxsat = parse_float(v)?,
                "minval" => rc.minval = parse_float(v)?,
                "maxval" => rc.maxval = parse_float(v)?,
                "keepskin" => rc.keepskin = !["0", "false", "no"].contains(&v),
                "all" => rc.all = !["0", "false", "no"].contains(&v),
                "region" => rc.region = Some(parse_indices(v)?),
                "tex" => rc.tex = Some(parse_indices(v)?),
                "sat" => rc.sat = parse_float(v)?,
                "val" => rc.val = parse_float(v)?,
                "keeplum" => rc.keeplum = parse_float(v)?,
                "tint" => {
                    let (col, strength) = v.split_once(':').unwrap_or((v, ""));
                    rc.tint = Some(parse_hex(col)?);
                    if !strength.is_empty() {
                        rc.tint_strength = parse_float(strength)?;
                    }
                }
                "solid" => rc.solid = Some(parse_hex(v)?),
                _ => return Err(Error::new(format!("unknown recolour key '{k}'"))),
            }
        }
        if rc.region.is_some() && (rc.explicit_band || rc.all) {
            return Err(Error::new(format!(
                "recolour spec '{spec}': region= cannot be combined with from/width/minsat/.../all"
            )));
        }
        Ok(rc)
    }
    /// Bind region indices, or bare presets' defaults, to the source's detected regions.
    pub fn resolve(&mut self, regions: &[Region]) -> Result<()> {
        let picked = if let Some(want) = &self.region {
            let bad: Vec<_> = want
                .iter()
                .filter(|i| **i >= regions.len())
                .map(|i| i.to_string())
                .collect();
            if !bad.is_empty() {
                return Err(Error::new(format!(
                    "recolour spec '{}': the source has {} colour regions (0..{}), not {}",
                    self.spec,
                    regions.len(),
                    regions.len() as isize - 1,
                    bad.join("/")
                )));
            }
            want.clone()
        } else if !self.explicit_band && !self.all && !regions.is_empty() {
            let defaults: Vec<_> = regions
                .iter()
                .enumerate()
                .filter(|(_, r)| r.default)
                .map(|(i, _)| i)
                .collect();
            if defaults.is_empty() {
                vec![0]
            } else {
                defaults
            }
        } else {
            return Ok(());
        };
        self.skip_tex = picked
            .iter()
            .flat_map(|i| regions[*i].skip.iter().copied())
            .collect();
        self.bands = Some(picked);
        self.regions = regions.to_vec();
        self.cache.clear();
        self.texel_cache.clear();
        Ok(())
    }
    fn band_for(&self, h: f64, s: f64, v: f64) -> Option<usize> {
        let mut best = None;
        let mut distance = f64::INFINITY;
        for (i, r) in self.regions.iter().enumerate() {
            if r.contains(h, s, v) {
                let d = r.distance(h, s, v);
                if d < distance {
                    distance = d;
                    best = Some(i);
                }
            }
        }
        best.filter(|i| self.bands.as_ref().is_some_and(|b| b.contains(i)))
    }
    fn tinting(&self) -> bool {
        self.bands
            .as_ref()
            .is_some_and(|b| b.iter().all(|i| self.regions[*i].tint.is_some()))
    }
    fn in_band(&self, h: f64, s: f64, v: f64) -> bool {
        if self.bands.is_some() {
            return self.band_for(h, s, v).is_some();
        }
        if s < self.minsat {
            return false;
        }
        if self.all {
            return true;
        }
        if s > self.maxsat
            || v < self.minval
            || v > self.maxval
            || (self.keepskin && is_skin(h, s, v))
        {
            return false;
        }
        hue_distance(h, self.band_from) <= self.band_width
    }
    /// The target hue (0..1 turns), or `None` when the spec sets none.
    pub fn hue(&self) -> Option<f64> {
        self.hue.map(|h| h.rem_euclid(1.))
    }
    /// Describe the selected regions for the clone log.
    pub fn describe(&self) -> String {
        if let Some(b) = &self.bands {
            b.iter()
                .map(|i| self.regions[*i].name.clone())
                .collect::<Vec<_>>()
                .join(" + ")
        } else if self.all {
            "every strong colour".into()
        } else {
            format!("hue {:.3} +- {:.3}", self.band_from, self.band_width)
        }
    }
    fn blockwise(&self) -> bool {
        self.bands
            .as_ref()
            .is_some_and(|b| b.iter().all(|i| self.regions[*i].blockwise))
    }
    /// Widen detected colour boxes for the independently painted HUD/menu portraits.
    pub fn for_palette(&self) -> Self {
        if !self.blockwise() {
            return self.clone();
        }
        let mut rc = self.clone();
        let mut order = self.bands.clone().unwrap();
        order.extend((0..self.regions.len()).filter(|i| !self.bands.as_ref().unwrap().contains(i)));
        rc.regions = order
            .iter()
            .map(|i| {
                let mut r = self.regions[*i].clone();
                if r.tint.is_none() {
                    r.width += 0.03;
                    r.minsat = 0.15f64.max(r.minsat - 0.2);
                    r.maxsat = 1.;
                    r.minval = 0.;
                    r.maxval = 1.;
                    if r.keepskin != 0 {
                        r.keepskin = 2;
                    }
                }
                r
            })
            .collect();
        rc.bands = Some((0..self.bands.as_ref().unwrap().len()).collect());
        rc.cache.clear();
        rc.texel_cache.clear();
        rc
    }
    /// Whether this body texture may change under this spec.
    pub fn touches(&self, index: usize) -> bool {
        self.tex.as_ref().is_none_or(|t| t.contains(&index)) && !self.skip_tex.contains(&index)
    }
    fn texel_in_band(&mut self, rgb: [u8; 3]) -> bool {
        if let Some(v) = self.texel_cache.get(&rgb) {
            return *v;
        }
        let [h, s, v] = hsv(rgb);
        let hit = self.in_band(h, s, v);
        self.texel_cache.insert(rgb, hit);
        hit
    }
    fn colourise(&self, band: &Region, rgb: [u8; 3], mut v: f64) -> [u8; 3] {
        let Some(h) = self.hue else {
            return rgb;
        };
        let k = ((v - 0.04) / (band.val * 0.8 - 0.04).max(0.05)).clamp(0., 1.);
        let s = (band.tint.unwrap() * k * self.sat).min(1.);
        if band.val < 0.4 {
            v = (v * (1. + 0.5 * k)).min(1.);
        }
        hsv_to_rgb([h.rem_euclid(1.), s, v])
            .map(|x| ((x * self.val).min(1.) * 255.).round_ties_even() as u8)
    }
    /// Map an RGB pixel using f64 arithmetic and Python's round-to-even quantization.
    pub fn map_rgb8(&self, rgb: [u8; 3], force: bool) -> [u8; 3] {
        if let Some(s) = self.solid {
            return [(s >> 16) as u8, (s >> 8) as u8, s as u8];
        }
        let [mut h, mut s, v] = hsv(rgb);
        let mut band = self.band_for(h, s, v);
        if let Some(i) = band
            && self.regions[i].tint.is_some()
        {
            return self.colourise(&self.regions[i], rgb, v);
        }
        if band.is_none() && force && self.tinting() {
            for i in self.bands.as_ref().unwrap() {
                let b = &self.regions[*i];
                if s <= b.maxsat + 0.15 && b.minval - 0.1 <= v && v <= b.maxval + 0.1 {
                    return self.colourise(b, rgb, v);
                }
            }
        }
        let hit = if self.bands.is_some() {
            band.is_some() || (force && !self.tinting() && s >= 0.12 && !is_skin(h, s, v))
        } else {
            self.in_band(h, s, v) || (force && s >= 0.12 && !is_skin(h, s, v))
        };
        if hit && band.is_none() {
            band = self.bands.as_ref().and_then(|b| b.first().copied());
        }
        if hit {
            if let Some(hue) = self.hue {
                h = hue.rem_euclid(1.);
            }
            s = (s * self.sat).min(1.);
            if let Some(i) = band
                && self.regions[i].boost != 1.
            {
                s = (s * self.regions[i].boost).min(1.);
            }
        }
        let mut out = hsv_to_rgb([h, s, v]);
        if hit {
            if self.keeplum != 0. && self.hue.is_some() {
                let lum = match_luma(out, luma(rgb.map(|x| x as f64 / 255.)));
                let k = if let Some(i) = band {
                    if self.regions[i].lumk != 1. {
                        self.keeplum * self.regions[i].lumk
                    } else {
                        self.keeplum
                    }
                } else {
                    self.keeplum
                };
                for i in 0..3 {
                    out[i] += (lum[i] - out[i]) * k;
                }
            }
            out = out.map(|x| (x * self.val).min(1.));
        }
        if let Some(tint) = self.tint {
            let t = self.tint_strength;
            for (i, x) in out.iter_mut().enumerate() {
                *x = *x * (1. - t) + ((tint >> (16 - i * 8)) & 255) as f64 / 255. * t;
            }
        }
        out.map(|x| (x * 255.).round_ties_even() as u8)
    }
    /// Map a CMPR endpoint, caching by endpoint and forced-block membership.
    pub fn map565(&mut self, c: u16, force: bool) -> u16 {
        let key = c as u32 | if force { 0x10000 } else { 0 };
        if let Some(v) = self.cache.get(&key) {
            return *v;
        }
        let out = to_rgb565(self.map_rgb8(rgb565(c), force));
        self.cache.insert(key, out);
        out
    }
    /// Map an RGB5A3 palette entry while retaining its original alpha bits.
    pub fn map_rgb5a3(&self, p: u16) -> u16 {
        if p & 0x8000 != 0 {
            let c = [
                ((p >> 10) & 31) as u8,
                ((p >> 5) & 31) as u8,
                (p & 31) as u8,
            ];
            let [r, g, b] = self.map_rgb8(c.map(|x| (x << 3) | (x >> 2)), false);
            0x8000 | ((r as u16 >> 3) << 10) | ((g as u16 >> 3) << 5) | (b as u16 >> 3)
        } else {
            let a = (p >> 12) & 7;
            if a == 0 && self.tinting() {
                return p;
            }
            let [r, g, b] = self.map_rgb8(
                [
                    (((p >> 8) & 15) * 17) as u8,
                    (((p >> 4) & 15) * 17) as u8,
                    ((p & 15) * 17) as u8,
                ],
                false,
            );
            (a << 12) | ((r as u16 >> 4) << 8) | ((g as u16 >> 4) << 4) | (b as u16 >> 4)
        }
    }
    fn block_share(&mut self, pal: &[Option<[u8; 3]>; 4], n: [usize; 4]) -> f64 {
        let mut inside = 0;
        let mut total = 0;
        for i in 0..4 {
            if n[i] != 0
                && let Some(c) = pal[i]
            {
                total += n[i];
                if self.texel_in_band(c) {
                    inside += n[i];
                }
            }
        }
        if total == 0 {
            0.
        } else {
            inside as f64 / total as f64
        }
    }
    /// Recolour one CMPR block while retaining four-colour/transparent mode and index meaning.
    pub fn recolour_block(&mut self, block: &[u8]) -> Result<[u8; 8]> {
        let c0 = crate::binary::u16_at(block, 0)?;
        let c1 = crate::binary::u16_at(block, 2)?;
        let mut idx = crate::binary::u32_at(block, 4)?;
        let mut force = false;
        if self.blockwise() {
            let pal = cmpr_palette(c0, c1);
            let n = index_counts(idx);
            let share = self.block_share(&pal, n);
            if share == 0. {
                return Ok(block.try_into().unwrap());
            }
            if self.tinting() && share < 1. {
                let top = self
                    .bands
                    .as_ref()
                    .unwrap()
                    .iter()
                    .map(|i| self.regions[*i].maxval)
                    .fold(f64::NEG_INFINITY, f64::max);
                let mut strong = 0;
                let mut bright = 0;
                for i in 0..4 {
                    if n[i] != 0
                        && let Some(c) = pal[i]
                        && !self.texel_in_band(c)
                    {
                        let [_, s, v] = hsv(c);
                        if s >= 0.4 && v >= 0.2 {
                            strong += n[i];
                        } else if v >= top + 0.35 {
                            bright += n[i];
                        }
                    }
                }
                if (strong >= 2 && share <= 0.5) || bright >= 2 {
                    return Ok(block.try_into().unwrap());
                }
            }
            force = if self.tinting() {
                share >= 0.999
            } else {
                share >= 0.5
            };
        }
        let mut n0 = self.map565(c0, force);
        let mut n1 = self.map565(c1, force);
        if c0 > c1 {
            if n0 == n1 {
                n1 ^= 1;
            }
            if n0 < n1 {
                std::mem::swap(&mut n0, &mut n1);
                idx ^= 0x55555555;
            }
        } else if n0 > n1 {
            std::mem::swap(&mut n0, &mut n1);
            let mut out = 0;
            for i in 0..16 {
                let mut v = (idx >> (i * 2)) & 3;
                if v < 2 {
                    v ^= 1;
                }
                out |= v << (i * 2);
            }
            idx = out;
        }
        let mut out = [0; 8];
        out[..2].copy_from_slice(&n0.to_be_bytes());
        out[2..4].copy_from_slice(&n1.to_be_bytes());
        out[4..].copy_from_slice(&idx.to_be_bytes());
        Ok(out)
    }
    /// Change texture endpoints/palettes in a character GPC, preserving all geometry and headers.
    pub fn recolour_gpc(&mut self, gpc: &mut Gpc) -> Result<()> {
        for t in &gpc.textures {
            if !self.touches(t.index) {
                continue;
            }
            if t.fmt != 14 {
                for k in 0..t.colours {
                    let o = t.tlut + 2 * k;
                    let p = crate::binary::u16_at(&gpc.data, o)?;
                    let q = if t.tlut_fmt == 2 {
                        self.map_rgb5a3(p)
                    } else {
                        self.map565(p, false)
                    };
                    gpc.data[o..o + 2].copy_from_slice(&q.to_be_bytes());
                }
            } else {
                for o in (t.data..t.data + t.size).step_by(8) {
                    let b = self.recolour_block(&gpc.data[o..o + 8])?;
                    gpc.data[o..o + 8].copy_from_slice(&b);
                }
            }
        }
        Ok(())
    }
    /// Recolour a whole RGB5A3 palette for a HUD or menu image.
    pub fn recolour_tlut(&self, data: &[u8]) -> Result<Vec<u8>> {
        let mut out = Vec::with_capacity(data.len());
        for p in (0..data.len()).step_by(2) {
            out.extend(
                self.map_rgb5a3(crate::binary::u16_at(data, p)?)
                    .to_be_bytes(),
            );
        }
        Ok(out)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn colorsys_sectors() {
        for c in [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.], [0.4, 0.4, 0.4]] {
            let rgb = hsv_to_rgb(rgb_to_hsv(c));
            for i in 0..3 {
                assert!((rgb[i] - c[i]).abs() < 1e-14);
            }
        }
    }
    #[test]
    fn alpha_and_cmpr_modes() {
        let mut rc = Recolour::new("solid=00ff00").unwrap();
        for block in [
            [255, 255, 0, 0, 0, 0, 0, 0],
            [0, 0, 255, 255, 255, 255, 255, 255],
        ] {
            let b = rc.recolour_block(&block).unwrap();
            assert_eq!(
                crate::binary::u16_at(&b, 0).unwrap() > crate::binary::u16_at(&b, 2).unwrap(),
                block[0] > block[2]
            );
        }
        assert_eq!(rc.map_rgb5a3(0x3123) & 0x7000, 0x3000);
    }
}
