#!/usr/bin/env python3
"""Tests for tools/datamap (standard library unittest; runs on Windows too).

    py test_mssb_files.py                      # synthetic-disc tests + real-disc tests if a disc is found
    set MSSB_DISC=C:\\path\\to\\extracted_disc    (or .iso) to point the real-disc tests at your copy
    set MSSB_SLOW=1                            # also decompress every file and run a real-disc replace

The synthetic disc is built in a temp folder from scratch (a tiny DOL/REL/ZZZZ/aaaa/FST with the same
table addresses as the retail game), so the replace logic is tested without any game data.  The
real-disc tests only read the disc (replace writes to a temp copy).
"""
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import disc as dm  # noqa: E402
import catalog as ct  # noqa: E402
import mssb_files as mf  # noqa: E402
import stats as st  # noqa: E402

cc = dm.cc
REPO = HERE.parents[1]
REAL = os.environ.get("MSSB_DISC") or str(REPO / "orig" / "GYQE01")
HAVE_REAL = (Path(REAL) / "files" / "ZZZZ.dat").exists() or (Path(REAL).is_file())
SLOW = os.environ.get("MSSB_SLOW") == "1"

# Retail numbers the real-disc tests pin down (GYQE01, rev 0).
REAL_FILES = 1571
REAL_ZZZZ_REFERENCED = 254779396
REAL_ZZZZ_PADDING = 1277948
REAL_AAAA_REFERENCED = 1531432


# ----------------------------------------------------------------------------------------------
# Synthetic disc
# ----------------------------------------------------------------------------------------------
TEXT_ADDR, TEXT_SIZE = 0x80008E00, 0x1A000
DATA_ADDR, DATA_SIZE = 0x800E8300, 0x28000
REL_DATA_SIZE = {"menus": 0x30400, "game": 0x28600, "challenge": 0x11400}
REL_IDS = {"menus": 2, "game": 3, "challenge": 1}


def payload(n, seed):
    """Compressible but non-trivial bytes."""
    out = bytearray()
    x = seed
    while len(out) < n:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out += bytes([(x >> 16) & 0x0F]) * (1 + (x & 3))
    return bytes(out[:n])


def noise(n, seed):
    out = bytearray()
    x = seed
    while len(out) < n:
        x = (x * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
        out += struct.pack(">Q", x)
    return bytes(out[:n])


def make_rel(name, rows):
    data = bytearray(REL_DATA_SIZE[name])
    for off, d in rows:
        struct.pack_into(">4I", data, off, *d)
    hdr = bytearray(0x40)
    struct.pack_into(">I", hdr, 0, REL_IDS[name])
    struct.pack_into(">II", hdr, 0x0C, 17, 0x40)
    table = bytearray(17 * 8)
    text_off = 0x40 + len(table)
    struct.pack_into(">II", table, 1 * 8, text_off | 1, 0x20)
    data_off = text_off + 0x20
    struct.pack_into(">II", table, 5 * 8, data_off, len(data))
    return bytes(hdr + table + bytes(0x20) + data)


def make_dol(rows, blob_size):
    text = bytearray(TEXT_SIZE)
    hi, lo = (blob_size >> 16) & 0xFFFF, blob_size & 0xFFFF
    if lo & 0x8000:
        hi += 1
    struct.pack_into(">I", text, dm.BLOB_LIS - TEXT_ADDR, 0x3C600000 | hi)
    struct.pack_into(">I", text, dm.BLOB_ADDI - TEXT_ADDR, 0x38630000 | lo)
    data = bytearray(DATA_SIZE)
    for ram, d in rows:
        struct.pack_into(">4I", data, ram - DATA_ADDR, *d)
    hdr = bytearray(0x100)
    struct.pack_into(">I", hdr, 0x00, 0x100)                       # text0 offset
    struct.pack_into(">I", hdr, 0x1C, 0x100 + TEXT_SIZE)           # data0 offset
    struct.pack_into(">I", hdr, 0x48, TEXT_ADDR)
    struct.pack_into(">I", hdr, 0x64, DATA_ADDR)
    struct.pack_into(">I", hdr, 0x90, TEXT_SIZE)
    struct.pack_into(">I", hdr, 0xAC, DATA_SIZE)
    return bytes(hdr + text + data)


def make_fst(files):
    """files: [(name, offset, size)] in the root directory."""
    names = b""
    ents = [struct.pack(">III", 0x01000000, 0, len(files) + 1)]
    for name, off, size in files:
        ents.append(struct.pack(">III", len(names), off, size))
        names += name.encode() + b"\0"
    return b"".join(ents) + names


class Synth:
    """Builds the synthetic disc folder and remembers what is where."""

    def __init__(self, root):
        self.root = Path(root)
        zz = bytearray()

        def put(data, align=dm.SECTOR):
            off = dm.align(len(zz), align)
            zz.extend(bytes(off - len(zz)))
            zz.extend(data)
            return off

        put(noise(0x1000, 1))                       # unreferenced gap at 0
        self.f1 = payload(0x3000, 2)                 # res.1: 3 copies (dol res, dol res_dol2, game.rel)
        p1 = cc.lzss_compress(self.f1)
        o1 = put(p1)
        self.d1 = (0x40B, 0x40000000 | len(self.f1), o1, len(p1))
        self.f2 = payload(0x6A2, 3)                  # text_menu.0: stored, odd size
        p2 = self.f2 + b"\0\0"
        o2 = put(p2)
        self.d2 = (0, len(self.f2), o2, len(p2))
        self.f3 = payload(0x900, 4)                  # res.2: slot exactly one sector, then res.3
        p3 = cc.lzss_compress(self.f3)
        o3 = put(p3)
        self.d3 = (0x40B, 0x40000000 | len(self.f3), o3, len(p3))
        self.f4 = payload(0x500, 5)                  # res.3
        p4 = cc.lzss_compress(self.f4)
        o4 = put(p4)
        self.d4 = (0x40B, 0x40000000 | len(self.f4), o4, len(p4))
        # blob with three members
        self.members = [payload(0x300 + 0x80 * i, 10 + i) for i in range(3)]
        blob = bytearray()
        self.mdesc = []
        for m in self.members:
            pm = cc.lzss_compress(m)
            mo = dm.align(len(blob), 0x20)
            blob.extend(bytes(mo - len(blob)))
            blob.extend(pm)
            self.mdesc.append((0x40B, 0x40000000 | len(m), mo, len(pm)))
        blob.extend(bytes(dm.align(len(blob), 0x20) - len(blob)))
        ob = put(bytes(blob))
        self.blob_desc = (0, len(blob), ob, len(blob))
        self.f5 = payload(0x1200, 6)                 # partial: challenge partial_ch reads 0x200 of it
        o5 = put(self.f5)
        self.d5 = (0, len(self.f5), o5, len(self.f5))
        self.d5_partial = (0, 0x200, o5, len(self.f5))
        self.f6 = payload(st.FILE_SIZE, 9)          # stats.0: stored, named by menus.rel and game.rel
        o6 = put(self.f6)
        self.d6 = (0, len(self.f6), o6, len(self.f6))
        put(noise(0x2345, 7))                        # trailing unreferenced data
        zz.extend(bytes(dm.align(len(zz), 4) - len(zz)))
        self.zzzz = bytes(zz)

        dol_rows = [(0x800E8B08 + 16 * 1, self.d1), (0x800EEFD0 + 16 * 1, self.d1),
                    (0x800E8B08 + 16 * 2, self.d3), (0x800E8B08 + 16 * 3, self.d4),
                    (dm.BLOB_DESC_ADDR, self.blob_desc)]
        dol_rows += [(dm.MASTER_ADDR + 16 * i, d) for i, d in enumerate(self.mdesc)]
        dol_rows.append((dm.MASTER_ADDR + 16 * 400, self.mdesc[1]))     # a second row naming member 1
        rels = {"menus": make_rel("menus", [(0x12298, self.d2), (0xC8, self.d6)]),
                "game": make_rel("game", [(0x6130 + 16 * 1, self.d1), (0x0, self.d6)]),
                "challenge": make_rel("challenge", [(0xA940, self.d5_partial), (0x104F4, self.d5)])}
        aaaa = bytearray(0x800)
        for (name, addr) in dm.REL_DESC_ADDRS:
            p = cc.lzss_compress(rels[name])
            off = dm.align(len(aaaa), dm.SECTOR)
            aaaa.extend(bytes(off - len(aaaa)))
            aaaa.extend(p)
            dol_rows.append((addr, (0x40B, 0x40000000 | len(rels[name]), off, len(p))))
        aaaa.extend(bytes(dm.align(len(aaaa), 0x20) - len(aaaa)))
        self.aaaa = bytes(aaaa)
        self.rels = rels
        dol = make_dol(dol_rows, len(blob))
        boot = bytearray(0x440)
        boot[0:6] = b"GYQE01"
        struct.pack_into(">I", boot, 0x1C, 0xC2339F3D)
        dol_off = 0x10000
        fst_off = dm.align(dol_off + len(dol), 0x8000)
        bnr = b"BNR1" + bytes(0x1960 - 4)
        layout = []
        pos = fst_off + 0x8000
        for name, data in (("aaaa.dat", self.aaaa), ("opening.bnr", bnr), ("ZZZZ.dat", self.zzzz)):
            layout.append((name, pos, len(data)))
            pos = dm.align(pos + len(data), 0x8000)
        fst = make_fst(layout)
        struct.pack_into(">III", boot, 0x420, dol_off, fst_off, len(fst))
        apl = bytearray(0x40)
        struct.pack_into(">II", apl, 0x14, 0x20, 0)
        sysd, filesd = self.root / "sys", self.root / "files"
        sysd.mkdir(parents=True)
        filesd.mkdir()
        (sysd / "boot.bin").write_bytes(bytes(boot))
        (sysd / "bi2.bin").write_bytes(bytes(0x2000))
        (sysd / "apploader.img").write_bytes(bytes(apl[:0x20]))
        (sysd / "main.dol").write_bytes(dol)
        (sysd / "fst.bin").write_bytes(fst)
        (filesd / "aaaa.dat").write_bytes(self.aaaa)
        (filesd / "opening.bnr").write_bytes(bnr)
        (filesd / "ZZZZ.dat").write_bytes(self.zzzz)


def run_replace(src, fid, data, out, **kw):
    """mssb_files replace without the CLI wrapper's JSON printing."""
    args = mf.argparse.Namespace(disc=str(src), id=fid, file=None, out=str(out), reuse_gaps=kw.get("reuse_gaps", False))
    tmp = Path(out).parent / ("new_%s.bin" % fid.replace(".", "_"))
    tmp.write_bytes(data)
    args.file = str(tmp)
    mf.cmd_replace(args)


def diff_ranges(a, b):
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


class TextFormat(unittest.TestCase):
    def test_encode_decode(self):
        import formats as fm
        line = 'Hi, <06>Mario<05>!\\nScore{0019} 10-2 (wow) "ok?"'
        b = fm.text_import("#mssb-text strings=1 magic=0x0131\n0\t" + line + "\n")
        self.assertEqual(fm.text_export(b).splitlines()[1], "0\t" + line)
        with self.assertRaises(ValueError):
            fm.encode_text("tab\there")


def tree_hashes(root):
    root = Path(root)
    return {str(p.relative_to(root)): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


class StatsFormat(unittest.TestCase):
    def test_export_apply_identity(self):
        data = payload(st.FILE_SIZE, 21)
        obj = st.export(data)
        self.assertEqual(st.apply(data, obj), data)
        self.assertEqual(st.apply(data, json.loads(json.dumps(obj))), data)
        self.assertEqual(st.apply(data, {"format": st.FORMAT}), data)

    def test_one_field(self):
        data = bytes(st.FILE_SIZE)
        new = st.apply(data, {"format": st.FORMAT, "characters": [
            {"id": 2, "speed": 77, "captain_star": "dk", "fielding_abilities": ["laser", "super_curve"],
             "chemistry": {"mario": 30}}]})
        diff = [(i, new[i]) for i in range(len(new)) if new[i] != data[i]]
        r = 2 * st.ROW
        self.assertEqual(diff, [(r + 0x22, 0x20), (r + 0x23, 0x10), (r + 0x2F, 77), (r + 0x34, 5), (r + 0x3B, 30)])

    def test_errors(self):
        data = bytes(st.FILE_SIZE)
        for bad in ({"characters": []},
                    {"format": st.FORMAT, "characters": [{"id": 0, "sped": 1}]},
                    {"format": st.FORMAT, "characters": [{"id": 0, "speed": 256}]},
                    {"format": st.FORMAT, "characters": [{"id": 0, "name": "luigi"}]},
                    {"format": st.FORMAT, "characters": [{"id": 0, "star_pitch": "slider"}]},
                    {"format": st.FORMAT, "captain_teams": [{"block": 0, "lineups": [{"members": ["mario"]}]}]}):
            with self.assertRaises(st.StatsError):
                st.apply(data, bad)


class SyntheticDisc(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mssb_test_"))
        self.s = Synth(self.tmp / "in")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cat(self, root):
        return ct.Catalog(dm.Disc(root))

    def test_catalog(self):
        c = self.cat(self.s.root)
        f1 = c.find("res.1")
        self.assertEqual(sorted(r.name for r in f1.refs), ["game_res_a.1", "res.1", "res_dol2.1"])
        self.assertEqual(c.data(f1), self.s.f1)
        self.assertEqual(c.data(c.find("text_menu.0")), self.s.f2)
        self.assertEqual([c.data(c.find("master.%d" % i)) for i in range(3)], self.s.members)
        self.assertIs(c.find("master.400"), c.find("master.1"))
        f5 = c.find("challenge_104f4.0")
        self.assertEqual(sorted(r.name for r in f5.refs), ["challenge_104f4.0", "partial_ch.0"])
        self.assertEqual(c.data(f5), self.s.f5)
        self.assertEqual(c.find("ZZZZ:0x%X" % self.s.d1[2]), f1)
        cov, pad, gaps = c.coverage("ZZZZ")
        self.assertEqual(gaps[0], (0, 0x1000))
        self.assertTrue(all(r.name for r in f1.refs))

    def check_untouched(self, before, after, exclude_ids):
        cb, ca = self.cat(before), self.cat(after)
        for key, f in cb.files.items():
            if f.id in exclude_ids or f.container == "aaaa":
                continue
            g = ca.files[key]
            self.assertEqual([r.desc for r in g.refs], [r.desc for r in f.refs], f.id)
            self.assertEqual(ca.raw(g), cb.raw(f), f.id)
        return cb, ca

    def test_replace_same_in_place(self):
        out = self.tmp / "out"
        run_replace(self.s.root, "res.1", self.s.f1, out)
        cb, ca = self.check_untouched(self.s.root, out, {"res.1"})
        f = ca.find("res.1")
        self.assertEqual(ca.data(f), self.s.f1)
        self.assertEqual(f.offset, self.s.d1[2])
        # all three rows (two in main.dol, one in game.rel) carry the same new descriptor
        self.assertEqual(len(set(r.desc for r in f.refs)), 1)
        self.assertEqual(len(f.refs), 3)
        # ZZZZ.dat only changed inside the file's own sector slot
        zb = (self.s.root / "files/ZZZZ.dat").read_bytes()
        za = (out / "files/ZZZZ.dat").read_bytes()
        for a, b in diff_ranges(zb, za):
            self.assertTrue(self.s.d1[2] <= a and b <= dm.align(self.s.d1[2] + self.s.d1[3], dm.SECTOR))
        # the input was not written to
        self.assertEqual(zb, self.s.zzzz)

    def test_replace_modified_grows_appends(self):
        out = self.tmp / "out"
        new = noise(0x2800, 99)                       # incompressible: cannot fit the old slot
        run_replace(self.s.root, "res.1", new, out)
        cb, ca = self.check_untouched(self.s.root, out, {"res.1"})
        f = ca.find("res.1")
        self.assertEqual(ca.data(f), new)
        self.assertGreaterEqual(f.offset, len(self.s.zzzz))
        self.assertEqual(f.offset % dm.SECTOR, 0)
        self.assertEqual([r.desc for r in f.refs], [f.refs[0].desc] * 3)
        fst = (out / "sys/fst.bin").read_bytes()
        sizes = {p: e["size"] for e, p in cc.fst_paths(fst)}
        self.assertEqual(sizes["ZZZZ.dat"], (out / "files/ZZZZ.dat").stat().st_size)
        self.assertEqual(sizes["aaaa.dat"], (out / "files/aaaa.dat").stat().st_size)
        # game.rel was repacked; its decoded image differs only in the descriptor row
        rb, ra = cb.disc.rels["game"].data, ca.disc.rels["game"].data
        row = ca.disc.rels["game"].file_off(5, 0x6130 + 16)
        self.assertEqual(diff_ranges(rb, ra) and min(a for a, b in diff_ranges(rb, ra)) >= row, True)
        self.assertTrue(all(row <= a and b <= row + 16 for a, b in diff_ranges(rb, ra)))

    def test_reuse_gaps(self):
        out = self.tmp / "out"
        new = noise(0xC00, 5)
        run_replace(self.s.root, "res.2", new, out, reuse_gaps=True)
        ca = self.cat(out)
        f = ca.find("res.2")
        self.assertEqual(ca.data(f), new)
        gaps = self.cat(self.s.root).coverage("ZZZZ")[2]
        self.assertTrue(any(a <= f.offset and f.offset + f.packed <= b for a, b in gaps))
        self.assertEqual((out / "files/ZZZZ.dat").stat().st_size, len(self.s.zzzz))

    def test_replace_stored_text(self):
        out = self.tmp / "out"
        new = bytearray(self.s.f2)
        new[0x40:0x44] = b"\x12\x34\x56\x78"
        run_replace(self.s.root, "text_menu.0", bytes(new), out)
        self.check_untouched(self.s.root, out, {"text_menu.0"})
        ca = self.cat(out)
        f = ca.find("text_menu.0")
        self.assertEqual(ca.data(f), bytes(new))
        self.assertEqual(f.desc, self.s.d2)            # same size: descriptor unchanged
        self.assertEqual((out / "files/aaaa.dat").read_bytes(), self.s.aaaa)   # no REL repack needed

    def test_blob_member_grows(self):
        out = self.tmp / "out"
        new = noise(0x900, 3)
        run_replace(self.s.root, "master.1", new, out)
        ca = self.cat(out)
        self.assertEqual(ca.data(ca.find("master.1")), new)
        self.assertEqual(ca.data(ca.find("master.0")), self.s.members[0])
        self.assertEqual(ca.data(ca.find("master.2")), self.s.members[2])
        self.assertIs(ca.find("master.400"), ca.find("master.1"))
        bd = ca.disc.blob_desc()
        self.assertGreaterEqual(bd[2], len(self.s.zzzz))
        dol = cc.Dol(bytes(ca.disc.dol.data))
        self.assertGreaterEqual(dol.blob_alloc_size(), bd[1])

    def test_blob_member_in_place(self):
        out = self.tmp / "out"
        run_replace(self.s.root, "master.2", self.s.members[2][:0x100], out)
        ca = self.cat(out)
        self.assertEqual(ca.data(ca.find("master.2")), self.s.members[2][:0x100])
        self.assertEqual(ca.disc.blob_desc(), self.s.blob_desc)

    def test_partial_read_moves(self):
        out = self.tmp / "out"
        new = noise(0x3000, 8)
        run_replace(self.s.root, "challenge_104f4.0", new, out)
        ca = self.cat(out)
        f = ca.find("challenge_104f4.0")
        self.assertEqual(ca.data(f), new)
        part = [r for r in f.refs if r.table["id"] == "partial_ch"][0]
        self.assertEqual(part.desc[2], f.offset)
        self.assertEqual(part.desc[1], 0x200)

    def test_partial_only_refused(self):
        work_root = self.tmp / "w"
        shutil.copytree(self.s.root, work_root)
        work = mf.Work(work_root)
        f = work.cat.find("challenge_104f4.0")
        self.assertFalse(f.partial_only)
        f.refs = [r for r in f.refs if ct.is_partial(r.desc)]   # as if only the 0x200 read existed
        self.assertTrue(f.partial_only)
        with self.assertRaises(SystemExit):
            mf.apply_replace(work, "challenge_104f4.0", self.s.f5)
        work.zz.close()
        work.disc.close()

    def test_replace_rel(self):
        out = self.tmp / "out"
        rel = bytearray(self.s.rels["challenge"])
        rel[-4:] = b"\x01\x02\x03\x04"
        run_replace(self.s.root, "rel.2", bytes(rel), out)
        ca = self.cat(out)
        self.assertEqual(bytes(ca.disc.rels["challenge"].data), bytes(rel))
        self.assertEqual(bytes(ca.disc.rels["game"].data), self.s.rels["game"])

    def test_iso_round_trip(self):
        iso_in = self.tmp / "in.iso"
        mf.write_iso(self.s.root, iso_in, (self.s.root / "sys/fst.bin").read_bytes())
        c = self.cat(iso_in)
        self.assertEqual(c.data(c.find("res.1")), self.s.f1)
        out = self.tmp / "out.iso"
        new = noise(0x2800, 42)
        run_replace(iso_in, "res.1", new, out)
        ca = self.cat(out)
        self.assertEqual(ca.data(ca.find("res.1")), new)
        self.assertEqual(ca.data(ca.find("text_menu.0")), self.s.f2)
        self.assertFalse(any(p.name.startswith("mssb_work_") for p in self.tmp.iterdir()))

    def test_stats_round_trip_and_edit(self):
        js = self.tmp / "stats.json"
        mf.cmd_stats_export(mf.argparse.Namespace(disc=str(self.s.root), out=str(js)))
        mf.cmd_stats_import(mf.argparse.Namespace(disc=str(self.s.root), json=str(js), out=str(self.tmp / "same")))
        self.assertEqual(tree_hashes(self.tmp / "same"), tree_hashes(self.s.root))
        obj = json.loads(js.read_text(encoding="utf-8"))
        obj["characters"][5]["speed"] = 200
        js.write_text(json.dumps(obj), encoding="utf-8")
        mf.cmd_stats_import(mf.argparse.Namespace(disc=str(self.s.root), json=str(js), out=str(self.tmp / "mod")))
        a, b = tree_hashes(self.s.root), tree_hashes(self.tmp / "mod")
        self.assertEqual([k for k in a if a[k] != b[k]], [str(Path("files/ZZZZ.dat"))])
        za = (self.tmp / "mod" / "files/ZZZZ.dat").read_bytes()
        off = self.s.d6[2] + 5 * st.ROW + 0x2F
        self.assertEqual(diff_ranges(self.s.zzzz, za), [(off, off + 1)])
        self.assertEqual(za[off], 200)

    def test_extract_manifest(self):
        out = self.tmp / "x"
        mf.cmd_extract(mf.argparse.Namespace(disc=str(self.s.root), id="all", dir=str(out), raw=False, fst=False))
        man = mf.json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        ids = {m["id"]: m for m in man}
        self.assertEqual((out / ids["res.1"]["file"]).read_bytes(), self.s.f1)
        self.assertEqual(ids["res.1"]["aliases"], ["res_dol2.1", "game_res_a.1"])


@unittest.skipUnless(HAVE_REAL, "no retail disc (set MSSB_DISC)")
class RealDisc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.disc = dm.Disc(REAL)
        cls.cat = ct.Catalog(cls.disc)

    def test_tables_are_descriptors(self):
        self.assertEqual(self.cat.problems, [])
        self.assertEqual(len(self.cat.files), REAL_FILES)

    def test_no_unregistered_descriptors(self):
        known = set((r.image, r.file_off) for f in self.cat.files.values() for r in f.refs)
        music = self.disc.rels["challenge"].file_off(5, ct.FST_MUSIC_TABLE[1])
        extra = []
        for img in self.disc.images():
            for p in dm.scan_image(img, self.disc.zzzz_size):
                if (img.name, p) in known:
                    continue
                if img.name == "challenge" and music <= p < music + 16 * ct.FST_MUSIC_TABLE[2]:
                    continue
                extra.append(img.where(p))
        self.assertEqual(extra, [])

    def test_descriptor_copies_agree(self):
        # every file's rows carry one tuple, except the single partial read
        multi = [f.id for f in self.cat.files.values() if len(set(r.desc for r in f.refs)) > 1]
        self.assertEqual(multi, [])
        partial = [f.id for f in self.cat.files.values() if any(ct.is_partial(r.desc) for r in f.refs)]
        self.assertEqual(partial, ["partial_ch.0"])

    def test_coverage(self):
        cov, pad, gaps = self.cat.coverage("ZZZZ")
        self.assertEqual(cov, REAL_ZZZZ_REFERENCED)
        self.assertEqual(pad, REAL_ZZZZ_PADDING)
        self.assertEqual(cov + pad + sum(b - a for a, b in gaps), self.disc.zzzz_size)
        cov, pad, gaps = self.cat.coverage("aaaa")
        self.assertEqual(cov, REAL_AAAA_REFERENCED)
        self.assertEqual(gaps, [(0, 0x800)])

    def test_heads_sniff(self):
        unknown = [f.id for f in self.cat.files.values() if self.cat.sniff(f)[0] == "error"]
        self.assertEqual(unknown, [])

    def test_text_banks_round_trip(self):
        import formats as fm
        banks = [f for f in self.cat.files.values() if self.cat.sniff(f)[0] == "text"]
        self.assertEqual(len(banks), 5)
        for f in banks:
            data = self.cat.data(f)
            self.assertEqual(fm.text_import(fm.text_export(data)), data, f.id)

    def test_stats_retail(self):
        data = self.cat.data(self.cat.find("stats.0"))
        obj = st.export(data)
        self.assertEqual(st.apply(data, obj), data)
        self.assertEqual(st.warnings(data), [])
        mario, dk = obj["characters"][0], obj["characters"][2]
        self.assertEqual((mario["captain_star"], mario["star_pitch"], mario["fielding_abilities"]),
                         ("mario", "fastball", ["wall_jump", "sliding_catch"]))
        self.assertEqual((dk["captain_star"], dk["character_class"], dk["weight"]), ("dk", "power", 4))
        self.assertEqual([c["char_id"] for c in obj["characters"]], list(range(54)))
        self.assertNotIn("unreferenced", obj)
        self.assertEqual([t["captain"] for t in obj["captain_teams"]],
                         ["mario", "peach", "wario", "dk", "yoshi", "bowser", "luigi", "daisy", "waluigi",
                          "diddy", "birdo", "bowserjr"])
        self.assertEqual(obj["captain_teams"][0]["lineups"][0]["members"][:2], ["mario", "luigi"])

    @unittest.skipUnless(SLOW, "set MSSB_SLOW=1 (copies the disc)")
    def test_real_stats_import_identical(self):
        tmp = Path(tempfile.mkdtemp(prefix="mssb_real_"))
        try:
            js = tmp / "stats.json"
            mf.cmd_stats_export(mf.argparse.Namespace(disc=REAL, out=str(js)))
            mf.cmd_stats_import(mf.argparse.Namespace(disc=REAL, json=str(js), out=str(tmp / "same")))
            if Path(REAL).is_dir():
                self.assertEqual(tree_hashes(tmp / "same"), tree_hashes(REAL))
            else:
                c2 = ct.Catalog(dm.Disc(tmp / "same"))
                self.assertEqual(c2.data(c2.find("stats.0")), self.cat.data(self.cat.find("stats.0")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(SLOW, "set MSSB_SLOW=1 (decompresses ~1 GB)")
    def test_every_file_decompresses(self):
        bad = []
        for f in self.cat.files.values():
            raw = self.cat.raw(f)
            if f.compressed:
                lb, rp = dm.lzss_params(f.flags)
                try:
                    out, used = cc.lzss_decompress(raw, f.size, lb_bits=lb, rp_bits=rp)
                except cc.LZSSError as e:
                    bad.append("%s: %s" % (f.id, e))
                    continue
                if len(out) != f.size or not (f.packed - 0x20 <= used <= f.packed + 3):
                    bad.append("%s: %d bytes from %d of %d packed" % (f.id, len(out), used, f.packed))
            elif len(raw) != f.packed:
                bad.append("%s: short read" % f.id)
        self.assertEqual(bad, [])

    @unittest.skipUnless(SLOW, "set MSSB_SLOW=1 (copies the disc twice)")
    def test_real_replace_round_trip(self):
        tmp = Path(tempfile.mkdtemp(prefix="mssb_real_"))
        try:
            f = self.cat.find("res.1")
            data = self.cat.data(f)
            run_replace(REAL, "res.1", data, tmp / "same")
            c2 = ct.Catalog(dm.Disc(tmp / "same"))
            self.assertEqual(c2.data(c2.find("res.1")), data)
            self.assertEqual(len(set(r.desc for r in c2.find("res.1").refs)), 1)
            shutil.rmtree(tmp / "same")
            mod = bytearray(data)
            mod[0x1000:0x1100] = bytes(0x100)
            run_replace(REAL, "res.1", bytes(mod), tmp / "mod")
            c3 = ct.Catalog(dm.Disc(tmp / "mod"))
            g = c3.find("res.1")
            self.assertEqual(c3.data(g), bytes(mod))
            self.assertEqual(sorted(r.name for r in g.refs), sorted(r.name for r in f.refs))
            self.assertEqual(len(set(r.desc for r in g.refs)), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
