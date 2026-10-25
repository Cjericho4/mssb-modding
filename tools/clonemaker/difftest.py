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
                compare_files(py_out,rs_out)
                done=events[-1]["payload"]
                assert done["hint"] == g.pick_hint(game.dol,source,[s for s,_ in specs],ui)
                if keep:
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
        assert normalize(py.stdout,a,ai)==normalize(rs.stdout,b,bi),(label,"stdout",py.stdout,rs.stdout)
        assert normalize(py.stderr,a,ai)==normalize(rs.stderr,b,bi),(label,"stderr",py.stderr,rs.stderr)
        compare_trees(a, b)
        if py.returncode:
            print("PASS clone %s matching reference rejection: %s" % (label,py.stderr.strip()),flush=True)
            return
        compare_files(ai, bi)
        source = opts[opts.index("--source")+1] if "--source" in opts else "mario"
        ui = opts[opts.index("--ui")+1] if "--ui" in opts else "source"
        slot_args = sum((["--slot", opts[i+1]] for i, x in enumerate(opts) if x == "--slot"), [])
        validator = [sys.executable, MOD / "test_clone.py", "--disc", disc, "--patched", b,
                     "--source", source, "--ui", ui, *slot_args]
        # The validator assumes the default gameplay/stats/overlay settings.
        if (not label[0].isdigit()) and not any(x in opts for x in ("--no-stats", "--no-gameplay", "--no-aram-overlay", "--colour-word", "--relocate-menu-art")):
            result = run(validator)
            assert "ALL CHECKS PASSED" in result, label + " test_clone"
        print("PASS clone %s Python %.3fs Rust %.3fs" % (label, py_time, rs_time), flush=True)
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
        futures = [pool.submit(clone_case, cli, work, disc, case) for case in grid_cases(full) if case[0] not in SKIP_PASSED]
        for future in futures:
            future.result()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--disc", type=Path, default=ROOT / "orig/GYQE01")
    for flag in ("synthetic", "codecs", "helpers", "iso", "grid", "full-grid", "textures", "api", "gui", "cli"):
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
    if not any((args.synthetic, args.codecs, args.helpers, args.iso, args.grid, args.full_grid, args.textures, args.api, args.gui, args.cli)):
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
        if args.grid or args.full_grid:
            clone_grid(CORE / "target/release/mssb-clone", work, args.disc, args.full_grid, args.jobs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
