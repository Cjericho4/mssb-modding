#!/usr/bin/env python3
"""Local byte-for-byte checks against Clone Maker's Python reference (stdlib only).

All generated files live in a TemporaryDirectory under .scratch and are removed even on
failure. No original game data is printed. CI can use --synthetic without retail inputs.
"""
import argparse
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "tools/modding"
CORE = ROOT / "tools/clonemaker/core"
RAM_ISOS = False
SKIP_PASSED = set()
sys.path.insert(0, str(MOD))


def run(cmd):
    proc = subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)
    if proc.returncode:
        raise RuntimeError("command failed (%d): %s\n%s%s" %
                           (proc.returncode, " ".join(map(str, cmd)), proc.stdout, proc.stderr))
    return proc.stdout


def first_difference(a, b):
    """Return the first different byte (including a differing length), or None."""
    offset = 0
    with open(a, "rb") as fa, open(b, "rb") as fb:
        while True:
            aa, bb = fa.read(1 << 20), fb.read(1 << 20)
            if aa != bb:
                return offset + next((i for i, (x, y) in enumerate(zip(aa, bb)) if x != y),
                                     min(len(aa), len(bb)))
            if not aa:
                return None
            offset += len(aa)


def describe(path, offset, disc):
    import clone_character as cc
    dol = cc.Dol((disc / "sys/main.dol").read_bytes())
    if str(path).endswith("main.dol"):
        for name, addr, count in (("subfiles", cc.ADDR_SUBFILES, 54 * 19),
                                  ("master", cc.ADDR_MASTER, 516)):
            base = dol.off(addr, count * 16)
            if base <= offset < base + count * 16:
                return "%s descriptor %d" % (name, (offset - base) // 16)
    if str(path).endswith("ZZZZ.dat"):
        for c in range(54):
            for t in (0, 1):
                d = dol.desc(cc.ADDR_SUBFILES, c * 19 + t)
                if d[2] <= offset < d[2] + d[3]:
                    return "%s track %d compressed resource" % (cc.CHAR_NAMES[c], t)
    if str(path).endswith("aaaa.dat"):
        for i,a in enumerate(cc.ADDR_REL_DESCS):
            d=dol.desc(a,0)
            if d[2]<=offset<d[2]+d[3]:
                return "%s compressed resource" % cc.REL_NAMES[i]
    return "resource outside known descriptor ranges"


def compare_files(a, b, disc=None):
    off = first_difference(a, b)
    if off is not None:
        detail = describe(a, off, disc) if disc else "binary payload"
        if str(a).endswith(".gpc"):
            import clone_character as cc
            g=cc.Gpc(Path(a).read_bytes())
            for t in g.textures:
                if t["data"]<=off<t["data"]+t["size"] or t["tlut"]<=off<t["tlut"]+2*t["colours"]:
                    detail="texture %d (format %d)" % (t["index"],t["fmt"])
                    break
        if str(a).endswith(".iso"):
            import struct
            import clone_character as cc
            with open(a,"rb") as f:
                boot=f.read(0x440)
                fst_off,fst_size=struct.unpack_from(">II",boot,0x424)
                f.seek(fst_off)
                fst=f.read(fst_size)
            for e,p in cc.fst_paths(fst):
                if e["offset"]<=off<e["offset"]+e["size"]:
                    detail="%s + 0x%X" % (p,off-e["offset"])
                    break
        raise AssertionError("%s: first differing offset 0x%X (%s)" % (a, off, detail))


def compare_trees(a, b):
    aa = {p.relative_to(a) for p in a.rglob("*") if p.is_file()}
    bb = {p.relative_to(b) for p in b.rglob("*") if p.is_file()}
    if aa != bb:
        raise AssertionError("output file sets differ: %s" % sorted(aa ^ bb))
    for p in sorted(aa):
        compare_files(a / p, b / p, a)


# --- #29 voice: the intended differences from the frozen Python reference --------------------
SOUND_GROUPS = 0x800EF508   # lbl_800EF508: sound.N descriptors; voice bank of unique index u = row 5 + u
VOICE_BASE = 0x8168         # game.rel .data: u16 SFX base per charID
CHARSIZE = 0x800E8300       # main.dol charSizeMultipliers: 54 x 2 f32 model scale (#38)
FIELDING = [(0x79B4, 0x10), (0x7F10, 2)]  # game.rel .data, scaled by CHARSIZE (#38)


def voice_plan(disc, opts):
    """What the Rust core changes on purpose compared with the reference for these CLI options:
    the clone rows of game.rel 0x8168 always hold the source's SFX base, and with --ui donor each
    donor family's voice bank (other than the source's) is merged with the source's sounds."""
    import clone_character as cc
    def value(flag, default):
        return opts[opts.index(flag) + 1] if flag in opts else default
    source = cc.parse_char(value("--source", "mario"))
    slots = [cc.parse_char(opts[i + 1].split(":")[0]) for i, x in enumerate(opts) if x == "--slot"] or [29, 30, 31, 32]
    dol = cc.Dol((Path(disc) / "sys/main.dol").read_bytes())
    unique = lambda ch: dol.u8(cc.ADDR_FAMILY + ch * 6 + 2)
    rows = {}
    if value("--ui", "source") == "donor":
        for s in slots:
            if unique(s) != unique(source):
                rows.setdefault(5 + unique(s), []).append(s)
    return {"source": source, "slots": slots, "rows": rows, "no_gameplay": "--no-gameplay" in opts}


def voice_changes(plan):
    # every clone now differs on purpose: the model scale row (#38) is always copied
    return True


def chunked_equal(a, b, allowed, label):
    """Files a (reference) and b equal except inside `allowed` [start, end) ranges; the shorter
    one counts as zero-padded (the appended ZZZZ.dat tail)."""
    step = 1 << 22
    with open(a, "rb") as fa, open(b, "rb") as fb:
        off = 0
        while True:
            x, y = fa.read(step), fb.read(step)
            if not x and not y:
                return
            n = max(len(x), len(y))
            x, y = bytearray(x.ljust(n, b"\0")), bytearray(y.ljust(n, b"\0"))
            for s, e in allowed:
                s, e = max(s - off, 0), min(e - off, n)
                if s < e:
                    x[s:e] = y[s:e] = bytes(e - s)
            if x != y:
                k = next(i for i in range(n) if x[i] != y[i])
                raise AssertionError("%s: unexpected difference at 0x%X" % (label, off + k))
            off += n


def check_voice_group(merged, donor, sources, label):
    """Independent check with tools/datamap/sound.py: every FX of the donor and of each source
    resolves in the merged bank to the same FX entry, macro steps and samples (data, ADPCM
    coefficients, rate, length, loop); same group id; FX table sorted (dataGetFX bsearch)."""
    sys.path.insert(0, str(ROOT / "tools/datamap"))
    import sound as sd
    m = sd.Group(merged)
    assert len(m.groups) == 1 and m.groups[0]["id"] == sd.Group(donor).groups[0]["id"], label + ": group id"
    ids = [f["id"] for f in m.groups[0]["fx"]]
    assert ids == sorted(set(ids)), label + ": FX table not sorted/unique"
    want = set()
    for raw, base in [(donor, None)] + sources:
        o = sd.Group(raw)
        if base is not None:
            have = {f["id"] for f in o.groups[0]["fx"]}
            assert all(base + k in have for k in range(13)), label + ": source lines"
        for _, fx, smp in o.fx_table():
            want.add(fx["id"])
            mf = next(f for f in m.groups[0]["fx"] if f["id"] == fx["id"])
            assert mf == fx, (label, "FX 0x%X entry" % fx["id"])
            assert m.object_samples(fx["macro"]) == smp, (label, "FX 0x%X samples" % fx["id"])
            assert m.macro_steps(fx["macro"]) == o.macro_steps(fx["macro"]), (label, "FX 0x%X macro" % fx["id"])
            for sid in smp:
                a, b = m.sample(sid), o.sample(sid)
                assert (a.data[:a.byte_size()], a.adpcm, a.info, a.length_word, a.loop_start, a.loop_length) == \
                       (b.data[:b.byte_size()], b.adpcm, b.info, b.length_word, b.loop_start, b.loop_length), (label, "sample 0x%X" % sid)
        for key in ("macros", "samples", "curves", "keymaps", "layers"):
            assert set(o.groups[0][key]) <= set(m.groups[0][key]), (label, key)
    assert set(ids) == want, label + ": FX that no input has"


def check_voice_tree(a, b, disc, plan, label):
    """The Rust tree b equals the reference tree a except the intended #29 voice changes."""
    import struct
    import clone_character as cc
    pa, pb = cc.Disc(a), cc.Disc(b)
    da = cc.Dol((pa.sys / "main.dol").read_bytes())
    db = cc.Dol((pb.sys / "main.dol").read_bytes())
    rows = plan["rows"]
    rel_changed = [True, bool(rows)]  # game.rel (0x8168 and #38 fielding rows), challenge.rel
    # main.dol: only the merged rows' sound descriptors and the re-packed RELs' descriptors
    allowed = [(da.off(SOUND_GROUPS + r * 16, 16), da.off(SOUND_GROUPS + r * 16, 16) + 16) for r in rows]
    allowed += [(da.off(cc.ADDR_REL_DESCS[1 + i], 16), da.off(cc.ADDR_REL_DESCS[1 + i], 16) + 16)
                for i in range(2) if rel_changed[i]]
    # #38: the clone rows of charSizeMultipliers hold the source's scale
    src_size = da.data[da.off(CHARSIZE + plan["source"] * 8, 8):][:8]
    for s in plan["slots"]:
        o = da.off(CHARSIZE + s * 8, 8)
        assert db.data[o:o + 8] == src_size, (label, "charSizeMultipliers slot", s)
        allowed.append((o, o + 8))
    assert len(da.data) == len(db.data), label + ": main.dol size"
    chunked_equal(pa.sys / "main.dol", pb.sys / "main.dol", allowed, label + " main.dol")
    # aaaa.dat: byte-identical outside the re-packed RELs; inside them, the expanded RELs differ
    # only at the clone rows of 0x8168 (game.rel) and at the merged rows' descriptor copies
    ends = sorted(da.desc(x, 0)[2] for x in cc.ADDR_REL_DESCS) + [(pa.files / "aaaa.dat").stat().st_size]
    allowed = []
    for i in range(2):
        if rel_changed[i]:
            off = da.desc(cc.ADDR_REL_DESCS[1 + i], 0)[2]
            allowed.append((off, min(e for e in ends if e > off)))
            assert db.desc(cc.ADDR_REL_DESCS[1 + i], 0)[2] == off, label + ": REL moved"
    chunked_equal(pa.files / "aaaa.dat", pb.files / "aaaa.dat", allowed, label + " aaaa.dat")
    ga, gb = cc.load_rel(pa, da, 1), cc.load_rel(pb, db, 1)
    # .data located on the clean game.rel (its signature rows may be clone rows now)
    od = cc.Disc(disc)
    base, _ = cc.rel_data_section(bytearray(cc.load_rel(od, cc.Dol((od.sys / "main.dol").read_bytes()), 1)))
    src_base = struct.unpack_from(">H", gb, base + VOICE_BASE + plan["source"] * 2)[0]
    for s in plan["slots"]:
        assert struct.unpack_from(">H", gb, base + VOICE_BASE + s * 2)[0] == src_base, (label, "0x8168 slot", s)
    ga = bytearray(ga)
    for s in plan["slots"]:
        ga[base + VOICE_BASE + s * 2:base + VOICE_BASE + s * 2 + 2] = gb[base + VOICE_BASE + s * 2:base + VOICE_BASE + s * 2 + 2]
    if not plan["no_gameplay"]:
        for off, rs in FIELDING:
            src = ga[base + off + plan["source"] * rs:][:rs]
            for s in plan["slots"]:
                o = base + off + s * rs
                assert gb[o:o + rs] == src, (label, "game.rel 0x%X slot" % off, s)
                ga[o:o + rs] = src
    assert bytes(ga) == bytes(gb), label + ": game.rel differs outside 0x8168 and the #38 rows"
    ca, cb = bytearray(cc.load_rel(pa, da, 2)), cc.load_rel(pb, db, 2)
    zz = []
    for r in rows:
        old, new = da.desc(SOUND_GROUPS, r), db.desc(SOUND_GROUPS, r)
        assert old != new and new[0] == 0 and new[1] == new[3] and new[2] % 0x800 == 0, (label, "sound.%d descriptor" % r)
        assert cc.replace_desc(ca, old, new) >= 1, (label, "challenge.rel copy of sound.%d" % r)
        zz.append((new[2], new[2] + new[3]))
    assert bytes(ca) == bytes(cb), label + ": challenge.rel differs outside the sound descriptors"
    # ZZZZ.dat: the reference bytes plus the merged banks appended after the reference's data
    za, zb = pa.zzzz_size(), pb.zzzz_size()
    used = max([za] + [e for _, e in zz])
    assert zb == cc.align(used, 0x8000), (label, "ZZZZ.dat size", za, zb)
    chunked_equal(pa.files / "ZZZZ.dat", pb.files / "ZZZZ.dat", zz, label + " ZZZZ.dat")
    for s, e in zz:  # only zero padding of the reference under the merged banks
        assert s >= za or not pa.read_zzzz(s, min(e, za) - s).strip(b"\0"), (label, "merged bank over reference data")
    # fst.bin: only the ZZZZ.dat size
    fa, fb = (pa.sys / "fst.bin").read_bytes(), (pb.sys / "fst.bin").read_bytes()
    ent = [e for e, p in cc.fst_paths(fb) if p == "ZZZZ.dat"][0]
    assert ent["size"] == zb, label + ": fst.bin ZZZZ.dat size"
    o = ent["index"] * 12 + 8
    assert fa[:o] == fb[:o] and fa[o + 4:] == fb[o + 4:], label + ": fst.bin"
    # every other file byte-identical
    names = {p.relative_to(a) for p in a.rglob("*") if p.is_file()}
    assert names == {p.relative_to(b) for p in b.rglob("*") if p.is_file()}, label + ": file sets"
    for p in sorted(names):
        if str(p) not in ("sys/main.dol", "sys/fst.bin", "files/aaaa.dat", "files/ZZZZ.dat"):
            compare_files(a / p, b / p, a)
    # the merged banks, read back with the datamap reader
    for r, (s, e) in zip(rows, zz):
        u = r - 5
        src_row = 5 + da.u8(cc.ADDR_FAMILY + plan["source"] * 6 + 2)
        dd = da.desc(SOUND_GROUPS, r)
        sd_ = da.desc(SOUND_GROUPS, src_row)
        check_voice_group(pb.read_zzzz(s, e - s), pa.read_zzzz(dd[2], dd[3]),
                          [(pa.read_zzzz(sd_[2], sd_[3]), src_base)], "%s sound.%d" % (label, r))
    return len(rows)


def voice_log(text, plan):
    """Drop the log lines the voice changes add or alter."""
    drop = ["voice: ", "game.rel: "]
    if plan["rows"]:
        drop += ["challenge.rel: ", "ZZZZ.dat: ", "DRY RUN summary: ", "wrote "]
    return "".join(line for line in text.splitlines(True) if not any(line.startswith(d) for d in drop))


def check_voice_iso(tree, iso, work):
    """The Rust ISO is the reference ISO writer's image of the Rust tree."""
    import clone_character as cc
    expected = work / "voice-expected.iso"
    try:
        cc.write_iso(cc.Disc(tree), expected, lambda _: None)
        compare_files(expected, iso)
    finally:
        expected.unlink(missing_ok=True)


def codec(probe, work, label, data, size=None, lb=11, rp=4):
    import clone_character as cc
    inp, out = work / "codec.in", work / "codec.out"
    inp.write_bytes(data)
    if size is not None:
        expected, used = cc.lzss_decompress(data, size, lb, rp)
        actual_used = int(run([probe, "codec", "decode", inp, out, size, lb, rp, 0]))
        assert out.read_bytes() == expected and used == actual_used, label + " decode"
        data = expected
        inp.write_bytes(data)
    for mode, fn, chain in (("greedy", cc.lzss_compress, 48),
                            ("optimal", cc.lzss_compress_optimal, 256)):
        t = time.perf_counter()
        expected = fn(data, lb, rp, chain)
        py_time = time.perf_counter() - t
        t = time.perf_counter()
        run([probe, "codec", mode, inp, out, 0, lb, rp, chain])
        rs_time = time.perf_counter() - t
        actual = out.read_bytes()
        if actual != expected:
            off = next((i for i, (x, y) in enumerate(zip(actual, expected)) if x != y),
                       min(len(actual), len(expected)))
            raise AssertionError("%s %s first differing offset 0x%X" % (label, mode, off))
        inp.write_bytes(actual)
        used = int(run([probe, "codec", "decode", inp, out, len(data), lb, rp, 0]))
        py_dec, py_used = cc.lzss_decompress(actual, len(data), lb, rp)
        assert out.read_bytes() == py_dec == data and used == py_used, label + " roundtrip"
        inp.write_bytes(data)
        yield py_time, rs_time


def synthetic(probe, work):
    rng = random.Random(31)
    samples = [b"", b"x", bytes(range(256)), bytes([0]) * 8193,
               b"abcabcd" * 1000, bytes(rng.randrange(256) for _ in range(9000))]
    for i, data in enumerate(samples):
        for lb, rp in ((11, 4), (3, 2)):
            list(codec(probe, work, "synthetic %d" % i, data, lb=lb, rp=rp))
    print("PASS synthetic: 12 inputs, both encoders and decoder")


def retail_codecs(probe, work, disc):
    import clone_character as cc
    d = cc.Disc(disc)
    dol = cc.Dol((d.sys / "main.dol").read_bytes())
    resources = {}
    def add(label, name, desc, base=0):
        flags, size, off, comp = desc
        if size & cc.SIZE_COMPRESSED_BIT:
            resources.setdefault((name, base + off, comp, size & 0x0FFFFFFF), label)
    for c in range(54):
        for t in (0, 1):
            add("%s track %d" % (cc.CHAR_NAMES[c], t), "ZZZZ.dat",
                dol.desc(cc.ADDR_SUBFILES, c * 19 + t))
        for m in dol.pair(c):
            if m != -1:
                add("master %d" % m, "ZZZZ.dat", dol.desc(cc.ADDR_MASTER, m),
                    dol.desc(cc.ADDR_MASTER, cc.MASTER_BLOB_ENTRY)[2])
    for a in (cc.ADDR_PORTRAIT_DESC, cc.ADDR_MENU_COMMON_DESC, cc.ADDR_MENU_GRID_DESC):
        add("UI %08X" % a, "ZZZZ.dat", dol.desc(a, 0))
    for i, a in enumerate(cc.ADDR_REL_DESCS):
        add(cc.REL_NAMES[i], "aaaa.dat", dol.desc(a, 0))
    py = rs = 0.0
    for k, ((name, off, comp, size), label) in enumerate(resources.items(), 1):
        raw = d.read_zzzz(off, comp) if name == "ZZZZ.dat" else d.read_aaaa(off, comp)
        for p, r in codec(probe, work, label, raw, size):
            py += p
            rs += r
        print("PASS codecs %d/%d: %s" % (k, len(resources), label), flush=True)
    print("PASS retail codecs: %d resources; encode Python %.3fs Rust %.3fs (includes Rust process startup)" %
          (len(resources), py, rs))


def helpers(probe, disc):
    import clone_character as cc
    d = cc.Disc(disc)
    dol = cc.Dol((d.sys / "main.dol").read_bytes())
    expected = []
    cc.preflight(d, dol, expected.append)
    for o, a, s in dol.sections:
        expected.append("section %d %d %d" % (o, a, s))
    expected.append("dol_size %d" % cc.dol_size(dol.data))
    for e, p in cc.fst_paths((d.sys / "fst.bin").read_bytes()):
        expected.append("fst %d %d %d %s" % (e["index"], e["offset"], e["size"], p))
    for c in range(54):
        b, o = dol.pair(c)
        pe = cc.private_entry(dol, c)
        expected.append("char %d %d %d %s %s" % (c, b, o, "None" if pe is None else "Some(%d)" % pe, list(dol.family(c))))
    for i in range(3):
        rel = cc.load_rel(d, dol, i)
        mid, secs, imps = cc.rel_header(rel)
        expected.append("rel %d %d %s %s" % (i, mid, secs, imps))
        for p, sec, off, kind, ts, add in cc.rel_self_relocs(rel):
            expected.append("reloc %d %s %d %d %d %d" % (p, "None" if sec is None else "Some(%d)" % sec, off, kind, ts, add))
    actual = run([probe, "inspect", disc]).splitlines()
    assert actual == expected, next(((a, b) for a, b in zip(actual, expected) if a != b), "line count")
    print("PASS helpers: DOL sections, FST paths, 54 character tables, all REL self-relocations, preflight")


def iso_roundtrip(probe, work, disc):
    import clone_character as cc
    a, b, extracted = work / "python.iso", work / "rust.iso", work / "extracted"
    try:
        cc.write_iso(cc.Disc(disc), a, lambda _: None)
        run([probe, "write-iso", disc, b])
        compare_files(a, b)
        run([probe, "extract-iso", a, extracted])
        # Compare only FST/system contents: decomp extra files are not on the disc.
        py_extracted = work / "py-extracted"
        cc.extract_iso(a, py_extracted, lambda _: None)
        compare_trees(py_extracted, extracted)
        b.unlink()
        run([probe, "write-iso", extracted, b])
        compare_files(a, b)
        print("PASS ISO: writers, extractors and extract/write roundtrip byte-identical")
    finally:
        for p in (a, b):
            p.unlink(missing_ok=True)
        for p in (extracted, work / "py-extracted"):
            if p.exists():
                shutil.rmtree(p)


def textures(probe, work, disc):
    import clone_character as cc
    import json
    d = cc.Disc(disc)
    dol = cc.Dol((d.sys / "main.dol").read_bytes())
    for c in range(54):
        folder = work / "textures"
        try:
            run([probe, "textures", disc, c, folder])
            g = cc.Gpc(cc.effective_body(d, dol, c))
            regions = cc.source_regions(d, dol, c, g)
            actual = json.loads((folder / "regions.json").read_text())
            assert len(actual) == len(regions), (c, "region count", len(actual), len(regions))
            for a, r in zip(actual, regions):
                for k in r.__slots__:
                    value = getattr(r, k)
                    av = a[k]
                    if k == "skip":
                        av, value = set(av), set(value)
                    if k == "rgb":
                        value = list(value)
                    if isinstance(value, float):
                        assert abs(av-value) <= 1e-14, (c,k,av,value)
                    else:
                        assert av == value, (c,k,av,value)
            for t in g.textures:
                assert (folder / ("decode-%d.rgb" % t["index"])).read_bytes() == cc.decode_texture(cc.texture_bytes(g, t), t), (c, t["index"], "decode")
            for preset in [*cc.COLOUR_PRESETS, "hue=0.317,sat=1.1,keeplum=0.73", "tint=00ff99:0.3", "all=1,hue=yellow", "hue=blue,region=0"]:
                rc = cc.Recolour(preset).resolve(regions)
                out = cc.Gpc(bytes(g.data))
                cc.recolour_gpc(out, rc)
                expected = folder / "expected.gpc"
                expected.write_bytes(out.data)
                compare_files(expected, folder / ("recolour-%s.gpc" % preset))
                # Every possible palette value catches RGB5A3 alpha and rounding differences.
                pal = rc.for_palette()
                data = b"".join(pal.map_rgb5a3(v).to_bytes(2,"big") for v in range(65536))
                assert data == (folder / ("palette-%s.bin" % preset)).read_bytes(), (c,preset,"palette")
            print("PASS textures %d %s: regions, decode, 12 recolours, all RGB5A3 values" % (c,cc.CHAR_NAMES[c]), flush=True)
        finally:
            if folder.exists():
                shutil.rmtree(folder)


def cli_checks(cli, probe, work, disc):
    import json
    import clone_gui as g
    cases = [[],["--help"],["--disc",str(disc)],["--disc",str(disc),"--out",str(disc)],
             ["--disc",str(disc),"--ui","bad"],["--disc"],["--unknown"],
             ["--disc",str(disc),"--dry-run"],
             ["--disc",str(disc),"--dry-run","--source","dk","--slot","29:blue","--slot","30:green"],
             ["--disc",str(disc),"--dry-run","--slot","29:blue","--slot","29:green"],
             ["--disc",str(disc),"--dry-run","--source","54"],
             ["--disc",str(disc),"--dry-run","--slot","12:blue"],
             ["--disc",str(work/"missing"),"--dry-run"],
             ["--disc",str(disc),"--list-slots"]]
    cases += [["--disc",str(disc),"--list-regions","--source",str(c)] for c in range(54)]
    for i,args in enumerate(cases):
        def command(cmd):
            p=subprocess.run([str(x) for x in cmd],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            return p.returncode,p.stdout,p.stderr
        expected=command([sys.executable,MOD/"clone_character.py",*args])
        actual=command([cli,*args])
        assert expected==actual,(i,args,expected,actual)
    print("PASS CLI: %d help/usage/error/dry-run/listing comparisons" % len(cases),flush=True)
    base={"game":str(disc),"output":str(work/"output.iso"),"source":0,"clones":[{"slot":29,"colour":"blue"}],"keeplum":0.5,"regions":[0],"ui_source":True,"copy_stats":True,"keep_colour_word":False,"keep_work":False}
    tests=[({},4_000_000_000),({"clones":[]},4_000_000_000),
           ({"clones":[{"slot":29,"colour":"blue"}]*2},4_000_000_000),
           ({"source":29},4_000_000_000),({"output":str(work/"x.txt")},4_000_000_000),
           ({"output":str(work/"missing/x.iso")},4_000_000_000),
           ({"output":str(disc/"x.iso")},4_000_000_000),({},10)]
    for changes,free in tests:
        job={**base,**changes}
        py_job=g.Job(job["game"],job["output"],job["source"],[(r["slot"],g.colour_spec(r["colour"])) for r in job["clones"]])
        try:
            g.validate_job(py_job,free_bytes=free)
            expected=None
        except SystemExit as e:
            expected={"error":str(e)}
        path=work/"validation.json"
        path.write_text(json.dumps(job))
        assert json.loads(run([probe,"validate",path,free]))==expected
    print("PASS job validation: %d valid/invalid path/row/space comparisons" % len(tests),flush=True)


def api_checks(probe, work, disc):
    import json
    import base64
    import clone_gui as g
    import test_gui_logic as tg
    folder = work / "api"
    run([probe, "api", disc, folder])
    game, ok, message = g.load_game(disc)
    snap = json.loads((folder / "snapshot.json").read_text())
    assert snap["open"] == {"ok": ok, "message": message, "kind": "iso" if disc.is_file() else "folder"}
    assert snap["sources"] == [{"id": c, "name": g.pretty_name(c)} for c in g.SOURCES]
    assert snap["output"] == str(g.default_output(disc))
    for source in g.SOURCES:
        choices = g.donor_choices(game.dol, source)
        assert snap["choices"][source] == [{"id": c.id, "name": c.name, "note": c.note, "label": c.label, "recommended": c.recommended} for c in choices]
        pv = game.preview(source)
        expected = {"regions": [{"index": i,"label":g.region_label(r),"default":r.default,"swatch":r.swatch} for i,r in enumerate(pv.regions)],
                    "slots": [{"id":c.id,"label":c.label} for c in choices],
                    "default_rows": [{"slot":s,"colour":col} for s,col in g.default_rows(choices)]}
        assert snap["source_info"][source] == expected, (source,"source_info")
        slots = [s for s,_ in g.default_rows(choices)]
        assert snap["hints"][source] == g.pick_hint(game.dol,source,slots)
        assert tg.png_info((folder / ("before-%d.png" % source)).read_bytes()) == tg.png_info(pv.before_png)
        for i,colour in enumerate(("blue","green","#00ff00")):
            result=json.loads((folder/("preview-%d-%d.json" % (source,i))).read_text())
            spec=g.colour_spec(colour)
            assert tg.png_info(base64.b64decode(result["before_png"])) == tg.png_info(pv.before_png)
            assert tg.png_info(base64.b64decode(result["after_png"])) == tg.png_info(pv.after_png(spec)), (source,colour,"preview pixels")
            rc=g.cc.Recolour(spec).resolve(pv.regions)
            caption="All %d textures of %s, before and as %s in %s (changes: %s)" % (pv.tex_count,g.pretty_name(source),g.pretty_name(slots[0]),g.colour_label(colour),rc.describe())
            assert result["caption"] == caption
        print("PASS API %d %s: donors, defaults, hint, regions, preview PNG pixels" % (source,g.pretty_name(source)),flush=True)
    shutil.rmtree(folder)


def gui_jobs(probe, work, disc):
    import json
    import clone_character as cc
    import clone_gui as g
    # Build the ISO inside scratch and remove it along with all job outputs.
    source_iso=work/"input.iso"
    cc.write_iso(cc.Disc(disc),source_iso,lambda _:None)
    try:
        for label,source,input_path,keep,regions,colours,donors,ui,keeplum in (
                ("iso-dk",2,source_iso,True,[0,1],["blue","green"],[29,30],True,0.5),
                ("folder-luigi",1,disc,False,[0],["#00ff00"],[29],True,0.5),
                ("iso-default-toads",0,source_iso,True,[0],["blue","green","purple","yellow"],[29,30,31,32],True,0.5),
                ("folder-donor-custom",0,disc,False,[0],["#20c040"],[1],False,0.7)):
            folder=work/label
            folder.mkdir()
            try:
                py_out=folder/"python.iso"
                rs_out=folder/"rust.iso"
                game,ok,_=g.load_game(input_path)
                assert ok
                selected=g.region_selection(game.preview(source).regions,regions)
                specs=[(slot,g.colour_spec(colour,keeplum,regions=selected)) for slot,colour in zip(donors,colours)]
                py_job=g.Job(input_path,py_out,source,specs,keep_work=True,ui_source=ui)
                py=g.run_job(py_job,lambda _:None)
                job={"game":str(input_path),"output":str(rs_out),"source":source,"clones":[{"slot":s,"colour":colour} for (s,_),colour in zip(specs,colours)],"keeplum":keeplum,"regions":regions,"ui_source":ui,"copy_stats":True,"keep_colour_word":False,"keep_work":keep}
                jobfile=folder/"job.json"
                jobfile.write_text(json.dumps(job))
                events=[json.loads(line) for line in run([probe,"job",jobfile]).splitlines()]
                assert events[-1]["event"] == "done"
                done=events[-1]["payload"]
                plan=voice_plan(disc,["--source",str(source),"--ui","source" if ui else "donor"]+sum((["--slot",str(s)] for s,_ in specs),[]))
                if voice_changes(plan):
                    trees=[folder/"py-tree",folder/"rs-tree"]
                    try:
                        for iso,tree in zip((py_out,rs_out),trees):
                            cc.extract_iso(iso,tree,lambda _:None)
                        check_voice_tree(*trees,disc,plan,label)
                        check_voice_iso(trees[1],rs_out,folder)
                    finally:
                        for tree in trees:
                            shutil.rmtree(tree,ignore_errors=True)
                else:
                    compare_files(py_out,rs_out)
                assert done["hint"] == g.pick_hint(game.dol,source,[s for s,_ in specs],ui)
                if keep and voice_changes(plan):
                    check_voice_tree(Path(py["work"]),Path(done["work"]),disc,plan,label)
                elif keep:
                    compare_trees(Path(py["work"]),Path(done["work"]))
                else:
                    assert done["work"] is None
                    assert set(folder.glob("MSSB-Clone-Maker-work-*")) == {Path(py["work"]).parent}, "Rust work directory was not cleaned"
                progress=[e["payload"] for e in events if e["event"]=="progress"]
                assert progress and all(0<=p["fraction"]<=1 for p in progress)
                log=rs_out.with_suffix(".log").read_text()
                event_log="".join(e["payload"]["line"]+"\n" for e in events if e["event"]=="log")
                assert log==event_log
                py_log=py_out.with_suffix(".log").read_text()
                rs_work=next(e["payload"]["line"].split(": ",1)[1] for e in events if e["event"]=="log" and e["payload"]["line"].startswith("working folder: "))
                py_work=str(Path(py["work"]).parent)
                normalized_py=py_log.replace(py_work,"WORK").replace(str(py_out),"ISO").replace("python.partial.iso","PARTIAL")
                normalized_rs=log.replace(rs_work,"WORK").replace(str(rs_out),"ISO").replace("rust.partial.iso","PARTIAL")
                if keep:
                    if voice_changes(plan):
                        normalized_py,normalized_rs=voice_log(normalized_py,plan),voice_log(normalized_rs,plan)
                    assert normalized_py==normalized_rs, "GUI log mismatch"
                assert not list(folder.glob("*.partial.iso"))
                print("PASS GUI job %s: ISO bytes, kept/removed work, log, progress, hint" % label,flush=True)
                # Cancellation must remove its incomplete ISO and working extraction.
                job.update(output=str(folder/"cancel.iso"),keep_work=False)
                jobfile.write_text(json.dumps(job))
                proc=subprocess.run([str(probe),"job",str(jobfile),"cancel"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                assert proc.returncode==1 and proc.stderr.strip()=="cancelled"
                assert not (folder/"cancel.iso").exists() and not (folder/"cancel.partial.iso").exists()
                assert "cancelled" in (folder/"cancel.log").read_text()
                print("PASS GUI cancellation and cleanup",flush=True)
            finally:
                shutil.rmtree(folder)
    finally:
        source_iso.unlink(missing_ok=True)


# --- #9 "Animations from": no Python reference; each case is checked against the same Rust job
# without --anims, which must differ only where animations.md / clone.rs TIMING_ROWS say.
ANIMPARAMS = 0x800FB798     # main.dol: 15 x s16[54] frame-event windows
ANIM_REL_ROWS = [(0x7870, 6, 6), (0x7E34, 4, 2)]  # game.rel .data: (table, row size, bytes from the animations)


def anim_case(cli, work, disc, label, opts, anims, expect_error=None, same=False):
    """Run the Rust CLI with and without the --anims options and compare the two extractions."""
    import struct
    import clone_character as cc
    plain, moved = work / (label + "-plain"), work / (label + "-anims")
    extra = sum((["--anims", "%s:%s" % pair] for pair in anims), [])
    try:
        proc = subprocess.run([str(x) for x in [cli, "--disc", disc, *opts, *extra, "--out", moved]],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if expect_error:
            assert proc.returncode and expect_error in proc.stderr + proc.stdout, (label, proc.stderr)
            assert not moved.exists(), label + ": refused before copying the game"
            print("PASS anims %s refused: %s" % (label, (proc.stderr + proc.stdout).strip().splitlines()[-1]), flush=True)
            return
        assert proc.returncode == 0, (label, proc.stderr)
        base = run([cli, "--disc", disc, *opts, "--out", plain])
        anim_lines = [l for l in proc.stdout.splitlines() if l.startswith("  animations: ")]
        if same:
            compare_trees(plain, moved)
            assert any("shares every bank" in l for l in anim_lines), (label, "log")
            print("PASS anims %s: same banks as the source, byte-identical" % label, flush=True)
            return
        pa, pb = cc.Disc(plain), cc.Disc(moved)
        da = cc.Dol((pa.sys / "main.dol").read_bytes())
        db = cc.Dol((pb.sys / "main.dol").read_bytes())
        od = cc.Dol((Path(disc) / "sys/main.dol").read_bytes())
        no_gameplay = "--no-gameplay" in opts
        allowed = [(da.off(cc.ADDR_REL_DESCS[1], 16), da.off(cc.ADDR_REL_DESCS[1], 16) + 16)]
        rel_rows = []
        for slot_name, char in anims:
            s, a = cc.parse_char(slot_name), cc.parse_char(char)
            for t in range(2, 19):
                assert db.desc(cc.ADDR_SUBFILES, s * 19 + t) == od.desc(cc.ADDR_SUBFILES, a * 19 + t), (label, "track", t)
                o = da.off(cc.ADDR_SUBFILES + (s * 19 + t) * 16, 16)
                allowed.append((o, o + 16))
            assert db.desc(cc.ADDR_MASTER, 432 + s) == od.desc(cc.ADDR_MASTER, 432 + a), (label, "master[432+slot]")
            o = da.off(cc.ADDR_MASTER + (432 + s) * 16, 16)
            allowed.append((o, o + 16))
            for k in range(15):
                o = da.off(ANIMPARAMS + (k * 54 + s) * 2, 2)
                want = od.data[od.off(ANIMPARAMS + (k * 54 + (s if no_gameplay else a)) * 2, 2):][:2]
                assert db.data[o:o + 2] == want, (label, "window", k)
                allowed.append((o, o + 2))
            rel_rows.append((s, a))
        assert len(da.data) == len(db.data)
        chunked_equal(pa.sys / "main.dol", pb.sys / "main.dol", allowed, label + " main.dol")
        off = da.desc(cc.ADDR_REL_DESCS[1], 0)[2]
        ends = sorted(da.desc(x, 0)[2] for x in cc.ADDR_REL_DESCS) + [(pa.files / "aaaa.dat").stat().st_size]
        chunked_equal(pa.files / "aaaa.dat", pb.files / "aaaa.dat", [(off, min(e for e in ends if e > off))], label + " aaaa.dat")
        ga, gb = bytearray(cc.load_rel(pa, da, 1)), cc.load_rel(pb, db, 1)
        orig = cc.load_rel(cc.Disc(disc), od, 1)
        base_off, _ = cc.rel_data_section(bytearray(orig))
        for s, a in rel_rows:
            for table, size, n in ANIM_REL_ROWS:
                o = base_off + table + s * size
                want = bytes(ga[o:o + size]) if no_gameplay else orig[base_off + table + a * size:][:n] + bytes(ga[o + n:o + size])
                assert gb[o:o + size] == want, (label, "game.rel 0x%X" % table)
                ga[o:o + size] = want
        assert bytes(ga) == bytes(gb), label + ": game.rel differs outside the animation rows"
        for name in ("files/ZZZZ.dat", "sys/fst.bin"):
            compare_files(plain / name, moved / name)
        names = {p.relative_to(plain) for p in plain.rglob("*") if p.is_file()}
        assert names == {p.relative_to(moved) for p in moved.rglob("*") if p.is_file()}, label + ": file sets"
        for p in sorted(names):
            if str(p) not in ("sys/main.dol", "files/aaaa.dat"):
                compare_files(plain / p, moved / p, plain)
        # the log: the plain job's lines plus the animation lines (the game.rel line gains a part)
        extra_lines = [l for l in proc.stdout.replace(str(moved), str(plain)).splitlines() if l not in base.splitlines()]
        assert all(l.startswith("  animations: ") or l.startswith("game.rel: ") for l in extra_lines), (label, extra_lines)
        assert len(anim_lines) >= 2 * len(anims), (label, anim_lines)
        print("PASS anims %s: %d clone(s) moved, rest byte-identical (%d log lines)" % (label, len(anims), len(anim_lines)), flush=True)
    finally:
        for p in (plain, moved):
            if p.exists():
                shutil.rmtree(p)


def anim_checks(cli, work, disc):
    anim_case(cli, work, disc, "mario-luigi", ["--source", "mario", "--slot", "29:blue", "--slot", "30:green"], [("29", "luigi")])
    anim_case(cli, work, disc, "toad-two", ["--source", "redtoad", "--slot", "30:blue", "--slot", "44:green", "--ui", "donor"],
              [("30", "luigi"), ("44", "babymario")])
    # the animations' character is itself a clone slot: its retail banks and rows are used
    anim_case(cli, work, disc, "from-a-slot", ["--source", "mario", "--slot", "29:blue", "--slot", "30:green"], [("30", "bluetoad")])
    anim_case(cli, work, disc, "no-gameplay", ["--source", "mario", "--slot", "29", "--no-gameplay"], [("29", "wario")])
    anim_case(cli, work, disc, "same-banks", ["--source", "bluetoad", "--slot", "30"], [("30", "redtoad")], same=True)
    anim_case(cli, work, disc, "missing-bones", ["--source", "mario", "--slot", "29"], [("29", "dk")],
              expect_error="Donkey Kong's animations move bones that Mario's body does not have")
    anim_case(cli, work, disc, "not-a-clone", ["--source", "mario", "--slot", "29"], [("30", "luigi")],
              expect_error="not one of the clone slots")


# --- #18 clones of several characters: no Python reference; each clone of a mixed job must get
# exactly the rows and files a one-character job of its own character gives it.
CHARSIZE = 0x800E8300       # main.dol: u8[54][8] model scale rows
REL_ROWS = [(0x8168, 2), (0x6EF8, 0x24), (0x7690, 2), (0x7870, 6), (0x7E34, 4), (0x17000, 4), (0x79B4, 0x10), (0x7F10, 2)]


def multi_case(cli, work, disc, label, opts, groups, expect_error=None, voice_line=None):
    """groups: [(source, [SLOT:COLOUR, ...])]; the first source is --source, the rest use --from."""
    import clone_character as cc
    first = groups[0][0]
    argv = ["--source", first]
    for c, slots in groups:
        for spec in slots:
            argv += ["--slot", spec]
            if c != first:
                argv += ["--from", "%s:%s" % (spec.split(":")[0], c)]
    mixed = work / (label + "-mixed")
    singles = [work / ("%s-%d" % (label, i)) for i in range(len(groups))]
    try:
        proc = subprocess.run([str(x) for x in [cli, "--disc", disc, *opts, *argv, "--out", mixed]],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if expect_error:
            assert proc.returncode and expect_error in proc.stderr + proc.stdout, (label, proc.stderr)
            print("PASS multi %s refused: %s" % (label, (proc.stderr + proc.stdout).strip().splitlines()[-1]), flush=True)
            return
        assert proc.returncode == 0, (label, proc.stderr)
        if voice_line:
            assert any(l.startswith("voice: ") and all(v in l for v in voice_line) for l in proc.stdout.splitlines()), (label, "voice merge")
        pm = cc.Disc(mixed)
        dm = cc.Dol((pm.sys / "main.dol").read_bytes())
        gm = cc.load_rel(pm, dm, 1)
        bm, _ = cc.rel_data_section(bytearray(gm))
        stats_m = pm.read_zzzz(cc.ZZZZ_STATS_OFFSET, 54 * 0xA0)
        mine = []
        for (c, slots), single in zip(groups, singles):
            run([cli, "--disc", disc, *opts, "--source", c, *sum((["--slot", x] for x in slots), []), "--out", single])
            ps = cc.Disc(single)
            ds = cc.Dol((ps.sys / "main.dol").read_bytes())
            gs = cc.load_rel(ps, ds, 1)
            bs, _ = cc.rel_data_section(bytearray(gs))
            stats_s = ps.read_zzzz(cc.ZZZZ_STATS_OFFSET, 54 * 0xA0)
            for spec in slots:
                s = cc.parse_char(spec.split(":")[0])
                mine.append(s)
                for t in range(19):
                    assert cc.load_subfile(pm, dm, s * 19 + t) == cc.load_subfile(ps, ds, s * 19 + t), (label, s, "track", t)
                assert cc.effective_body(pm, dm, s) == cc.effective_body(ps, ds, s), (label, s, "ARAM body")
                for table, size in [(cc.ADDR_FAMILY, 6), (cc.ADDR_GLOVE, 2), (cc.ADDR_MODELMAP, 2), (cc.ADDR_ROWKIND, 1), (CHARSIZE, 8)] \
                        + ([(cc.ADDR_JUMPTABLE, 4)] if s < 48 else []):
                    assert dm.read(table + s * size, size) == ds.read(table + s * size, size), (label, s, hex(table))
                for k in range(15):
                    assert dm.read(ANIMPARAMS + (k * 54 + s) * 2, 2) == ds.read(ANIMPARAMS + (k * 54 + s) * 2, 2), (label, s, "window", k)
                for k in range(7):
                    assert dm.desc(cc.ADDR_MASTER, 54 + s * 7 + k) == ds.desc(cc.ADDR_MASTER, 54 + s * 7 + k), (label, s, "master", k)
                assert dm.desc(cc.ADDR_MASTER, 432 + s) == ds.desc(cc.ADDR_MASTER, 432 + s), (label, s, "master[432+s]")
                for k in range(6):
                    assert dm.desc(cc.ADDR_SUBITEM_FILES, s * 6 + k) == ds.desc(cc.ADDR_SUBITEM_FILES, s * 6 + k), (label, s, "sub-item", k)
                for table, size in REL_ROWS:
                    assert gm[bm + table + s * size:][:size] == gs[bs + table + s * size:][:size], (label, s, "game.rel 0x%X" % table)
            clones = [cc.parse_char(x.split(":")[0]) for _, sl in groups for x in sl]
            for spec in slots if "--no-stats" not in opts else []:
                s = cc.parse_char(spec.split(":")[0])
                # the row except chemistry with other clones, and every non-clone's chemistry with s
                row_m, row_s = bytearray(stats_m[s * 0xA0:(s + 1) * 0xA0]), bytearray(stats_s[s * 0xA0:(s + 1) * 0xA0])
                for o in clones:
                    row_m[0x3B + o] = row_s[0x3B + o] = 0
                assert row_m == row_s, (label, s, "stats row")
                for j in range(54):
                    if j not in clones:
                        assert stats_m[j * 0xA0 + 0x3B + s] == stats_s[j * 0xA0 + 0x3B + s], (label, s, "chemistry", j)
            shutil.rmtree(single)
        print("PASS multi %s: %d clones of %d characters match their one-character jobs" % (label, len(mine), len(groups)), flush=True)
    finally:
        for p in [mixed, *singles]:
            if p.exists():
                shutil.rmtree(p)


def multi_checks(cli, work, disc):
    multi_case(cli, work, disc, "mario-luigi", [], [("mario", ["29:blue", "30:green"]), ("luigi", ["44:purple"])])
    # two characters' voices into one family bank (both clones on Toad colours, donor menus)
    multi_case(cli, work, disc, "shared-bank", ["--ui", "donor"], [("mario", ["29:blue"]), ("luigi", ["30:green"])],
               voice_line=("Mario's", "Luigi's"))
    multi_case(cli, work, disc, "three", ["--no-stats"], [("peach", ["29:blue"]), ("dk", ["44:green"]), ("bowser", ["45:red"])])
    multi_case(cli, work, disc, "source-is-slot", [], [("mario", ["29:blue", "luigi:green"]), ("luigi", ["30:green"])],
               expect_error="is cloned, so it cannot also be a donor slot")


def grid_cases(full):
    import test_matrix as tm
    yield "default", []
    for label, src, slots, ui in tm.CLI_PATCHES:
        yield label, ["--source", src, "--ui", ui] + sum((["--slot", s] for s in slots), [])
    for flag in ("--no-stats", "--no-gameplay", "--no-aram-overlay"):
        yield flag[2:], [flag]
    for ui in ("source", "donor"):
        yield "ui-" + ui, ["--ui", ui]
    yield "mario-band", sum((["--slot", "%d:hue=%s,from=0,width=0.035,minsat=0.5" % (slot,colour)]
                             for slot,colour in ((29,"blue"),(30,"green"),(31,"purple"),(32,"yellow"))), [])
    yield "colour-word", ["--colour-word", "donor"]
    yield "custom", ["--slot", "29:hue=0.317,sat=1.1,keeplum=0.73"]
    yield "relocate-menu", ["--relocate-menu-art"]
    if full:
        import clone_character as cc
        for src in range(54):
            donors=[s for s in (29,30,31,32,34,35,36,44,45) if s!=src][:8]
            pairs=list(zip(donors,cc.COLOUR_PRESETS))
            yield "%d-all-presets" % src, ["--source",str(src)]+sum(
                (["--slot","%d:%s" % (slot,preset)] for slot,preset in pairs),[])
            if src==9:  # Eight Bowser portraits exceed the reference's HUD slot; four fit.
                for part in range(2):
                    yield "9-presets-%d" % part, ["--source","9"]+sum(
                        (["--slot","%d:%s" % (slot,preset)] for slot,preset in pairs[part*4:(part+1)*4]),[])


VOICE_CASES = [
    ("v-rio", ["--source", "mario", "--slot", "29", "--ui", "donor"]),
    ("v-three-families", ["--source", "mario", "--slot", "29:blue", "--slot", "44:green", "--slot", "2:red", "--ui", "donor"]),
    ("v-eight-two-families", ["--source", "mario", "--ui", "donor"] + sum((["--slot", "%d" % s] for s in (29, 30, 31, 32, 44, 45, 46, 47)), [])),
    ("v-dk-donor", ["--source", "dk", "--slot", "1:green", "--slot", "30:red", "--ui", "donor"]),
    ("v-same-family", ["--source", "redtoad", "--slot", "29", "--slot", "30", "--ui", "donor"]),
    ("v-mixed-family", ["--source", "redtoad", "--slot", "29", "--slot", "44", "--ui", "donor"]),
    ("v-curve-source", ["--source", "monty", "--slot", "29", "--ui", "donor"]),
    ("v-curve-donor", ["--source", "mario", "--slot", "49:green", "--ui", "donor"]),
    ("v-bros", ["--source", "hammerbro", "--slot", "44", "--slot", "45", "--ui", "donor"]),
    ("v-donor-nogameplay", ["--source", "luigi", "--slot", "29", "--slot", "30", "--ui", "donor", "--no-gameplay"]),
    ("v-source-nogameplay", ["--source", "luigi", "--slot", "29", "--slot", "30", "--no-gameplay"]),
    ("v-donor-nostats-nooverlay", ["--source", "babymario", "--slot", "34:green", "--ui", "donor", "--no-stats", "--no-aram-overlay"]),
]


def clone_case(cli, work, disc, case):
    label, opts = case
    work = work / label
    work.mkdir()
    try:
        clone_case_in(cli, work, disc, label, opts)
    finally:
        shutil.rmtree(work)


def clone_case_in(cli, work, disc, label, opts):
    a, b = work / "python", work / "rust"
    ai, bi = work / "python.iso", work / "rust.iso"
    ram_handles = []
    try:
        if RAM_ISOS:
            for path in (ai,bi):
                fd=os.memfd_create("mssb-clone-difftest",os.MFD_CLOEXEC)
                ram_handles.append(fd)
                path.symlink_to("/proc/%d/fd/%d" % (os.getpid(),fd))
        base = ["--disc", disc, *opts]
        def capture(cmd):
            return subprocess.run([str(x) for x in cmd],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def normalize(text,folder,iso):
            return text.replace(str(iso),"ISO").replace(str(folder),"OUT")
        t = time.perf_counter()
        py = capture([sys.executable, MOD / "clone_character.py", *base, "--out", a,
                      *([] if RAM_ISOS else ["--iso",ai])])
        if RAM_ISOS and py.returncode==0:
            import clone_character as cc
            cc.write_iso(cc.Disc(a),ai,lambda _:None)
        py_time = time.perf_counter() - t
        t = time.perf_counter()
        rs = capture([cli, *base, "--out", b, *([] if RAM_ISOS else ["--iso",bi])])
        if RAM_ISOS and rs.returncode==0:
            run([cli.with_name("mssb-clone-probe"),"write-iso",b,bi])
        rs_time = time.perf_counter() - t
        assert py.returncode==rs.returncode,(label,"exit status",py.stderr,rs.stderr)
        plan = voice_plan(disc, opts)
        voice = voice_changes(plan) and py.returncode == 0
        py_out, rs_out = normalize(py.stdout,a,ai), normalize(rs.stdout,b,bi)
        # The reference never logs "voice: " lines (a family that already shares the source's
        # bank logs one without any other change).
        py_out, rs_out = voice_log(py_out, plan), voice_log(rs_out, plan)
        if py.returncode == 0:
            assert sum(line.startswith("voice: ") and "-> ZZZZ.dat+" in line for line in rs.stdout.splitlines()) == len(plan["rows"]), (label, "voice log")
        assert py_out==rs_out,(label,"stdout",py.stdout,rs.stdout)
        assert normalize(py.stderr,a,ai)==normalize(rs.stderr,b,bi),(label,"stderr",py.stderr,rs.stderr)
        if py.returncode:
            compare_trees(a, b)
            print("PASS clone %s matching reference rejection: %s" % (label,py.stderr.strip()),flush=True)
            return
        if voice:
            merged = check_voice_tree(a, b, disc, plan, label)
            check_voice_iso(b, bi, work)
        else:
            compare_trees(a, b)
            compare_files(ai, bi)
        source = opts[opts.index("--source")+1] if "--source" in opts else "mario"
        ui = opts[opts.index("--ui")+1] if "--ui" in opts else "source"
        slot_args = sum((["--slot", opts[i+1]] for i, x in enumerate(opts) if x == "--slot"), [])
        validator = [sys.executable, MOD / "test_clone.py", "--disc", disc, "--patched", b,
                     "--source", source, "--ui", ui, *slot_args]
        # The validator assumes the default gameplay/stats/overlay settings.
        # The validator assumes the reference's sound files (it predates #29): it checks the
        # reference output when the voice changes apply, and the Rust output (identical) otherwise.
        if (not label[0].isdigit()) and not any(x in opts for x in ("--no-stats", "--no-gameplay", "--no-aram-overlay", "--colour-word", "--relocate-menu-art")):
            if voice:
                validator[validator.index("--patched") + 1] = a
            result = run(validator)
            assert "ALL CHECKS PASSED" in result, label + " test_clone"
        print("PASS clone %s Python %.3fs Rust %.3fs%s" % (label, py_time, rs_time,
              " (voice: %d merged bank(s), 0x8168 rows, rest byte-identical)" % merged if voice else ""), flush=True)
    finally:
        for p in (a, b):
            if p.exists():
                shutil.rmtree(p)
        for p in (ai, bi):
            p.unlink(missing_ok=True)
        for fd in ram_handles:
            os.close(fd)


def clone_grid(cli, work, disc, full, jobs):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        cases = VOICE_CASES if full == "voice" else grid_cases(full)
        futures = [pool.submit(clone_case, cli, work, disc, case) for case in cases if case[0] not in SKIP_PASSED]
        for future in futures:
            future.result()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--disc", type=Path, default=ROOT / "orig/GYQE01")
    for flag in ("synthetic", "codecs", "helpers", "iso", "grid", "full-grid", "voice-grid", "sound", "textures", "api", "gui", "cli", "anims", "multi"):
        ap.add_argument("--" + flag, action="store_true")
    ap.add_argument("--jobs", type=int, default=3, help="independent clone comparisons in parallel")
    ap.add_argument("--ram-isos", action="store_true", help="Linux: store clone comparison ISOs in anonymous RAM files via scratch symlinks")
    ap.add_argument("--skip-passed", type=Path, help="resume a grid, skipping PASS clone labels from this earlier log")
    args = ap.parse_args()
    global RAM_ISOS, SKIP_PASSED
    RAM_ISOS=args.ram_isos
    if RAM_ISOS and not hasattr(os,"memfd_create"):
        ap.error("--ram-isos needs Linux memfd_create")
    if args.skip_passed:
        SKIP_PASSED={line.split()[2] for line in args.skip_passed.read_text().splitlines() if line.startswith("PASS clone ")}

    run(["gcc", "-O2", "-shared", "-fPIC", "-o", MOD / "_lzss.so", MOD / "lzss.c"])
    cargo = Path.home() / ".cargo/bin/cargo"
    run([cargo, "build", "--release", "--manifest-path", CORE / "Cargo.toml"])
    probe = CORE / "target/release/mssb-clone-probe"
    scratch = ROOT / ".scratch"
    scratch.mkdir(exist_ok=True)
    os.environ["TMPDIR"] = str(scratch)
    if not any((args.synthetic, args.codecs, args.helpers, args.iso, args.grid, args.full_grid, args.voice_grid, args.sound, args.textures, args.api, args.gui, args.cli, args.anims, args.multi)):
        args.synthetic = args.codecs = args.helpers = args.iso = True
    with tempfile.TemporaryDirectory(prefix="core-difftest-", dir=scratch) as tmp:
        work = Path(tmp)
        if args.synthetic:
            synthetic(probe, work)
        if args.helpers:
            helpers(probe, args.disc)
        if args.codecs:
            retail_codecs(probe, work, args.disc)
        if args.iso:
            iso_roundtrip(probe, work, args.disc)
        if args.cli:
            cli_checks(CORE / "target/release/mssb-clone", probe, work, args.disc)
        if args.api:
            api_checks(probe, work, args.disc)
        if args.gui:
            gui_jobs(probe, work, args.disc)
        if args.textures:
            textures(probe, work, args.disc)
        if args.sound:
            print(run([probe, "sound", args.disc]).splitlines()[-1], flush=True)
        if args.grid or args.full_grid:
            clone_grid(CORE / "target/release/mssb-clone", work, args.disc, args.full_grid, args.jobs)
        if args.voice_grid:
            clone_grid(CORE / "target/release/mssb-clone", work, args.disc, "voice", args.jobs)
        if args.anims:
            anim_checks(CORE / "target/release/mssb-clone", work, args.disc)
        if args.multi:
            multi_checks(CORE / "target/release/mssb-clone", work, args.disc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
