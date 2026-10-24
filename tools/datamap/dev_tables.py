#!/usr/bin/env python3
"""Developer helper (needs the mssb-dtk checkout: config/ symbols and build/ asm).

Scans main.dol and the three RELs for disk-file descriptors, groups them into runs, names each run
by the symbol that contains it and lists the functions that reference that symbol.  The output was
used to write the TABLES registry in catalog.py; rerun it after symbol renames.

    python tools/datamap/dev_tables.py orig/GYQE01
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disc as dm  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SYMFILES = {"dol": ROOT / "config/GYQE01/symbols.txt", "menus": ROOT / "config/GYQE01/menus/symbols.txt",
            "game": ROOT / "config/GYQE01/game/symbols.txt", "challenge": ROOT / "config/GYQE01/challenge/symbols.txt"}
ASMDIRS = {"dol": ROOT / "build/GYQE01/asm", "menus": ROOT / "build/GYQE01/menus/asm",
           "game": ROOT / "build/GYQE01/game/asm", "challenge": ROOT / "build/GYQE01/challenge/asm"}
SYM_RE = re.compile(r"^(\S+) = (\.\w+):0x([0-9A-Fa-f]+); // type:(\w+)(?: size:0x([0-9A-Fa-f]+))?")


def load_symbols(mod):
    out = []
    for line in SYMFILES[mod].read_text(encoding="utf-8").splitlines():
        m = SYM_RE.match(line)
        if m and m.group(4) == "object":
            out.append((m.group(2), int(m.group(3), 16), int(m.group(5) or "0", 16), m.group(1)))
    return out


def symbol_at(syms, sect, addr):
    best = None
    for s, a, size, name in syms:
        if s == sect and a <= addr < a + max(size, 1):
            best = (name, a)
    return best


def refs(mod, name):
    """Functions whose asm mentions `name`."""
    found = set()
    pat = re.compile(r"\b%s\b" % re.escape(name))
    for p in ASMDIRS[mod].rglob("*.s"):
        fn = None
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(".fn "):
                fn = line.split()[1].rstrip(",")
            elif line.startswith(".obj "):
                fn = "data:" + line.split()[1].rstrip(",")
            elif pat.search(line) and not line.startswith((".obj", ".endobj")):
                found.add(fn)
    return sorted(f for f in found if f)


def main():
    d = dm.Disc(sys.argv[1])
    sects = {"dol": None}
    for img in d.images():
        syms = load_symbols(img.name)
        hits = list(dm.scan_image(img, d.zzzz_size))
        runs = []
        for p in hits:
            if runs and runs[-1][1] == p:
                runs[-1][1] = p + 16
                runs[-1][2] += 1
            else:
                runs.append([p, p + 16, 1])
        bysym = defaultdict(list)
        for start, end, n in runs:
            sec, so, disp = img.loc(start)
            sname = ".data" if img.name == "dol" else ".data"
            sym = symbol_at(syms, sname if img.name != "dol" else ".data", disp if img.name == "dol" else so)
            bysym[sym].append((start, n))
        for sym, rs in bysym.items():
            name = sym[0] if sym else "?"
            total = sum(n for _, n in rs)
            where = ", ".join("%s x%d" % (img.where(s), n) for s, n in rs)
            print("%-10s %-24s %4d  %s" % (img.name, name, total, where))
            if sym:
                print("           refs: %s" % ", ".join(refs(img.name, name)[:12]))


if __name__ == "__main__":
    main()
