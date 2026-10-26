#!/usr/bin/env python3
"""clone_gui.py -- the logic layer of the MSSB Clone Maker (Mario Superstar Baseball, USA / GYQE01).

The window that used to live here (Tk) was replaced by the Rust/Tauri app in tools/clonemaker/app.
What remains is the plain-Python logic that window drove, kept as the reference for the Rust core
(tools/clonemaker/core): game loading and preflight, donor slots and defaults, colour specs, the
texture-preview atlas, Job / validate_job / run_job (progress, cancel, log file) and friendly_error.
tools/clonemaker/difftest.py --gui compares the Rust core against it; recolour_audit.py and
test_matrix.py import it.  No tkinter; Python 3.8+, standard library only.  The module name is kept
so that imports on other branches keep working.  Tested by test_gui_logic.py.
"""
import base64
import colorsys
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import traceback
import zlib
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_character as cc  # noqa: E402

APP_TITLE = "MSSB Clone Maker"
GAME_OK = "Mario Superstar Baseball (USA) — OK"
ADDR_MAIN_CHARS = 0x80108DB8          # mainCharArray: select-grid cell -> charID (32 cells)
FAMILY_TOAD = 2
DEFAULT_ROWS = [(29, "blue"), (30, "green"), (31, "purple"), (32, "yellow")]
PRESETS = list(cc.COLOUR_PRESETS)     # red, orange, yellow, green, cyan, blue, purple, pink
NEEDED_FREE_BYTES = 3_300_000_000     # working copy (~1.5 GB) + the new .iso (~1.5 GB)
DISC_IMAGE_OTHER = (".rvz", ".nkit", ".gcz", ".wbfs", ".ciso", ".wia", ".tgc")
# Sources clone_character refuses (none any more: Petey's CI4 textures are recoloured by palette);
# test_gui_logic.py checks this list against the disc.
UNSUPPORTED_SOURCES = set(cc.UNSUPPORTED_SOURCES)
SOURCES = [c for c in range(cc.NUM_CHARS) if c not in UNSUPPORTED_SOURCES]
BAND_REDS = "reds"
BAND_ALL = "all"

PRETTY = {
    "DK": "Donkey Kong", "Diddy": "Diddy Kong", "BowserJr": "Bowser Jr.", "KingBoo": "King Boo",
    "BabyMario": "Baby Mario", "BabyLuigi": "Baby Luigi", "HammerBro": "Hammer Bro",
    "FireBro": "Fire Bro", "BoomerangBro": "Boomerang Bro",
}


FAMILY_PRETTY = {"ShyGuy": ("Shy Guy", "Shy Guys"), "DryBones": ("Dry Bones", "Dry Bones"),
                 "Bros": ("Hammer Bro", "Hammer Bros")}


def family_name(fam, plural=False):
    n = cc.FAMILY_NAMES[fam]
    one, many = FAMILY_PRETTY.get(n, (n, n + "s"))
    return many if plural else one


def pretty_name(c):
    n = cc.CHAR_NAMES[c]
    return PRETTY.get(n) or re.sub(r"(?<=[a-z])(?=[A-Z])", " ", n)


# ----------------------------------------------------------------------------------------------
# Opening the game: an .iso is read in place (no 1.4 GB extraction just to check it)
# ----------------------------------------------------------------------------------------------
class IsoDisc:
    """Enough of clone_character.Disc for preflight() and the preview, read straight from an ISO.
    Only boot.bin / main.dol / fst.bin (~1 MB) are written, to a small temp folder."""

    def __init__(self, iso_path):
        self.iso = Path(iso_path)
        with open(self.iso, "rb") as f:
            boot = f.read(0x440)
            if len(boot) < 0x440 or struct.unpack_from(">I", boot, 0x1C)[0] != 0xC2339F3D:
                raise SystemExit("This file is not a GameCube disc image (.iso).")
            dol_off, fst_off, fst_size = struct.unpack_from(">III", boot, 0x420)
            f.seek(dol_off)
            dol_hdr = f.read(0x100)
            f.seek(dol_off)
            dol = f.read(cc.dol_size(dol_hdr))
            f.seek(fst_off)
            fst = f.read(fst_size)
        if len(fst) != fst_size:
            raise SystemExit("This .iso is incomplete (it ends early). Make a fresh copy of the game.")
        self._files = {path: (e["offset"], e["size"]) for e, path in cc.fst_paths(fst)}
        if "ZZZZ.dat" not in self._files or "aaaa.dat" not in self._files:
            raise SystemExit("This is a GameCube game, but not Mario Superstar Baseball.")
        self._tmp = tempfile.TemporaryDirectory(prefix="mssb-clone-check-")
        self.root = Path(self._tmp.name)
        self.sys = self.root / "sys"
        self.files = None
        self.sys.mkdir()
        (self.sys / "boot.bin").write_bytes(boot)
        (self.sys / "main.dol").write_bytes(dol)
        (self.sys / "fst.bin").write_bytes(fst)

    def _read(self, name, off, size):
        base, total = self._files[name]
        with open(self.iso, "rb") as f:
            f.seek(base + off)
            return f.read(size)

    def read_zzzz(self, off, size):
        return self._read("ZZZZ.dat", off, size)

    def read_aaaa(self, off, size):
        return self._read("aaaa.dat", off, size)

    def zzzz_size(self):
        return self._files["ZZZZ.dat"][1]

    def close(self):
        self._tmp.cleanup()


def open_disc(path):
    """A .iso (IsoDisc) or an extracted folder (clone_character.Disc); SystemExit(plain message)."""
    p = Path(path)
    if p.is_dir():
        if not (p / "sys").is_dir() and (p / "DATA" / "sys").is_dir():
            p = p / "DATA"
        if not (p / "sys" / "main.dol").exists() or not (p / "files" / "ZZZZ.dat").exists():
            raise SystemExit("This folder is not an extracted Mario Superstar Baseball disc "
                             "(it needs the sys and files folders). Choose the .iso instead.")
        return cc.Disc(p)
    if not p.is_file():
        raise SystemExit("File not found: %s" % p)
    if p.suffix.lower() in DISC_IMAGE_OTHER:
        raise SystemExit("This is a compressed %s file. Convert it to .iso first: in Dolphin or Rio, "
                         "right-click the game → Convert File… → Format: ISO." % p.suffix.lower())
    return IsoDisc(p)


def friendly_preflight(lines):
    """Turn clone_character.preflight's log lines into one plain-English sentence."""
    text = " ".join(lines)
    if "game id" in text:
        return "This is not the USA version of Mario Superstar Baseball (game ID GYQE01). Only the USA disc is supported."
    if "already patched" in text or "not retail" in text:
        return "This copy has already been patched. Choose your original, unmodified game."
    if "truncated" in text:
        return "This copy of the game is incomplete. Make a fresh copy of the disc."
    if "main.dol is" in text or "do not look like retail" in text:
        return "This doesn't look like an unmodified Mario Superstar Baseball (USA) disc."
    return lines[0].replace("preflight: ", "") if lines else "This game could not be checked."


class Game:
    """The chosen game: its disc, DOL and a per-source preview cache."""

    def __init__(self, path):
        self.path = Path(path)
        self.disc = open_disc(path)
        self.dol = cc.Dol((self.disc.sys / "main.dol").read_bytes())
        self._previews = {}
        self._lock = threading.Lock()

    def check(self):
        """(ok, message) from clone_character.preflight."""
        lines = []
        try:
            cc.preflight(self.disc, self.dol, lines.append)
        except SystemExit:
            return False, friendly_preflight([l for l in lines if l.startswith("preflight:")])
        return True, GAME_OK

    def preview(self, source):
        """SourcePreview for `source` (slow the first time: call it off the UI thread)."""
        with self._lock:
            if source not in self._previews:
                self._previews[source] = SourcePreview(self, source)
            return self._previews[source]

    def cached_preview(self, source):
        return self._previews.get(source)

    def close(self):
        if isinstance(self.disc, IsoDisc):
            self.disc.close()


def load_game(path):
    """(game or None, ok, message) for the status line; never raises for a bad file."""
    try:
        game = Game(path)
    except SystemExit as e:
        return None, False, str(e.code)
    except OSError as e:
        return None, False, "Couldn't read %s: %s" % (path, e.strerror or e)
    except Exception as e:  # a corrupt file can trip any parser
        return None, False, "This doesn't look like a Mario Superstar Baseball disc (%s)." % e
    ok, msg = game.check()
    return game, ok, msg


# ----------------------------------------------------------------------------------------------
# Donor slots
# ----------------------------------------------------------------------------------------------
class SlotChoice:
    __slots__ = ("id", "name", "note", "recommended")

    def __init__(self, id, name, note, recommended):
        self.id, self.name, self.note, self.recommended = id, name, note, recommended

    @property
    def label(self):
        return "%s — %s" % (self.name, self.note)

    def __repr__(self):
        return "SlotChoice(%d, %r)" % (self.id, self.label)


def donor_choices(dol, source):
    """Slots that can hold a clone of `source`, colour variants (recommended) first, Toads first."""
    main = set(dol.read(ADDR_MAIN_CHARS, 32))
    default_case = dol.u32(cc.ADDR_JUMPTABLE)
    src_case = dol.u32(cc.ADDR_JUMPTABLE + source * 4) if source < cc.JUMPTABLE_CASES else default_case
    out = []
    for s in range(cc.NUM_CHARS):
        if s == source or cc.private_entry(dol, s) is None:
            continue
        if s >= cc.JUMPTABLE_CASES and src_case != default_case:
            continue
        fam = dol.family(s)
        if s not in main and fam[0] and not fam[3]:
            out.append(SlotChoice(s, pretty_name(s), "recommended (an extra %s colour)" % family_name(fam[0]), True))
        else:
            out.append(SlotChoice(s, pretty_name(s), "replaces %s on the select screen" % pretty_name(s), False))
    out.sort(key=lambda c: (not c.recommended, dol.family(c.id)[0] != FAMILY_TOAD, c.id))
    return out


def default_rows(choices):
    """[(slot, colour)]: the four Toad colours when they are allowed (topped up with other
    recommended slots), coloured blue, green, purple, yellow."""
    ids = [c.id for c in choices]
    slots = [s for s, _ in DEFAULT_ROWS if s in ids]
    slots += [c.id for c in choices if c.recommended and c.id not in slots][:len(DEFAULT_ROWS) - len(slots)]
    return [(s, col) for s, (_, col) in zip(slots, DEFAULT_ROWS)]


def next_free_slot(choices, used):
    for c in choices:
        if c.id not in used:
            return c.id
    return None


def pick_hint(dol, source, slots, ui_source=True):
    """What to do in the game, in one or two sentences."""
    variants = [s for s in slots if dol.family(s)[0] and not dol.family(s)[3]]
    mains = [s for s in slots if s not in variants]
    src = pretty_name(source)
    parts = []
    fams = {}
    for s in variants:
        fams.setdefault(dol.family(s)[0], []).append(s)
    for fam, members in fams.items():
        everyone = [c for c in range(cc.NUM_CHARS) if dol.family(c)[0] == fam and not dol.family(c)[3]]
        names = ", ".join(pretty_name(s) for s in members)
        if set(everyone) <= set(members):
            parts.append("put several %s on one team: every extra one after the first is a %s clone (%s)"
                         % (family_name(fam, True), src, names))
        else:
            parts.append("put several %s on one team: the game gives the extra ones colours by itself, and the "
                         "%s ones are %s clones" % (family_name(fam, True), names, src))
    for s in mains:
        parts.append("pick %s on the select screen%s" % (pretty_name(s), " (it now shows as %s)" % src if ui_source else ""))
    return "In the game, " + "; ".join(parts) + "." if parts else ""


# ----------------------------------------------------------------------------------------------
# Colours
# ----------------------------------------------------------------------------------------------
def is_custom(colour):
    return colour.startswith("#")


def colour_hue(colour):
    if is_custom(colour):
        r, g, b = (int(colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        return colorsys.rgb_to_hsv(r, g, b)[0]
    return cc.COLOUR_PRESETS[colour]


def colour_swatch(colour):
    """#rrggbb to paint next to a row."""
    if is_custom(colour):
        return colour.lower()
    r, g, b = colorsys.hsv_to_rgb(colour_hue(colour), 0.85, 0.9)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def colour_label(colour):
    return "Custom %s" % colour.upper() if is_custom(colour) else colour.capitalize()


def colour_spec(colour, keeplum=0.5, band=BAND_REDS, regions=None):
    """Recolour spec string for clone_character (the same text the --slot option takes).
    regions: indices of the source's colour regions to change, or None for its default ones (the
    spec then names no band and clone_character picks the defaults: Mario's reds, Luigi's green...)."""
    hue = colour if not is_custom(colour) else "%.3f" % colour_hue(colour)
    spec = "hue=%s,keeplum=%s" % (hue, ("%.2f" % keeplum).rstrip("0").rstrip(".") or "0")
    if band == BAND_ALL:
        spec += ",all=1"
    elif regions is not None:
        spec += ",region=" + "/".join(str(i) for i in sorted(regions))
    cc.Recolour(spec)  # validate
    return spec


def region_selection(regions, ticked):
    """Ticked region indices -> the regions= argument of colour_spec (None when they are exactly the
    defaults, so a default clone's spec stays plain "hue=...,keeplum=...")."""
    ticked = sorted(set(ticked))
    if ticked == [i for i, r in enumerate(regions) if r.default]:
        return None
    return ticked


def region_label(r):
    return r.name[:1].upper() + r.name[1:]


# ----------------------------------------------------------------------------------------------
# Preview: every body texture of the source, before and after the real recolour code
# ----------------------------------------------------------------------------------------------
decode_cmpr = cc.decode_cmpr
PREVIEW_WIDTH = 256          # atlas width in texels (textures packed in shelves, tallest first)
ATLAS_BG = b"\x10\x10\x1a"   # the gaps between textures (the theme's SURFACE)


def largest_texture(gpc):
    return max(gpc.textures, key=lambda t: (t["w"] * t["h"], -t["index"]))


def atlas_layout(textures, width=PREVIEW_WIDTH, gap=2):
    """Shelf-pack textures (tallest first) into `width` texels: ([(texture, x, y)], height)."""
    order = sorted(textures, key=lambda t: (-t["h"], -t["w"], t["index"]))
    x = y = shelf = 0
    out = []
    for t in order:
        if x and x + t["w"] > width:
            x, y, shelf = 0, y + shelf + gap, 0
        out.append((t, x, y))
        x += t["w"] + gap
        shelf = max(shelf, t["h"])
    return out, y + shelf


def png_bytes(w, h, rgb):
    """Minimal RGB PNG encoder (stdlib zlib)."""
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    stride = w * 3
    raw = b"".join(b"\0" + rgb[y * stride:(y + 1) * stride] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class SourcePreview:
    """The source's in-match body (ARAM body + overlay): its colour regions, and every texture packed
    into one atlas image, before and after a recolour spec.  Slow to build (it unpacks the body), so
    the window builds it on its preview thread; Game.preview() caches one per source."""

    def __init__(self, game, source):
        self.source = source
        self.body = cc.Gpc(cc.effective_body(game.disc, game.dol, source))
        self.regions = cc.source_regions(game.disc, game.dol, source, self.body)
        self.layout, self.h = atlas_layout(self.body.textures)
        self.w = PREVIEW_WIDTH
        self.tex_count = len(self.body.textures)
        self._decoded = {}                     # texture bytes -> decoded RGB (unchanged ones are shared)
        self.before_png = png_bytes(self.w, self.h, self._atlas(self.body))
        self._after = {}

    def _decode(self, data, t):
        rgb = self._decoded.get(data)
        if rgb is None:
            if len(self._decoded) > 400:
                self._decoded.clear()
            rgb = self._decoded[data] = cc.decode_texture(data, t)
        return rgb

    def _atlas(self, gpc):
        w = self.w
        out = bytearray(ATLAS_BG * (w * self.h))
        for t, x, y in self.layout:
            rgb = self._decode(cc.texture_bytes(gpc, t), t)
            row = t["w"] * 3
            for yy in range(t["h"]):
                o = ((y + yy) * w + x) * 3
                out[o:o + row] = rgb[yy * row:(yy + 1) * row]
        return bytes(out)

    def recoloured(self, spec):
        """The body recoloured by `spec` exactly as the patch does it (regions bound to this source)."""
        g = cc.Gpc(bytes(self.body.data))
        cc.recolour_gpc(g, cc.Recolour(spec).resolve(self.regions))
        return g

    def after_png(self, spec):
        if spec not in self._after:
            if len(self._after) > 32:
                self._after.clear()
            self._after[spec] = png_bytes(self.w, self.h, self._atlas(self.recoloured(spec)))
        return self._after[spec]


def png_base64(png):
    return base64.b64encode(png).decode("ascii")


class PreviewWorker:
    """One background thread for the slow preview work (unpacking a body, finding its colours,
    recolouring and packing the atlas), so the window never waits for it.  Requests coalesce: only
    the newest waiting one runs.  Each result goes to post(("preview", request, result or error))."""

    def __init__(self, post):
        self.post = post
        self._cv = threading.Condition()
        self._next = None
        threading.Thread(target=self._run, daemon=True).start()

    def request(self, req, fn):
        with self._cv:
            self._next = (req, fn)
            self._cv.notify()

    def _run(self):
        while True:
            with self._cv:
                while self._next is None:
                    self._cv.wait()
                req, fn = self._next
                self._next = None
            try:
                res = fn()
            except BaseException as e:  # noqa: B902 - shown in the preview caption
                res = e
            self.post(("preview", req, res))


# ----------------------------------------------------------------------------------------------
# The patch job (what the big button runs)
# ----------------------------------------------------------------------------------------------
class Job:
    def __init__(self, game_path, output_iso, source, slots, ui_source=True, copy_stats=True, keep_work=False,
                 keep_colour_word=False):
        self.game_path = Path(game_path)
        self.output_iso = Path(output_iso)
        self.source = source
        self.slots = list(slots)          # [(slot id, recolour spec)]
        self.ui_source = ui_source
        self.copy_stats = copy_stats
        self.keep_work = keep_work
        self.keep_colour_word = keep_colour_word   # roster screen: keep the donor's "Purple" etc. before the name


def default_output(game_path):
    p = Path(game_path)
    base = p.stem if p.is_file() else p.name
    return p.parent / (base + " - Clones.iso")


def job_args(job, work_out, iso_path):
    """The argparse namespace clone_character.clone() reads (same meaning as the CLI options)."""
    return Namespace(
        disc=str(job.game_path), out=str(work_out), source=str(job.source),
        slot=["%d:%s" % (s, spec) for s, spec in job.slots], iso=str(iso_path),
        ui="source" if job.ui_source else "donor", no_aram_overlay=False,
        colour_word="donor" if job.keep_colour_word else "none",
        no_stats=not job.copy_stats, no_gameplay=False, dry_run=False, force=True, list_slots=False)


def validate_job(job, free_bytes=None):
    """Raise SystemExit(plain message) for anything the patcher would trip over later."""
    if not job.slots:
        raise SystemExit("Add at least one clone.")
    ids = [s for s, _ in job.slots]
    if len(set(ids)) != len(ids):
        dup = [s for s in ids if ids.count(s) > 1][0]
        raise SystemExit("%s is used by two clones. Pick a different slot for one of them." % pretty_name(dup))
    if job.source in ids:
        raise SystemExit("A character can't be a clone of itself. Pick another slot.")
    if job.output_iso.suffix.lower() != ".iso":
        raise SystemExit("The output file must end in .iso.")
    out = job.output_iso.resolve()
    src = job.game_path.resolve()
    if out == src:
        raise SystemExit("Choose a different name for the new game: it must not replace your original.")
    if src.is_dir() and src in out.parents:
        raise SystemExit("Save the new game outside the game's folder.")
    if not out.parent.is_dir():
        raise SystemExit("The folder %s does not exist." % out.parent)
    # Windows: a protected folder (Program Files, the drive root) would make tempfile.mkdtemp retry
    # "forever" (TMP_MAX is 2**31 there), so try a real write first
    probe = out.parent / (".mssb-clone-write-test-%d" % os.getpid())
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        raise SystemExit("Windows won't let this program save in %s. Choose a folder you own, such as "
                         "Documents or the Desktop." % out.parent)
    if free_bytes is None:
        free_bytes = shutil.disk_usage(str(out.parent)).free
    if free_bytes < NEEDED_FREE_BYTES:
        raise SystemExit("Not enough free disk space on the drive of %s: about %.1f GB is needed while the "
                         "game is being made, %.1f GB is free." % (out.parent, NEEDED_FREE_BYTES / 1e9, free_bytes / 1e9))


class Cancelled(Exception):
    pass


def log_path(job):
    return job.output_iso.with_suffix(".log")


def run_job(job, log, progress=None, cancel=None):
    """Patch into a temporary working folder next to the output, write the .iso, clean up.
    Every log line also goes to <output>.log (with the traceback if something breaks).
    `progress(fraction, label)` follows clone_character.clone(); setting the `cancel` Event stops the
    job at its next progress step with Cancelled.  Returns clone_character.clone()'s result plus "work"
    (the kept folder, or None)."""
    validate_job(job)
    logf = open(log_path(job), "w", encoding="utf-8")

    def out(line):
        logf.write(line + "\n")
        logf.flush()
        log(line)

    def step(fraction, label):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if progress:
            progress(fraction, label)
    work = job.output_iso.parent / ("MSSB-Clone-Maker-work-%s" % os.urandom(4).hex())
    try:
        work.mkdir()
    except OSError:
        logf.close()
        raise
    partial = job.output_iso.with_name(job.output_iso.stem + ".partial.iso")
    try:
        out("MSSB Clone Maker: %s from %s" % (job.output_iso, job.game_path))
        out("working folder: %s" % work)

        def patch_log(line):
            if not line.startswith("Boot: "):     # the CLI's "open the folder" hint; the GUI hands out the .iso
                out(line)
        result = cc.clone(job_args(job, work / "disc", partial), log=patch_log, progress=step)
        os.replace(str(partial), str(job.output_iso))
        out("saved %s" % job.output_iso)
        result["iso"] = job.output_iso
        result["work"] = work / "disc" if job.keep_work else None
        return result
    except Cancelled:
        out("cancelled")
        raise
    except SystemExit as e:
        out("ERROR: %s" % e.code)
        raise
    except BaseException as e:  # noqa: B902 - logged, then shown by the caller
        out("ERROR: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)).rstrip())
        raise
    finally:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
        if job.keep_work:
            out("kept the working folder: %s" % (work / "disc"))
        else:
            out("removing the working folder")
            shutil.rmtree(str(work), ignore_errors=True)
        logf.close()


def friendly_error(exc):
    """(message, details or None) for a dialog; details is a traceback for unexpected errors."""
    if isinstance(exc, Cancelled):
        return "Cancelled. Nothing was saved.", None
    if isinstance(exc, SystemExit):
        msg = str(exc.code)
        msg = msg.replace("rerun with --ui donor", "untick \"Show clones as…\" under Advanced and try again")
        msg = msg.replace("rerun with --no-gameplay", "this combination does not fit; try fewer clones")
        return msg, None
    if isinstance(exc, MemoryError):
        return "The computer ran out of memory. Close other programs and try again.", None
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) == 28:
            return "The drive is full. Free up some space (about 3.3 GB) and try again.", None
        where = " (%s)" % exc.filename if getattr(exc, "filename", None) else ""
        busy = (" If that game is open in Dolphin or Rio, close it first." if getattr(exc, "errno", None) in (1, 13)
                or getattr(exc, "winerror", None) == 32 else "")
        return "Couldn't read or write a file%s: %s.%s" % (where, exc.strerror or exc, busy), None
    return ("Something went wrong while making the game. The technical details are in the log on the "
            "right of the window and in the .log file saved next to the new game.",
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
