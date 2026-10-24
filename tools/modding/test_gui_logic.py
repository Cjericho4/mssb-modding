#!/usr/bin/env python3
"""Checks for the non-window part of clone_gui.py (stdlib only; no tkinter or display needed).

    python3 test_gui_logic.py --disc <extracted retail disc> --iso <retail .iso> [--work <scratch>] [--quick]

Covers opening/checking a game (ISO read in place and extracted folder), the slot list, colour ->
recolour spec, the preview images (decoded with the real recolour code), job validation and the
argparse namespace, and runs the same run_job() the "Create patched game" button runs, end to end,
then verifies the kept working folder with test_clone.py (must print ALL CHECKS PASSED) and the
written .iso against it.  --quick skips the second end-to-end run (folder input, Luigi, cleanup).
"""
import argparse
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import clone_gui as g  # noqa: E402
import clone_character as cc  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def png_info(png):
    """(w, h, decompressed pixel rows) of our minimal PNG."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack_from(">II", png, 16)
    pos, idat = 8, b""
    while pos < len(png):
        n = struct.unpack_from(">I", png, pos)[0]
        tag = png[pos + 4:pos + 8]
        if tag == b"IDAT":
            idat += png[pos + 8:pos + 8 + n]
        pos += 12 + n
    return w, h, zlib.decompress(idat)


def same_file_region(f, off, path):
    with open(path, "rb") as h:
        f.seek(off)
        while True:
            b = h.read(1 << 22)
            if not b:
                return True
            if f.read(len(b)) != b:
                return False


def verify_iso(iso, folder):
    """The .iso holds exactly the patched folder's DOL, FST and files (what write_iso promises)."""
    folder = Path(folder)
    with open(iso, "rb") as f:
        boot = f.read(0x440)
        check(boot[:6] == b"GYQE01" and struct.unpack_from(">I", boot, 0x1C)[0] == 0xC2339F3D, "iso: GYQE01 boot block with disc magic")
        dol_off, fst_off, fst_size = struct.unpack_from(">III", boot, 0x420)
        fst = (folder / "sys" / "fst.bin").read_bytes()
        f.seek(fst_off)
        check(f.read(fst_size) == fst, "iso: fst.bin matches the patched folder")
        check(same_file_region(f, dol_off, folder / "sys" / "main.dol"), "iso: main.dol matches the patched folder")
        ok = all(same_file_region(f, e["offset"], folder / "files" / p) for e, p in cc.fst_paths(fst))
        check(ok, "iso: every file matches the patched folder (incl. the grown ZZZZ.dat)")


def run_test_clone(disc, patched, source, slots, ui):
    cmd = [sys.executable, str(HERE / "test_clone.py"), "--disc", str(disc), "--patched", str(patched),
           "--source", str(source), "--ui", ui]
    for s, spec in slots:
        cmd += ["--slot", "%d:%s" % (s, spec)]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    fails = [l for l in r.stdout.splitlines() if "FAIL" in l]
    check(r.returncode == 0 and "ALL CHECKS PASSED" in r.stdout,
          "test_clone.py --patched %s: %s" % (patched.name, "ALL CHECKS PASSED" if not fails else "; ".join(fails[:5])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--disc", default=str(HERE.parents[1] / "orig" / "GYQE01"))
    ap.add_argument("--iso", default=str(HERE.parents[1] / "orig" / "Mario Superstar Baseball (US).iso"))
    ap.add_argument("--work", default=str(HERE.parents[1] / ".scratch" / "gui_test"))
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    work = Path(args.work)
    if work.exists():
        shutil.rmtree(str(work))
    work.mkdir(parents=True)

    print("0. module")
    check("tkinter" not in sys.modules, "clone_gui imports without tkinter")
    check(g.pretty_name(19) == "Bowser Jr." and g.pretty_name(29) == "Blue Toad" and g.pretty_name(47) == "Black Shy Guy", "pretty names")

    print("1. opening and checking the game")
    game, ok, msg = g.load_game(args.iso)
    check(ok and msg == g.GAME_OK and isinstance(game.disc, g.IsoDisc), "ISO: %s" % msg)
    fgame, fok, fmsg = g.load_game(args.disc)
    check(fok and fmsg == g.GAME_OK and isinstance(fgame.disc, cc.Disc), "folder: %s" % fmsg)
    check(bytes(game.dol.data) == bytes(fgame.dol.data), "ISO main.dol == folder main.dol")
    check(game.disc.zzzz_size() == fgame.disc.zzzz_size() == cc.RETAIL_ZZZZ_SIZE, "ISO ZZZZ.dat size from the FST")
    for off, n in ((0, 64), (cc.ZZZZ_STATS_OFFSET, 0xA0 * 54), (0x1A15E800, 4096)):
        check(game.disc.read_zzzz(off, n) == fgame.disc.read_zzzz(off, n), "ISO read_zzzz(0x%X, %d) == folder" % (off, n))
    check(game.disc.read_aaaa(0x100, 256) == fgame.disc.read_aaaa(0x100, 256), "ISO read_aaaa == folder")
    junk = work / "junk.iso"
    junk.write_bytes(b"\0" * 0x1000)
    _, ok, msg = g.load_game(junk)
    check(not ok and "not a GameCube disc" in msg, "junk file: %s" % msg)
    rvz = work / "game.rvz"
    rvz.write_bytes(b"RVZ\x01")
    _, ok, msg = g.load_game(rvz)
    check(not ok and "Convert File" in msg, "rvz: %s" % msg)
    _, ok, msg = g.load_game(work)
    check(not ok and "not an extracted" in msg, "random folder: %s" % msg)
    _, ok, msg = g.load_game(work / "missing.iso")
    check(not ok and "not found" in msg, "missing file: %s" % msg)
    check(g.friendly_preflight(["preflight: files/ZZZZ.dat is 1 bytes, retail is 2 (already patched: point --disc at the clean extraction)"]).startswith("This copy has already been patched"),
          "preflight 'already patched' -> plain English")
    check("USA version" in g.friendly_preflight(["preflight: sys/boot.bin game id is b'GYQP01', expected GYQE01 (USA)"]), "preflight wrong region -> plain English")
    check("incomplete" in g.friendly_preflight(["preflight: files/ZZZZ.dat is 1 bytes, retail is 2 (truncated extraction)"]), "preflight truncated -> plain English")

    print("2. slot list")
    dol = game.dol
    ch = g.donor_choices(dol, 0)
    ids = [c.id for c in ch]
    check(ids[:4] == [29, 30, 31, 32] and all(c.recommended for c in ch[:4]), "Mario: the four Toad colours first, recommended")
    check(0 not in ids and not ({12, 13, 16, 20, 21, 24, 33, 48, 52} & set(ids)), "source and shared-body family bases are not offered")
    check(all(cc.private_entry(dol, s) is not None for s in ids), "every offered slot has a private ARAM entry")
    rec = [c for c in ch if c.recommended]
    check(all(not c.recommended for c in ch[len(rec):]), "recommended slots come before main characters")
    luigi = [c for c in ch if c.id == 1][0]
    check(not luigi.recommended and "replaces Luigi" in luigi.label, "Luigi: %s" % luigi.label)
    check(g.default_rows(ch) == [(29, "blue"), (30, "green"), (31, "purple"), (32, "yellow")], "default rows")
    ch29 = g.donor_choices(dol, 29)
    check(29 not in [c.id for c in ch29] and [s for s, _ in g.default_rows(ch29)][:3] == [30, 31, 32] and len(g.default_rows(ch29)) == 4,
          "Blue Toad source: its own slot excluded, still four rows %r" % g.default_rows(ch29))
    dk = g.donor_choices(dol, 2)
    check(all(c.id < cc.JUMPTABLE_CASES for c in dk), "DK (own setup case): no slot >= 48 offered")
    check(g.next_free_slot(ch, {29, 30}) == 31, "next free slot")
    hint = g.pick_hint(dol, 0, [29, 30, 31, 32, 1])
    check("several Toads" in hint and "every extra one" in hint and "pick Luigi" in hint, "pick hint: %s" % hint)

    print("3. colours")
    check(g.colour_spec("blue") == "hue=blue,keeplum=0.5", "preset spec %s" % g.colour_spec("blue"))
    check(g.colour_spec("green", 1.0, g.BAND_ALL) == "hue=green,keeplum=1,all=1", "band/keeplum spec %s" % g.colour_spec("green", 1.0, g.BAND_ALL))
    check(g.colour_spec("green", 0.0) == "hue=green,keeplum=0", "keeplum 0 spec %s" % g.colour_spec("green", 0.0))
    spec = g.colour_spec("#00ff00", 0.25)
    rc = cc.Recolour(spec)
    check(spec == "hue=0.333,keeplum=0.25" and abs(rc.hue - 1 / 3) < 0.001 and rc.keeplum == 0.25, "custom spec %s" % spec)
    check(abs(g.colour_hue("#0000ff") - 2 / 3) < 1e-6 and g.colour_hue("purple") == cc.COLOUR_PRESETS["purple"], "colour hue")
    check(g.colour_swatch("#ABCDEF") == "#abcdef" and len(g.colour_swatch("red")) == 7, "swatches")
    check(g.colour_label("#a0b0c0") == "Custom #A0B0C0" and g.colour_label("pink") == "Pink", "labels")

    print("4. preview")
    blk = struct.pack(">HHI", 0x001F, 0xF800, 0xFFFFFFFF)          # c0 < c1: 3-colour mode, all index 3
    rgb = g.decode_cmpr(blk * 4, 8, 8)
    check(rgb[:3] in (b"\x99\x99\x99", b"\xcc\xcc\xcc"), "CMPR 3-colour mode: index 3 is transparent (checker)")
    blk = struct.pack(">HHI", 0xF800, 0x001F, 0x00000000)          # c0 > c1, all index 0
    check(g.decode_cmpr(blk * 4, 8, 8)[:3] == b"\xff\x00\x00", "CMPR index 0 = c0")
    blk = struct.pack(">HHI", 0xF800, 0x001F, 0x40000000)          # first pixel index 1
    out = g.decode_cmpr(blk * 4, 8, 8)
    check(out[:3] == b"\x00\x00\xff" and out[3:6] == b"\xff\x00\x00", "CMPR first pixel in the top bits")
    pv = game.preview(0)
    check(game.preview(0) is pv, "preview cached per source")
    check((pv.w, pv.h) == (256, 256), "Mario's main texture is 256x256")
    w, h, raw = png_info(pv.before_png)
    check((w, h, len(raw)) == (256, 256, 256 * (1 + 256 * 3)), "before PNG 256x256 RGB")
    spec = g.colour_spec("blue")
    after = pv.after_png(spec)
    check(png_info(after)[:2] == (256, 256) and after != pv.before_png, "after PNG differs from before")
    check(pv.after_png(spec) is after, "after PNG cached")
    body = cc.Gpc(cc.effective_body(game.disc, game.dol, 0))
    cc.recolour_gpc(body, cc.Recolour(spec))
    t = body.textures[pv.tex_index]
    check(bytes(body.data[t["data"]:t["data"] + t["size"]]) == pv.recoloured_blocks(spec), "preview recolour == recolour_gpc on the real body")
    check(pv.recoloured_blocks("hue=blue,tex=0") == pv.blocks, "tex= filter that skips the atlas leaves it unchanged")
    (work / "preview_before.png").write_bytes(pv.before_png)
    (work / "preview_after.png").write_bytes(after)
    pl = game.preview(1)
    check(pl.w * pl.h == max(t["w"] * t["h"] for t in cc.Gpc(cc.effective_body(game.disc, dol, 1)).textures), "Luigi preview uses his largest texture")

    print("5. jobs")
    out_iso = work / "Mario Superstar Baseball (US) - Clones.iso"
    check(g.default_output(args.iso).name == "Mario Superstar Baseball (US) - Clones.iso", "default output next to the ISO")
    check(g.default_output(args.disc) == Path(args.disc).parent / "GYQE01 - Clones.iso", "default output for a folder")
    slots = [(s, g.colour_spec(c)) for s, c in g.default_rows(ch)]
    job = g.Job(args.iso, out_iso, 0, slots, keep_work=True)
    ns = g.job_args(job, work / "w" / "disc", work / "x.iso")
    need = ("dry_run", "out", "disc", "force", "source", "slot", "no_aram_overlay", "ui", "no_gameplay", "no_stats", "iso")
    check(all(hasattr(ns, k) for k in need), "namespace has every attribute clone() reads")
    check(ns.slot == ["29:hue=blue,keeplum=0.5", "30:hue=green,keeplum=0.5", "31:hue=purple,keeplum=0.5", "32:hue=yellow,keeplum=0.5"]
          and ns.ui == "source" and not ns.no_stats and ns.force and not ns.dry_run, "namespace values %r" % ns.slot)
    check(g.job_args(g.Job(args.iso, out_iso, 0, slots, ui_source=False, copy_stats=False), "o", "i").ui == "donor", "ui donor / no stats")

    def refused(j, needle, free=10 ** 12):
        try:
            g.validate_job(j, free_bytes=free)
        except SystemExit as e:
            return needle in str(e.code)
        return False
    check(refused(g.Job(args.iso, out_iso, 0, []), "at least one"), "no clones refused")
    check(refused(g.Job(args.iso, out_iso, 0, slots + [(29, "hue=red")]), "two clones"), "duplicate slot refused")
    check(refused(g.Job(args.iso, out_iso, 29, slots), "itself"), "source as donor refused")
    check(refused(g.Job(args.iso, args.iso, 0, slots), "must not replace your original"), "overwriting the input ISO refused")
    check(refused(g.Job(args.disc, Path(args.disc) / "x.iso", 0, slots), "outside the game's folder"), "output inside the input folder refused")
    check(refused(g.Job(args.iso, work / "x.gcm", 0, slots), ".iso"), "non-.iso output refused")
    check(refused(g.Job(args.iso, work / "nope" / "x.iso", 0, slots), "does not exist"), "missing output folder refused")
    check(refused(g.Job(args.iso, out_iso, 0, slots), "free disk space", free=10 ** 9), "low disk space refused")
    try:
        g.validate_job(job, free_bytes=10 ** 12)
        check(True, "valid job accepted")
    except SystemExit as e:
        check(False, "valid job refused: %s" % e)
    m, d = g.friendly_error(SystemExit("x; rerun with --ui donor"))
    check("Advanced" in m and d is None, "SystemExit -> plain message")
    m, d = g.friendly_error(OSError(28, "No space left on device"))
    check("drive is full" in m and d is None, "ENOSPC -> plain message")
    try:
        raise KeyError("boom")
    except KeyError as e:
        m, d = g.friendly_error(e)
    check("log" in m and "KeyError" in d and "Traceback" in d, "unexpected error -> message + traceback details")

    print("6. end to end: ISO in, Toads, kept working folder (the button's code path)")
    lines = []
    res = g.run_job(job, lines.append)
    check(out_iso.exists() and not any(work.glob("*.partial.iso")), "wrote %s, no .partial.iso left" % out_iso.name)
    check(res["work"] is not None and (res["work"] / "sys" / "main.dol").exists(), "working folder kept: %s" % res["work"])
    check(any(l.startswith("Done.") for l in lines) and any("preflight: clean" in l for l in lines), "log lines reached the callback (%d lines)" % len(lines))
    check(res["slots"] == [(s, spec) for s, spec in slots] and res["source"] == 0, "result lists the clones")
    run_test_clone(args.disc, res["work"], 0, slots, "source")
    verify_iso(out_iso, res["work"])
    _, ok, msg = g.load_game(out_iso)
    check(not ok and "already been patched" in msg, "re-opening the output: %s" % msg)

    if not args.quick:
        print("7. end to end: folder in, Luigi custom colour, --ui donor, working folder removed")
        out2 = work / "luigi.iso"
        slots2 = [(1, g.colour_spec("#20c040", 0.7))]
        job2 = g.Job(args.disc, out2, 0, slots2, ui_source=False)
        res2 = g.run_job(job2, lambda l: None)
        check(out2.exists() and res2["work"] is None, "wrote %s, working folder not kept" % out2.name)
        left = [p for p in work.glob("MSSB-Clone-Maker-work-*") if p.parent == work]
        check(len(left) == 1, "only the kept working folder from step 6 remains (%d)" % len(left))
        # verify the ISO by extracting it the way the patcher would read it
        ext = work / "luigi_extract"
        cc.extract_iso(out2, ext, lambda l: None)
        run_test_clone(args.disc, ext, 0, slots2, "donor")
        shutil.rmtree(str(ext))

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
