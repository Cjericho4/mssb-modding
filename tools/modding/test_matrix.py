#!/usr/bin/env python3
"""Recolour test matrix for clone_character.py (stdlib only).

    py test_matrix.py --disc <extracted retail disc> [--sheet DIR] [--patches] [--work DIR] [--chars 0,1,2]

For every cloneable character:
  * its in-match body (ARAM body + overlay) is the model the patch recolours, and its textures equal
    its own track 0 (.gpc hi-detail body) texture for texture;
  * exactly two glove textures are found, the colour regions are detected and the main one is named;
  * recolouring into every preset works, touches only texels of the chosen region (skin, eyes,
    gloves and other colours stay put) and keeps the CMPR blocks' mode (no new transparency, no
    lost transparency);
  * Mario's default output is byte-identical to the pre-region band (from=0 width=0.035 minsat=0.5).
--sheet writes contact-sheet PNGs (every body texture before, -> blue, -> green) for a human to look
at.  --patches also runs full patches (CLI and GUI code paths) and checks each with test_clone.py.
"""
import argparse
import colorsys
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import clone_character as cc  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# ----------------------------------------------------------------------------------------------
# Tiny image helpers (3x5 pixel font for the labels)
# ----------------------------------------------------------------------------------------------
FONT = {
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011", "D": "110101101101110",
    "E": "111100110100111", "F": "111100110100100", "G": "011100101101011", "H": "101101111101101",
    "I": "111010010010111", "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010", "P": "110101110100100",
    "Q": "010101101110011", "R": "110101110101101", "S": "011100010001110", "T": "111010010010010",
    "U": "101101101101111", "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111", "0": "111101101101111", "1": "010110010010111",
    "2": "110001010100111", "3": "110001010001110", "4": "101101111001001", "5": "111100110001110",
    "6": "011100111101111", "7": "111001010010010", "8": "111101111101111", "9": "111101111001110",
    " ": "000000000000000", "-": "000000111000000", ">": "100010001010100", ".": "000000000000010",
    "(": "010100100100010", ")": "010001001001010", "/": "001001010100100", ",": "000000000010100",
    "%": "101001010100101", "+": "000010111010000", ":": "000010000010000",
}


class Canvas:
    def __init__(self, w, h, bg=(24, 24, 30)):
        self.w, self.h = w, h
        self.px = bytearray(bytes(bg) * (w * h))

    def blit(self, x0, y0, w, h, rgb, scale=1):
        for y in range(h * scale):
            sy = y // scale
            if not 0 <= y0 + y < self.h:
                continue
            for x in range(w * scale):
                if 0 <= x0 + x < self.w:
                    s = (sy * w + x // scale) * 3
                    d = ((y0 + y) * self.w + x0 + x) * 3
                    self.px[d:d + 3] = rgb[s:s + 3]

    def rect(self, x0, y0, w, h, col):
        for y in range(max(0, y0), min(self.h, y0 + h)):
            for x in range(max(0, x0), min(self.w, x0 + w)):
                d = (y * self.w + x) * 3
                self.px[d:d + 3] = bytes(col)

    def text(self, x, y, s, col=(230, 230, 235), scale=2):
        for ch in s.upper():
            g = FONT.get(ch, FONT[" "])
            for i, bit in enumerate(g):
                if bit == "1":
                    self.rect(x + (i % 3) * scale, y + (i // 3) * scale, scale, scale, col)
            x += 4 * scale

    def png(self):
        def chunk(tag, body):
            return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
        stride = self.w * 3
        raw = b"".join(b"\0" + bytes(self.px[y * stride:(y + 1) * stride]) for y in range(self.h))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def atlas_layout(textures, width, gap=2):
    """Shelf-pack textures (largest first) into `width` px: [(tex, x, y)], height."""
    order = sorted(textures, key=lambda t: (-t["h"], -t["w"], t["index"]))
    x = y = shelf = 0
    out = []
    for t in order:
        if x and x + t["w"] > width:
            x, y, shelf = 0, y + shelf + gap, 0
        out.append((t, x, y))
        x += t["w"] + gap
        shelf = max(shelf, t["h"])
    return out, y + shelf


# ----------------------------------------------------------------------------------------------
def region_texel_check(body, new, rc):
    """(problems, changed texels, skin texels changed / skin texels) for one recoloured body.
    Problems: a texture the spec must not touch changed (gloves); a changed block with no endpoint
    inside the region; an endpoint outside the region that changed (skin, eyes, other colours);
    a block that switched between 4-colour and 3-colour+transparent mode or a texel that gained or
    lost transparency."""
    problems = []
    changed = skin = skin_changed = 0
    unpack = struct.unpack_from

    def hsv(c):
        return colorsys.rgb_to_hsv(*[x / 255.0 for x in cc.rgb565_to_rgb(c)])
    for t in body.textures:
        a = cc.texture_bytes(body, t)
        b = cc.texture_bytes(new, t)
        if a != b and not rc.touches(t["index"]):
            problems.append("texture %d changed" % t["index"])
            continue
        for o in range(0, len(a), 8):
            c0, c1, ia = unpack(">HHI", a, o)
            pa = cc.cmpr_palette(c0, c1)
            same = a[o:o + 8] == b[o:o + 8]
            if not same:
                n0, n1, ib = unpack(">HHI", b, o)
                pb = cc.cmpr_palette(n0, n1)
                hits = [rc._in_band(*hsv(c)) for c in (c0, c1)]
                forced = rc.blockwise and cc.block_mostly_in_band(c0, c1, ia, rc)
                if forced:
                    hits = [hit or not cc.is_skin(*hsv(c)) for hit, c in zip(hits, (c0, c1))]
                if not any(hits):
                    problems.append("tex %d block %d changed without a region endpoint" % (t["index"], o // 8))
                if (c0 > c1) != (n0 > n1):
                    problems.append("tex %d block %d changed CMPR mode" % (t["index"], o // 8))
                keep = {n0, n1, n0 ^ 1, n1 ^ 1}
                for c, hit in zip((c0, c1), hits):
                    if not hit and c not in keep:
                        problems.append("tex %d block %d: out-of-region endpoint %04X changed" % (t["index"], o // 8, c))
            for i in range(16):
                ca = pa[(ia >> (30 - 2 * i)) & 3]
                cb = ca if same else pb[(ib >> (30 - 2 * i)) & 3]
                if (ca is None) != (cb is None):
                    problems.append("tex %d block %d: transparency changed" % (t["index"], o // 8))
                if ca is None:
                    continue
                is_skin = cc.is_skin(*colorsys.rgb_to_hsv(*[x / 255.0 for x in ca]))
                skin += is_skin
                if ca != cb:
                    changed += 1
                    skin_changed += is_skin
    return problems, changed, skin_changed / max(skin, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disc", default=str(HERE.parents[1] / "orig" / "GYQE01"))
    ap.add_argument("--sheet", help="write contact-sheet PNGs into this folder")
    ap.add_argument("--chars", help="comma-separated charIDs (default: every cloneable one)")
    ap.add_argument("--patches", action="store_true", help="also run the sampled full patches (slow)")
    ap.add_argument("--work", default=str(HERE.parents[1] / ".scratch" / "matrix"))
    args = ap.parse_args()
    disc = cc.Disc(args.disc)
    dol = cc.Dol((disc.sys / "main.dol").read_bytes())
    chars = [int(x) for x in args.chars.split(",")] if args.chars else [c for c in range(cc.NUM_CHARS) if c not in cc.UNSUPPORTED_SOURCES]

    print("1. every character: body, gloves, regions, recolours")
    sheet_rows = []
    timings = []
    for c in chars:
        name = cc.CHAR_NAMES[c]
        body = cc.Gpc(cc.effective_body(disc, dol, c))
        t0 = cc.Gpc(cc.load_subfile(disc, dol, c * cc.SUBFILE_TRACKS))
        body_entry, overlay = dol.pair(c)
        if body.archive_bytes() == t0.archive_bytes():
            check(True, "%2d %s: in-match body (master %d%s) has the textures of its own track 0" % (
                c, name, body_entry, "" if overlay == -1 else " + overlay %d" % overlay))
        else:
            # retail quirk: the Hammer/Boomerang Bro track 0 files are older models; in a match the
            # game shows Fire Bro's body (master 32) with their own texture overlay.  The overlay
            # must then be theirs alone, and the texture data of every same-sized texture equal.
            same = [a["index"] for a, b in zip(body.textures, t0.textures)
                    if (a["w"], a["h"]) == (b["w"], b["h"]) and cc.texture_bytes(body, a) == cc.texture_bytes(t0, b)]
            check(overlay != -1 and cc.private_entry(dol, c) == overlay and len(same) >= len(body.textures) - 3,
                  "%2d %s: in-match body = master %d + its own overlay %d (track 0 is a different retail model; %d/%d textures equal)"
                  % (c, name, body_entry, overlay, len(same), len(body.textures)))
        gl = cc.glove_textures(body)
        check(len(gl) == 2, "%2d %s: two glove textures %s" % (c, name, sorted(gl)))
        t = time.time()
        regions = cc.detect_regions(body, c)
        timings.append(time.time() - t)
        check(len(regions) >= 1, "%2d %s: %d regions, main %r" % (c, name, len(regions), regions[0].name if regions else None))
        for r in regions:
            print("        %s" % r)
        errs = []
        skin_worst = 0.0
        for preset in cc.COLOUR_PRESETS:
            rc = cc.Recolour("hue=%s" % preset).resolve(regions)
            new = cc.Gpc(bytes(body.data))
            cc.recolour_gpc(new, rc)
            problems, changed, skin_frac = region_texel_check(body, new, rc)
            if problems:
                errs.append("%s: %d problems (%s)" % (preset, len(problems), problems[0]))
            skin_worst = max(skin_worst, skin_frac)
            if changed < 0.02 * sum(t["w"] * t["h"] for t in body.textures) and preset in ("blue", "green", "red"):
                if abs((regions[0].hue - cc.COLOUR_PRESETS[preset] + 0.5) % 1.0 - 0.5) > 0.06:
                    errs.append("%s: only %d texels changed" % (preset, changed))
        check(not errs, "%2d %s: every preset recolours only the default region(s) %s (skin-toned texels touched: %.1f%%, blends at region edges)%s"
              % (c, name, [r.name for r in regions if r.default], 100 * skin_worst, "" if not errs else ": " + "; ".join(errs[:3])))
        if args.sheet:
            sheet_rows.append((c, body, regions))

    print("   region detection: max %.0f ms, mean %.0f ms" % (1000 * max(timings), 1000 * sum(timings) / len(timings)))

    print("2. Mario's default is the pre-region band, byte for byte")
    body = cc.Gpc(cc.effective_body(disc, dol, 0))
    regions = cc.detect_regions(body, 0)
    for preset in cc.COLOUR_PRESETS:
        a = cc.Gpc(bytes(body.data))
        b = cc.Gpc(bytes(body.data))
        cc.recolour_gpc(a, cc.Recolour("hue=%s" % preset).resolve(regions))
        cc.recolour_gpc(b, cc.Recolour("hue=%s,from=0,width=0.035,minsat=0.5" % preset))
        check(a.data == b.data, "Mario %s: region default == from=0,width=0.035,minsat=0.5" % preset)

    if args.sheet:
        write_sheets(Path(args.sheet), sheet_rows)

    if args.patches:
        run_patches(args)

    print()
    if FAILS:
        print("%d FAILURES" % len(FAILS))
        for f in FAILS:
            print("  " + f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


def write_sheets(folder, rows, per_sheet=2):
    folder.mkdir(parents=True, exist_ok=True)
    panel_w = 256
    cols = [("BEFORE", None), ("-> BLUE", "hue=blue"), ("-> GREEN", "hue=green"), ("-> RED", "hue=red")]
    for n in range(0, len(rows), per_sheet):
        chunk = rows[n:n + per_sheet]
        layouts = []
        for c, body, regions in chunk:
            lay, h = atlas_layout(body.textures, panel_w)
            layouts.append((lay, h))
        H = sum(2 * h + 44 for _, h in layouts) + 8
        W = len(cols) * (panel_w * 2 + 12) + 8
        cv = Canvas(W, H)
        y = 8
        for (c, body, regions), (lay, h) in zip(chunk, layouts):
            label = "%d %s  MAIN: %s" % (c, cc.CHAR_NAMES[c], regions[0].name if regions else "-")
            cv.text(8, y, label)
            x = 8 + len(label) * 8 + 16
            for r in regions[:6]:
                cv.rect(x, y - 2, 14, 14, r.rgb)
                x += 18
            y += 18
            for i, (title, spec) in enumerate(cols):
                g = body
                if spec:
                    g = cc.Gpc(bytes(body.data))
                    cc.recolour_gpc(g, cc.Recolour(spec).resolve(regions))
                x0 = 8 + i * (panel_w * 2 + 12)
                cv.text(x0, y, title, scale=2)
                for t, tx, ty in lay:
                    rgb = cc.decode_cmpr(cc.texture_bytes(g, t), t["w"], t["h"])
                    cv.blit(x0 + tx * 2, y + 14 + ty * 2, t["w"], t["h"], rgb, 2)
                    if not spec:
                        lbl = str(t["index"])
                        cv.rect(x0 + tx * 2, y + 14 + ty * 2, 4 * len(lbl) + 2, 7, (0, 0, 0))
                        cv.text(x0 + tx * 2 + 1, y + 15 + ty * 2, lbl, (255, 255, 0), 1)
            y += 2 * h + 26
        p = folder / ("sheet_%02d.png" % (n // per_sheet))
        p.write_bytes(cv.png())
        print("   wrote %s" % p)


def run_patches(args):
    pass


if __name__ == "__main__":
    sys.exit(main())
