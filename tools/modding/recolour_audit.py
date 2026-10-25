#!/usr/bin/env python3
"""Recolour quality audit (stdlib only; measures and renders, never changes the recolour logic).

    py recolour_audit.py [--disc DIR] [--out DIR] [--sources 2,4] [--jobs N] [--sheets] [--selftest]

For every cloneable source x colour (the 8 presets + 2 custom) x setting (keep shading 0.2/0.5/0.8 on the
default regions; each detected region on its own at 0.5) the source's body textures are recoloured exactly as
the patcher does (clone_gui.colour_spec -> Recolour.resolve -> recolour_gpc) and compared texel by texel.
Per texture, as a fraction of its visible texels, then squashed to a 0..1 score (1 - exp(-fraction / ref)):
  missed      texels that should have changed and still show the source hue: inside the chosen region, or in
              its hue family (+-width+0.05, sat >= 0.35, not skin) just outside it; tint regions (white/grey/
              black bodies): bright region texels that stay unsaturated.  Skipped when the target hue is within
              0.1 of the region's hue (no visible change expected).
  specks      changed texels that now show the target hue (and did not before), in 8-connected groups of <= 8
  protected   texels outside the region (skin, eyes, logos, other regions) whose colour changed by > 10
  fringes     CMPR 4x4 blocks holding texels inside and outside the region: the outside texels that changed
Case score = the worst of the four metrics' worst-texture scores (count below a minimum scores 0).  Outputs (default .scratch/audit): metrics.csv,
ranked.md, and with --sheets sheets/, crops/ and review_list.md.
"""
import argparse
import colorsys
import csv
import math
import sys
import time
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
SPECK_MAX = 8           # largest 8-connected group of new target-hue texels still called a speck
REFS = {"missed": 0.08, "specks": 0.006, "prot": 0.10, "fringes": 0.05}   # fraction of visible texels giving a score of 0.63
MINC = {"missed": 6, "specks": 3, "prot": 8, "fringes": 8}                  # fewer texels than this score 0
METRICS = tuple(REFS)
KIND_COLOURS = {"missed": (255, 0, 255), "specks": (0, 255, 255), "prot": (255, 150, 0), "fringes": (255, 40, 40)}
CLEAR = bytes((0, 0, 0)), bytes((255, 255, 255))


def hue_dist(a, b):
    return abs((a - b + 0.5) % 1.0 - 0.5)


def key_rgb(k):
    return bytes((k >> 16, (k >> 8) & 255, k & 255))


def keys_of(rgb):
    return [(r << 16) | (g << 8) | b for r, g, b in zip(rgb[0::3], rgb[1::3], rgb[2::3])]


def squash(frac, ref, count, least):
    return 0.0 if count < least else 1.0 - math.exp(-frac / ref)


# ----------------------------------------------------------------------------------------------
# Judging texels
# ----------------------------------------------------------------------------------------------
class Judge:
    """Answers per colour: is it in the chosen region / a source-hue candidate / skin / another region, and
    does a recoloured colour show the target hue or still the source hue.  Cached by 0xRRGGBB key."""

    def __init__(self, rc):
        self.rc = rc
        self.target = rc.hue % 1.0
        self.tint = rc.tinting
        self._before, self._after = {}, {}

    def before(self, k):
        """(in region (or claimed away from it by a neighbour), missed-candidate, source hue or None, skin, in another region) of a source colour."""
        r = self._before.get(k)
        if r is None:
            rc = self.rc
            h, s, v = colorsys.rgb_to_hsv((k >> 16) / 255.0, ((k >> 8) & 255) / 255.0, (k & 255) / 255.0)
            inr = rc.texel_in_band(key_rgb(k))
            skin = cc.is_skin(h, s, v)
            inside = [x for x in rc.regions if x.contains(h, s, v)]
            if inside and not inr:
                # claimed by a neighbouring region although its hue is nearer a chosen one (Peach's two pinks)
                best = min(inside, key=lambda x: x.distance(h, s, v))
                inr = any(any(b is x for b in rc.bands) and hue_dist(h, x.hue) < hue_dist(h, best.hue) for x in inside)
            other = bool(inside) and not inr
            cand, src = False, None
            if self.tint:
                cand = inr and v >= 0.5
            else:
                near = [b for b in rc.bands if hue_dist(h, b.hue) <= b.width + 0.05 and s >= 0.35 and v >= 0.15 and not skin and not other]
                if inr and not near:
                    near = [min(rc.bands, key=lambda b: hue_dist(h, b.hue))]
                if near:
                    src = min(near, key=lambda b: hue_dist(h, b.hue)).hue
                    cand = hue_dist(src, self.target) >= 0.1
            r = self._before[k] = (inr, cand, src, skin, other)
        return r

    def after(self, k):
        """(shows the target hue, saturation, hue) of a recoloured colour."""
        r = self._after.get(k)
        if r is None:
            h, s, v = colorsys.rgb_to_hsv((k >> 16) / 255.0, ((k >> 8) & 255) / 255.0, (k & 255) / 255.0)
            r = self._after[k] = (hue_dist(h, self.target) <= 0.06 and s >= 0.3 and v >= 0.15, s, h)
        return r


def texel_changed(a, b):
    return a != b and max(abs((a >> 16) - (b >> 16)), abs(((a >> 8) & 255) - ((b >> 8) & 255)), abs((a & 255) - (b & 255))) > CHANGE


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


def texture_metrics(bk, ak, vis, w, h, cmpr, judge):
    """(counts, scores, masks) of one texture: bk/ak = before/after 0xRRGGBB per texel, vis = visible flags."""
    count = {"missed": 0, "specks": 0, "prot": 0, "prot_skin": 0, "prot_region": 0, "prot_other": 0, "fringes": 0}
    masks = {m: set() for m in METRICS}
    new_target = set()
    blocks = {}
    for i in range(w * h):
        if not vis[i]:
            continue
        inr, cand, src, skin, other = judge.before(bk[i])
        same = bk[i] == ak[i]
        changed = not same and texel_changed(bk[i], ak[i])
        if cand:
            tgt, sat, hue = judge.after(ak[i])
            if not changed or (sat < 0.12 if src is None else (sat >= 0.35 and hue_dist(hue, src) <= 0.05)):
                masks["missed"].add(i)
        if cmpr:
            blk = blocks.setdefault((i // w // 4, i % w // 4), [0, []])
            if inr:
                blk[0] += 1
            elif changed:
                blk[1].append(i)
            else:
                blk[1].append(-1)
        if changed and judge.after(ak[i])[0] and not judge.after(bk[i])[0]:
            new_target.add(i)
        if changed and not inr:
            masks["prot"].add(i)
            count["prot_skin" if skin else "prot_region" if other else "prot_other"] += 1
    for inside, outs in blocks.values():
        if inside:
            masks["fringes"].update(i for i in outs if i >= 0)
    masks["specks"] = speck_cells(new_target, w, SPECK_MAX)
    n = max(1, sum(vis))
    scores = {}
    for m in METRICS:
        count[m] = len(masks[m])
        scores[m] = squash(count[m] / n, REFS[m], count[m], MINC[m])
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

    def recolour(self, colour, regions, keep):
        spec = gui.colour_spec(colour, keep, regions=regions)
        rc = cc.Recolour(spec).resolve(self.regions)
        g = cc.Gpc(bytes(self.body.data))
        cc.recolour_gpc(g, rc)
        return rc, g

    def measure(self, rc, g, masks=False):
        """{texture index: (counts, scores, masks, after keys, after rgb)} for every texture the spec touches."""
        judge, out = Judge(rc), {}
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


def colour_name(colour):
    return colour.lstrip("#")


FIELDS = ["source", "source_name", "colour", "setting", "texture", "w", "h", "visible", "missed", "specks", "prot",
          "prot_skin", "prot_region", "prot_other", "fringes", "s_missed", "s_specks", "s_prot", "s_fringes", "score"]


def audit_source(job):
    disc_path, c = job
    disc = cc.Disc(disc_path)
    dol = cc.Dol((disc.sys / "main.dol").read_bytes())
    src = SourceData(disc, dol, c)
    rows = []
    for colour in list(cc.COLOUR_PRESETS) + list(CUSTOM):
        for name, regions, keep in src.settings():
            rc, g = src.recolour(colour, regions, keep)
            for idx, (count, scores, _, _) in src.measure(rc, g).items():
                t = src.tex[idx][0]
                row = {"source": c, "source_name": cc.CHAR_NAMES[c], "colour": colour_name(colour), "setting": name,
                       "texture": idx, "w": t["w"], "h": t["h"], "visible": sum(src.tex[idx][4])}
                row.update(count)
                row.update({"s_" + m: round(scores[m], 4) for m in METRICS})
                row["score"] = round(max(scores.values()), 4)
                rows.append(row)
    return rows


def cases_of(rows):
    """{(source, colour, setting): {metric: worst texture score, 'score', 'tex': worst texture, 'name'}}"""
    cases = {}
    for r in rows:
        k = (int(r["source"]), r["colour"], r["setting"])
        c = cases.setdefault(k, {"name": r["source_name"], "tex": None, "worst": -1.0, **{m: 0.0 for m in METRICS}})
        for m in METRICS:
            c[m] = max(c[m], float(r["s_" + m]))
        if float(r["score"]) > c["worst"]:
            c["worst"], c["tex"] = float(r["score"]), int(r["texture"])
    for c in cases.values():
        c["score"] = max(c[m] for m in METRICS)
    return cases


def case_text(k, c):
    return "%s -> %s [%s]: score %.2f (missed %.2f, specks %.2f, protected %.2f, fringes %.2f; worst texture %s)" % (
        c["name"], k[1], k[2], c["score"], c["missed"], c["specks"], c["prot"], c["fringes"], c["tex"])


def write_ranked(path, cases, threshold=0.5):
    order = sorted(cases.items(), key=lambda kv: -kv[1]["score"])
    lines = ["# Recolour audit: ranked", "", "%d cases, %d with score >= %.1f." % (len(order), sum(c["score"] >= threshold for _, c in order), threshold), "",
             "## Worst 60 overall", ""]
    lines += ["%d. %s" % (i + 1, case_text(k, c)) for i, (k, c) in enumerate(order[:60])]
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
    for m in ("prot", "fringes", "specks", "missed"):
        for i in masks[m]:
            out[i * 3:i * 3 + 3] = bytes(KIND_COLOURS[m])
    return bytes(out)


def sheet_png(src, k, c, res, z=4, width=256):
    lay, height = gui.atlas_layout(src.body.textures, width)
    panel = width * z
    cv = Canvas(3 * panel + 4 * 8, height * z + 60)
    cv.text(8, 6, "%s -> %s [%s] score %.2f" % (c["name"], k[1], k[2], c["score"]), scale=2)
    cv.text(8, 24, "MISSED", KIND_COLOURS["missed"], 2)
    for n, m in enumerate(("specks", "prot", "fringes")):
        cv.text(8 + (n + 1) * 130, 24, m.upper().replace("PROT", "PROTECTED"), KIND_COLOURS[m], 2)
    for p, title in enumerate(("BEFORE", "AFTER", "DEFECTS")):
        cv.text(8 + p * (panel + 8), 42, title, scale=2)
    for t, x, y in lay:
        if t["index"] not in res:
            continue
        count, scores, masks, after = res[t["index"]]
        before = src.tex[t["index"]][2]
        for p, img in enumerate((before, after, diff_image(before, after, t["w"], t["h"], masks))):
            zoom_blit(cv, 8 + p * (panel + 8) + x * z, 60 + y * z, t["w"], t["h"], img, z)
        cv.text(8 + x * z + 2, 62 + y * z, str(t["index"]), (255, 255, 0), 1)
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
    order = sorted(cases.items(), key=lambda kv: -kv[1]["score"])
    chosen, used = [], {}
    for k, c in order:
        if len(chosen) >= worst:
            break
        if used.get(k[0], 0) < per_source:
            chosen.append(k)
            used[k[0]] = used.get(k[0], 0) + 1
    chosen += [k for k in must if k in cases and k not in chosen]
    quiet = [k for k, c in sorted(cases.items()) if c["score"] < 0.05 and k[2] == "keep0.5" and k not in chosen]
    step = max(1, len(quiet) // clean)
    return chosen, quiet[::step][:clean]


def write_sheets(out, disc_path, cases):
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
            rc, g = src.recolour(colour, regions, keep)
            res = src.measure(rc, g)
            name = "%s_%s_%s" % (c["name"], k[1], k[2])
            (out / "sheets" / (name + ".png")).write_bytes(sheet_png(src, k, c, res))
            crops = crop_pngs(src, k, c, res)
            for i, png in crops:
                (out / "crops" / ("%s_t%d.png" % (name, i))).write_bytes(png)
            review.append("- [%s] sheets/%s.png | %s | crops: %s" % (label, name, case_text(k, c), ", ".join("crops/%s_t%d.png" % (name, i) for i, _ in crops) or "none"))
            print("   sheet", name)
    (out / "review_list.md").write_text("# Review list\n\nOne line per sheet (worst cases first, then a clean sample).\n"
                                        "Defect colours: magenta missed, cyan specks, orange protected, red fringes.\n\n" + "\n".join(review) + "\n")


# ----------------------------------------------------------------------------------------------
def selftest():
    """A 16x16 CMPR-shaped texture: red half (region), yellow half; check each metric fires only where it should."""
    region = cc.Region("red", 0.0, 0.035)
    rc = cc.Recolour("hue=blue").resolve([region])
    judge = Judge(rc)
    w = h = 16
    red, yellow, blue, pink = 0xC81E1E, 0xE0C020, 0x1E3CC8, 0xC81E96

    def tex(f):
        return [f(i % w, i // w) for i in range(w * h)]
    before = tex(lambda x, y: red if x < 8 else yellow)
    vis = [1] * (w * h)

    def run(after):
        return texture_metrics(before, after, vis, w, h, True, judge)[0]
    ok = run(tex(lambda x, y: blue if x < 8 else yellow))
    assert not any(ok[m] for m in METRICS), ok
    miss = run(tex(lambda x, y: blue if x < 8 and not (x < 4 and y < 4) else yellow if x >= 8 else red))
    assert miss["missed"] == 16 and miss["prot"] == 0, miss
    speck = run(tex(lambda x, y: blue if x < 8 else blue if (x, y) == (12, 3) else yellow))
    assert speck["specks"] == 1 and speck["fringes"] == 0, speck
    # yellow texels turned pink in blocks that hold no red (x 8..11): protected, not fringes
    plain = run(tex(lambda x, y: blue if x < 8 else pink if x < 12 else yellow))
    assert plain["prot"] == 16 * 4 and plain["fringes"] == 0, plain
    # red up to x=5: the blocks at x 4..7 are mixed, so their yellow texels (2 columns x 16 rows) are fringes
    mixed = texture_metrics(tex(lambda x, y: red if x < 6 else yellow), tex(lambda x, y: blue if x < 6 else pink), vis, w, h, True, judge)[0]
    assert mixed["fringes"] == 32, mixed
    assert squash(0.0, 0.1, 100, 3) == 0.0 and squash(1.0, 0.1, 100, 3) > 0.99 and squash(1.0, 0.1, 2, 3) == 0.0
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disc", default=str(HERE.parents[1] / "orig" / "GYQE01"))
    ap.add_argument("--out", default=str(HERE.parents[1] / ".scratch" / "audit"))
    ap.add_argument("--sources", help="comma-separated charIDs (default: every cloneable one)")
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--sheets", action="store_true", help="also write sheets/, crops/ and review_list.md")
    ap.add_argument("--from-csv", action="store_true", help="reuse metrics.csv instead of measuring again")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if args.from_csv:
        rows = list(csv.DictReader((out / "metrics.csv").open()))
    else:
        srcs = [int(x) for x in args.sources.split(",")] if args.sources else gui.SOURCES
        rows = []
        with Pool(args.jobs) as pool:
            for part in pool.imap_unordered(audit_source, [(args.disc, c) for c in srcs]):
                rows += part
                print("   %s done (%d rows, %.0f s)" % (cc.CHAR_NAMES[part[0]["source"]] if part else "-", len(part), time.time() - t0))
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
        write_sheets(out, args.disc, cases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
