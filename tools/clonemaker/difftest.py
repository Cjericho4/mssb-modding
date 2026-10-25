#!/usr/bin/env python3
"""Local byte-for-byte checks against Clone Maker's Python reference (stdlib only).

All generated files live in a TemporaryDirectory under .scratch and are removed even on
failure. No original game data is printed. CI can use --synthetic without retail inputs.
"""
import argparse
import importlib.util
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
sys.path.insert(0, str(MOD))


def run(cmd):
    return subprocess.run([str(x) for x in cmd], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True).stdout


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
    return "resource outside known descriptor ranges"


def compare_files(a, b, disc=None):
    off = first_difference(a, b)
    if off is not None:
        detail = describe(a, off, disc) if disc else "binary payload"
        raise AssertionError("%s: first differing offset 0x%X (%s)" % (a, off, detail))


def compare_trees(a, b):
    aa = {p.relative_to(a) for p in a.rglob("*") if p.is_file()}
    bb = {p.relative_to(b) for p in b.rglob("*") if p.is_file()}
    if aa != bb:
        raise AssertionError("output file sets differ: %s" % sorted(aa ^ bb))
    for p in sorted(aa):
        compare_files(a / p, b / p, a)


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


def grid_cases(full):
    import test_matrix as tm
    yield "default", []
    for label, src, slots, ui in tm.CLI_PATCHES:
        yield label, ["--source", src, "--ui", ui] + sum((["--slot", s] for s in slots), [])
    for flag in ("--no-stats", "--no-gameplay", "--no-aram-overlay"):
        yield flag[2:], [flag]
    for ui in ("source", "donor"):
        yield "ui-" + ui, ["--ui", ui]
    yield "colour-word", ["--colour-word", "donor"]
    yield "custom", ["--slot", "29:hue=0.317,sat=1.1,keeplum=0.73"]
    yield "relocate-menu", ["--relocate-menu-art"]
    if full:
        import clone_character as cc
        for src in range(54):
            for preset in cc.COLOUR_PRESETS:
                slot = 30 if src == 29 else 29
                yield "%d-%s" % (src, preset), ["--source", str(src), "--slot", "%d:%s" % (slot, preset)]


def clone_grid(cli, work, disc, full):
    for label, opts in grid_cases(full):
        a, b = work / "python", work / "rust"
        ai, bi = work / "python.iso", work / "rust.iso"
        try:
            base = ["--disc", disc, *opts]
            t = time.perf_counter()
            py = run([sys.executable, MOD / "clone_character.py", *base, "--out", a, "--iso", ai])
            py_time = time.perf_counter() - t
            t = time.perf_counter()
            rs = run([cli, *base, "--out", b, "--iso", bi])
            rs_time = time.perf_counter() - t
            compare_trees(a, b)
            compare_files(ai, bi)
            assert py.replace(str(a), "OUT").replace(str(ai), "ISO") == rs.replace(str(b), "OUT").replace(str(bi), "ISO"), "log mismatch: " + label
            print("PASS clone %s Python %.3fs Rust %.3fs" % (label, py_time, rs_time), flush=True)
        finally:
            for p in (a, b):
                if p.exists():
                    shutil.rmtree(p)
            for p in (ai, bi):
                p.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--disc", type=Path, default=ROOT / "orig/GYQE01")
    for flag in ("synthetic", "codecs", "helpers", "iso", "grid", "full-grid"):
        ap.add_argument("--" + flag, action="store_true")
    args = ap.parse_args()
    run(["gcc", "-O2", "-shared", "-fPIC", "-o", MOD / "_lzss.so", MOD / "lzss.c"])
    cargo = Path.home() / ".cargo/bin/cargo"
    run([cargo, "build", "--release", "--manifest-path", CORE / "Cargo.toml"])
    probe = CORE / "target/release/mssb-clone-probe"
    scratch = ROOT / ".scratch"
    scratch.mkdir(exist_ok=True)
    if not any((args.synthetic, args.codecs, args.helpers, args.iso, args.grid, args.full_grid)):
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
        if args.grid or args.full_grid:
            clone_grid(CORE / "target/release/mssb-clone", work, args.disc, args.full_grid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
