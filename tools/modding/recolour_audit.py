#!/usr/bin/env python3
"""Recolour quality audit (stdlib only; measures and renders, never changes the recolour logic).

    py recolour_audit.py [--disc DIR] [--out DIR] [--sources 2,4] [--jobs N] [--sheets] [--selftest]
                         [--backend python|rust --rust-dir DIR]

For every cloneable source x colour (the 8 presets + 2 custom) x setting (keep shading 0.2/0.5/0.8 on the
default regions; each detected region on its own at 0.5) the source's body textures are recoloured (backend
python: exactly as the patcher does, clone_gui.colour_spec -> Recolour.resolve -> recolour_gpc; backend rust:
.gpc files written by the Rust core, see RustBackend) and compared texel by texel.  Only texels of the chosen
region(s) count as "should change"; the region test is the Python one in both backends.
Per texture, as a fraction of its visible texels (region texels for the region metrics), squashed to 0..1 by
(1 - exp(-fraction / ref)) * (1 - exp(-count / cref)), so a handful of texels on a tiny tile cannot score 1:
  missed      region texels that should visibly change (not already near the target colour) and did not, or
              still show their own hue; tint regions (white/grey/black bodies): bright texels left unsaturated
  specks      texels outside the region that now show the target hue (and clearly did not before), in 8-connected
              groups of <= 8 new target-hue texels (small region features that changed correctly are not specks)
  protected   texels outside the region (skin, eyes, logos, other regions) whose colour changed by >= 24
  fringes     CMPR 4x4 blocks holding texels inside and outside the region: the outside texels that changed so
  offtarget   changed region texels far from the requested colour: wrong hue for a coloured target, still
              coloured for a white/grey one, and for a custom colour a brightness far from the one picked
  muddy       the lit half of the region (by original brightness) coming out dim or greyish for a coloured
              target: a dim yellow/orange (dark red -> olive "yellow"), or other hues clearly darker or greyer
              than the original; the limits follow the picked colour for custom colours
  banding     neighbouring region texels that were nearly equal and are now far apart (a step in a gradient),
              and region texels of one source colour that came out as two tones (CMPR blocks mapped apart)
Case score = the worst texture score of any metric, muddy x0.6 and banding x0.8 (tone problems rank below leaks).  Outputs (default .scratch/audit): metrics.csv, ranked.md,
and with --sheets sheets/, crops/ and review_list.md.
"""
import argparse
import colorsys
import csv
import json
import math
import sys
import time
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import clone_character as cc  # noqa: E402
import clone_gui as gui  # noqa: E402
from test_matrix import Canvas  # noqa: E402

CUSTOM = ("#20c040", "#f0f0f0")
KEEPS = (0.2, 0.5, 0.8)
CHANGE = 10             # max channel difference that counts as a changed texel (565 rounding is below this)
VISIBLE = 24            # change outside the region that counts as protected/fringe (1-px CMPR blending is below)
SPECK_MAX = 8           # largest 8-connected group of new target-hue texels still called a speck
STEP = 32               # channel difference that reads as a visible step or a second tone (banding)
# fraction giving 0.63: of the texture's visible texels, or of its region texels for missed/offtarget/muddy/banding
REFS = {"missed": 0.08, "specks": 0.006, "prot": 0.10, "fringes": 0.05, "offtarget": 0.10, "muddy": 0.15, "banding": 0.02}
# defect texel count giving a size factor of 0.63 (a 6-texel strip cannot reach 1.00)
CREFS = {"missed": 24, "specks": 12, "prot": 48, "fringes": 48, "offtarget": 24, "muddy": 24, "banding": 12}
METRICS = tuple(REFS)
# case score weight per kind: muddy and banding are tone problems (medium in the #36 review), so a full-strength
# leak, miss or wrong colour ranks above them; the CSV keeps the unweighted scores
SEVERITY = {"missed": 1.0, "specks": 1.0, "prot": 1.0, "fringes": 1.0, "offtarget": 1.0, "muddy": 0.6, "banding": 0.8}
LABELS = {"missed": "missed", "specks": "specks", "prot": "protected", "fringes": "fringes", "offtarget": "off-target",
          "muddy": "muddy", "banding": "banding"}
KIND_COLOURS = {"missed": (255, 0, 255), "specks": (0, 255, 255), "prot": (255, 150, 0), "fringes": (255, 40, 40),
                "offtarget": (255, 255, 0), "muddy": (140, 90, 40), "banding": (60, 120, 255)}
CLEAR = bytes((0, 0, 0)), bytes((255, 255, 255))


def hue_dist(a, b):
    return abs((a - b + 0.5) % 1.0 - 0.5)


def key_rgb(k):
    return bytes((k >> 16, (k >> 8) & 255, k & 255))


def keys_of(rgb):
    return [(r << 16) | (g << 8) | b for r, g, b in zip(rgb[0::3], rgb[1::3], rgb[2::3])]


def squash(frac, ref, count, cref):
    return (1.0 - math.exp(-frac / ref)) * (1.0 - math.exp(-count / cref))


def hsv_of(k):
    return colorsys.rgb_to_hsv((k >> 16) / 255.0, ((k >> 8) & 255) / 255.0, (k & 255) / 255.0)


def target_hsv(colour):
    """The requested colour as (h, s, v): a preset names only a hue (full saturation and brightness are
    nominal: only its hue is checked), a custom #rrggbb all three."""
    if colour.startswith("#"):
        return colorsys.rgb_to_hsv(*(int(colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5)))
    return cc.COLOUR_PRESETS[colour] % 1.0, 1.0, 1.0


# ----------------------------------------------------------------------------------------------
# Judging texels
# ----------------------------------------------------------------------------------------------
class Judge:
    """Answers per colour: is it in the chosen region / should it visibly change / skin / another region, and
    is a recoloured colour the target hue, off the requested colour, or muddy.  Cached by 0xRRGGBB key."""

    def __init__(self, rc, colour):
        self.rc = rc
        self.th, self.ts, self.tv = target_hsv(colour)
        self.custom = colour.startswith("#")
        self.neutral = self.ts < 0.25        # white/grey/black requested: no hue to reach
        self.tint = rc.tinting
        # muddy limits: dim yellows/oranges read as olive/brown long before dim blues read as dark
        vmin = 0.7 if 0.04 <= self.th <= 0.2 else 0.4
        smin = 0.4
        if self.custom:
            vmin, smin = min(vmin, self.tv - 0.15), min(smin, self.ts - 0.2)   # a dark/greyish pick is wanted
        self.vmin, self.smin = vmin, smin
        self._before, self._after = {}, {}

    def near_target(self, h, s, v):
        """Is a colour already close enough to the requested one that no visible change is expected?"""
        if self.neutral:
            return s < self.ts + 0.25
        return s >= 0.15 and hue_dist(h, self.th) < 0.1

    def before(self, k):
        """(in region (or claimed away from it by a neighbour), should change, skin, in another region, value)
        of a source colour."""
        r = self._before.get(k)
        if r is None:
            rc = self.rc
            h, s, v = hsv_of(k)
            inr = rc.texel_in_band(key_rgb(k))
            skin = cc.is_skin(h, s, v)
            inside = [x for x in rc.regions if x.contains(h, s, v)]
            if inside and not inr:
                # claimed by a neighbouring region although its hue is nearer a chosen one (Peach's two pinks)
                best = min(inside, key=lambda x: x.distance(h, s, v))
                inr = any(any(b is x for b in rc.bands) and hue_dist(h, x.hue) < hue_dist(h, best.hue) for x in inside)
            other = bool(inside) and not inr
            if self.tint:
                cand = inr and v >= 0.5 and not self.neutral
            else:
                cand = inr and v >= 0.15 and not self.near_target(h, s, v)
            r = self._before[k] = (inr, cand, skin, other, v)
        return r

    def after(self, k):
        """(shows the target hue, saturation, hue, value, off the requested colour) of a recoloured colour."""
        r = self._after.get(k)
        if r is None:
            h, s, v = hsv_of(k)
            tgt = not self.neutral and hue_dist(h, self.th) <= 0.06 and s >= 0.3 and v >= 0.15
            if v < 0.12:
                off = False
            elif self.neutral:
                off = s > self.ts + 0.25
            else:
                off = s >= 0.15 and hue_dist(h, self.th) > 0.08
            r = self._after[k] = (tgt, s, h, v, off)
        return r

    def muddy(self, s0, v0, s, v):
        """Did a coloured target come out dim or greyish, and clearly more so than the original (a dark source
        that stays as dark is not muddy: Waluigi's purple -> navy)?"""
        if self.neutral or self.tint:
            return False
        if v < self.vmin and (self.vmin > 0.5 or v < v0 - 0.1):    # a dim yellow/orange is olive/brown whatever the source
            return True
        return s < self.smin and s < s0 - 0.2


def channel_step(a, b):
    """Largest per-channel difference of two 0xRRGGBB colours."""
    return max(abs((a >> 16) - (b >> 16)), abs(((a >> 8) & 255) - ((b >> 8) & 255)), abs((a & 255) - (b & 255)))


def texel_changed(a, b):
    return a != b and channel_step(a, b) > CHANGE


def speck_cells(cells, w, limit):
    """The members of the 8-connected groups of <= limit cells (cells: set of texel indices)."""
    seen, out = set(), set()
    for c in cells:
        if c in seen:
            continue
        group, todo = [], [c]
        seen.add(c)
        while todo:
            p = todo.pop()
            group.append(p)
            y, x = divmod(p, w)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    q = p + dy * w + dx
                    if (dy or dx) and 0 <= x + dx < w and q in cells and q not in seen:
                        seen.add(q)
                        todo.append(q)
        if len(group) <= limit:
            out.update(group)
    return out


REGION_METRICS = ("missed", "offtarget", "muddy", "banding")   # measured against the region's texels


def texture_metrics(bk, ak, vis, w, h, cmpr, judge):
    """(counts, scores, masks) of one texture: bk/ak = before/after 0xRRGGBB per texel, vis = visible flags."""
    count = {"region": 0, "changed": 0, "moved": 0, "prot_skin": 0, "prot_region": 0, "prot_other": 0}
    masks = {m: set() for m in METRICS}
    new_target = set()
    blocks = {}
    results = {}                # region texels by source colour
    region = [i for i in range(w * h) if vis[i] and judge.before(bk[i])[0]]
    values = sorted(judge.before(bk[i])[4] for i in region)
    lit = values[len(values) // 2] if values else 2.0      # the brighter half of the region, by original value
    for i in range(w * h):
        if not vis[i]:
            continue
        inr, cand, skin, other, v0 = judge.before(bk[i])
        changed = texel_changed(bk[i], ak[i])
        tgt, sat, hue, val, off = judge.after(ak[i])
        if cand:
            src_h, src_s = hsv_of(bk[i])[:2]
            if not changed or (sat < 0.12 if judge.tint else (sat >= 0.35 and src_s >= 0.35 and hue_dist(hue, src_h) <= 0.05)):
                masks["missed"].add(i)
        if inr and changed:
            count["changed"] += 1
            h0, s0 = hsv_of(bk[i])[:2]
            if (s0 - sat >= 0.25) if judge.neutral else (sat >= 0.15 and s0 >= 0.15 and hue_dist(hue, h0) >= 0.1):
                count["moved"] += 1      # a real change of colour, not a shade tweak (clean-sample filter)
            if off or (judge.custom and v0 >= lit and abs(val - judge.tv) > 0.45):
                masks["offtarget"].add(i)
            if v0 >= lit and v0 >= 0.15 and judge.muddy(s0, v0, sat, val):
                masks["muddy"].add(i)
        if inr:
            results.setdefault(bk[i], []).append(i)
            # banding: a neighbour (right, below) that matched this texel before and is far from it now
            for j in ((i + 1) if (i + 1) % w else -1, i + w if i + w < w * h else -1):
                if j >= 0 and vis[j] and judge.before(bk[j])[0] and channel_step(bk[i], bk[j]) <= CHANGE \
                        and channel_step(ak[i], ak[j]) >= STEP:
                    masks["banding"].update((i, j))
        leak = not inr and channel_step(bk[i], ak[i]) >= VISIBLE
        if cmpr:
            blk = blocks.setdefault((i // w // 4, i % w // 4), [0, []])
            if inr:
                blk[0] += 1
            elif leak:
                blk[1].append(i)
        if tgt and channel_step(bk[i], ak[i]) >= VISIBLE and not judge.near_target(*hsv_of(bk[i])):
            new_target.add(i)       # a visible move to the target hue from a clearly different colour
        if leak:
            masks["prot"].add(i)
            count["prot_skin" if skin else "prot_region" if other else "prot_other"] += 1
    for inside, outs in blocks.values():
        if inside:
            masks["fringes"].update(outs)
    # banding, second kind: one source colour turned into two tones (CMPR blocks mapped differently, Birdo's body)
    for cells in results.values():
        if len(cells) > 1:
            common = Counter(ak[i] for i in cells).most_common(1)[0][0]
            masks["banding"].update(i for i in cells if channel_step(ak[i], common) >= STEP)
    # a small group of new target-hue texels away from the recoloured area; only its texels outside the region
    # count (a small region feature, Daisy's ring, changing is right; texels touching the region are fringes)
    masks["specks"] = set(i for i in speck_cells(new_target, w, SPECK_MAX) if not judge.before(bk[i])[0])
    count["region"] = len(region)
    n, nr = max(1, sum(vis)), max(1, len(region))
    scores = {}
    for m in METRICS:
        count[m] = len(masks[m])
        scores[m] = squash(count[m] / (nr if m in REGION_METRICS else n), REFS[m], count[m], CREFS[m])
    return count, scores, masks


# ----------------------------------------------------------------------------------------------
# Source loading and the grid
# ----------------------------------------------------------------------------------------------
class SourceData:
    """A source's body, regions and the decoded source textures (RGB keys, visibility)."""

    def __init__(self, disc, dol, c):
        self.c = c
        self.body = cc.Gpc(cc.effective_body(disc, dol, c))
        self.regions = cc.source_regions(disc, dol, c, self.body)
        self.tex = {}
        for t in self.body.textures:
            data = cc.texture_bytes(self.body, t)
            black = cc.decode_texture(data, t, CLEAR[:1] * 2)
            white = cc.decode_texture(data, t, CLEAR[1:] * 2)
            vis = bytearray(1 if black[i:i + 3] == white[i:i + 3] else 0 for i in range(0, len(black), 3))
            self.tex[t["index"]] = (t, data, black, keys_of(black), vis)

    def settings(self):
        """[(name, regions argument for colour_spec, keep)]: three shadings on the defaults, each region alone."""
        defaults = [i for i, r in enumerate(self.regions) if r.default]
        out = [("keep%.1f" % k, None, k) for k in KEEPS]
        for i, r in enumerate(self.regions):
            if [i] != defaults:
                out.append(("only%d%s" % (i, "".join(ch for ch in r.name if ch.isalnum())), [i], 0.5))
        return out

    def cases(self):
        """[(colour, setting name, regions, keep)]: every case of this source."""
        return [(colour, name, regions, keep) for colour in list(cc.COLOUR_PRESETS) + list(CUSTOM)
                for name, regions, keep in self.settings()]

    def recolour(self, colour, regions, keep, setting, backend):
        """(rc, recoloured body): rc is always the Python spec resolved on the Python regions (it says which
        texels are in the region); the body comes from the backend."""
        spec = gui.colour_spec(colour, keep, regions=regions)
        rc = cc.Recolour(spec).resolve(self.regions)
        return rc, backend.recolour(self, rc, colour, setting)

    def measure(self, rc, g, colour):
        """{texture index: (counts, scores, masks, after rgb)} for every texture the spec touches."""
        judge, out = Judge(rc, colour), {}
        for idx, (t, data, rgb, bk, vis) in self.tex.items():
            if not rc.touches(idx):
                continue
            new = cc.texture_bytes(g, t)
            if new == data:
                ak, argb = bk, rgb
            else:
                argb = cc.decode_texture(new, t, CLEAR[:1] * 2)
                ak = keys_of(argb)
            out[idx] = texture_metrics(bk, ak, vis, t["w"], t["h"], t["fmt"] == cc.TEX_FMT_CMPR, judge) + (argb,)
        return out


# ----------------------------------------------------------------------------------------------
# Recolour backends
# ----------------------------------------------------------------------------------------------
class BackendError(Exception):
    """A backend cannot supply a body (raised in a worker process; main() turns it into a clean exit)."""


class PythonBackend:
    """The patcher's own recolour (clone_character.recolour_gpc)."""
    name = "python"

    def prepare(self, src):
        pass

    def recolour(self, src, rc, colour, setting):
        g = cc.Gpc(bytes(src.body.data))
        cc.recolour_gpc(g, rc)
        return g


class RustBackend:
    """Recoloured bodies written by the Rust core: <dir>/<charID>/<colour>_<setting>.gpc (colour without '#').
    prepare() writes <dir>/<charID>/cases.json, one {name, colour, keeplum, regions} per case (regions: the
    source's region indices, or null for its defaults), which is what a Rust probe command reads to write the
    files (`mssb-clone-probe recolour <disc> <charID> <dir>/<charID>/cases.json <dir>/<charID>`).
    The Rust colour handling (custom colours included) is what gets measured; the Python regions decide which
    texels belong to the region, so a Rust region-detection change shows up as missed/protected texels."""
    name = "rust"

    def __init__(self, folder):
        self.folder = Path(folder)

    def prepare(self, src):
        d = self.folder / str(src.c)
        d.mkdir(parents=True, exist_ok=True)
        cases = [{"name": "%s_%s" % (colour_name(colour), name), "colour": colour, "keeplum": keep, "regions": regions}
                 for colour, name, regions, keep in src.cases()]
        (d / "cases.json").write_text(json.dumps({"source": src.c, "cases": cases}, indent=1))
        missing = [x["name"] for x in cases if not (d / (x["name"] + ".gpc")).is_file()]
        if missing:
            raise BackendError("rust backend: %d of %d bodies missing in %s (e.g. %s.gpc); write them from %s"
                             % (len(missing), len(cases), d, missing[0], d / "cases.json"))

    def recolour(self, src, rc, colour, setting):
        g = cc.Gpc((self.folder / str(src.c) / ("%s_%s.gpc" % (colour_name(colour), setting))).read_bytes())
        if [(t["index"], t["w"], t["h"], t["fmt"]) for t in g.textures] != [(t["index"], t["w"], t["h"], t["fmt"]) for t in src.body.textures]:
            raise BackendError("rust backend: %s %s %s: texture layout differs from the source body" % (src.c, colour, setting))
        return g


def make_backend(args):
    if args.backend == "rust":
        if not args.rust_dir:
            raise SystemExit("--backend rust needs --rust-dir")
        return RustBackend(args.rust_dir)
    return PythonBackend()


def colour_name(colour):
    return colour.lstrip("#")


FIELDS = (["source", "source_name", "colour", "setting", "texture", "w", "h", "visible", "region", "changed", "moved"] + list(METRICS)
          + ["prot_skin", "prot_region", "prot_other"] + ["s_" + m for m in METRICS] + ["score"])


def audit_source(job):
    disc_path, c, backend = job
    disc = cc.Disc(disc_path)
    dol = cc.Dol((disc.sys / "main.dol").read_bytes())
    src = SourceData(disc, dol, c)
    backend.prepare(src)
    rows = []
    for colour, name, regions, keep in src.cases():
        rc, g = src.recolour(colour, regions, keep, name, backend)
        for idx, (count, scores, _, _) in src.measure(rc, g, colour).items():
            t = src.tex[idx][0]
            row = {"source": c, "source_name": cc.CHAR_NAMES[c], "colour": colour_name(colour), "setting": name,
                   "texture": idx, "w": t["w"], "h": t["h"], "visible": sum(src.tex[idx][4])}
            row.update(count)
            row.update({"s_" + m: round(scores[m], 4) for m in METRICS})
            row["score"] = round(max(scores[m] * SEVERITY[m] for m in METRICS), 4)
            rows.append(row)
    return rows


def cases_of(rows):
    """{(source, colour, setting): {metric: worst texture score, 'score', 'tex': worst texture, 'name'}}"""
    cases = {}
    for r in rows:
        k = (int(r["source"]), r["colour"], r["setting"])
        c = cases.setdefault(k, {"name": r["source_name"], "tex": None, "worst": -1.0, "region": 0, "changed": 0, "moved": 0,
                                 **{m: 0.0 for m in METRICS}})
        c["region"] += int(r["region"])
        c["changed"] += int(r["changed"])
        c["moved"] += int(r["moved"])
        for m in METRICS:
            c[m] = max(c[m], float(r["s_" + m]))
        if float(r["score"]) > c["worst"]:
            c["worst"], c["tex"] = float(r["score"]), int(r["texture"])
    for c in cases.values():
        c["score"] = max(c[m] * SEVERITY[m] for m in METRICS)
    return cases


def case_text(k, c):
    return "%s -> %s [%s]: score %.2f (%s; worst texture %s)" % (
        c["name"], k[1], k[2], c["score"], ", ".join("%s %.2f" % (LABELS[m], c[m]) for m in METRICS), c["tex"])


def ranked(cases):
    """Worst first: by score, ties (many cases reach 1.00) by the sum of all metrics (several defects at once)."""
    return sorted(cases.items(), key=lambda kv: (-kv[1]["score"], -sum(kv[1][m] for m in METRICS)))


def write_ranked(path, cases, threshold=0.5):
    order = ranked(cases)
    lines = ["# Recolour audit: ranked", "", "%d cases, %d with score >= %.1f." % (len(order), sum(c["score"] >= threshold for _, c in order), threshold), "",
             "## Worst 60 overall", ""]
    lines += ["%d. %s" % (i + 1, case_text(k, c)) for i, (k, c) in enumerate(order[:60])]
    for m in METRICS:
        # one defect kind (the white custom colour fills the top of most lists, so it is listed apart)
        top = sorted(order, key=lambda kv: -kv[1][m])
        lines += ["", "## Worst 15 by %s (worst 5 with the custom white #f0f0f0 listed apart)" % LABELS[m], ""]
        lines += ["- " + case_text(k, c) for k, c in [kv for kv in top if kv[0][1] != "f0f0f0"][:15] if c[m] > 0]
        lines += ["- white: " + case_text(k, c) for k, c in [kv for kv in top if kv[0][1] == "f0f0f0"][:5] if c[m] > 0]
    lines += ["", "## By source (worst 5 each)", ""]
    by = {}
    for k, c in order:
        by.setdefault(k[0], []).append((k, c))
    for s in sorted(by, key=lambda s: -by[s][0][1]["score"]):
        lst = by[s]
        lines += ["### %s (%d cases >= %.1f of %d)" % (lst[0][1]["name"], sum(c["score"] >= threshold for _, c in lst), threshold, len(lst))]
        lines += ["- " + case_text(k, c) for k, c in lst[:5]] + [""]
    path.write_text("\n".join(lines))
    return order


# ----------------------------------------------------------------------------------------------
# Sheets and crops
# ----------------------------------------------------------------------------------------------
def zoom_blit(cv, x0, y0, w, h, rgb, z):
    """Texel-sharp zoom: copies a w*h RGB image into the canvas at z times its size."""
    for y in range(h):
        row = bytearray(w * z * 3)
        for ch in range(3):
            for k in range(z):
                row[k * 3 + ch::z * 3] = rgb[y * w * 3 + ch:(y + 1) * w * 3:3]
        for k in range(z):
            d = ((y0 + y * z + k) * cv.w + x0) * 3
            cv.px[d:d + w * z * 3] = row


def diff_image(before, after, w, h, masks):
    """The after texture dimmed to grey with the defect texels in colour (later metrics win overlaps)."""
    out = bytearray(w * h * 3)
    for i in range(w * h):
        r, g, b = after[i * 3:i * 3 + 3]
        y = (r + g + b) // 3 // 3 + 20
        out[i * 3:i * 3 + 3] = bytes((y, y, y))
    for m in ("prot", "fringes", "banding", "muddy", "offtarget", "specks", "missed"):
        for i in masks[m]:
            out[i * 3:i * 3 + 3] = bytes(KIND_COLOURS[m])
    return bytes(out)


def sheet_png(src, k, c, res, z=4, width=256):
    """before | after | defects of every body texture; textures the spec leaves alone (shared with the colour
    family, gloves) are drawn too, with a grey label and an empty defects panel."""
    lay, height = gui.atlas_layout(src.body.textures, width)
    panel = width * z
    cv = Canvas(3 * panel + 4 * 8, height * z + 60)
    cv.text(8, 6, "%s -> %s [%s] score %.2f" % (c["name"], k[1], k[2], c["score"]), scale=2)
    for n, m in enumerate(METRICS):
        cv.text(8 + n * 150, 24, LABELS[m].upper(), KIND_COLOURS[m], 2)
    for p, title in enumerate(("BEFORE", "AFTER", "DEFECTS")):
        cv.text(8 + p * (panel + 8), 42, title, scale=2)
    for t, x, y in lay:
        before = src.tex[t["index"]][2]
        if t["index"] in res:
            after = res[t["index"]][3]
            imgs = (before, after, diff_image(before, after, t["w"], t["h"], res[t["index"]][2]))
        else:
            imgs = (before, before, diff_image(before, before, t["w"], t["h"], {m: () for m in METRICS}))
        for p, img in enumerate(imgs):
            zoom_blit(cv, 8 + p * (panel + 8) + x * z, 60 + y * z, t["w"], t["h"], img, z)
        cv.text(8 + x * z + 2, 62 + y * z, str(t["index"]), (255, 255, 0) if t["index"] in res else (128, 128, 128), 1)
    return cv.png()


def crop_pngs(src, k, c, res, z=8, size=32, top=2):
    """[(texture, png)]: before | after | defects crops of the densest defect spot of the worst textures."""
    out = []
    worst = sorted(res, key=lambda i: -max(res[i][1].values()))[:top]
    for idx in worst:
        t = src.tex[idx][0]
        masks = res[idx][2]
        cells = set().union(*masks.values())
        if not cells:
            continue
        w, h = t["w"], t["h"]
        cw, ch = min(size, w), min(size, h)
        # centre the window on the defect texel with the most defect neighbours
        best = max(cells, key=lambda i: sum(((i + dy * w + dx) in cells) for dy in range(-3, 4) for dx in range(-3, 4)))
        x0 = min(max(best % w - cw // 2, 0), w - cw)
        y0 = min(max(best // w - ch // 2, 0), h - ch)
        diff = diff_image(src.tex[idx][2], res[idx][3], w, h, masks)
        cv = Canvas(3 * (cw * z + 8) + 8, ch * z + 28)
        cv.text(8, 4, "%s %s [%s] tex %d at %d,%d" % (c["name"], k[1], k[2], idx, x0, y0), scale=1)
        for p, img in enumerate((src.tex[idx][2], res[idx][3], diff)):
            crop = b"".join(img[((y0 + y) * w + x0) * 3:((y0 + y) * w + x0 + cw) * 3] for y in range(ch))
            zoom_blit(cv, 8 + p * (cw * z + 8), 20, cw, ch, crop, z)
        out.append((idx, cv.png()))
    return out


def pick_sheets(cases, worst=60, clean=20, per_source=6, must=((2, "blue", "only1redtie"), (2, "blue", "keep0.5"), (4, "blue", "keep0.5"))):
    order = ranked(cases)
    chosen, used = [], {}
    for k, c in order:
        if len(chosen) >= worst:
            break
        if used.get(k[0], 0) < per_source:
            chosen.append(k)
            used[k[0]] = used.get(k[0], 0) + 1
    chosen += [k for k in must if k in cases and k not in chosen]
    # a clean sample must really change colour: most region texels moved hue (or lost it for a white/grey target)
    quiet = [k for k, c in sorted(cases.items()) if c["score"] < 0.1 and k[2] == "keep0.5" and k not in chosen
             and c["moved"] >= 0.5 * c["region"] > 0]
    # one per source first (spread over the roster), then fill up
    firsts = [k for n, k in enumerate(quiet) if all(q[0] != k[0] for q in quiet[:n])]
    step = max(1, len(firsts) // clean)
    pick = firsts[::step][:clean]
    return chosen, pick + [k for k in quiet if k not in pick][:clean - len(pick)]


def write_sheets(out, disc_path, cases, backend):
    disc = cc.Disc(disc_path)
    dol = cc.Dol((disc.sys / "main.dol").read_bytes())
    bad, clean = pick_sheets(cases)
    for d in ("sheets", "crops"):
        (out / d).mkdir(parents=True, exist_ok=True)
    review, src = [], None
    for label, keys in (("worst", bad), ("clean sample", clean)):
        for k in keys:
            c = cases[k]
            if src is None or src.c != k[0]:
                src = SourceData(disc, dol, k[0])
            colour = "#" + k[1] if k[1] in [colour_name(x) for x in CUSTOM] else k[1]
            regions, keep = next((r, kp) for n, r, kp in src.settings() if n == k[2])
            rc, g = src.recolour(colour, regions, keep, k[2], backend)
            res = src.measure(rc, g, colour)
            name = "%s_%s_%s" % (c["name"], k[1], k[2])
            (out / "sheets" / (name + ".png")).write_bytes(sheet_png(src, k, c, res))
            crops = crop_pngs(src, k, c, res)
            for i, png in crops:
                (out / "crops" / ("%s_t%d.png" % (name, i))).write_bytes(png)
            review.append("- [%s] sheets/%s.png | %s | crops: %s" % (label, name, case_text(k, c), ", ".join("crops/%s_t%d.png" % (name, i) for i, _ in crops) or "none"))
            print("   sheet", name)
    (out / "review_list.md").write_text("# Review list\n\nOne line per sheet (worst cases first, then a clean sample).\n"
                                        "Defect colours: magenta missed, cyan specks, orange protected, red fringes, yellow off-target, "
                                        "brown muddy, blue banding.\n\n" + "\n".join(review) + "\n")


# ----------------------------------------------------------------------------------------------
def selftest():
    """A 16x16 CMPR-shaped texture: red half (region), yellow half; check each metric fires only where it should."""
    region = cc.Region("red", 0.0, 0.035)

    def judge_for(colour):
        return Judge(cc.Recolour(gui.colour_spec(colour, 0.5)).resolve([region]), colour)
    judge = judge_for("blue")
    w = h = 16
    red, yellow, blue, pink = 0xC81E1E, 0xE0C020, 0x1E3CC8, 0xC81E96

    def tex(f):
        return [f(i % w, i // w) for i in range(w * h)]
    before = tex(lambda x, y: red if x < 8 else yellow)
    vis = [1] * (w * h)

    def run(after, j=judge, src=before):
        return texture_metrics(src, after, vis, w, h, True, j)[0]
    ok = run(tex(lambda x, y: blue if x < 8 else yellow))
    assert not any(ok[m] for m in METRICS) and ok["region"] == ok["changed"] == ok["moved"] == 128, ok
    miss = run(tex(lambda x, y: blue if x < 8 and not (x < 4 and y < 4) else yellow if x >= 8 else red))
    assert miss["missed"] == 16 and miss["prot"] == 0, miss
    speck = run(tex(lambda x, y: blue if x < 8 else blue if (x, y) == (12, 3) else yellow))
    assert speck["specks"] == 1 and speck["fringes"] == 0, speck
    # yellow texels turned pink in blocks that hold no red (x 8..11): protected, not fringes
    plain = run(tex(lambda x, y: blue if x < 8 else pink if x < 12 else yellow))
    assert plain["prot"] == 16 * 4 and plain["fringes"] == 0, plain
    # red up to x=5: the blocks at x 4..7 are mixed, so their yellow texels (2 columns x 16 rows) are fringes
    mixed = run(tex(lambda x, y: blue if x < 6 else pink), src=tex(lambda x, y: red if x < 6 else yellow))
    assert mixed["fringes"] == 32, mixed
    # white requested, red result (the custom-colour hue-only bug): off target; left red: missed
    white = judge_for("#f0f0f0")
    assert run(tex(lambda x, y: 0xE04060 if x < 8 else yellow), white)["offtarget"] == 128
    assert run(before, white)["missed"] == 128
    assert run(tex(lambda x, y: 0xD8D0D0 if x < 8 else yellow), white)["offtarget"] == 0
    # dark red -> olive is muddy, -> bright yellow is not; dark blue is not muddy (only dim yellows/oranges are)
    yel = judge_for("yellow")
    assert run(tex(lambda x, y: 0x80701A if x < 8 else yellow), yel)["muddy"] == 128
    assert run(tex(lambda x, y: 0xF0D020 if x < 8 else yellow), yel)["muddy"] == 0
    assert run(tex(lambda x, y: 0x10208C if x < 8 else yellow))["muddy"] == 0
    # a smooth red ramp that comes out as two flat colours split at x=4: the two columns at the step are banding
    ramp = tex(lambda x, y: (0xC0 + x) << 16 | 0x1E1E if x < 8 else yellow)
    band = run(tex(lambda x, y: blue if x < 4 else 0x6040E0 if x < 8 else yellow), src=ramp)
    assert band["banding"] == 32 and run(tex(lambda x, y: blue if x < 8 else yellow), src=ramp)["banding"] == 0, band
    assert squash(0.0, 0.1, 100, 12) == 0.0 and squash(1.0, 0.1, 200, 12) > 0.99 and squash(1.0, 0.1, 2, 12) < 0.2
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disc", default=str(HERE.parents[1] / "orig" / "GYQE01"))
    ap.add_argument("--out", default=str(HERE.parents[1] / ".scratch" / "audit"))
    ap.add_argument("--sources", help="comma-separated charIDs (default: every cloneable one)")
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--sheets", action="store_true", help="also write sheets/, crops/ and review_list.md")
    ap.add_argument("--from-csv", action="store_true", help="reuse metrics.csv instead of measuring again")
    ap.add_argument("--backend", choices=("python", "rust"), default="python", help="whose recolour to measure")
    ap.add_argument("--rust-dir", help="rust backend: folder of <charID>/<colour>_<setting>.gpc (cases.json written per source)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    backend = make_backend(args)
    t0 = time.time()
    if args.from_csv:
        rows = list(csv.DictReader((out / "metrics.csv").open()))
    else:
        srcs = [int(x) for x in args.sources.split(",")] if args.sources else gui.SOURCES
        rows = []
        try:
            with Pool(args.jobs) as pool:
                for part in pool.imap_unordered(audit_source, [(args.disc, c, backend) for c in srcs]):
                    rows += part
                    print("   %s done (%d rows, %.0f s)" % (cc.CHAR_NAMES[part[0]["source"]] if part else "-", len(part), time.time() - t0))
        except BackendError as e:
            raise SystemExit(str(e))
        rows.sort(key=lambda r: (r["source"], r["colour"], r["setting"], r["texture"]))
        with (out / "metrics.csv").open("w", newline="") as f:
            wr = csv.DictWriter(f, FIELDS)
            wr.writeheader()
            wr.writerows(rows)
    cases = cases_of(rows)
    order = write_ranked(out / "ranked.md", cases)
    print("%d rows, %d cases, measured in %.0f s; worst:" % (len(rows), len(cases), time.time() - t0))
    for k, c in order[:15]:
        print("  " + case_text(k, c))
    if args.sheets:
        try:
            write_sheets(out, args.disc, cases, backend)
        except BackendError as e:
            raise SystemExit(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
