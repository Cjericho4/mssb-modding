#!/usr/bin/env python3
"""clone_gui.py -- a simple window for clone_character.py (Mario Superstar Baseball, USA / GYQE01).

Pick your .iso, pick a character and some colours, press "Create patched game".  Python 3.8+,
standard library only (tkinter ships with the python.org Windows installer):

    py clone_gui.py

The top half of this file is plain logic with no tkinter (tested by test_gui_logic.py on machines
without a display); the window itself is built by run_gui() at the bottom, which imports tkinter
only when it is called.
"""
import base64
import colorsys
import os
import queue
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
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




# ----------------------------------------------------------------------------------------------
# The window (tkinter is imported here, not at the top, so the logic above runs without it).
# Its look (palette, fonts, panels, the red button, dialogs) lives in gui_theme.py.
# ----------------------------------------------------------------------------------------------
tk = ttk = filedialog = colorchooser = theme = None
dialogs = None    # themed showinfo / showerror / askyesno, the same calls as tkinter.messagebox


def _import_tk():
    global tk, ttk, filedialog, colorchooser, theme
    import tkinter
    from tkinter import ttk as _ttk, filedialog as _fd, colorchooser as _cc
    import gui_theme
    tk, ttk, filedialog, colorchooser, theme = tkinter, _ttk, _fd, _cc, gui_theme


COLOUR_CHOICES = [c.capitalize() for c in PRESETS] + ["Custom…"]
GREEN, RED, GREY, GOLD = "#22c55e", "#ef4444", "#8f8fa3", "#f5bb00"
DONOR_WIDTH, COLOUR_WIDTH = 32, 12     # minimum widths; the slot list stretches


def make_root():
    """The Tk root with DPI awareness, the bundled fonts and the night theme (and `dialogs`)."""
    global dialogs
    theme.dpi_aware()
    theme.load_private_fonts()
    root = tk.Tk()
    if os.environ.get("MSSB_GUI_SCALE"):          # for screenshots: pretend to be a high-DPI screen
        root.tk.call("tk", "scaling", float(os.environ["MSSB_GUI_SCALE"]) * 96 / 72)
    theme.init(root)
    dialogs = theme.Dialogs(root)
    return root


class App:
    def __init__(self, root):
        T, px = theme, theme.px
        self.root = root
        self.q = queue.Queue()
        self.game = None
        self.choices = []
        self.rows = []                 # dicts: frame, donor (StringVar), colour (str), widgets
        self.busy = False
        self._preview_job = None
        self._preview_seq = 0          # newest preview request; older results are dropped
        self._row_seq = 0
        self.regions = []              # colour regions of self.regions_source (from its SourcePreview)
        self.regions_source = None
        self.region_vars = []
        self.worker = PreviewWorker(self.q.put)
        root.title(APP_TITLE)
        self.inputs = []               # widgets disabled while patching / before a game is chosen

        self.scroller = T.Scroller(root)
        self.scroller.pack(fill="both", expand=True)
        page = self.page = T.Page(self.scroller.view, "MSSB Clone Maker", "Mario Superstar Baseball · USA",
                                  "Make recoloured copies of a character.")
        edge, gap = px(22), px(18)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, minsize=page.header_h + px(4))
        page.rowconfigure(4, weight=1)
        left = dict(column=0, sticky="new", padx=(edge, gap // 2))

        self._edge, self._gap = edge, gap
        self.stacked = False           # True: the preview sits under the form (narrow windows, see relayout)
        # 01 game
        card = page.add_card(T.Card(page, "Your game", chip="01"), row=1, pady=(edge - px(4), gap), **left)
        self.left_cards = [card]
        b = card.body
        b.columnconfigure(2, weight=1)
        self.btn_iso = ttk.Button(b, text="Choose your game (.iso)…", command=self.choose_iso)
        self.btn_iso.grid(row=0, column=0, sticky="w")
        self.btn_dir = ttk.Button(b, text="or a folder…", command=self.choose_folder)
        self.btn_dir.grid(row=0, column=1, sticky="w", padx=(px(8), 0))
        self.game_path = tk.StringVar()
        ttk.Entry(b, textvariable=self.game_path, state="readonly", style="Path.TEntry", font=T.F["mono"],
                  takefocus=0).grid(row=0, column=2, sticky="ew", padx=(px(8), 0))
        self.status = tk.Label(b, text="No game chosen yet.", fg=GREY, bg=T.PANEL, font=T.F["medium"],
                               anchor="w", justify="left", wraplength=px(600))
        self.status.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(px(10), 0))

        # 02 character + clones (+ advanced)
        card = page.add_card(T.Card(page, "Clones", chip="02"), row=2, pady=(0, gap), **left)
        self.left_cards.append(card)
        T.section(card.right, "Character to clone", row=0, column=0, pady=(0, 0))
        self.source_var = tk.StringVar(value=pretty_name(0))
        self.cb_source = ttk.Combobox(card.right, textvariable=self.source_var, state="readonly", width=18,
                                      values=[pretty_name(c) for c in SOURCES])
        self.cb_source.grid(row=0, column=1, sticky="e", padx=(px(12), 0))
        self.cb_source.bind("<<ComboboxSelected>>", lambda e: self.source_changed())
        self.inputs.append(self.cb_source)
        b = card.body
        b.columnconfigure(0, weight=1)

        # the source's colour regions: which of its colours the clones change
        self.regions_frame = tk.Frame(b, bg=T.PANEL)
        self.regions_frame.grid(row=1, column=0, sticky="ew", pady=(0, px(12)))
        T.section(self.regions_frame, "Colours to change", row=0, column=0, pady=(0, 4))
        self.regions_box = tk.Frame(self.regions_frame, bg=T.PANEL)
        self.regions_box.grid(row=1, column=0, sticky="w")
        self.regions_hint = ttk.Label(self.regions_box, text="Choose your game first.", style="Muted.TLabel")
        self.regions_hint.grid(row=0, column=0, sticky="w")

        self.rows_frame = ttk.Frame(b)
        self.rows_frame.grid(row=2, column=0, sticky="ew")
        self.rows_frame.columnconfigure(0, weight=1)
        hdr = ttk.Frame(self.rows_frame)
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, px(6)))
        hdr.columnconfigure(1, weight=1)
        heads = [T.section(hdr, t, row=0, column=i, pady=(0, 0)) for i, t in
                 enumerate(("Preview", "Slot it takes over", "Colour"))]
        probes = [ttk.Combobox(b, width=DONOR_WIDTH), ttk.Combobox(b, width=COLOUR_WIDTH),
                  ttk.Button(b, text="×", style="Icon.TButton", width=2)]
        # column widths shared by the header and every row: view, slot (stretches), colour, swatch, remove
        self._cols = [max(heads[0].winfo_reqwidth(), px(24)) + px(12), probes[0].winfo_reqwidth() + px(8),
                      probes[1].winfo_reqwidth(), px(26 + 10), probes[2].winfo_reqwidth() + px(8)]
        for w in probes:
            w.destroy()
        for i, w in enumerate(self._cols):
            hdr.columnconfigure(i, minsize=w)
        self.no_game_hint = ttk.Label(self.rows_frame, text="Choose your game first.", style="Muted.TLabel")
        self.no_game_hint.grid(row=1, column=0, sticky="w", pady=px(4))
        self.preview_row = tk.IntVar(value=-1)

        bar = tk.Frame(b, bg=T.PANEL)
        bar.grid(row=3, column=0, sticky="ew", pady=(px(10), 0))
        bar.columnconfigure(1, weight=1)
        self.btn_add = ttk.Button(bar, text="+ Add a clone", command=self.add_row)
        self.btn_add.grid(row=0, column=0, sticky="w")
        self.inputs.append(self.btn_add)
        self.adv_open = False
        self.btn_adv = ttk.Button(bar, text="Advanced options  ▸", command=self.toggle_advanced)
        self.btn_adv.grid(row=0, column=2, sticky="e")

        self.adv = ttk.Frame(b)
        self.adv.grid(row=4, column=0, sticky="ew")
        self.adv.columnconfigure(1, weight=1)
        tk.Frame(self.adv, bg=T.LINE, height=1).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(px(14), px(12)))
        T.section(self.adv, "Advanced", red=True, row=1, column=0, columnspan=3, pady=(0, 4))
        self.ui_source = tk.BooleanVar(value=True)
        self.copy_stats = tk.BooleanVar(value=True)
        self.keep_work = tk.BooleanVar(value=False)
        self.keep_colour_word = tk.BooleanVar(value=False)
        self.keeplum = tk.DoubleVar(value=0.5)
        self.chk_ui = ttk.Checkbutton(self.adv, variable=self.ui_source)
        self.chk_ui.grid(row=2, column=0, columnspan=3, sticky="w")
        self.chk_colour = ttk.Checkbutton(self.adv, text="Keep the donor's colour word (e.g. 'Purple')", variable=self.keep_colour_word)
        self.chk_colour.grid(row=3, column=0, columnspan=3, sticky="w", padx=(px(22), 0))
        self.ui_source.trace_add("write", lambda *_: self.chk_colour.state(
            ["!disabled"] if self.ui_source.get() and self.chk_ui.instate(["!disabled"]) else ["disabled"]))
        self.chk_stats = ttk.Checkbutton(self.adv, variable=self.copy_stats)
        self.chk_stats.grid(row=4, column=0, columnspan=3, sticky="w")
        T.section(self.adv, "Keep shading", row=5, column=0, pady=(10, 0))
        self.scale = ttk.Scale(self.adv, from_=0.0, to=1.0, variable=self.keeplum, length=px(220),
                               command=lambda v: self.keeplum_changed())
        self.scale.grid(row=5, column=1, sticky="w", padx=(px(14), 0), pady=(px(10), 0))
        self.keeplum_lbl = ttk.Label(self.adv, text="0.50", font=T.F["mono"])
        self.keeplum_lbl.grid(row=5, column=2, sticky="e", pady=(px(10), 0))
        ttk.Label(self.adv, text="Higher keeps the light and dark areas of the original; lower is brighter and flatter.",
                  style="Muted.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(px(2), 0))
        self.chk_keep = ttk.Checkbutton(self.adv, text="Keep the working folder (for troubleshooting)", variable=self.keep_work)
        self.chk_keep.grid(row=7, column=0, columnspan=3, sticky="w", pady=(px(10), 0))
        self.inputs += [self.chk_ui, self.chk_colour, self.chk_stats, self.scale, self.chk_keep]
        self.adv.grid_remove()
        self.update_source_labels()

        # 03 save + create
        card = page.add_card(T.Card(page, "Save the new game as", chip="03"), row=3, pady=(0, edge), **left)
        self.left_cards.append(card)
        b = card.body
        b.columnconfigure(0, weight=1)
        self.out_path = tk.StringVar()
        self.ent_out = ttk.Entry(b, textvariable=self.out_path, font=T.F["mono"])
        self.ent_out.grid(row=0, column=0, sticky="ew")
        self.btn_out = ttk.Button(b, text="Save as…", command=self.choose_output)
        self.btn_out.grid(row=0, column=1, padx=(px(8), 0))
        self.inputs += [self.ent_out, self.btn_out]
        self.btn_go = T.AngledButton(b, "Create patched game", self.create)
        self.btn_go.grid(row=0, column=2, sticky="e", padx=(px(16), 0))
        self.inputs.append(self.btn_go)
        # shown while the game is being made: stage + percent, the bar, Cancel
        self.run_box = ttk.Frame(b)
        self.run_box.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(px(14), px(2)))
        self.run_box.columnconfigure(0, weight=1)
        self.progress_lbl = ttk.Label(self.run_box, text="", style="Muted.TLabel")
        self.progress_lbl.grid(row=0, column=0, sticky="w", pady=(0, px(4)))
        self.progress = ttk.Progressbar(self.run_box, mode="determinate", maximum=1000, length=px(160))
        self.progress.grid(row=1, column=0, sticky="ew")
        self.btn_cancel = ttk.Button(self.run_box, text="Cancel", command=self.cancel)
        self.btn_cancel.grid(row=1, column=1, padx=(px(10), 0))
        self.run_box.grid_remove()
        self.cancel_event = threading.Event()
        self._progress_shown = None

        # preview + log (right column, full height)
        card = page.add_card(T.Card(page, "Preview"), row=1, column=1, rowspan=4, sticky="nsew",
                             padx=(gap // 2, edge), pady=(edge - px(4), edge))
        self.preview_card = card
        self.preview_chip = T.Chip(card.right, pretty_name(0), "neutral")
        self.preview_chip.grid(row=0, column=0)
        b = card.body
        b.rowconfigure(5, weight=1)
        self._zoom = max(1, int(T.S))      # whole-number zoom keeps the texels square
        side = PREVIEW_WIDTH * self._zoom
        self._blank = tk.PhotoImage(width=side, height=side)
        self.preview_caption = ttk.Label(b, text="All of the character's textures, before and after.",
                                         style="Muted.TLabel", wraplength=2 * side + px(16), justify="left")
        self.preview_caption.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, px(10)))
        T.section(b, "Before", row=1, column=0)
        T.section(b, "After", row=1, column=1, pady=(0, 6))
        self.img_before = tk.Label(b, image=self._blank, text="No preview yet", compound="center", fg=GREY,
                                   font=T.F["small"], bg=T.FIELD, bd=0, highlightthickness=1,
                                   highlightbackground=T.LINE)
        self.img_before.grid(row=2, column=0, sticky="nw")
        self.img_after = tk.Label(b, image=self._blank, text="", compound="center", fg=GREY, font=T.F["small"],
                                  bg=T.FIELD, bd=0, highlightthickness=1, highlightbackground=T.LINE)
        self.img_after.grid(row=2, column=1, sticky="nw", padx=(px(16), 0))
        self.img_after.grid_configure(sticky="nw")
        self._log_sep = tk.Frame(b, bg=T.LINE, height=1)
        self._log_sep.grid(row=3, column=0, columnspan=2, sticky="ew", pady=px(16))
        self._log_head = T.section(b, "Log", red=True, row=4, column=0)
        lf = self._log_frame = tk.Frame(b, bg=T.FIELD, highlightthickness=1, highlightbackground=T.LINE)
        lf.grid(row=5, column=0, columnspan=2, sticky="nsew")
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self.log_text = tk.Text(lf, height=5, width=10, wrap="word", state="disabled", font=T.F["mono"],
                                bg=T.FIELD, fg=T.LOG_TEXT, bd=0, highlightthickness=0, relief="flat",
                                padx=px(10), pady=px(8), insertbackground=T.TEXT,
                                selectbackground=T.mix(T.FIELD, T.RED, 0.4), selectforeground=T.TEXT)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_text.tag_configure("err", foreground=RED)
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("head", foreground=T.TEXT)
        sb = ttk.Scrollbar(lf, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, px(3)), pady=px(3))
        self.log_text.configure(yscrollcommand=sb.set)

        self.set_enabled(False)
        root.update_idletasks()
        w, h = page.winfo_reqwidth(), page.winfo_reqheight()
        self.scroller.host(page)
        self.scroller.view.bind("<Configure>", lambda e: self.relayout(e.width), add="+")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight() - px(80)
        # narrower than both columns: the preview goes under the form; narrower still: it scrolls sideways
        root.minsize(min(self.stacked_width(), sw, px(640)), min(h, sh, px(560)))
        root.geometry("%dx%d" % (min(max(w, px(1320)), sw), min(max(h, px(780)), sh)))
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)

    # ---------------------------------------------------------------- layout
    def wide_width(self):
        """Width the two-column layout needs (form | preview).  The preview's width is the one it had
        in that layout (stacked, it also holds the log beside the pictures)."""
        if not self.stacked:
            self._preview_w = self.preview_card.winfo_reqwidth()
        return max(c.winfo_reqwidth() for c in self.left_cards) + self._preview_w + 2 * self._edge + self._gap

    def stacked_width(self):
        return max(c.winfo_reqwidth() for c in self.left_cards + [self.preview_card]) + 2 * self._edge

    def relayout(self, width):
        """Two columns when the window is wide enough, else the preview and log under the form (small
        screens, 125-150 % scaling).  Only re-grids on a change of mode; nothing is redrawn otherwise."""
        if width <= 1:
            return
        need = self.wide_width()
        stack = width < need if not self.stacked else width < need + theme.px(8)
        if stack == self.stacked:
            return
        self.stacked = stack
        edge, gap = self._edge, self._gap
        side = (edge, edge) if stack else (edge, gap // 2)
        for c in self.left_cards:
            c.grid_configure(padx=side)
        self.left_cards[-1].grid_configure(pady=(0, gap if stack else edge))
        b = self.preview_card.body
        if stack:
            self.preview_card.grid_configure(row=4, column=0, rowspan=1, padx=(edge, edge), pady=(0, edge))
            # the log goes beside the before/after pictures, using the width the form leaves
            self._log_sep.grid_remove()
            self._log_head.grid_configure(row=1, column=2, columnspan=1, padx=(theme.px(16), 0))
            self._log_frame.grid_configure(row=2, column=2, columnspan=1, padx=(theme.px(16), 0))
            b.columnconfigure(2, weight=1, minsize=theme.px(280))
            b.rowconfigure(5, weight=0)
        else:
            self.preview_card.grid_configure(row=1, column=1, rowspan=4, padx=(gap // 2, edge), pady=(edge - theme.px(4), edge))
            self._log_sep.grid()
            self._log_head.grid_configure(row=4, column=0, columnspan=1, padx=0)
            self._log_frame.grid_configure(row=5, column=0, columnspan=2, padx=0)
            b.columnconfigure(2, weight=0, minsize=0)
            b.rowconfigure(5, weight=1)

    # ---------------------------------------------------------------- helpers
    def set_enabled(self, on):
        for w in self.inputs + [w for row in self.rows for w in row["widgets"]] + getattr(self, "_region_checks", []):
            try:
                if w.instate(["disabled"]) == on:          # only touch widgets that change (each one
                    w.state(["!disabled"] if on else ["disabled"])   # is a relayout of the page)
            except Exception:
                pass
        for w in (self.btn_iso, self.btn_dir):
            w.state(["disabled"] if self.busy else ["!disabled"])
        if on and not self.ui_source.get():               # the colour word only matters with source menus
            self.chk_colour.state(["disabled"])

    def set_status(self, text, colour, pill, pill_colour):
        self.status.configure(text=text, fg=colour)
        self.page.set_pill(pill, pill_colour)

    def log(self, line):
        tag = ("err" if line.startswith("ERROR") else "ok" if line.startswith(("saved ", "selftest")) else
               "head" if line.startswith("----") else ())
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def source(self):
        names = [pretty_name(c) for c in range(cc.NUM_CHARS)]
        return names.index(self.source_var.get()) if self.source_var.get() in names else 0

    def region_ticks(self):
        return [i for i, v in enumerate(self.region_vars) if v.get()]

    def spec_for(self, colour):
        sel = None
        if self.regions_source == self.source() and self.region_vars:
            sel = region_selection(self.regions, self.region_ticks())
        return colour_spec(colour, round(self.keeplum.get(), 2), regions=sel)

    def update_source_labels(self):
        n = pretty_name(self.source())
        self.chk_ui.configure(text="Show clones as %s: name and pictures in menus, HUD, announcer (recommended)" % n)
        self.chk_stats.configure(text="Give clones %s's stats" % n)

    def toggle_advanced(self):
        self.adv_open = not self.adv_open
        if self.adv_open:
            self.adv.grid()
        else:
            self.adv.grid_remove()
        self.btn_adv.configure(text="Advanced options  " + ("▾" if self.adv_open else "▸"))
        self.fit_height()

    def fit_height(self):
        """Grow the window (within the screen) when its content no longer fits; past that it scrolls."""
        self.root.update_idletasks()
        need = self.page.winfo_reqheight()
        room = self.root.winfo_screenheight() - theme.px(80)
        if self.root.winfo_height() < min(need, room):
            self.root.geometry("%dx%d" % (self.root.winfo_width(), min(need, room)))

    # ---------------------------------------------------------------- game
    def choose_iso(self):
        p = filedialog.askopenfilename(title="Choose your Mario Superstar Baseball game",
                                       filetypes=[("GameCube disc image", "*.iso *.gcm"), ("All files", "*.*")])
        if p:
            self.start_load(p)

    def choose_folder(self):
        p = filedialog.askdirectory(title="Choose the extracted game folder (it contains sys and files)")
        if p:
            self.start_load(p)

    def start_load(self, path):
        self.game_path.set(path)
        self.set_status("Checking the game…", GREY, "Checking the game…", GOLD)
        self.set_enabled(False)
        self.btn_iso.state(["disabled"])
        self.btn_dir.state(["disabled"])
        threading.Thread(target=self._load_worker, args=(path,), daemon=True).start()

    def _load_worker(self, path):
        try:
            game, ok, msg = load_game(path)
        except BaseException as e:  # noqa: B902 - report everything, never die silently in a thread
            game, ok, msg = None, False, friendly_error(e)[0]
        self.q.put(("game", path, game, ok, msg))

    def game_loaded(self, path, game, ok, msg):
        self.btn_iso.state(["!disabled"])
        self.btn_dir.state(["!disabled"])
        if self.game is not None:
            self.game.close()
        self.game = game if ok else None
        if game is not None and not ok:
            game.close()
        self.set_status(("✔ " if ok else "✖ ") + msg, GREEN if ok else RED,
                        "GYQE01 ready" if ok else "Game not usable", GREEN if ok else RED)
        if not ok:
            self.set_enabled(False)
            return
        self.out_path.set(str(default_output(path)))
        self.rebuild_rows(reset=True)
        self._clear_regions(None)
        self.set_enabled(True)
        self.show_regions()
        self.schedule_preview(now=True)

    # ---------------------------------------------------------------- rows
    def rebuild_rows(self, reset=False):
        """Fit the clone rows to the current source: keep (or, on reset, replace with the defaults)
        the rows' slots and colours, reusing the row widgets (creating widgets is the slow part)."""
        src = self.source()
        self.choices = donor_choices(self.game.dol, src)
        valid = {c.id for c in self.choices}
        self.no_game_hint.grid_remove()
        if reset or not self.rows:
            wanted = default_rows(self.choices)
        else:
            wanted, used = [], set()
            for r in self.rows:
                slot = r["slot"] if r["slot"] in valid and r["slot"] not in used else next_free_slot(self.choices, used)
                if slot is not None:
                    wanted.append((slot, r["colour"]))
                    used.add(slot)
        while len(self.rows) > len(wanted):
            self.rows.pop()["frame"].destroy()
        labels = tuple(c.label for c in self.choices)
        for row, (slot, colour) in zip(self.rows, wanted):
            if row.get("labels") != labels:
                row["donor_cb"].configure(values=labels)
                row["labels"] = labels
            self.set_row(row, slot, colour)
        for slot, colour in wanted[len(self.rows):]:
            self.add_row(slot, colour)
        if self.rows and self.preview_row.get() not in [r["id"] for r in self.rows]:
            self.preview_row.set(self.rows[0]["id"])

    def set_row(self, row, slot, colour):
        row["slot"], row["colour"] = slot, colour
        for var, value in ((row["donor"], self.choice(slot).label), (row["colour_var"], colour_label(colour))):
            if var.get() != value:
                var.set(value)
        row["swatch"].set(colour_swatch(colour))

    def add_row(self, slot=None, colour=None):
        if self.game is None:
            return
        px = theme.px
        used = {r["slot"] for r in self.rows}
        if slot is None:
            slot = next_free_slot(self.choices, used)
            if slot is None:
                dialogs.showinfo(APP_TITLE, "Every available slot already has a clone.")
                return
        if colour is None:
            taken = {r["colour"] for r in self.rows}
            colour = next((c for c in ["blue", "green", "purple", "yellow", "orange", "cyan", "pink", "red"] if c not in taken), "blue")
        self._row_seq += 1
        row = {"id": self._row_seq, "slot": slot, "colour": colour}
        f = ttk.Frame(self.rows_frame)
        f.grid(row=len(self.rows) + 1, column=0, sticky="ew", pady=px(3))
        for i, w in enumerate(self._cols):
            f.columnconfigure(i, minsize=w)
        f.columnconfigure(1, weight=1)
        row["frame"] = f
        rb = ttk.Radiobutton(f, variable=self.preview_row, value=row["id"], command=self.schedule_preview)
        rb.grid(row=0, column=0, sticky="w")
        row["donor"] = tk.StringVar(value=self.choice(slot).label)
        cb = ttk.Combobox(f, textvariable=row["donor"], values=[c.label for c in self.choices], state="readonly",
                          width=DONOR_WIDTH)
        cb.grid(row=0, column=1, sticky="ew", padx=(0, px(8)))
        cb.bind("<<ComboboxSelected>>", lambda e, row=row: self.donor_changed(row))
        row["donor_cb"] = cb
        row["labels"] = tuple(c.label for c in self.choices)
        row["colour_var"] = tk.StringVar(value=colour_label(colour))
        cc_ = ttk.Combobox(f, textvariable=row["colour_var"], values=COLOUR_CHOICES, state="readonly", width=COLOUR_WIDTH)
        cc_.grid(row=0, column=2, sticky="w")
        cc_.bind("<<ComboboxSelected>>", lambda e, row=row: self.colour_changed(row))
        sw = theme.Swatch(f, colour_swatch(colour))
        sw.grid(row=0, column=3, padx=(px(10), 0))
        row["swatch"] = sw
        rm = ttk.Button(f, text="×", style="Icon.TButton", width=2, command=lambda row=row: self.remove_row(row))
        rm.grid(row=0, column=4, padx=(px(8), 0))
        row["widgets"] = [rb, cb, cc_, rm]
        self.rows.append(row)
        if len(self.rows) == 1:
            self.preview_row.set(row["id"])
        self.btn_add.configure(text="+ Add another clone")
        self.schedule_preview()

    def choice(self, slot):
        return next(c for c in self.choices if c.id == slot)

    def remove_row(self, row):
        row["frame"].destroy()
        self.rows.remove(row)
        for i, r in enumerate(self.rows):
            r["frame"].grid(row=i + 1)
        if self.rows and self.preview_row.get() == row["id"]:
            self.preview_row.set(self.rows[0]["id"])
        self.btn_add.configure(text="+ Add another clone" if self.rows else "+ Add a clone")
        self.schedule_preview()

    def donor_changed(self, row):
        label = row["donor"].get()
        row["slot"] = next(c.id for c in self.choices if c.label == label)
        self.schedule_preview()

    def colour_changed(self, row):
        v = row["colour_var"].get()
        if v.startswith("Custom"):
            res = colorchooser.askcolor(color=colour_swatch(row["colour"]), title="Pick a colour (only the hue is used)")
            if res and res[1]:
                row["colour"] = res[1].lower() if res[1].startswith("#") else "#%02x%02x%02x" % tuple(int(x) for x in res[0])
        else:
            row["colour"] = v.lower()
        row["colour_var"].set(colour_label(row["colour"]))
        row["swatch"].set(colour_swatch(row["colour"]))
        self.preview_row.set(row["id"])
        self.schedule_preview()

    def source_changed(self):
        self.update_source_labels()
        self.preview_chip.set(pretty_name(self.source()))
        if self.game is not None:
            self.rebuild_rows()
            self.set_enabled(not self.busy)
            self.show_regions()
            self.schedule_preview(now=True)

    def keeplum_changed(self):
        self.keeplum_lbl.configure(text="%.2f" % self.keeplum.get())
        self.schedule_preview()

    # ---------------------------------------------------------------- colour regions
    def show_regions(self):
        """Checkboxes for the source's colour regions (the defaults ticked), once its SourcePreview
        exists; until then a "finding its colours" note."""
        src = self.source()
        pv = self.game.cached_preview(src) if self.game is not None else None
        if pv is None:
            if self.regions_source is not None or not self.regions_hint.winfo_ismapped():
                self._clear_regions("Finding %s's colours…" % pretty_name(src))
            return
        if self.regions_source == src:
            return
        self._clear_regions(None)
        self.regions, self.regions_source = pv.regions, src
        px = theme.px
        for i, r in enumerate(pv.regions[:6]):
            v = tk.BooleanVar(value=r.default)
            cell = tk.Frame(self.regions_box, bg=theme.PANEL)
            cell.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, px(18)), pady=(0, px(2)))
            tk.Frame(cell, bg=r.swatch, width=px(14), height=px(14), highlightthickness=1,
                     highlightbackground=theme.SOFT_LINE).grid(row=0, column=0, padx=(0, px(6)))
            chk = ttk.Checkbutton(cell, text=region_label(r), variable=v,
                                  command=lambda i=i: self.regions_changed(i))
            chk.grid(row=0, column=1, sticky="w")
            if self.busy:
                chk.state(["disabled"])
            self.region_vars.append(v)
            self._region_widgets.append(cell)
            self._region_checks.append(chk)
        if not pv.regions:
            self._clear_regions("%s has no colours that can be changed." % pretty_name(src))

    def _clear_regions(self, note):
        for w in getattr(self, "_region_widgets", []):
            w.destroy()
        self._region_widgets, self._region_checks, self.region_vars = [], [], []
        self.regions, self.regions_source = [], None
        if note:
            self.regions_hint.configure(text=note)
            self.regions_hint.grid()
        else:
            self.regions_hint.grid_remove()

    def regions_changed(self, i):
        if not any(v.get() for v in self.region_vars):
            self.region_vars[i].set(True)        # at least one colour has to change
            return
        self.schedule_preview()

    # ---------------------------------------------------------------- preview
    def schedule_preview(self, now=False):
        """Ask for a new preview; quick successive changes (dragging the slider) coalesce."""
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(1 if now else 40, self.request_preview)

    def request_preview(self):
        self._preview_job = None
        row = next((r for r in self.rows if r["id"] == self.preview_row.get()), None)
        if self.game is None or row is None:
            return
        game, src = self.game, self.source()
        spec = self.spec_for(row["colour"])
        self._preview_seq += 1
        req = {"seq": self._preview_seq, "source": src, "slot": row["slot"], "colour": row["colour"], "spec": spec}
        if game.cached_preview(src) is None:
            self.preview_caption.configure(text="Loading %s's textures…" % pretty_name(src))

        def work():
            pv = game.preview(src)
            return pv, pv.after_png(spec)
        self.worker.request(req, work)

    def preview_done(self, req, res):
        if self.game is None:
            return
        if req["source"] == self.source():
            self.show_regions()
        self._preview_done_seq = req["seq"]
        if req["seq"] != self._preview_seq:
            return                                   # a newer request is on its way
        if isinstance(res, BaseException):
            self.preview_caption.configure(text="No preview for %s: %s" % (pretty_name(req["source"]), res))
            for lbl in (self.img_before, self.img_after):
                lbl.configure(image=self._blank, text="")
                lbl.image = None
            return
        pv, after = res
        if getattr(self, "_shown_before", None) is not pv:
            img = tk.PhotoImage(data=png_base64(pv.before_png))
            img = img.zoom(self._zoom) if self._zoom > 1 else img
            self.img_before.configure(image=img, text="")
            self.img_before.image = img
            self._shown_before = pv
        img = tk.PhotoImage(data=png_base64(after))
        img = img.zoom(self._zoom) if self._zoom > 1 else img
        self.img_after.configure(image=img, text="")
        self.img_after.image = img             # keep a reference or Tk shows nothing
        self.preview_chip.set(pretty_name(req["source"]))
        rc = cc.Recolour(req["spec"]).resolve(pv.regions)
        self.preview_caption.configure(text="All %d textures of %s, before and as %s in %s (changes: %s)" % (
            pv.tex_count, pretty_name(req["source"]), self.choice(req["slot"]).name if any(
                c.id == req["slot"] for c in self.choices) else pretty_name(req["slot"]),
            colour_label(req["colour"]), rc.describe()))

    def render_preview(self, timeout=60):
        """Synchronous preview (selftest, tests): request one and wait for its result."""
        self.request_preview()
        end = time.time() + timeout
        while time.time() < end and getattr(self, "_preview_done_seq", None) != self._preview_seq:
            self.root.update()
            time.sleep(0.01)

    # ---------------------------------------------------------------- output + run
    def choose_output(self):
        cur = Path(self.out_path.get()) if self.out_path.get() else None
        p = filedialog.asksaveasfilename(
            title="Save the patched game as", defaultextension=".iso", filetypes=[("GameCube disc image", "*.iso")],
            initialdir=str(cur.parent) if cur else None, initialfile=cur.name if cur else "Mario Superstar Baseball - Clones.iso")
        if p:
            self.out_path.set(p)

    def make_job(self):
        return Job(self.game_path.get(), self.out_path.get().strip(), self.source(),
                   [(r["slot"], self.spec_for(r["colour"])) for r in self.rows],
                   ui_source=self.ui_source.get(), copy_stats=self.copy_stats.get(), keep_work=self.keep_work.get(),
                   keep_colour_word=self.keep_colour_word.get())

    def create(self):
        if self.game is None or self.busy:
            return
        if not self.out_path.get().strip():
            self.choose_output()
            if not self.out_path.get().strip():
                return
        try:
            job = self.make_job()
            validate_job(job)
        except BaseException as e:  # noqa: B902
            dialogs.showerror(APP_TITLE, friendly_error(e)[0])
            return
        if job.output_iso.exists() and not dialogs.askyesno(APP_TITLE, "%s already exists. Replace it?" % job.output_iso.name):
            return
        self.busy = True
        self.set_enabled(False)
        self.page.set_pill("Creating the game…", GOLD)
        self.cancel_event.clear()
        self._progress_shown = None
        self.progress.configure(value=0)
        self.progress_lbl.configure(text="Starting…")
        self.btn_cancel.state(["!disabled"])
        self.run_box.grid()
        self.log("---- creating %s" % job.output_iso)
        self.log("the full log is also saved to %s" % log_path(job))
        threading.Thread(target=self._job_worker, args=(job,), daemon=True).start()

    def cancel(self):
        self.cancel_event.set()
        self.btn_cancel.state(["disabled"])
        self.progress_lbl.configure(text="Cancelling…")

    def _job_worker(self, job):
        def progress(fraction, label):
            shown = (label, int(fraction * 1000))
            if shown != self._progress_shown:      # ponytail: one message per 0.1 %, not per MB
                self._progress_shown = shown
                self.q.put(("progress",) + shown)
        try:
            result = run_job(job, lambda line: self.q.put(("log", line)), progress, self.cancel_event)
            hint = pick_hint(self.game.dol, job.source, [s for s, _ in job.slots], job.ui_source)
            self.q.put(("done", result, hint))
        except BaseException as e:  # noqa: B902 - SystemExit in a thread would vanish silently
            self.q.put(("error",) + friendly_error(e))

    def poll(self):
        try:
            while True:
                self.handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(25, self.poll)

    def handle(self, msg):
        kind = msg[0]
        if kind == "log":
            self.log(msg[1])
        elif kind == "progress":
            if not self.cancel_event.is_set():
                self.progress_lbl.configure(text="%s…  %d%%" % (msg[1], msg[2] // 10))
            self.progress.configure(value=msg[2])
        elif kind == "preview":
            self.preview_done(msg[1], msg[2])
        elif kind == "game":
            self.game_loaded(*msg[1:])
        elif kind == "done":
            self.finish()
            result, hint = msg[1], msg[2]
            self.page.set_pill("Game created", GREEN)
            extra = "\n\nThe working folder was kept: %s" % result["work"] if result.get("work") else ""
            dialogs.showinfo("Done!", "Saved to %s.\n\nOpen it in Dolphin or Project Rio: File → Open…\n\n%s%s"
                             % (result["iso"], hint, extra))
        elif kind == "error" and msg[1].startswith("Cancelled"):
            self.finish()
            self.page.set_pill("Cancelled", GREY)
            self.log(msg[1])
        elif kind == "error":
            self.finish()
            message = msg[1]
            self.page.set_pill("Not created", RED)
            self.log("ERROR: " + message)    # the traceback, if any, is already in the log (run_job)
            dialogs.showerror("Couldn't create the patched game", message)

    def finish(self):
        self.busy = False
        self.run_box.grid_remove()
        self.set_enabled(self.game is not None)

    def on_close(self):
        if self.busy and not dialogs.askyesno(APP_TITLE, "The game is still being made. Quit anyway?\n\n"
                                              "A half-finished working folder named MSSB-Clone-Maker-work-… "
                                              "will be left next to the output; you can delete it."):
            return
        if self.game is not None:
            self.game.close()
        self.root.destroy()


def run_gui():
    _import_tk()
    root = make_root()
    App(root)
    theme.dark_title_bar(root)
    root.mainloop()


def selftest(argv):
    """Build the window, optionally load a game and render a preview, then close.  Exit code 0 = OK.
    Used by the Windows build (the .exe) and for screenshots:
        clone_gui.py --selftest [--game P] [--hold S] [--expect-fonts] [--expect-fast-lzss]
    --expect-fonts fails unless Rajdhani / Inter / JetBrains Mono were loaded (bundled or installed);
    --expect-fast-lzss fails unless the C codec (_lzss.dll, from lzss.c) was loaded;
    --log P writes the output to P (a --windowed .exe has no console);
    --profile-resize drags the window size through 80 steps under cProfile and prints the hot spots."""
    if "--log" in argv:
        sys.stdout = sys.stderr = open(argv[argv.index("--log") + 1], "w", encoding="utf-8")
    game = hold = None
    if "--game" in argv:
        game = argv[argv.index("--game") + 1]
    if "--hold" in argv:
        hold = float(argv[argv.index("--hold") + 1])
    try:
        _import_tk()
        root = make_root()
        app = App(root)
        dark = theme.dark_title_bar(root)
        root.update()
        print("selftest: scale %.2f, fonts %s, dark title bar %s" % (
            theme.S, "fallback for " + ", ".join(theme.FONT_FALLBACK) if theme.FONT_FALLBACK else "bundled",
            "on" if dark else "off"))
        # narrow window: the preview stacks under the form; wide again: back to two columns
        wide = app.wide_width()
        app.relayout(wide - theme.px(40))
        root.update()
        stacked = app.stacked and app.preview_card.grid_info()["column"] == 0
        app.relayout(wide + theme.px(40))
        root.update()
        if not stacked or app.stacked or app.preview_card.grid_info()["column"] != 1:
            print("selftest: the narrow-window layout did not switch (stacked %s)" % stacked)
            return 1
        app.relayout(app.scroller.view.winfo_width())
        if "--expect-fonts" in argv and theme.FONT_FALLBACK:
            print("selftest: the bundled fonts did not load")
            return 1
        if "--profile-resize" in argv:
            import cProfile
            import pstats
            prof = cProfile.Profile()
            t0 = time.perf_counter()
            prof.enable()
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            for i in range(80):        # a drag inside the screen (bigger is clamped): a redraw after each step
                root.geometry("%dx%d" % (int(sw * 0.55) + 4 * i, int(sh * 0.6) + 2 * i))
                root.update()
                time.sleep(0.02)       # a real drag gives the after() jobs time to fire
            prof.disable()
            busy = time.perf_counter() - t0 - 80 * 0.02
            print("selftest: 80 resizes, %.2f s busy (%.1f ms per step, sleeps excluded)" % (busy, busy * 12.5))
            pstats.Stats(prof, stream=sys.stdout).sort_stats("cumulative").print_stats(30)
            pstats.Stats(prof, stream=sys.stdout).sort_stats("tottime").print_stats(15)
        if "--expect-fast-lzss" in argv and cc._fast_lzss is None:
            print("selftest: the C codec did not load (%s); packing would take minutes" % cc._fast_lzss_why)
            return 1
        if game:
            app.game_path.set(game)
            app.game_loaded(game, *load_game(game))
            if app.game is None:
                print("selftest: game did not load: %s" % app.status.cget("text"))
                return 1
            app.toggle_advanced()
            app.render_preview()
            if not getattr(app.img_after, "image", None):
                print("selftest: no preview image")
                return 1
            app.log("selftest: %d clone rows, preview %dx%d" % (len(app.rows), app.img_after.image.width(), app.img_after.image.height()))
        root.update()
        if hold:
            root.after(int(hold * 1000), root.destroy)
            root.mainloop()
        else:
            root.destroy()
    except BaseException:  # noqa: B902
        traceback.print_exc()
        return 1
    print("selftest: OK")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--selftest"]:
        return selftest(argv[1:])
    try:
        _import_tk()
    except ImportError:
        print("tkinter is not available. On Windows, install Python from python.org (tkinter is included); "
              "on Linux, install your distribution's python3-tk package.")
        return 1
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
