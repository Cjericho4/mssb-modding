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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disc", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--source", default="mario")
    ap.add_argument("--slot", action="append", default=[])
    ap.add_argument("--iso", action="store_true", help="also write and verify an ISO (1.5 GB)")
    args = ap.parse_args()

    disc = cc.Disc(args.disc)
    dol = cc.Dol((disc.sys / "main.dol").read_bytes())
    C = cc.parse_char(args.source)
    slots = [cc.parse_slot_spec(s) for s in args.slot] or [(29, cc.Recolour("hue=blue")), (30, cc.Recolour("hue=green")), (31, cc.Recolour("hue=purple")), (32, cc.Recolour("hue=yellow"))]

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

    print("3. run the patcher")
    work = Path(args.work)
    out = work / "patched"
    argv = ["--disc", args.disc, "--out", str(out), "--source", args.source, "--force"]
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
    check(not bad, "original ZZZZ.dat region unchanged outside the stats block")

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
               (dol.off(cc.ADDR_MASTER + 515 * 16), dol.off(cc.ADDR_MASTER + 515 * 16) + 16)]
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
    text_end = dol.off(cc.ADDR_BLOB_ADDI) + 4
    text_diffs = [r for r in diff_ranges(dol.data, pdol.data) if r[0] < dol.sections[1][0] + dol.sections[1][2]]
    check(len(text_diffs) <= 2 and all(e - s <= 4 for s, e in text_diffs), ".text changed only at the two ARAM-size instructions")

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

    print(" game.rel / fst.bin / other files")
    rel = (disc.files / "game.rel").read_bytes()
    prel = (pdisc.files / "game.rel").read_bytes()
    base, size = cc.rel_data_section(rel)
    allowed = []
    for off, rs in [cc.REL_VOICE] + cc.REL_TABLES:
        for S, _ in slots:
            allowed.append((base + off + S * rs, base + off + (S + 1) * rs))
            check(prel[base + off + S * rs:base + off + (S + 1) * rs] == rel[base + off + C * rs:base + off + (C + 1) * rs], "game.rel table 0x%X row %d" % (off, S)) if off == cc.REL_VOICE[0] else None
    bad = covered(diff_ranges(rel, prel), allowed)
    check(not bad, "game.rel differs only in the per-character tables")
    fst = (disc.sys / "fst.bin").read_bytes()
    pfst = (pdisc.sys / "fst.bin").read_bytes()
    zent = [e for e, p in cc.fst_paths(pfst) if p == "ZZZZ.dat"][0]
    check(zent["size"] == new_zz, "fst.bin ZZZZ.dat size = %d" % zent["size"])
    check(not covered(diff_ranges(fst, pfst), [(zent["index"] * 12 + 8, zent["index"] * 12 + 12)]), "fst.bin differs only in that field")
    for name in ("sys/boot.bin", "sys/bi2.bin", "sys/apploader.img", "files/menus.rel", "files/challenge.rel", "files/aaaa.dat", "files/opening.bnr"):
        check((disc.root / name).read_bytes() == (pdisc.root / name).read_bytes(), "%s identical" % name)

    if args.iso:
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
