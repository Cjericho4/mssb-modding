#!/usr/bin/env python3
"""mssb_files.py -- list, inspect, extract and replace the data files of Mario Superstar Baseball
(GameCube, USA, GYQE01).

Almost all game data lives inside two big FST files, files/ZZZZ.dat (450 MB) and files/aaaa.dat
(the three RELs).  Neither has a directory: the game finds each file through 16-byte descriptors
hard-coded in main.dol and the RELs.  This tool knows every descriptor table (catalog.py) and so can
address every file by a stable id such as `res.15` (the menu common file) or `subfile.19`.

    py mssb_files.py list     <disc>                 [--csv out.csv | --json out.json] [--table T]
    py mssb_files.py info     <disc> <id>
    py mssb_files.py extract  <disc> <id|all> <dir>  [--raw] [--fst]
    py mssb_files.py replace  <disc> <id> <file> --out <folder|game.iso> [--reuse-gaps]
    py mssb_files.py coverage <disc>                 [--probe]
    py mssb_files.py tables
    py mssb_files.py text-export <disc> <id> <out.txt>    (text banks: text_menu.0, menu_1ff84.1, ...)
    py mssb_files.py text-import <in.txt> <out.bin>       (then `replace` the bank with out.bin)
    py mssb_files.py stats-export <disc> <out.json>       (character stats, chemistry, captain teams)
    py mssb_files.py stats-import <disc> <in.json> --out <folder|game.iso>
    py mssb_files.py sound-list   <disc> [sound.N]        (sound groups; one group's SFX/voice lines)
    py mssb_files.py sound-export <disc> <sound.N|all> <dir>   (samples -> WAV + manifest.json)
    py mssb_files.py sound-import <disc> <sound.N> <dir> --out <folder|game.iso>
    py mssb_files.py voice-swap   <disc> <target> <voice> --out <folder|game.iso>
    py mssb_files.py music-export <disc> <name|all> <dir>  (snd/*.adp -> stereo WAV)

<disc> is an extracted disc folder (Dolphin: right-click -> Properties -> Filesystem -> Extract
Entire Disc, giving sys/ and files/) or a .iso.  The input is never written to.

Python 3.8+, standard library only; works on Windows (`py mssb_files.py ...`).
Extracted files are for your own copy of the game only: never share or commit them.
"""
import argparse
import csv
import hashlib
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disc as dm  # noqa: E402
import formats as fm  # noqa: E402
import catalog as ct  # noqa: E402
import stats as st  # noqa: E402
import sound as sd  # noqa: E402

cc = dm.cc
EXT = {"model": ".gpc", "stadium": ".stadium.bin", "ui": ".ui.bin", "texarc": ".tex.bin", "anim": ".anim",
       "sectioned": ".bin", "musyx": ".musyx.bin", "text": ".text.bin", "hvqm": ".h4m", "rel": ".rel",
       "stats": ".stats.bin", "script": ".script.bin", "blob": ".blob.bin"}


def log(msg):
    print(msg, file=sys.stderr)


def open_catalog(path):
    d = dm.Disc(path)
    return d, ct.Catalog(d)


# ----------------------------------------------------------------------------------------------
# list / info / tables
# ----------------------------------------------------------------------------------------------
def file_row(cat, f):
    fmt, summary = cat.sniff(f)
    return {
        "id": f.id,
        "aliases": " ".join(r.name for r in f.refs[1:]),
        "table": f.refs[0].table["title"],
        "meaning": f.meaning(),
        "container": f.container,
        "offset": "0x%08X" % f.offset,
        "packed": f.packed,
        "size": f.size,
        "compression": ("LZSS %d/%d" % dm.lzss_params(f.flags)) if f.compressed else "stored",
        "format": fmt,
        "description": summary,
        "descriptor_copies": len(f.refs),
    }


def cmd_list(args):
    d, cat = open_catalog(args.disc)
    rows = []
    for f in cat.sorted_files():
        if args.table and not any(r.table["id"] == args.table for r in f.refs):
            continue
        rows.append(file_row(cat, f))
    if args.fst:
        for e, path in d.fst_files():
            rows.append({"id": "fst:" + path, "aliases": "", "table": "FST", "meaning": path, "container": "FST",
                         "offset": "0x%08X" % e["offset"], "packed": e["size"], "size": e["size"],
                         "compression": "stored", "format": _fst_format(path), "description": _fst_desc(path),
                         "descriptor_copies": 0})
    if args.json:
        _write_out(args.json, json.dumps(rows, indent=1))
    if args.csv:
        out = sys.stdout if args.csv == "-" else open(args.csv, "w", newline="", encoding="utf-8")
        w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
        if out is not sys.stdout:
            out.close()
    if not args.json and not args.csv:
        print("%-16s %-4s %-10s %9s %9s %-9s %s" % ("id", "in", "offset", "packed", "size", "format", "what"))
        for r in rows:
            print("%-16s %-4s %-10s %9X %9X %-9s %s | %s" % (
                r["id"], r["container"][:4], r["offset"], r["packed"], r["size"], r["format"], r["meaning"],
                r["description"]))
        log("%d files (%d descriptor rows)%s" % (len(rows), sum(r["descriptor_copies"] for r in rows),
                                                  "" if args.fst else "; add --fst for the FST files"))


def _write_out(dest, text):
    if dest == "-":
        print(text)
    else:
        Path(dest).write_text(text, encoding="utf-8")


def _fst_format(path):
    if path.endswith(".adp"):
        return "adp"
    if path.endswith(".bnr"):
        return "banner"
    return "container"


def _fst_desc(path):
    if path.endswith(".adp"):
        return "DVD streamed ADPCM music (48 kHz stereo, played by the DVD drive)"
    if path.endswith(".bnr"):
        return "disc banner (BNR1: 96x32 RGB5A3 + names)"
    if path == "ZZZZ.dat":
        return "main data container (no directory: addressed by descriptors)"
    if path == "aaaa.dat":
        return "REL container (menus/game/challenge.rel, LZSS)"
    return ""


def cmd_tables(args):
    for t in ct.TABLES:
        where = ("dol 0x%08X" % t["addr"]) if t["module"] == "dol" else "%s.rel .data+0x%X" % (t["module"], t["addr"])
        print("%-18s %-24s x%-4d %-5s %s" % (t["id"], where, t["count"], t["container"], t["title"]))
        print("%18s index: %s" % ("", t["index"]))
        print("%18s loader: %s" % ("", t["loader"]))


def cmd_info(args):
    d, cat = open_catalog(args.disc)
    f = cat.find(args.id)
    row = file_row(cat, f)
    for k in ("id", "meaning", "table", "container", "offset", "compression", "format", "description"):
        print("%-12s %s" % (k + ":", row[k]))
    print("%-12s 0x%X unpacked, 0x%X on disc" % ("size:", f.size, f.packed))
    slot = slot_end(cat, f, reuse_gaps=False) - f.offset
    print("%-12s 0x%X bytes available in place (slot)" % ("slot:", slot))
    print("descriptor rows naming this file (%d):" % len(f.refs))
    for r in f.refs:
        img = d.image(r.image)
        print("  %-22s %-28s %s" % (r.name, img.where(r.file_off), " ".join("%08X" % x for x in r.desc)))
    extra = [c for c in cat.copies(f.desc, f.container)
             if not any(r.image == c[0] and r.file_off == c[1] for r in f.refs)]
    for name, off in extra:
        print("  %-22s %-28s (unregistered copy)" % ("-", d.image(name).where(off)))
    data = cat.data(f)
    h = hashlib.sha1(data).hexdigest()
    print("sha1:        %s" % h)
    for line in fm.describe(row["format"], data)[:args.lines]:
        print("  " + line)


# ----------------------------------------------------------------------------------------------
# extract
# ----------------------------------------------------------------------------------------------
def cmd_extract(args):
    d, cat = open_catalog(args.disc)
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    files = cat.sorted_files() if args.id == "all" else [cat.find(args.id)]
    manifest = []
    for n, f in enumerate(files):
        fmt, summary = cat.sniff(f)
        raw = cat.raw(f)
        data = raw if args.raw else dm.unpack(f.desc, raw)
        name = f.id + (".lzss" if args.raw and f.compressed else EXT.get(fmt, ".bin"))
        (out / name).write_bytes(data)
        manifest.append({"file": name, "id": f.id, "aliases": [r.name for r in f.refs[1:]],
                         "meaning": f.meaning(), "container": f.container, "offset": f.offset,
                         "flags": f.flags, "size": f.size, "packed": f.packed, "format": fmt,
                         "description": summary, "raw": bool(args.raw),
                         "sha1": hashlib.sha1(data).hexdigest()})
        if len(files) > 1 and n % 100 == 0:
            log("  %d/%d %s" % (n, len(files), f.id))
    if args.fst or args.id == "all" and args.fst:
        for e, path in d.fst_files():
            if path in ("ZZZZ.dat", "aaaa.dat"):
                continue
            dest = out / "fst" / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = d.read_fst_file(path)
            dest.write_bytes(data)
            manifest.append({"file": "fst/" + path, "id": "fst:" + path, "container": "FST", "size": len(data),
                             "format": _fst_format(path), "sha1": hashlib.sha1(data).hexdigest()})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    log("extracted %d files to %s (manifest.json lists them)" % (len(manifest), out))


# ----------------------------------------------------------------------------------------------
# coverage
# ----------------------------------------------------------------------------------------------
def probe_gap(d, a, b):
    """Best guess for an unreferenced region: magic/format hits on sector boundaries."""
    hits = {}
    first = None
    step = dm.SECTOR
    for off in range(a, b, step):
        head = d.read_zzzz(off, 0x200)
        kind = None
        if head[:5] == b"HVQM4":
            kind = "HVQM4 movie header"
        elif head[8:16] == b"AdGCForm":
            kind = "'AdGCForm' header"
        elif len(head) >= 4 and dm.u32(head, 0) == fm.ANIM_VERSION:
            kind = "stored ANIM bank"
        elif fm.texture_archive(head + bytes(0x10000), 0, 0x10000 + len(head)) and dm.u16(head, 2) == 0:
            kind = "stored texture archive"
        if kind:
            hits[kind] = hits.get(kind, 0) + 1
            if first is None:
                first = (off, kind)
    return hits, first


def cmd_coverage(args):
    d, cat = open_catalog(args.disc)
    for cont in ("ZZZZ", "aaaa"):
        size = d.container_size(cont)
        cov, pad, gaps = cat.coverage(cont)
        gsum = sum(b - a for a, b in gaps)
        nfiles = sum(1 for f in cat.files.values() if f.container == cont)
        print("%s.dat: %d bytes, %d files" % (cont, size, nfiles))
        print("  referenced by descriptors: %10d bytes  %6.2f%%" % (cov, 100.0 * cov / size))
        print("  sector padding after files:%10d bytes  %6.2f%%" % (pad, 100.0 * pad / size))
        print("  unreferenced gaps:         %10d bytes  %6.2f%%  (%d gaps)" % (gsum, 100.0 * gsum / size, len(gaps)))
        for a, b in gaps:
            guess = ""
            if cont == "ZZZZ" and args.probe:
                hits, first = probe_gap(d, a, b)
                guess = ("  probe: " + ", ".join("%s x%d" % kv for kv in hits.items())) if hits else "  probe: no known magic"
            print("    0x%08X..0x%08X  0x%08X bytes%s" % (a, b, b - a, guess))
    blob = [f for f in cat.files.values() if f.container == "blob"]
    bsize = d.container_size("blob")
    used = sum(f.packed for f in blob)
    print("ARAM blob (master[515]): 0x%X bytes, %d member files, %d bytes in members (%.2f%%)" % (
        bsize, len(blob), used, 100.0 * used / bsize))
    if cat.problems:
        print("table rows that are not descriptors:")
        for p in cat.problems:
            print("  " + p)


# ----------------------------------------------------------------------------------------------
# replace
# ----------------------------------------------------------------------------------------------
def slot_end(cat, f, reuse_gaps):
    """End of the region `f` may occupy in place."""
    same = sorted(g.offset for g in cat.files.values() if g.container == f.container and g.offset > f.offset)
    nxt = same[0] if same else cat.disc.container_size(f.container)
    if f.container == "blob":
        # members are packed back to back on 0x20 boundaries
        return min(nxt, cat.disc.container_size("blob")) if same else cat.disc.container_size("blob")
    if f.container == "ZZZZ" and f.id.startswith("blob."):
        return f.offset + f.packed
    end = dm.align(f.offset + f.extent, dm.SECTOR)
    if reuse_gaps:
        end = nxt
    return min(end, nxt) if same else end


def find_gap(cat, need):
    """First unreferenced ZZZZ gap (sector aligned) that holds `need` bytes, or None."""
    for a, b in cat.coverage("ZZZZ")[2]:
        a = max(dm.align(a, dm.SECTOR), dm.SECTOR)      # offset 0 is never used by a descriptor
        if b - a >= need:
            return a
    return None


class Work:
    """A writable disc folder being patched."""

    def __init__(self, root):
        self.root = Path(root)
        self.disc = dm.Disc(root)
        self.cat = ct.Catalog(self.disc)
        self.zz = open(self.root / "files" / "ZZZZ.dat", "r+b")
        self.aaaa = bytearray(self.disc.aaaa)
        self.dirty_rels = set()
        self.dol_dirty = False
        self.changes = []          # (container, start, end) byte ranges written
        self.rows = set()          # (image, file_off) of every descriptor row rewritten

    def zz_size(self):
        self.zz.seek(0, 2)
        return self.zz.tell()

    def zz_write(self, off, data):
        self.zz.seek(off)
        self.zz.write(data)
        self.changes.append(("ZZZZ", off, off + len(data)))

    def zz_append(self, data, align=dm.SECTOR):
        end = self.zz_size()
        off = dm.align(end, align)
        if off > end:
            self.zz_write(end, bytes(off - end))
        self.zz_write(off, data)
        return off

    def set_desc(self, image_name, file_off, new):
        img = self.disc.image(image_name)
        if tuple(img.desc_at(file_off)) == tuple(new):
            return
        img.set_desc(file_off, new)
        self.rows.add((image_name, file_off))
        if image_name == "dol":
            self.dol_dirty = True
        else:
            self.dirty_rels.add(image_name)

    def finish(self):
        """Repack changed RELs into aaaa.dat, write main.dol, aaaa.dat and fst.bin."""
        d = self.disc
        for name, addr in dm.REL_DESC_ADDRS:
            if name not in self.dirty_rels:
                continue
            old = d.rel_desc[name]
            log("  repacking %s.rel (LZSS, this takes a while) ..." % name)
            packed = dm.pack(bytes(d.rels[name].data), old[0])
            if dm.unpack((old[0], old[1], 0, len(packed)), packed) != bytes(d.rels[name].data):
                raise SystemExit("internal error: %s.rel does not survive its LZSS round trip" % name)
            starts = sorted(dd[2] for dd in d.rel_desc.values() if dd[2] > old[2])
            limit = starts[0] if starts else len(self.aaaa)
            if old[2] + len(packed) <= limit:
                off = old[2]
            else:
                off = dm.align(len(self.aaaa), dm.SECTOR)
                self.aaaa.extend(bytes(off - len(self.aaaa)))
                log("  %s.rel no longer fits its slot: appended to aaaa.dat at 0x%X" % (name, off))
            end = off + len(packed)
            if end > len(self.aaaa):
                self.aaaa.extend(bytes(end - len(self.aaaa)))
            self.aaaa[off:end] = packed
            self.changes.append(("aaaa", off, end))
            new = (old[0], old[1], off, len(packed))
            self.set_desc("dol", d.dol.ram_off(addr), new)
            d.rel_desc[name] = new
            loose = self.root / "files" / ("%s.rel" % name)
            if loose.exists():      # not in the FST (dtk extracts it); keep it in sync anyway
                loose.write_bytes(bytes(d.rels[name].data))
        (self.root / "files" / "aaaa.dat").write_bytes(bytes(self.aaaa))
        if self.dol_dirty:
            (self.root / "sys" / "main.dol").write_bytes(bytes(d.dol.data))
        self.zz.flush()
        fst = (self.root / "sys" / "fst.bin").read_bytes()
        fst = cc.patch_fst_size(fst, "ZZZZ.dat", self.zz_size())
        fst = cc.patch_fst_size(fst, "aaaa.dat", len(self.aaaa))
        (self.root / "sys" / "fst.bin").write_bytes(fst)
        self.zz.close()
        d.close()


def build_packed(f, data):
    if f.compressed:
        packed = dm.pack(data, f.flags)
        check = dm.unpack((f.flags, dm.SIZE_COMPRESSED | len(data), 0, len(packed)), packed)
        if check != data:
            raise SystemExit("internal error: LZSS round trip failed")
        return packed, dm.SIZE_COMPRESSED | len(data), len(packed)
    padded = data + bytes(dm.align(len(data), 4) - len(data))
    return padded, len(data), len(padded)


def apply_replace(work, file_id, data, reuse_gaps=False):
    """Patch `work` (a Work) so that file `file_id` decodes to `data`.  Returns a report dict."""
    cat = work.cat
    d = work.disc
    f = cat.find(file_id)
    if f.id.startswith("blob."):
        raise SystemExit("blob.0 is the container of the master.* files: replace those instead")
    if f.partial_only:
        raise SystemExit("%s is only ever read partially (0x%X of 0x%X bytes); replacing it would change "
                         "what the game reads, so it is not supported" % (f.id, f.size, f.packed))
    if len(data) > dm.SIZE_MASK:
        raise SystemExit("file too large")
    old_fmt = cat.sniff(f)[0]
    new_fmt = fm.sniff(data[:fm.HEAD], len(data), not f.compressed)[0]
    if new_fmt != old_fmt and old_fmt not in ("stats", "blob"):   # those are known by table, not content
        log("warning: %s was %s, the new file sniffs as %s" % (f.id, old_fmt, new_fmt))
    packed, sizew, plen = build_packed(f, data)
    report = {"id": f.id, "old_offset": f.offset, "old_packed": f.packed, "old_size": f.size,
              "packed": plen, "size": len(data), "container": f.container}
    end = slot_end(cat, f, reuse_gaps)

    if f.container == "ZZZZ":
        if f.offset + plen <= end:
            new_off = f.offset
            work.zz_write(f.offset, packed + bytes(max(0, f.extent - plen)))
            report["placement"] = "in place"
        else:
            gap = find_gap(cat, plen) if reuse_gaps else None
            if gap is not None:
                new_off = gap
                work.zz_write(gap, packed)
                report["placement"] = "unreferenced gap at 0x%X" % gap
            else:
                new_off = work.zz_append(packed)
                report["placement"] = "appended to ZZZZ.dat"
    elif f.container == "blob":
        bdesc = d.blob_desc()
        if f.offset + plen <= end:
            new_off = f.offset
            work.zz_write(bdesc[2] + f.offset, packed + bytes(max(0, min(f.extent, end - f.offset) - plen)))
            report["placement"] = "in place (ARAM blob)"
        else:
            # grow the blob: copy it to the end of ZZZZ.dat with the new member appended
            old_blob = d.read_zzzz(bdesc[2], bdesc[3])
            new_off = dm.align(len(old_blob), 0x20)
            blob = old_blob + bytes(new_off - len(old_blob)) + packed
            blob += bytes(dm.align(len(blob), 0x20) - len(blob))
            boff = work.zz_append(blob)
            bnew = (0, len(blob), boff, len(blob))
            for img_name, fo in cat.copies(bdesc, "ZZZZ"):
                work.set_desc(img_name, fo, bnew)
            dol = cc.Dol(bytes(d.dol.data))
            if dol.blob_alloc_size() < len(blob):
                dol.set_blob_alloc_size(len(blob))
                for ram in (dm.BLOB_LIS, dm.BLOB_ADDI):
                    o = d.dol.ram_off(ram)
                    d.dol.data[o:o + 4] = dol.data[o:o + 4]
                work.dol_dirty = True
            report["placement"] = "ARAM blob grown to 0x%X and moved to ZZZZ.dat 0x%X" % (len(blob), boff)
            if len(blob) > 0x800000:
                log("warning: the ARAM blob is now %.1f MB; ARAM is 16 MB in total" % (len(blob) / 1048576.0))
    elif f.container == "aaaa":
        rel = {"rel.0": "menus", "rel.1": "game", "rel.2": "challenge"}[f.id]
        # replace the module image itself; finish() packs and places it
        d.rels[rel] = dm.Image(rel, data)
        work.dirty_rels.add(rel)
        report["placement"] = "repacked into aaaa.dat"
        report["new_offset"] = None
        return report
    else:
        raise SystemExit("cannot replace files in %s" % f.container)

    new_desc = (f.flags, sizew, new_off, plen)
    updated = []
    for r in f.refs:
        if r.desc == f.desc:
            continue
        if ct.is_partial(r.desc):
            # a partial read of the same file (e.g. a 0x200-byte header read): move it, keep its length
            nd = (r.desc[0], r.desc[1], new_off, max(plen, r.desc[1] & dm.SIZE_MASK))
            updated.append(r.name + " (partial read, moved)")
        else:
            nd = new_desc                      # a full copy that differs only in rounding
            updated.append(r.name)
        work.set_desc(r.image, r.file_off, nd)
    for img_name, fo in cat.copies(f.desc, f.container):
        work.set_desc(img_name, fo, new_desc)
        img = d.image(img_name)
        names = [r.name for r in f.refs if r.image == img_name and r.file_off == fo]
        updated.append(names[0] if names else img.where(fo))
    report["new_offset"] = new_off
    report["descriptors"] = updated
    report["rows"] = sorted(work.rows)
    return report


def write_iso(src_root, iso_path, orig_fst, base_iso=None):
    """Write an ISO from a disc folder.  Files that grew are moved past the last file (the FST
    offsets are patched); everything else keeps its retail position.  With `base_iso` (the input
    image) the output starts as a copy of it, so the filler data between files stays identical."""
    sysd = Path(src_root) / "sys"
    filesd = Path(src_root) / "files"
    boot = bytearray((sysd / "boot.bin").read_bytes())
    fst = bytearray((sysd / "fst.bin").read_bytes())
    old = {path: (e["offset"], e["size"]) for e, path in cc.fst_paths(orig_fst)}
    ents = list(cc.fst_paths(bytes(fst)))
    last_end = max(e["offset"] + e["size"] for e, _ in ents)
    last_path = max(ents, key=lambda ep: ep[0]["offset"])[1]
    tail = dm.align(last_end, 0x8000)
    for e, path in ents:
        size = (filesd / path).stat().st_size
        o_off, o_size = old.get(path, (e["offset"], e["size"]))
        if size > o_size and path != last_path:
            struct.pack_into(">I", fst, e["index"] * 12 + 4, tail)
            log("  %s grew: moved to ISO offset 0x%X" % (path, tail))
            tail = dm.align(tail + size, 0x8000)
    dol_off, fst_off, fst_size = struct.unpack_from(">III", boot, 0x420)
    if len(fst) != fst_size:
        raise SystemExit("fst.bin size changed")
    if base_iso:
        shutil.copyfile(str(base_iso), str(iso_path))
    with open(iso_path, "r+b" if base_iso else "wb") as out:
        out.write(boot)
        out.seek(0x440)
        out.write((sysd / "bi2.bin").read_bytes())
        out.seek(0x2440)
        out.write((sysd / "apploader.img").read_bytes())
        out.seek(dol_off)
        out.write((sysd / "main.dol").read_bytes())
        out.seek(fst_off)
        out.write(fst)
        end = 0
        for e, path in cc.fst_paths(bytes(fst)):
            src = filesd / path
            size = src.stat().st_size
            if size != e["size"]:
                raise SystemExit("fst.bin says %s is %d bytes but the file is %d" % (path, e["size"], size))
            out.seek(e["offset"])
            with open(src, "rb") as g:
                shutil.copyfileobj(g, out, 1 << 20)
            end = max(end, e["offset"] + size)
        if not base_iso:
            out.truncate(max(dm.align(end, 0x8000), 0x57058000))
        elif out.seek(0, 2) < end:
            out.truncate(dm.align(end, 0x8000))
    (sysd / "fst.bin").write_bytes(bytes(fst))


def verify(before, after_root, report, data):
    """Re-read the patched disc and check the replaced file and that nothing else changed."""
    d2 = dm.Disc(after_root)
    cat2 = ct.Catalog(d2)
    f2 = cat2.find(report["id"])
    got = cat2.data(f2)
    if got != data:
        raise SystemExit("VERIFY FAILED: %s does not decode to the new file" % report["id"])
    bad = []
    rep_names = set(r.name for r in before.cat.find(report["id"]).refs)
    for key, f in before.cat.files.items():
        if f.id == report["id"] or f.id.startswith("blob.") or f.id.startswith("rel."):
            continue
        g = cat2.files.get(key)
        if g is None or [r.desc for r in g.refs] != [r.desc for r in f.refs]:
            bad.append("%s: descriptors changed" % f.id)
            continue
        if before.disc.read(f.container, f.offset, f.packed) != d2.read(g.container, g.offset, g.packed):
            bad.append("%s: bytes changed" % f.id)
    # module images may only differ inside the descriptor rows the replace rewrote
    rows = set(tuple(r) for r in report.get("rows", []))
    for img in before.disc.images():
        img2 = d2.image(img.name)
        if report["container"] == "aaaa" and img.name == {"rel.0": "menus", "rel.1": "game",
                                                            "rel.2": "challenge"}.get(report["id"]):
            continue
        allowed = [(fo, 16) for (n, fo) in rows if n == img.name]
        if img.name == "dol":
            allowed += [(d2.dol.ram_off(dm.BLOB_LIS), 4), (d2.dol.ram_off(dm.BLOB_ADDI), 4)]
        a, b = img.data, img2.data
        if len(a) != len(b):
            bad.append("%s changed size" % img.name)
            continue
        if a == b:
            continue
        for k in range(0, len(a), 4):
            if a[k:k + 4] != b[k:k + 4] and not any(lo <= k < lo + n for lo, n in allowed):
                bad.append("%s: unexpected change at %s" % (img.name, img.where(k)))
                break
    for name in rep_names:
        g = cat2.by_id.get(name)
        if g is None or g is not f2 and g.offset != f2.offset:
            bad.append("%s: descriptor not updated" % name)
    d2.close()
    if bad:
        raise SystemExit("VERIFY FAILED:\n  " + "\n  ".join(bad[:20]))
    return True


def cmd_replace(args):
    src = Path(args.disc)
    out = Path(args.out)
    if out.resolve() == src.resolve():
        raise SystemExit("--out must not be the input")
    if out.exists():
        raise SystemExit("%s already exists: choose a new output folder or ISO name" % out)
    data = Path(args.file).read_bytes()
    iso_out = out.suffix.lower() == ".iso"
    work_root = Path(tempfile.mkdtemp(prefix="mssb_work_", dir=str(out.parent))) / "disc" if iso_out else out
    try:
        log("copying the disc to %s ..." % work_root)
        if src.is_file():
            cc.extract_iso(src, work_root, log)
        else:
            shutil.copytree(src, work_root)
        before = Work(work_root)
        orig_fst = bytes(before.disc.fst)
        snapshot = argparse.Namespace(disc=dm.Disc(src))      # the unpatched input, for verification
        snapshot.cat = ct.Catalog(snapshot.disc)
        report = apply_replace(before, args.id, data, reuse_gaps=args.reuse_gaps)
        before.finish()
        report["rows"] = sorted(before.rows)
        verify(snapshot, work_root, report, data)
        log("replaced %s: %s, 0x%X bytes (0x%X packed) at 0x%X" % (
            report["id"], report["placement"], report["size"], report["packed"], report["new_offset"] or 0))
        for name in report.get("descriptors", []):
            log("  descriptor updated: %s" % name)
        if iso_out:
            log("writing %s ..." % out)
            write_iso(work_root, out, orig_fst, base_iso=src if src.is_file() else None)
            verify(snapshot, out, report, data)
            log("verified %s" % out)
        else:
            log("verified %s" % out)
    finally:
        for obj in (locals().get("before"), locals().get("snapshot")):
            if obj is not None:
                obj.disc.close()
        if iso_out:
            shutil.rmtree(work_root.parent, ignore_errors=True)
            if work_root.parent.exists():
                log("warning: could not remove the work folder %s; delete it by hand" % work_root.parent)
    report.pop("rows", None)
    print(json.dumps(report, indent=1))


# ----------------------------------------------------------------------------------------------
# text banks
# ----------------------------------------------------------------------------------------------
def cmd_text_export(args):
    d, cat = open_catalog(args.disc)
    f = cat.find(args.id)
    if cat.sniff(f)[0] != "text":
        raise SystemExit("%s is not a text bank (text banks: %s)" % (
            f.id, ", ".join(g.id for g in cat.files.values() if cat.sniff(g)[0] == "text")))
    data = cat.data(f)
    text = fm.text_export(data)
    if fm.text_import(text) != data:
        raise SystemExit("internal error: %s does not round-trip through the text format" % f.id)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    log("wrote %d strings to %s (edit, then: text-import %s new.bin; replace <disc> %s new.bin --out ...)" % (
        dm.u16(data, 0), args.out, args.out, f.id))


def cmd_text_import(args):
    data = fm.text_import(Path(args.txt).read_text(encoding="utf-8"))
    Path(args.out).write_bytes(data)
    log("wrote %s (%d strings, 0x%X bytes)" % (args.out, dm.u16(data, 0), len(data)))


# ----------------------------------------------------------------------------------------------
# character stats
# ----------------------------------------------------------------------------------------------
STATS_ID = "stats.0"


def cmd_stats_export(args):
    d, cat = open_catalog(args.disc)
    obj = st.export(cat.data(cat.find(STATS_ID)))
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1)
        fh.write("\n")
    log("wrote %d characters and %d captain teams to %s (edit, then: stats-import <disc> %s --out ...)" % (
        len(obj["characters"]), len(obj["captain_teams"]), args.out, args.out))


def cmd_stats_import(args):
    d, cat = open_catalog(args.disc)
    old = cat.data(cat.find(STATS_ID))
    d.close()
    with open(args.json, encoding="utf-8") as fh:
        obj = json.load(fh)
    try:
        new = st.apply(old, obj)
    except st.StatsError as e:
        raise SystemExit("%s: %s" % (args.json, e))
    for note in st.warnings(new):
        log("note: " + note)
    changed = sum(a != b for a, b in zip(old, new))
    log("%d byte(s) of %s differ from the disc" % (changed, STATS_ID))
    out = Path(args.out)
    tmp = Path(tempfile.mkdtemp(prefix="mssb_stats_", dir=str(out.resolve().parent)))
    try:
        (tmp / "stats.bin").write_bytes(new)
        cmd_replace(argparse.Namespace(disc=args.disc, id=STATS_ID, file=str(tmp / "stats.bin"),
                                       out=args.out, reuse_gaps=False))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------------------------
# sounds (MusyX groups `sound.N`, voices, announcer) and music (.adp streams)
# ----------------------------------------------------------------------------------------------
SOUND_ROWS = 48
GAME_DATA = 5                      # game.rel section holding .data
GAME_VOICE_GID = 0x8148            # u8[32] voice group id per unique index (lbl_3_data_8148)
GAME_VOICE_BASE = 0x8168           # u16[54] voice SFX base per charID (lbl_3_data_8168)
DOL_UNIQUE = 0x800E8558            # 6-byte rows per charID, unique index at +2
DOL_ANNOUNCER = 0x80108DF4         # u16[33] announcer name SFX per unique index


def _sound_id(arg):
    a = str(arg)
    if a.isdigit():
        a = "sound.%d" % int(a)
    if not a.startswith("sound.") or not a[6:].isdigit() or not 0 <= int(a[6:]) < SOUND_ROWS:
        raise SystemExit("%s: expected a sound group id sound.0 .. sound.%d" % (arg, SOUND_ROWS - 1))
    return a


def _game_data(d, off, n):
    g = d.image("game")
    o = g.file_off(GAME_DATA, off)
    return bytes(g.data[o:o + n])


def voice_tables(d):
    """(unique index per charID, voice gid per unique index, SFX base per charID) from the disc."""
    dol = d.image("dol")
    o = dol.ram_off(DOL_UNIQUE)
    uniq = [dol.data[o + c * 6 + 2] for c in range(54)]
    gids = list(_game_data(d, GAME_VOICE_GID, 32))
    base = list(struct.unpack(">54H", _game_data(d, GAME_VOICE_BASE, 108)))
    return uniq, gids, base


def _char_or_unique(d, name):
    """A character name / charID number / unique-index name -> unique index (voice bank)."""
    uniq, _, _ = voice_tables(d)
    low = name.lower()
    for u, n in enumerate(sd.GRID_NAMES):
        if n.lower() == low:
            return u
    names = [n.lower() for n in cc.CHAR_NAMES]
    if low in names:
        return uniq[names.index(low)]
    if name.isdigit() and int(name) < len(uniq):
        return uniq[int(name)]
    raise SystemExit("unknown character %r (use a charID 0..53, a name like Mario / BlueToad, "
                     "or a voice name: %s)" % (name, ", ".join(sd.GRID_NAMES)))


def _line_of(row, fx_id, d):
    """Voice line index of an SFX in a voice row, else None."""
    if not sd.VOICE_ROW0 <= row < sd.VOICE_ROW0 + 32:
        return None
    uniq, _, base = voice_tables(d)
    for c, u in enumerate(uniq):
        if u == row - sd.VOICE_ROW0 and 0 <= fx_id - base[c] < sd.VOICE_LINES:
            return fx_id - base[c]
    return None


def cmd_sound_list(args):
    d, cat = open_catalog(args.disc)
    if not args.id:
        print("%-9s %-10s %4s %-17s %4s %-22s %s" % ("id", "size", "gid", "SFX ids", "smp", "rates", "what"))
        for row in range(SOUND_ROWS):
            f = cat.find("sound.%d" % row)
            g = sd.Group(cat.data(f))
            fx = [x[1]["id"] for x in g.fx_table()]
            rates = sorted(set(s.rate for s in g.samples))
            print("%-9s 0x%-8X %4s %-17s %4d %-22s %s" % (
                f.id, f.size, "/".join(str(x["id"]) for x in g.groups),
                "0x%X..0x%X" % (min(fx), max(fx)) if fx else "-", len(g.samples),
                ",".join(str(r) for r in rates), sd.row_meaning(row)))
        return
    sid = _sound_id(args.id)
    row = int(sid[6:])
    g = sd.Group(cat.data(cat.find(sid)))
    print("%s: %s" % (sid, sd.row_meaning(row)))
    for gr in g.groups:
        print("group %d (%s): %d macros, %d samples, %d SFX" % (
            gr["id"], "SFX" if gr["type"] == 1 else "songs", len(gr["macros"]), len(gr["samples"]), len(gr["fx"])))
    print("\nSFX     line macro   samples (id rate seconds)")
    for gid, fx, smps in g.fx_table():
        line = _line_of(row, fx["id"], d)
        desc = []
        for x in smps:
            try:
                s = g.sample(x)
                desc.append("0x%X %d %.2fs" % (x, s.rate, s.samples / float(s.rate)))
            except sd.SoundError:
                desc.append("0x%X (in another group)" % x)
        print("0x%-5X %-4s 0x%-5X %s%s" % (fx["id"], "" if line is None else line, fx["macro"], "; ".join(desc),
                                         ("   <- " + sd.LINE_USES[line]) if line in sd.LINE_USES else ""))


def _sample_name(s):
    return "smp_%04X.wav" % s.id


def cmd_sound_export(args):
    d, cat = open_catalog(args.disc)
    rows = range(SOUND_ROWS) if args.id == "all" else [int(_sound_id(args.id)[6:])]
    out = Path(args.dir)
    for row in rows:
        sid = "sound.%d" % row
        g = sd.Group(cat.data(cat.find(sid)))
        dest = out / sid if len(rows) > 1 else out
        dest.mkdir(parents=True, exist_ok=True)
        users = {}
        for gid, fx, smps in g.fx_table():
            for x in smps:
                users.setdefault(x, []).append(fx["id"])
        manifest = {"format": "mssb-sound 1", "id": sid, "what": sd.row_meaning(row),
                    "groups": [{"id": gr["id"], "type": "sfx" if gr["type"] == 1 else "songs"} for gr in g.groups],
                    "sfx": [], "samples": []}
        for gid, fx, smps in g.fx_table():
            line = _line_of(row, fx["id"], d)
            manifest["sfx"].append({"id": fx["id"], "group": gid, "macro": fx["macro"], "samples": smps,
                                    "line": line, "use": sd.LINE_USES.get(line, "")})
        for s in g.samples:
            pcm = s.decode()
            sd.write_wav(dest / _sample_name(s), pcm, s.rate)
            manifest["samples"].append({"file": _sample_name(s), "id": s.id, "rate": s.rate, "samples": s.samples,
                                        "seconds": round(s.samples / float(s.rate), 3), "root_key": s.root_key,
                                        "loop": [s.loop_start, s.loop_length] if s.loop_length else None,
                                        "sfx": users.get(s.id, [])})
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        log("%s: %d samples -> %s" % (sid, len(g.samples), dest))


def sound_import_bytes(old, folder):
    """New group bytes from the edited WAVs in `folder` (only files whose audio or rate changed)."""
    g = sd.Group(old)
    folder = Path(folder)
    repl, notes = {}, []
    for s in g.samples:
        p = folder / _sample_name(s)
        if not p.exists():
            continue
        pcm, rate = sd.read_wav(p)
        if rate == s.rate and pcm == s.decode():
            continue
        repl[s.id] = sd.Encoded.from_pcm(pcm, rate)
        notes.append("0x%X: %d samples at %d Hz (was %d at %d Hz)" % (s.id, len(pcm), rate, s.samples, s.rate))
    for p in sorted(folder.glob("*.wav")):
        if not any(p.name.lower() == _sample_name(s).lower() for s in g.samples):
            notes.append("ignored %s (no sample with that id in this group)" % p.name)
    return (g.with_samples(repl) if repl else None), notes


def _replace_with_bytes(disc, file_id, data, out, prefix):
    outp = Path(out)
    tmp = Path(tempfile.mkdtemp(prefix=prefix, dir=str(outp.resolve().parent)))
    try:
        (tmp / "new.bin").write_bytes(data)
        cmd_replace(argparse.Namespace(disc=disc, id=file_id, file=str(tmp / "new.bin"), out=out,
                                       reuse_gaps=False))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_sound_import(args):
    sid = _sound_id(args.id)
    d, cat = open_catalog(args.disc)
    old = cat.data(cat.find(sid))
    d.close()
    new, notes = sound_import_bytes(old, args.dir)
    for n in notes:
        log("  " + n)
    if new is None:
        raise SystemExit("no WAV in %s differs from %s: nothing to do" % (args.dir, sid))
    grow = len(new) - len(old)
    if grow > 0:
        log("note: %s grows by 0x%X bytes; its samples live in MusyX's 8 MB of ARAM while it is loaded "
            "(see .agent/sounds.md), so keep replacements short" % (sid, grow))
    _replace_with_bytes(args.disc, sid, new, args.out, "mssb_sound_")


def cmd_voice_swap(args):
    d, cat = open_catalog(args.disc)
    target = _char_or_unique(d, args.target)
    voice = _char_or_unique(d, args.voice)
    uniq, gids, base = voice_tables(d)
    members = [cc.CHAR_NAMES[c] for c, u in enumerate(uniq) if u == target]
    fx_bases = sorted(set(base[c] for c, u in enumerate(uniq) if u == target))
    if len(fx_bases) != 1:
        raise SystemExit("characters sharing voice bank %d use different SFX bases %s; not supported"
                         % (target, fx_bases))
    src = sd.Group(cat.data(cat.find("sound.%d" % (sd.VOICE_ROW0 + voice))))
    new = src.retarget(gids[target], fx_bases[0], sd.SWAP_ID_BASE + target * sd.SWAP_ID_STRIDE)
    d.close()
    log("voice bank of %s (%s) <- %s's voice (sound.%d -> sound.%d, group %d, SFX 0x%X..)" % (
        sd.GRID_NAMES[target], ", ".join(members), sd.GRID_NAMES[voice], sd.VOICE_ROW0 + voice,
        sd.VOICE_ROW0 + target, gids[target], fx_bases[0]))
    _replace_with_bytes(args.disc, "sound.%d" % (sd.VOICE_ROW0 + target), new, args.out, "mssb_voice_")


def cmd_music_export(args):
    d = dm.Disc(args.disc)
    paths = [p for e, p in d.fst_files() if p.lower().endswith(".adp")]
    if args.name != "all":
        paths = [p for p in paths if Path(p).stem.lower() in (args.name.lower(), args.name.lower() + "_h")
                 or Path(p).name.lower() == args.name.lower()]
        if not paths:
            raise SystemExit("no .adp named %s (try: %s)" % (args.name, ", ".join(
                Path(p).stem for e, p in d.fst_files() if p.lower().endswith(".adp"))))
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    for p in paths:
        left, right = sd.adp_decode(d.read_fst_file(p))
        dest = out / (Path(p).stem + ".wav")
        sd.write_wav_stereo(dest, left, right, sd.ADP_RATE)
        log("%s: %.1f s -> %s" % (p, len(left) / float(sd.ADP_RATE), dest))
    d.close()


# ----------------------------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("list", help="every file with id, offset, sizes, format")
    s.add_argument("disc")
    s.add_argument("--csv", help="write CSV here ('-' = stdout)")
    s.add_argument("--json", help="write JSON here ('-' = stdout)")
    s.add_argument("--table", help="only files named by this table id (see `tables`)")
    s.add_argument("--fst", action="store_true", help="also list the FST files (music, banner)")
    s = sub.add_parser("info", help="details and a parsed summary of one file")
    s.add_argument("disc")
    s.add_argument("id")
    s.add_argument("--lines", type=int, default=80, help="max summary lines")
    s = sub.add_parser("extract", help="decompress files to a folder (+ manifest.json)")
    s.add_argument("disc")
    s.add_argument("id", help="file id or 'all'")
    s.add_argument("dir")
    s.add_argument("--raw", action="store_true", help="write the packed bytes instead")
    s.add_argument("--fst", action="store_true", help="also copy the FST files (music .adp, banner)")
    s = sub.add_parser("replace", help="write a new disc folder/ISO with one file replaced")
    s.add_argument("disc")
    s.add_argument("id")
    s.add_argument("file", help="the new, uncompressed file")
    s.add_argument("--out", required=True, help="new output folder, or a path ending in .iso")
    s.add_argument("--reuse-gaps", action="store_true",
                   help="if the file does not fit in place, put it in unreferenced ZZZZ.dat space "
                        "instead of growing the disc (keeps the retail disc size)")
    s = sub.add_parser("coverage", help="how much of ZZZZ.dat / aaaa.dat the descriptors cover")
    s.add_argument("disc")
    s.add_argument("--probe", action="store_true", help="look for known magics inside the gaps")
    sub.add_parser("tables", help="every descriptor table this tool knows")
    s = sub.add_parser("text-export", help="text bank -> editable UTF-8 text")
    s.add_argument("disc")
    s.add_argument("id")
    s.add_argument("out")
    s = sub.add_parser("text-import", help="edited text -> text bank binary (feed it to replace)")
    s.add_argument("txt")
    s.add_argument("out")
    s = sub.add_parser("stats-export", help="character stats, chemistry and captain teams -> JSON")
    s.add_argument("disc")
    s.add_argument("out")
    s = sub.add_parser("stats-import", help="edited stats JSON -> new disc folder/ISO")
    s.add_argument("disc")
    s.add_argument("json")
    s.add_argument("--out", required=True, help="new output folder, or a path ending in .iso")
    s = sub.add_parser("sound-list", help="the 48 MusyX sound groups, or one group's SFX and samples")
    s.add_argument("disc")
    s.add_argument("id", nargs="?", help="sound.N (or N) for its SFX table")
    s = sub.add_parser("sound-export", help="a sound group's samples -> WAV files + manifest.json")
    s.add_argument("disc")
    s.add_argument("id", help="sound.N, N or 'all'")
    s.add_argument("dir")
    s = sub.add_parser("sound-import", help="edited WAVs (from sound-export) -> new disc folder/ISO")
    s.add_argument("disc")
    s.add_argument("id", help="sound.N or N")
    s.add_argument("dir", help="folder with smp_XXXX.wav files; unchanged files are skipped")
    s.add_argument("--out", required=True, help="new output folder, or a path ending in .iso")
    s = sub.add_parser("voice-swap", help="give one character's voice bank another character's voice")
    s.add_argument("disc")
    s.add_argument("target", help="whose voice changes (name, charID or voice name, e.g. Toad)")
    s.add_argument("voice", help="whose voice they get (e.g. Mario)")
    s.add_argument("--out", required=True, help="new output folder, or a path ending in .iso")
    s = sub.add_parser("music-export", help="DVD-streamed music (snd/*.adp) -> stereo 48 kHz WAV")
    s.add_argument("disc")
    s.add_argument("name", help="stream name (e.g. mario_01_h) or 'all'")
    s.add_argument("dir")
    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 1
    {"list": cmd_list, "info": cmd_info, "extract": cmd_extract, "replace": cmd_replace,
     "coverage": cmd_coverage, "tables": cmd_tables, "text-export": cmd_text_export,
     "text-import": cmd_text_import, "stats-export": cmd_stats_export,
     "stats-import": cmd_stats_import, "sound-list": cmd_sound_list, "sound-export": cmd_sound_export,
     "sound-import": cmd_sound_import, "voice-swap": cmd_voice_swap, "music-export": cmd_music_export}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
