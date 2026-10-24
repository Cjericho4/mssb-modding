#!/usr/bin/env python3
"""Offline checks for clone_character.py (stdlib only; runs on Windows).

    py test_clone.py --disc <extracted retail disc> --work <scratch folder>

1. LZSS codec round trip against real compressed files from ZZZZ.dat.
2. The retail variant mechanism: Blue Toad's track 0 == Red Toad's ARAM body with overlay 34 applied.
3. Runs the patcher (Mario -> Blue/Yellow/Green Toad), then re-parses the patched DOL, ZZZZ.dat,
   game.rel and fst.bin the way the game does and checks every clone entry, that the model files are
   structurally intact (root header, texture archive, layout/palette consistency), and that the
   output is byte-identical to the input outside the intended edits.
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_character as cc  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def diff_ranges(a, b):
    """List of (start, end) byte ranges where a and b differ (len must match)."""
    out = []
    n = min(len(a), len(b))
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            out.append((i, j))
            i = j
        else:
            i += 1
    if len(a) != len(b):
        out.append((n, max(len(a), len(b))))
    return out


def covered(ranges, allowed):
    """Every differing range must be inside one allowed (start, end) range."""
    merged = []
    for a, b in sorted(allowed):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    bad = []
    for s, e in ranges:
        if not any(a <= s and e <= b for a, b in merged):
            bad.append((s, e))
    return bad


def portrait_luminance(tf, t):
    """Decode a 50x50 CI8 + RGB5A3-TLUT portrait (8x4 tiles) to a list of luminance values (0 where transparent)."""
    pal = [struct.unpack_from(">H", tf.data, t["tlut"] + i * 2)[0] for i in range(256)]
    px = tf.pixels(t)
    out = []
    for y in range(t["h"]):
        for x in range(t["w"]):
            v = pal[px[((y // 4) * (cc.align(t["w"], 8) // 8) + x // 8) * 32 + (y % 4) * 8 + (x % 8)]]
            if v & 0x8000:
                out.append(0.3 * ((v >> 10) & 31) + 0.59 * ((v >> 5) & 31) + 0.11 * (v & 31))
            else:
                out.append((0.3 * ((v >> 8) & 15) + 0.59 * ((v >> 4) & 15) + 0.11 * (v & 15)) * 2 if (v >> 12) & 7 >= 4 else 0.0)
    return out


def correlation(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sa = sum((x - ma) ** 2 for x in a) ** 0.5
    sb = sum((x - mb) ** 2 for x in b) ** 0.5
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb + 1e-9)


def menu_textures(d, dl, addr):
    data, desc = cc.load_menu_file(d, dl, addr)
    return cc.TextureFile(data), desc


def check_menu_art(disc, dol, pdisc, pdol, C, slots, ui_source, old_zz, new_zz, relocated):
    """The donors' preview name, mini picture and grid portrait; everything else in both files
    decodes as before; descriptors and file bytes change only where intended.  Returns the
    ZZZZ.dat ranges the files may occupy in place."""
    print(" menu art (preview name, mini picture, grid portrait)")
    amap = cc.menu_art_map(*(cc.load_menu_file(disc, dol, a)[0] for a in (cc.ADDR_MENU_COMMON_DESC, cc.ADDR_MENU_GRID_DESC)))
    allowed_zz = []
    for label, addr in (("common menu file", cc.ADDR_MENU_COMMON_DESC), ("grid portrait file", cc.ADDR_MENU_GRID_DESC)):
        tf, desc = menu_textures(disc, dol, addr)
        ptf, pdesc = menu_textures(pdisc, pdol, addr)
        if not ui_source:
            check(pdesc == desc and pdisc.read_zzzz(desc[2], desc[3]) == disc.read_zzzz(desc[2], desc[3]), "%s untouched" % label)
            continue
        if relocated:
            check(pdesc[0] == desc[0] and pdesc[2] >= old_zz and pdesc[2] % 0x800 == 0 and pdesc[2] + pdesc[3] <= new_zz,
                  "%s moved to the appendix (ZZZZ.dat+0x%08X, %d B)" % (label, pdesc[2], pdesc[3]))
            check(pdisc.read_zzzz(desc[2], desc[3]) == disc.read_zzzz(desc[2], desc[3]), "%s: old slot left as it was" % label)
            dcopies = cc.MENU_DESC_COPIES[addr][0]
            check(bytes(pdol.data).count(struct.pack(">4I", *pdesc)) == dcopies and bytes(pdol.data).count(struct.pack(">4I", *desc)) == 0,
                  "%s: all %d DOL descriptor copies updated, none left over" % (label, dcopies))
        else:
            check(pdesc == desc, "%s descriptors unchanged (re-packed in place)" % label)
            raw = pdisc.read_zzzz(desc[2], desc[3])
            used = cc.lzss_decompress(raw, desc[1] & 0x0FFFFFFF)[1]
            check(raw[used:] == b"\0" * (desc[3] - used), "%s: re-packed stream fits (%d of %d B) and is zero padded" % (label, used, desc[3]))
            allowed_zz.append((desc[2], desc[2] + desc[3]))
        check(len(ptf.data) >= len(tf.data) and ptf.count == tf.count and ptf.data[:tf.archive + 4] == tf.data[:tf.archive + 4],
              "%s: root and texture count intact (%d -> %d B)" % (label, len(tf.data), len(ptf.data)))
        donor_tex = {}
        kinds = ("name", "mini") if addr == cc.ADDR_MENU_COMMON_DESC else ("grid",)
        for S, rc in slots:
            for k in kinds:
                donor_tex[amap[k][S]] = (k, S, rc)
        dec = lambda f, t: (f.pixels(t), f.tlut(t))  # noqa: E731
        others = [i for i in range(tf.count) if i not in donor_tex]
        check(all(dec(ptf, ptf.textures[i]) == dec(tf, tf.textures[i]) for i in others),
              "%s: all %d other textures decode identical (aliased buffers included)" % (label, len(others)))
        check(all(ptf.data[ptf.textures[i]["hdr"]:ptf.textures[i]["hdr"] + 0x20] == tf.data[tf.textures[i]["hdr"]:tf.textures[i]["hdr"] + 0x20] for i in others),
              "%s: all other texture headers identical" % label)
        new_bufs = []
        for i, (k, S, rc) in sorted(donor_tex.items()):
            src = tf.textures[amap[k][C]]
            pt = ptf.textures[i]
            want = tf.tlut(src) if k == "name" else cc.recolour_tlut(tf.tlut(src), rc.for_palette())
            hdr_same = ptf.data[pt["hdr"] + 8:pt["hdr"] + 0x20] == tf.data[tf.textures[i]["hdr"] + 8:tf.textures[i]["hdr"] + 0x20]
            check(ptf.pixels(pt) == tf.pixels(src) and ptf.tlut(pt) == want and hdr_same,
                  "%s %d of %s = %s's %s" % (k, i, cc.CHAR_NAMES[S], cc.CHAR_NAMES[C], "pixels" if k == "name" else "pixels + recoloured palette (%s)" % rc.spec))
            if k != "name" and want != tf.tlut(src):
                users = [t["index"] for t in ptf.textures if t["index"] != i and t["tlut"] < pt["tlut"] + 512 and pt["tlut"] < t["tlut"] + t["colours"] * 2]
                check(not users and pt["tlut"] % 32 == 0, "%s %d: recoloured palette is private (buffer 0x%X, 32-aligned)" % (k, i, pt["tlut"]))
                new_bufs.append((pt["tlut"], pt["tlut"] + 512))
            check(pt["data"] == ptf.textures[amap[k][C]]["data"], "%s %d: points at the source's pixel buffer" % (k, i))
        allowed = [(t["hdr"], t["hdr"] + 8) for i, t in ((i, tf.textures[i]) for i in donor_tex)] + new_bufs + [(len(tf.data), len(ptf.data))]
        bad = covered(diff_ranges(bytes(tf.data), bytes(ptf.data[:len(tf.data)])) + ([(len(tf.data), len(ptf.data))] if len(ptf.data) > len(tf.data) else []), allowed)
        check(not bad, "%s: byte-identical outside the donor headers' buffer offsets and the private palettes%s" % (label, "" if not bad else " unexpected %r" % bad[:3]))
    return allowed_zz


def check_rels(disc, dol, pdisc, pdol, C, slots, ui_source, colour_word, gameplay):
    """menus/game/challenge.rel: aaaa.dat changes only inside the REL slots, menus.rel only at the
    colour-word relocation addends (and moved-descriptor copies), the RELs' descriptor copies of the
    menu files agree with the DOL's."""
    print(" RELs (aaaa.dat)")
    descs = [dol.desc(a, 0) for a in cc.ADDR_REL_DESCS]
    ends = sorted(d[2] for d in descs) + [(disc.files / "aaaa.dat").stat().st_size]
    slots_aaaa = [(d[2], min(e for e in ends if e > d[2])) for d in descs]
    a, b = (disc.files / "aaaa.dat").read_bytes(), (pdisc.files / "aaaa.dat").read_bytes()
    check(len(a) == len(b) and not covered(diff_ranges(a, b), slots_aaaa), "aaaa.dat same size, byte-identical outside the three REL slots")
    moves = [(dol.desc(x, 0), pdol.desc(x, 0)) for x in (cc.ADDR_MENU_COMMON_DESC, cc.ADDR_MENU_GRID_DESC) if dol.desc(x, 0) != pdol.desc(x, 0)]
    donors = [S for S, _ in slots]
    for i, name in enumerate(cc.REL_NAMES):
        rel, prel = cc.load_rel(disc, dol, i), cc.load_rel(pdisc, pdol, i)
        if pdol.desc(cc.ADDR_REL_DESCS[i], 0) != descs[i]:
            check(pdol.desc(cc.ADDR_REL_DESCS[i], 0)[2] == descs[i][2], "%s re-packed in place" % name)
        allowed = []
        for old, new in moves:
            pos = [k for k in range(0, len(rel) - 15, 4) if rel[k:k + 16] == struct.pack(">4I", *old)]
            check(all(prel[k:k + 16] == struct.pack(">4I", *new) for k in pos) and bytes(prel).count(struct.pack(">4I", *old)) == 0,
                  "%s: %d descriptor copies of the moved file updated" % (name, len(pos)))
            allowed += [(k, k + 16) for k in pos]
        if i == 1 and gameplay:
            base, size = cc.rel_data_section(bytearray(rel))
            for off, rs in [cc.REL_VOICE] + cc.REL_TABLES:
                allowed += [(base + off + S * rs, base + off + (S + 1) * rs) for S in donors]
                check(all(prel[base + off + S * rs:base + off + (S + 1) * rs] == rel[base + off + C * rs:base + off + (C + 1) * rs] for S in donors),
                      "game.rel table 0x%X: donor rows = the source's" % off)
        if i == 0:
            entries = cc.colour_word_entries(rel)
            edited = [S for S in donors if ui_source and colour_word == "none" and cc.MENUS_COLOUR_LABEL.get(S, 0)]
            allowed += [(entries[S] + 4, entries[S] + 8) for S in edited]
            got = {c: struct.unpack_from(">I", prel, p + 4)[0] for c, p in entries.items()}
            want = {c: cc.MENUS_COLOUR_CASE[0 if c in edited else cc.MENUS_COLOUR_LABEL.get(c, 0)] for c in entries}
            check(got == want, "menus.rel colour word: charIDs %s -> label 0 (%s), every other case retail" % (
                edited, "--colour-word %s" % colour_word if ui_source else "--ui donor"))
        bad = covered(diff_ranges(rel, prel), allowed)
        check(len(rel) == len(prel) and not bad, "%s differs only in the intended %s" % (
            name, {0: "colour-word relocation addends", 1: "per-character tables", 2: "descriptor copies"}[i]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disc", required=True)
    ap.add_argument("--work", default=".clone_test_work", help="scratch folder for the patched copy (unused with --patched)")
    ap.add_argument("--source", default="mario")
    ap.add_argument("--slot", action="append", default=[])
    ap.add_argument("--iso", action="store_true", help="also write and verify an ISO (1.5 GB)")
    ap.add_argument("--patched", help="verify this already-patched folder instead of running the patcher (give the same --source/--slot you used)")
    ap.add_argument("--ui", choices=("source", "donor"), default="source", help="the --ui the patcher ran with (default source)")
    ap.add_argument("--colour-word", choices=("none", "donor"), default="none", help="the --colour-word the patcher ran with")
    ap.add_argument("--relocate-menu-art", action="store_true", help="run the patcher with its (test-only) forced move of the menu art files")
    ap.add_argument("--no-gameplay", action="store_true", help="the patcher ran with --no-gameplay")
    args = ap.parse_args()

    disc = cc.Disc(args.disc)
    dol = cc.Dol((disc.sys / "main.dol").read_bytes())
    C = cc.parse_char(args.source)
    slots = [cc.parse_slot_spec(s) for s in args.slot] or [(29, cc.Recolour("hue=blue")), (30, cc.Recolour("hue=green")), (31, cc.Recolour("hue=purple")), (32, cc.Recolour("hue=yellow"))]
    ui_source = args.ui == "source"
    regions = cc.source_regions(disc, dol, C)          # what clone() binds bare colours / region= to
    for _, rc in slots:
        rc.resolve(regions)

    print("0. retail lookup tables (the proof behind the fn_800698F8 rewrite)")
    main_arr = list(dol.read(0x80108DB8, 32))                      # mainCharArray: grid cell -> base charID
    var_arr = list(dol.read(0x80108DDC, 22))                       # variant charIDs, switch -> base cell
    var_cell = [0x15, 0x15, 0x16, 0x16, 0xD, 0xD, 0xD, 0xD, 0x19, 0x19, 0x19, 0xC, 0x14, 0x10, 0x10, 0x10, 0x10, 0x1F, 0x1F, 0x1F, 0x17, 0x17]

    def retail_grid(c):
        if c in main_arr:
            return main_arr.index(c)
        return var_cell[var_arr.index(c)]
    check(all(dol.u8(cc.ADDR_FAMILY + c * 6 + cc.FAMILY_UNIQUE) == retail_grid(c) for c in range(54)),
          "lbl_800E8558[c].uniqueIdx == fn_800698F8(c) for all 54 retail charIDs")
    check(dol.read(cc.ADDR_FINDCHAR, len(cc.FINDCHAR_RETAIL)) == cc.FINDCHAR_RETAIL, "retail fn_800698F8 head bytes")
    pdesc = cc.portrait_desc(dol)
    pfile = cc.TextureFile(cc.lzss_decompress(disc.read_zzzz(pdesc[2], pdesc[3]), pdesc[1] & 0x0FFFFFFF)[0])
    pors = pfile.portraits()
    check(len(set(pfile.pixels(t) + pfile.tlut(t) for t in pors)) == 54, "portrait file has 54 distinct 50x50 CI8 portraits (one per charID)")
    # charID ordering proof: the 26 base cells are byte-identical to the unique-keyed 32-portrait
    # file (checked elsewhere); for the colour variants, the most similar portrait (luminance
    # correlation of the decoded CI8 image) must be another member of the same colour family.
    lums = [portrait_luminance(pfile, t) for t in pors]
    fams = {}
    for c in range(54):
        fam = dol.u8(cc.ADDR_FAMILY + c * 6)
        if fam:
            fams.setdefault(fam, []).append(c)
    bad_order = []
    for members in fams.values():
        for v in members:
            if dol.u8(cc.ADDR_FAMILY + v * 6 + 3):
                continue  # the family base
            best = max((c for c in range(54) if c != v), key=lambda c: correlation(lums[v], lums[c]))
            if best not in members:
                bad_order.append((v, best))
    check(not bad_order, "portrait file is in charID order (every colour variant's nearest portrait is in its family)%s" % ("" if not bad_order else " bad: %r" % bad_order))
    rel0 = cc.load_game_rel(disc, dol)
    rbase, rsize = cc.rel_data_section(rel0)
    check(all(struct.unpack_from(">4I", rel0, rbase + o) == pdesc for o in cc.REL_PORTRAIT_DESCS), "game.rel portrait descriptor rows == the DOL's")

    print("1. LZSS round trip on retail files")
    for t in range(cc.SUBFILE_TRACKS):
        flags, sizeflags, off, comp = dol.desc(cc.ADDR_SUBFILES, C * cc.SUBFILE_TRACKS + t)
        raw = disc.read_zzzz(off, comp)
        size = sizeflags & 0x0FFFFFFF
        data, used = cc.lzss_decompress(raw, size)
        ok = len(data) == size and used <= comp
        rt = cc.lzss_decompress(cc.lzss_compress(data), size)[0] == data
        check(ok and rt, "subfile %d track %2d: %6d -> %6d B, used %6d/%6d, re-encode round trip %s" % (C, t, comp, size, used, comp, rt))
    for idx in (0, 13, 34, 432 + C, 54 + C * 7):
        data = cc.load_master(disc, dol, idx)
        check(cc.lzss_decompress(cc.lzss_compress(data), len(data))[0] == data, "master[%d] round trip (%d B)" % (idx, len(data)))

    print("2. retail variant mechanism (Blue Toad = Red Toad body + overlay 34)")
    t0_29 = cc.load_subfile(disc, dol, 29 * cc.SUBFILE_TRACKS)
    eff = cc.effective_body(disc, dol, 29)
    check(t0_29 == eff, "track0(BlueToad) == master[13] with master[34] written at root->texture")
    body = cc.Gpc(cc.load_master(disc, dol, 13))
    ov = cc.load_master(disc, dol, 34)
    check(len(ov) == body.archive_size, "overlay 34 length (%d) == texture archive size of body 13 (%d)" % (len(ov), body.archive_size))
    check(cc.load_master(disc, dol, 0) == cc.load_subfile(disc, dol, 0), "master[0] == Mario track 0")

    work = Path(args.work)
    if args.patched:
        print("3. verifying existing folder %s" % args.patched)
        out = Path(args.patched)
    else:
        print("3. run the patcher")
        out = work / "patched"
        argv = ["--disc", args.disc, "--out", str(out), "--source", args.source, "--force", "--ui", args.ui, "--colour-word", args.colour_word]
        argv += ["--relocate-menu-art"] if args.relocate_menu_art else []
        argv += ["--no-gameplay"] if args.no_gameplay else []
        for s in args.slot:
            argv += ["--slot", s]
        if args.iso:
            argv += ["--iso", str(work / "patched.iso")]
        cc.main(argv)

    print("4. verify the patched disc")
    pdisc = cc.Disc(out)
    pdol = cc.Dol((pdisc.sys / "main.dol").read_bytes())
    old_zz = disc.zzzz_size()
    new_zz = pdisc.zzzz_size()
    check(new_zz > old_zz, "ZZZZ.dat grew from %d to %d" % (old_zz, new_zz))
    # original ZZZZ.dat bytes unchanged except the stats block
    allowed_zz = [(cc.ZZZZ_STATS_OFFSET, cc.ZZZZ_STATS_OFFSET + cc.NUM_CHARS * cc.STATS_ROW)]
    if ui_source:
        allowed_zz.append((pdesc[2], pdesc[2] + pdesc[3]))       # the HUD portrait file, re-packed in place
    allowed_zz += check_menu_art(disc, dol, pdisc, pdol, C, slots, ui_source, old_zz, new_zz, args.relocate_menu_art)
    with open(disc.files / "ZZZZ.dat", "rb") as a, open(pdisc.files / "ZZZZ.dat", "rb") as b:
        pos = 0
        bad = False
        while pos < old_zz:
            n = min(1 << 22, old_zz - pos)
            x = a.read(n)
            y = b.read(n)
            if x != y:
                for s, e in diff_ranges(x, y):
                    if not any(lo <= pos + s and pos + e <= hi for lo, hi in allowed_zz):
                        bad = True
            pos += n
    check(not bad, "original ZZZZ.dat region unchanged outside the stats block%s" % (" and the HUD portrait and menu art files" if ui_source else ""))

    print(" ui (%s)" % args.ui)
    clone_ids = [s for s, _ in slots]
    if ui_source:
        check(pdol.read(cc.ADDR_FINDCHAR, len(cc.FINDCHAR_PATCH)) == cc.FINDCHAR_PATCH, "fn_800698F8 head == the uniqueIdx lookup (16 instructions)")
        check(pdol.read(cc.ADDR_FINDCHAR + len(cc.FINDCHAR_PATCH), 0x170 - len(cc.FINDCHAR_PATCH)) == dol.read(cc.ADDR_FINDCHAR + len(cc.FINDCHAR_PATCH), 0x170 - len(cc.FINDCHAR_PATCH)),
              "rest of fn_800698F8 untouched")
        src_u = dol.u8(cc.ADDR_FAMILY + C * 6 + cc.FAMILY_UNIQUE)
        for S, _ in slots:
            row, prow = dol.read(cc.ADDR_FAMILY + S * 6, 6), pdol.read(cc.ADDR_FAMILY + S * 6, 6)
            check(prow[cc.FAMILY_UNIQUE] == src_u and prow[:2] == row[:2] and prow[3:] == row[3:],
                  "lbl_800E8558[%d] = %s (uniqueIdx %d -> %d, family/base/colour kept)" % (S, list(prow), row[cc.FAMILY_UNIQUE], src_u))
        check(all(pdol.read(cc.ADDR_FAMILY + c * 6, 6) == dol.read(cc.ADDR_FAMILY + c * 6, 6) for c in range(54) if c not in clone_ids), "all other lbl_800E8558 rows unchanged")
        check(pdol.desc(cc.ADDR_PORTRAIT_DESC, 0) == pdesc and pdol.desc(cc.ADDR_PORTRAIT_DESC2, 0) == pdesc, "portrait descriptors unchanged (file re-packed in place)")
        praw = pdisc.read_zzzz(pdesc[2], pdesc[3])
        pdata, used = cc.lzss_decompress(praw, pdesc[1] & 0x0FFFFFFF)
        check(used <= pdesc[3] and praw[used:] == b"\0" * (pdesc[3] - used), "re-packed portrait stream fits (%d of %d B) and is zero padded" % (used, pdesc[3]))
        ppf = cc.TextureFile(pdata)
        ppors = ppf.portraits()
        check(len(pdata) == len(pfile.data) and all(ppf.data[t["hdr"]:t["hdr"] + 0x20] == pfile.data[t["hdr"]:t["hdr"] + 0x20] for t in pfile.textures)
              and ppf.data[:pfile.archive + 4] == pfile.data[:pfile.archive + 4], "portrait file: size, root and every texture header identical")
        others_same = all(ppf.pixels(t) == pfile.pixels(t) and ppf.tlut(t) == pfile.tlut(t) for i, t in enumerate(pors) if i not in clone_ids)
        check(others_same, "all other portraits identical")
        non_portrait = [t for t in pfile.textures if t not in pors]
        check(all(ppf.data[t["data"]:t["data"] + 64] == pfile.data[t["data"]:t["data"] + 64] for t in non_portrait), "non-portrait textures identical (first 64 B each)")
        for S, rc in slots:
            check(ppf.pixels(ppors[S]) == pfile.pixels(pors[C]) and ppf.tlut(ppors[S]) == cc.recolour_tlut(pfile.tlut(pors[C]), rc.for_palette()),
                  "portrait %d = %s's indices + recoloured palette (%s)" % (S, cc.CHAR_NAMES[C], rc.spec))
            check(ppf.tlut(ppors[S]) != pfile.tlut(pors[C]), "portrait %d palette actually changed" % S)
    else:
        check(pdol.read(cc.ADDR_FINDCHAR, 0x170) == dol.read(cc.ADDR_FINDCHAR, 0x170), "fn_800698F8 untouched")
        check(all(pdol.read(cc.ADDR_FAMILY + c * 6, 6) == dol.read(cc.ADDR_FAMILY + c * 6, 6) for c in range(54)), "lbl_800E8558 untouched")
        check(pdisc.read_zzzz(pdesc[2], pdesc[3]) == disc.read_zzzz(pdesc[2], pdesc[3]), "portrait file untouched")

    src_t0 = cc.Gpc(cc.load_subfile(disc, dol, C * cc.SUBFILE_TRACKS))
    src_t1 = cc.Gpc(cc.load_subfile(disc, dol, C * cc.SUBFILE_TRACKS + 1))
    src_body = cc.Gpc(cc.effective_body(disc, dol, C))
    src_case = dol.u32(cc.ADDR_JUMPTABLE + C * 4) if C < cc.JUMPTABLE_CASES else dol.u32(cc.ADDR_JUMPTABLE)

    def model_matches(new, ref, label):
        check(len(new.data) == len(ref.data), "%s: size %d" % (label, len(new.data)))
        check((new.texture, new.layout, new.geo, new.skn, new.extra) == (ref.texture, ref.layout, ref.geo, ref.skn, ref.extra), "%s: root header identical" % label)
        check(new.tex_count == ref.tex_count and all((a["w"], a["h"], a["fmt"], a["data"]) == (b["w"], b["h"], b["fmt"], b["data"]) for a, b in zip(new.textures, ref.textures)), "%s: texture headers identical" % label)
        texbytes = set()
        for t in ref.textures:
            texbytes.update(range(t["data"], t["data"] + t["size"]))
            texbytes.update(range(t["tlut"], t["tlut"] + 2 * t["colours"]))   # CI palettes (Petey)
        outside_same = all(new.data[i] == ref.data[i] for i in range(len(ref.data)) if i not in texbytes)
        check(outside_same, "%s: every byte outside the texture data is identical" % label)
        changed = sum(1 for i in texbytes if new.data[i] != ref.data[i])
        check(changed > 0, "%s: %d texture bytes recoloured" % (label, changed))
        try:
            li, gi = new.check()
            check(True, "%s: layout skinFileID %d < numDescriptors %d, %d bones" % (label, li["skinFileID"], gi["numDescriptors"], li["totalBones"]))
        except ValueError as e:
            check(False, "%s: %s" % (label, e))

    pblob = pdol.desc(cc.ADDR_MASTER, cc.MASTER_BLOB_ENTRY)
    oblob = dol.desc(cc.ADDR_MASTER, cc.MASTER_BLOB_ENTRY)
    for S, rc in slots:
        print(" slot %d %s" % (S, cc.CHAR_NAMES[S]))
        for t in range(2, cc.SUBFILE_TRACKS):
            check(pdol.desc(cc.ADDR_SUBFILES, S * 19 + t) == dol.desc(cc.ADDR_SUBFILES, C * 19 + t), "subfile track %d aliases the source" % t) if t in (2, 15, 18) else None
        check(all(pdol.desc(cc.ADDR_SUBFILES, S * 19 + t) == dol.desc(cc.ADDR_SUBFILES, C * 19 + t) for t in range(2, 19)), "subfile tracks 2..18 alias the source")
        for t, ref in ((0, src_t0), (1, src_t1)):
            flags, sizeflags, off, comp = pdol.desc(cc.ADDR_SUBFILES, S * 19 + t)
            check(flags == cc.LZSS_FLAGS and (sizeflags & cc.SIZE_COMPRESSED_BIT) and off % 32 == 0 and off >= old_zz and off + comp <= new_zz,
                  "track %d descriptor flags/size/offset sane (off 0x%X comp %d)" % (t, off, comp))
            g = cc.Gpc(cc.load_subfile(pdisc, pdol, S * 19 + t))
            model_matches(g, ref, "track %d model" % t)
        for k in range(cc.SUBITEMS_PER_CHAR):
            check(pdol.desc(cc.ADDR_MASTER, 54 + S * 7 + k) == dol.desc(cc.ADDR_MASTER, 54 + C * 7 + k), "master sub-item %d aliases the source" % k) if k == 0 else None
        check(all(pdol.desc(cc.ADDR_MASTER, 54 + S * 7 + k) == dol.desc(cc.ADDR_MASTER, 54 + C * 7 + k) for k in range(7)), "master sub-items 54+S*7+k alias the source")
        check(pdol.desc(cc.ADDR_MASTER, 432 + S) == dol.desc(cc.ADDR_MASTER, 432 + C), "master skeleton 432+S aliases the source")
        body, ov = pdol.pair(S)
        check(body == dol.pair(C)[0] and ov != -1, "pair = {%d, %d}" % (body, ov))
        oflags, osize, ooff, ocomp = pdol.desc(cc.ADDR_MASTER, ov)
        check(ooff >= oblob[3] and ooff + ocomp <= pblob[3], "overlay master[%d] lies in the appended part of the blob" % ov)
        ovdata = cc.load_master(pdisc, pdol, ov)
        check(len(ovdata) == src_body.archive_size, "overlay decompresses to the archive size %d" % len(ovdata))
        eff = cc.Gpc(cc.effective_body(pdisc, pdol, S))
        model_matches(eff, src_body, "ARAM body + overlay")
        check(pdol.u16(cc.ADDR_GLOVE + S * 2) == dol.u16(cc.ADDR_GLOVE + C * 2), "glove bone-list base")
        check(pdol.s16(cc.ADDR_MODELMAP + S * 2) == dol.s16(cc.ADDR_MODELMAP + C * 2), "stadium-object model map")
        check(pdol.u8(cc.ADDR_ROWKIND + S) == dol.u8(cc.ADDR_ROWKIND + C), "sub-item row kind")
        check(all(pdol.desc(cc.ADDR_SUBITEM_FILES, S * 6 + k) == dol.desc(cc.ADDR_SUBITEM_FILES, C * 6 + k) for k in range(6)), "disc-path sub-item files")
        if S < cc.JUMPTABLE_CASES:
            check(pdol.u32(cc.ADDR_JUMPTABLE + S * 4) == src_case, "per-character setup switch case")
        if not args.no_gameplay:
            check(all(pdol.s16(cc.ADDR_ANIMPARAMS + (k * 54 + S) * 2) == dol.s16(cc.ADDR_ANIMPARAMS + (k * 54 + C) * 2) for k in range(15)), "15 animation parameter tables")

    print(" blob")
    check(pblob[0] == oblob[0] and pblob[1] == pblob[3] and pblob[2] >= old_zz and pblob[2] % 0x800 == 0 and pblob[2] + pblob[3] <= new_zz, "blob descriptor {%08X %08X %08X %08X}" % pblob)
    check(pdisc.read_zzzz(pblob[2], oblob[3]) == disc.read_zzzz(oblob[2], oblob[3]), "moved blob starts with the original blob bytes")
    check(pdol.blob_alloc_size() == pblob[3], "fn_800227E4 ARAM allocation = 0x%X" % pdol.blob_alloc_size())
    check(pblob[3] - oblob[3] < 0x100000, "blob grew by %d bytes" % (pblob[3] - oblob[3]))
    # untouched entries of every tier
    touched_master = {cc.MASTER_BLOB_ENTRY}
    for S, _ in slots:
        touched_master.update(range(54 + S * 7, 54 + S * 7 + 7))
        touched_master.add(432 + S)
        touched_master.add(pdol.pair(S)[1])
    check(all(pdol.desc(cc.ADDR_MASTER, i) == dol.desc(cc.ADDR_MASTER, i) for i in range(516) if i not in touched_master), "all other master descriptors unchanged")
    check(all(pdol.desc(cc.ADDR_SUBFILES, i) == dol.desc(cc.ADDR_SUBFILES, i) for i in range(54 * 19) if i // 19 not in [s for s, _ in slots]), "all other subfile rows unchanged")
    check(all(pdol.pair(c) == dol.pair(c) for c in range(54) if c not in [s for s, _ in slots]), "all other body/overlay pairs unchanged")

    print(" DOL byte diff")
    allowed = [(dol.off(cc.ADDR_BLOB_LIS), dol.off(cc.ADDR_BLOB_LIS) + 4), (dol.off(cc.ADDR_BLOB_ADDI), dol.off(cc.ADDR_BLOB_ADDI) + 4),
               (dol.off(cc.ADDR_MASTER + 515 * 16), dol.off(cc.ADDR_MASTER + 515 * 16) + 16),
               ] + [(dol.off(a), dol.off(a) + 16) for a in cc.ADDR_REL_DESCS]  # RELs re-packed in place (size words)
    for a in (cc.ADDR_MENU_COMMON_DESC, cc.ADDR_MENU_GRID_DESC):
        if pdol.desc(a, 0) != dol.desc(a, 0):    # moved menu file: every DOL copy of its descriptor
            k = bytes(dol.data).find(dol.read(a, 16))
            while k >= 0:
                allowed.append((k, k + 16))
                k = bytes(dol.data).find(dol.read(a, 16), k + 1)
    text_allowed = [allowed[0], allowed[1]]
    if ui_source:
        allowed.append((dol.off(cc.ADDR_FINDCHAR), dol.off(cc.ADDR_FINDCHAR) + len(cc.FINDCHAR_PATCH)))
        text_allowed.append(allowed[-1])
        for S, _ in slots:
            allowed.append((dol.off(cc.ADDR_FAMILY + S * 6 + cc.FAMILY_UNIQUE), dol.off(cc.ADDR_FAMILY + S * 6 + cc.FAMILY_UNIQUE) + 1))
    for S, _ in slots:
        allowed += [(dol.off(cc.ADDR_SUBFILES + S * 19 * 16), dol.off(cc.ADDR_SUBFILES + S * 19 * 16) + 19 * 16),
                    (dol.off(cc.ADDR_MASTER + (54 + S * 7) * 16), dol.off(cc.ADDR_MASTER + (54 + S * 7) * 16) + 7 * 16),
                    (dol.off(cc.ADDR_MASTER + (432 + S) * 16), dol.off(cc.ADDR_MASTER + (432 + S) * 16) + 16),
                    (dol.off(cc.ADDR_MASTER + pdol.pair(S)[1] * 16), dol.off(cc.ADDR_MASTER + pdol.pair(S)[1] * 16) + 16),
                    (dol.off(cc.ADDR_PAIRS + S * 2), dol.off(cc.ADDR_PAIRS + S * 2) + 2),
                    (dol.off(cc.ADDR_GLOVE + S * 2), dol.off(cc.ADDR_GLOVE + S * 2) + 2),
                    (dol.off(cc.ADDR_MODELMAP + S * 2), dol.off(cc.ADDR_MODELMAP + S * 2) + 2),
                    (dol.off(cc.ADDR_ROWKIND + S), dol.off(cc.ADDR_ROWKIND + S) + 1),
                    (dol.off(cc.ADDR_SUBITEM_FILES + S * 6 * 16), dol.off(cc.ADDR_SUBITEM_FILES + S * 6 * 16) + 6 * 16),
                    (dol.off(cc.ADDR_JUMPTABLE + S * 4), dol.off(cc.ADDR_JUMPTABLE + S * 4) + 4)]
        for k in range(15):
            allowed.append((dol.off(cc.ADDR_ANIMPARAMS + (k * 54 + S) * 2), dol.off(cc.ADDR_ANIMPARAMS + (k * 54 + S) * 2) + 2))
    bad = covered(diff_ranges(dol.data, pdol.data), allowed)
    check(not bad, "main.dol differs only in the intended tables (%d differing ranges)%s" % (len(diff_ranges(dol.data, pdol.data)), "" if not bad else " unexpected: " + ", ".join("0x%X-0x%X" % r for r in bad[:5])))
    text_diffs = [r for r in diff_ranges(dol.data, pdol.data) if r[0] < dol.sections[1][0] + dol.sections[1][2]]
    check(not covered(text_diffs, text_allowed), ".text changed only at the two ARAM-size instructions%s" % (" and the fn_800698F8 head" if ui_source else ""))

    print(" stats block")
    st = pdisc.read_zzzz(cc.ZZZZ_STATS_OFFSET, 54 * 0xA0)
    so = disc.read_zzzz(cc.ZZZZ_STATS_OFFSET, 54 * 0xA0)
    for S, _ in slots:
        row = st[S * 0xA0:(S + 1) * 0xA0]
        src = so[C * 0xA0:(C + 1) * 0xA0]
        check(struct.unpack_from(">h", row, 0x24)[0] == S, "row %d keeps CharID %d" % (S, S))
        clone_cols = [s for s, _ in slots]
        same = all(row[i] == src[i] for i in range(0xA0) if i not in (0x24, 0x25) and not (0x3B <= i < 0x3B + 54 and i - 0x3B in clone_cols))
        check(same, "row %d otherwise equals the source's" % S)
        check(all(st[j * 0xA0 + 0x3B + S] == (so[C * 0xA0 + 0x3B + C] if j in clone_cols else so[j * 0xA0 + 0x3B + C]) for j in range(54)),
              "chemistry column %d equals the source's" % S)

    check_rels(disc, dol, pdisc, pdol, C, slots, ui_source, args.colour_word, not args.no_gameplay)
    print(" fst.bin / other files")
    fst = (disc.sys / "fst.bin").read_bytes()
    pfst = (pdisc.sys / "fst.bin").read_bytes()
    zent = [e for e, p in cc.fst_paths(pfst) if p == "ZZZZ.dat"][0]
    check(zent["size"] == new_zz, "fst.bin ZZZZ.dat size = %d" % zent["size"])
    check(not covered(diff_ranges(fst, pfst), [(zent["index"] * 12 + 8, zent["index"] * 12 + 12)]), "fst.bin differs only in that field")
    for name in ("sys/boot.bin", "sys/bi2.bin", "sys/apploader.img", "files/opening.bnr"):
        check((disc.root / name).read_bytes() == (pdisc.root / name).read_bytes(), "%s identical" % name)

    if args.iso and not args.patched:
        print(" iso")
        with open(work / "patched.iso", "rb") as iso:
            boot = iso.read(0x440)
            dol_off, fst_off, fst_size = struct.unpack_from(">III", boot, 0x420)
            iso.seek(fst_off)
            check(iso.read(fst_size) == pfst, "iso: fst at 0x%X equals the patched fst.bin" % fst_off)
            iso.seek(dol_off)
            check(iso.read(len(pdol.data)) == bytes(pdol.data), "iso: main.dol at 0x%X equals the patched DOL" % dol_off)
            for e, p in cc.fst_paths(pfst):
                iso.seek(e["offset"])
                with open(pdisc.files / p, "rb") as g:
                    same = True
                    left = e["size"]
                    while left and same:
                        n = min(left, 1 << 22)
                        same = iso.read(n) == g.read(n)
                        left -= n
                check(same, "iso: %s at 0x%X (%d B) matches" % (p, e["offset"], e["size"]))

    print()
    if FAILS:
        print("%d FAILURES" % len(FAILS))
        for f in FAILS:
            print("  " + f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
