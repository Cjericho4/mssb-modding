#!/usr/bin/env python3
"""Check that Clone Maker's "Export to Blender" (core/src/gltf.rs, `mssb-clone export-gltf`) writes
the same glTF as the Python reference tools/datamap/export_gltf.py (stdlib only).

For each character and option both exporters run into a temporary folder under .scratch, then:
the JSON trees must match key for key (numbers within --tol; asset.generator and the data URIs
are skipped), every accessor's data must match (u16 exactly, f32 within --tol), and every embedded
picture must have the same pixels (painted PNGs: the same bytes). If the reference fails, the
port must fail too. Nothing from the game is printed or kept.

    py tools/clonemaker/difftest_gltf.py --disc orig/GYQE01                 # seven characters
    py tools/clonemaker/difftest_gltf.py --disc orig/GYQE01 --chars all     # all 54
"""
import argparse
import base64
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "tools/clonemaker/core"
REFERENCE = ROOT / "tools/datamap/export_gltf.py"
SKIP = {("asset", "generator")}
# Mario, Donkey Kong, Peach, Baby Mario, King Boo, Blue Toad, Red Shy Guy
DEFAULT_CHARS = [0, 2, 4, 7, 15, 29, 37]


class Mismatch(Exception):
    pass


class Stats:
    def __init__(self):
        self.max_diff = 0.0
        self.identical = True

    def number(self, a, b, tol, where):
        if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, int) != isinstance(b, int):
            if a != b or type(a) is not type(b):
                raise Mismatch("%s: %r != %r" % (where, a, b))
        diff = abs(a - b)
        if diff:
            self.identical = False
        self.max_diff = max(self.max_diff, diff)
        if diff > tol:
            raise Mismatch("%s: %r != %r (diff %g)" % (where, a, b, diff))


def walk(a, b, path, stats, tol):
    where = "/".join(map(str, path)) or "document"
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            raise Mismatch("%s: keys %s != %s" % (where, sorted(a), sorted(b)))
        for key in a:
            if (path[-1:] and (path[-1], key) in SKIP) or key == "uri":
                continue
            walk(a[key], b[key], path + [key], stats, tol)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            raise Mismatch("%s: %d != %d items" % (where, len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, path + [i], stats, tol)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        stats.number(a, b, tol, where)
    elif a != b:
        raise Mismatch("%s: %r != %r" % (where, a, b))


def data_uri(uri):
    return base64.b64decode(uri.split(",", 1)[1])


def png_pixels(png):
    """Width, height, colour type and unfiltered rows of an 8-bit PNG."""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise Mismatch("not a PNG")
    pos, idat, header = 8, b"", None
    while pos < len(png):
        size, name = struct.unpack_from(">I4s", png, pos)
        body = png[pos + 8:pos + 8 + size]
        if name == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif name == b"IDAT":
            idat += body
        pos += 12 + size
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or interlace:
        raise Mismatch("unsupported PNG layout")
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(idat)
    stride = width * bpp
    rows, previous = [], bytearray(stride)
    for y in range(height):
        kind = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            corner = previous[i - bpp] if i >= bpp else 0
            if kind == 1:
                row[i] = (row[i] + left) & 255
            elif kind == 2:
                row[i] = (row[i] + up) & 255
            elif kind == 3:
                row[i] = (row[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                row[i] = (row[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        rows.append(bytes(row))
        previous = row
    return width, height, colour, b"".join(rows)


def compare(ref_path, port_path, tol, painted):
    a = json.loads(ref_path.read_text(encoding="utf-8"))
    b = json.loads(port_path.read_text(encoding="utf-8"))
    stats = Stats()
    walk(a, b, [], stats, tol)
    buffers = [data_uri(x["uri"]) for x in a["buffers"]], [data_uri(x["uri"]) for x in b["buffers"]]
    stats.bytes_equal = buffers[0] == buffers[1]
    for i, accessor in enumerate(a["accessors"]):
        view = a["bufferViews"][accessor["bufferView"]]
        n = accessor["count"] * {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[accessor["type"]]
        fmt = "<%d%s" % (n, "f" if accessor["componentType"] == 5126 else "H")
        x = struct.unpack_from(fmt, buffers[0][view["buffer"]], view["byteOffset"])
        y = struct.unpack_from(fmt, buffers[1][view["buffer"]], view["byteOffset"])
        if accessor["componentType"] != 5126:
            if x != y:
                raise Mismatch("accessor %d: integer data differs" % i)
            continue
        for k, (p, q) in enumerate(zip(x, y)):
            stats.number(p, q, tol, "accessor %d value %d" % (i, k))
    images = 0
    for i, (p, q) in enumerate(zip(a.get("images", []), b.get("images", []))):
        p, q = data_uri(p["uri"]), data_uri(q["uri"])
        if i in painted:
            if p != q:
                raise Mismatch("image %d: painted PNG bytes differ" % i)
        elif png_pixels(p) != png_pixels(q):
            raise Mismatch("image %d: pixels differ" % i)
        images += 1
    stats.summary = "%d nodes, %d accessors, %d images, %d animations" % (
        len(a["nodes"]), len(a["accessors"]), images, len(a.get("animations", [])))
    return stats


def run(cmd):
    return subprocess.run([str(x) for x in cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def paint(folder):
    """Replace one exported texture with a plain RGBA picture of the same size; return its index."""
    target = sorted(folder.glob("t*_*x*.png"))[1]
    width, height = map(int, target.stem.split("_")[1].split("x"))
    rows = b"".join(b"\0" + bytes([255, 0, 255, 255]) * width for _ in range(height))
    chunk = lambda name, data: (struct.pack(">I", len(data)) + name + data +
                                struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF))
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                       + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return int(target.stem[1:3])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--disc", required=True, help="extracted game folder or ISO")
    parser.add_argument("--chars", default=",".join(map(str, DEFAULT_CHARS)), help="charIDs, or 'all'")
    parser.add_argument("--tol", type=float, default=1e-5, help="largest allowed float difference")
    parser.add_argument("--no-build", action="store_true", help="use the existing release build")
    args = parser.parse_args()
    chars = range(54) if args.chars == "all" else [int(c) for c in args.chars.split(",")]
    if not args.no_build:
        subprocess.run(["cargo", "build", "--release", "--quiet"], cwd=CORE, check=True)
    cli = CORE / "target/release" / ("mssb-clone.exe" if sys.platform == "win32" else "mssb-clone")
    scratch = ROOT / ".scratch"
    scratch.mkdir(exist_ok=True)
    failures = 0
    both_failed = 0
    worst = 0.0
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        tmp = Path(tmp)
        cases = [(c, opts, ()) for c in chars for opts in ([], ["--all-animations"])]
        # painted textures: Clone Maker's own export, one picture replaced
        run([cli, "--disc", args.disc, "--source", chars[0], "--export-textures", tmp / "painted"])
        painted_index = paint(tmp / "painted")
        cases.append((chars[0], ["--textures", tmp / "painted"], (painted_index,)))
        print("%-4s %-16s %-6s %-11s %s" % ("id", "option", "result", "max |diff|", "contents"))
        for c, opts, painted in cases:
            label = "all animations" if opts == ["--all-animations"] else "painted" if painted else "swing"
            ref, port = tmp / "ref.gltf", tmp / "port.gltf"
            for f in (ref, port):
                f.unlink(missing_ok=True)
            a = run([sys.executable, REFERENCE, "--disc", args.disc, "--character", c, *opts, "--out", ref])
            b = run([cli, "export-gltf", "--game", args.disc, "--character", c, *opts, "--out", port])
            if a.returncode or b.returncode:
                both = bool(a.returncode) and bool(b.returncode)
                failures += not both
                both_failed += both
                print("%-4d %-16s %-6s %-11s reference: %s / port: %s" % (
                    c, label, "same" if both else "FAIL", "-",
                    (a.stderr.strip().splitlines() or ["ok"])[-1], (b.stderr.strip().splitlines() or ["ok"])[-1]))
                continue
            try:
                stats = compare(ref, port, args.tol, painted)
            except Mismatch as e:
                failures += 1
                print("%-4d %-16s %-6s %-11s %s" % (c, label, "FAIL", "-", e))
                continue
            worst = max(worst, stats.max_diff)
            print("%-4d %-16s %-6s %-11.3g %s%s" % (c, label, "ok", stats.max_diff, stats.summary,
                                                    ", buffer byte-identical" if stats.bytes_equal else ""))
        shutil.rmtree(tmp / "painted", ignore_errors=True)
    print("%d cases, %d failed, %d refused by both, largest float difference %g" % (
        len(cases), failures, both_failed, worst))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
