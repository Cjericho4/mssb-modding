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

    def check(self):
        """(ok, message) from clone_character.preflight."""
        lines = []
        try:
            cc.preflight(self.disc, self.dol, lines.append)
        except SystemExit:
            return False, friendly_preflight([l for l in lines if l.startswith("preflight:")])
        return True, GAME_OK

    def preview(self, source):
        if source not in self._previews:
            self._previews[source] = SourcePreview(self, source)
        return self._previews[source]

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


def colour_spec(colour, keeplum=0.5, band=BAND_REDS):
    """Recolour spec string for clone_character (the same text the --slot option takes)."""
    hue = colour if not is_custom(colour) else "%.3f" % colour_hue(colour)
    spec = "hue=%s,keeplum=%s" % (hue, ("%.2f" % keeplum).rstrip("0").rstrip(".") or "0")
    if band == BAND_ALL:
        spec += ",all=1"
    cc.Recolour(spec)  # validate
    return spec


# ----------------------------------------------------------------------------------------------
# Preview: decode the source's main body texture (CMPR) before/after the real recolour code
# ----------------------------------------------------------------------------------------------
def largest_texture(gpc):
    return max(gpc.textures, key=lambda t: (t["w"] * t["h"], -t["index"]))


def decode_cmpr(data, w, h):
    """GameCube CMPR (8x8 tiles of four DXT1 blocks) -> RGB bytes; transparent pixels on a grey checker."""
    out = bytearray(w * h * 3)
    o = 0
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for by, bx in ((0, 0), (0, 4), (4, 0), (4, 4)):
                c0, c1, idx = struct.unpack_from(">HHI", data, o)
                o += 8
                a = cc.rgb565_to_rgb(c0)
                b = cc.rgb565_to_rgb(c1)
                if c0 > c1:
                    pal = (a, b, tuple((2 * x + y) // 3 for x, y in zip(a, b)), tuple((x + 2 * y) // 3 for x, y in zip(a, b)))
                else:
                    pal = (a, b, tuple((x + y) // 2 for x, y in zip(a, b)), None)
                for i in range(16):
                    y = ty + by + (i >> 2)
                    x = tx + bx + (i & 3)
                    if x >= w or y >= h:
                        continue
                    col = pal[(idx >> (30 - 2 * i)) & 3]
                    if col is None:
                        col = (0x99, 0x99, 0x99) if ((x >> 3) ^ (y >> 3)) & 1 else (0xCC, 0xCC, 0xCC)
                    p = (y * w + x) * 3
                    out[p:p + 3] = bytes(col)
    return bytes(out)


def png_bytes(w, h, rgb):
    """Minimal RGB PNG encoder (stdlib zlib)."""
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    stride = w * 3
    raw = b"".join(b"\0" + rgb[y * stride:(y + 1) * stride] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class SourcePreview:
    """The source's in-match body (ARAM body + overlay), largest texture, decoded once."""

    def __init__(self, game, source):
        body = cc.Gpc(cc.effective_body(game.disc, game.dol, source))
        t = largest_texture(body)
        self.source = source
        self.w, self.h, self.tex_index = t["w"], t["h"], t["index"]
        self.blocks = bytes(body.data[t["data"]:t["data"] + t["size"]])
        self.before_png = png_bytes(self.w, self.h, decode_cmpr(self.blocks, self.w, self.h))
        self._after = {}

    def recoloured_blocks(self, spec):
        rc = cc.Recolour(spec)
        if rc.tex is not None and self.tex_index not in rc.tex:
            return self.blocks
        return b"".join(cc.recolour_block(self.blocks[o:o + 8], rc) for o in range(0, len(self.blocks), 8))

    def after_png(self, spec):
        if spec not in self._after:
            if len(self._after) > 32:
                self._after.clear()
            self._after[spec] = png_bytes(self.w, self.h, decode_cmpr(self.recoloured_blocks(spec), self.w, self.h))
        return self._after[spec]


def png_base64(png):
    return base64.b64encode(png).decode("ascii")


# ----------------------------------------------------------------------------------------------
# The patch job (what the big button runs)
# ----------------------------------------------------------------------------------------------
class Job:
    def __init__(self, game_path, output_iso, source, slots, ui_source=True, copy_stats=True, keep_work=False):
        self.game_path = Path(game_path)
        self.output_iso = Path(output_iso)
        self.source = source
        self.slots = list(slots)          # [(slot id, recolour spec)]
        self.ui_source = ui_source
        self.copy_stats = copy_stats
        self.keep_work = keep_work


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
    if free_bytes is None:
        free_bytes = shutil.disk_usage(str(out.parent)).free
    if free_bytes < NEEDED_FREE_BYTES:
        raise SystemExit("Not enough free disk space on the drive of %s: about %.1f GB is needed while the "
                         "game is being made, %.1f GB is free." % (out.parent, NEEDED_FREE_BYTES / 1e9, free_bytes / 1e9))


def run_job(job, log):
    """Patch into a temporary working folder next to the output, write the .iso, clean up.
    Returns clone_character.clone()'s result plus "work" (the kept folder, or None)."""
    validate_job(job)
    work = Path(tempfile.mkdtemp(prefix="MSSB-Clone-Maker-work-", dir=str(job.output_iso.parent)))
    partial = job.output_iso.with_name(job.output_iso.stem + ".partial.iso")
    try:
        log("working folder: %s" % work)

        def patch_log(line):
            if not line.startswith("Boot: "):     # the CLI's "open the folder" hint; the GUI hands out the .iso
                log(line)
        result = cc.clone(job_args(job, work / "disc", partial), log=patch_log)
        os.replace(str(partial), str(job.output_iso))
        log("saved %s" % job.output_iso)
        result["iso"] = job.output_iso
        result["work"] = work / "disc" if job.keep_work else None
        return result
    finally:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
        if job.keep_work:
            log("kept the working folder: %s" % (work / "disc"))
        else:
            log("removing the working folder")
            shutil.rmtree(str(work), ignore_errors=True)


def friendly_error(exc):
    """(message, details or None) for a dialog; details is a traceback for unexpected errors."""
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
        return "Couldn't read or write a file%s: %s" % (where, exc.strerror or exc), None
    return ("Something went wrong while making the game. The technical details are in the log at the "
            "bottom of the window.", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))


# ----------------------------------------------------------------------------------------------
# The window (tkinter is imported here, not at the top, so the logic above runs without it)
# ----------------------------------------------------------------------------------------------
tk = ttk = filedialog = messagebox = colorchooser = None


def _import_tk():
    global tk, ttk, filedialog, messagebox, colorchooser
    import tkinter
    from tkinter import ttk as _ttk, filedialog as _fd, messagebox as _mb, colorchooser as _cc
    tk, ttk, filedialog, messagebox, colorchooser = tkinter, _ttk, _fd, _mb, _cc


COLOUR_CHOICES = [c.capitalize() for c in PRESETS] + ["Custom…"]
BAND_CHOICES = [("Reds only (Mario's hat and shirt)", BAND_REDS), ("Every strong colour", BAND_ALL)]
GREEN, RED, GREY = "#1a7f37", "#c62828", "#666666"


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.game = None
        self.choices = []
        self.rows = []                 # dicts: frame, donor (StringVar), colour (str), widgets
        self.busy = False
        self._preview_job = None
        self._row_seq = 0
        root.title(APP_TITLE)
        root.minsize(900, 640)
        self.inputs = []               # ttk widgets disabled while patching / before a game is chosen

        style = ttk.Style(root)
        style.configure("Big.TButton", font=("Segoe UI", 13, "bold"), padding=(16, 10))
        style.configure("Head.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Step.TLabel", font=("Segoe UI", 10, "bold"))

        outer = ttk.Frame(root, padding=12)
        outer.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        ttk.Label(outer, text=APP_TITLE, style="Head.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(outer, text="Make recoloured copies of a character in Mario Superstar Baseball (USA).").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))

        left = ttk.Frame(outer)
        left.grid(row=2, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        right = ttk.Frame(outer, padding=(16, 0, 0, 0))
        right.grid(row=2, column=1, sticky="n")

        # 1. game
        r = 0
        ttk.Label(left, text="1. Your game", style="Step.TLabel").grid(row=r, column=0, sticky="w")
        r += 1
        g = ttk.Frame(left)
        g.grid(row=r, column=0, sticky="ew")
        g.columnconfigure(2, weight=1)
        self.btn_iso = ttk.Button(g, text="Choose your game (.iso)…", command=self.choose_iso)
        self.btn_iso.grid(row=0, column=0, sticky="w")
        self.btn_dir = ttk.Button(g, text="or a folder…", command=self.choose_folder)
        self.btn_dir.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.game_path = tk.StringVar()
        ttk.Entry(g, textvariable=self.game_path, state="readonly").grid(row=0, column=2, sticky="ew", padx=(8, 0))
        self.status = tk.Label(left, text="No game chosen yet.", fg=GREY, anchor="w", justify="left", wraplength=560)
        self.status.grid(row=r + 1, column=0, sticky="ew", pady=(4, 10))
        r += 2

        # 2. source
        ttk.Label(left, text="2. Character to clone", style="Step.TLabel").grid(row=r, column=0, sticky="w")
        r += 1
        self.source_var = tk.StringVar(value=pretty_name(0))
        self.cb_source = ttk.Combobox(left, textvariable=self.source_var, state="readonly", width=24,
                                      values=[pretty_name(c) for c in range(cc.NUM_CHARS)])
        self.cb_source.grid(row=r, column=0, sticky="w", pady=(2, 10))
        self.cb_source.bind("<<ComboboxSelected>>", lambda e: self.source_changed())
        self.inputs.append(self.cb_source)
        r += 1

        # 3. clones
        ttk.Label(left, text="3. Clones (each one takes over a character slot)", style="Step.TLabel").grid(row=r, column=0, sticky="w")
        r += 1
        self.rows_frame = ttk.Frame(left)
        self.rows_frame.grid(row=r, column=0, sticky="ew")
        hdr = ttk.Frame(self.rows_frame)
        hdr.grid(row=0, column=0, sticky="w")
        ttk.Label(hdr, text="Preview", width=8).grid(row=0, column=0)
        ttk.Label(hdr, text="Slot it takes over", width=46).grid(row=0, column=1, sticky="w")
        ttk.Label(hdr, text="Colour").grid(row=0, column=2, sticky="w")
        self.no_game_hint = ttk.Label(self.rows_frame, text="Choose your game first.", foreground=GREY)
        self.no_game_hint.grid(row=1, column=0, sticky="w", pady=4)
        r += 1
        self.btn_add = ttk.Button(left, text="+ Add a clone", command=self.add_row)
        self.btn_add.grid(row=r, column=0, sticky="w", pady=(4, 10))
        self.inputs.append(self.btn_add)
        r += 1
        self.preview_row = tk.IntVar(value=-1)

        # 5. advanced (collapsed)
        self.adv_open = False
        self.btn_adv = ttk.Button(left, text="▸ Advanced options", command=self.toggle_advanced)
        self.btn_adv.grid(row=r, column=0, sticky="w")
        r += 1
        self.adv = ttk.Frame(left, padding=(16, 4, 0, 4))
        self.adv.grid(row=r, column=0, sticky="ew")
        self.adv_row = r
        self.ui_source = tk.BooleanVar(value=True)
        self.copy_stats = tk.BooleanVar(value=True)
        self.keep_work = tk.BooleanVar(value=False)
        self.keeplum = tk.DoubleVar(value=0.5)
        self.band = tk.StringVar(value=BAND_CHOICES[0][0])
        self.chk_ui = ttk.Checkbutton(self.adv, variable=self.ui_source)
        self.chk_ui.grid(row=0, column=0, columnspan=3, sticky="w")
        self.chk_stats = ttk.Checkbutton(self.adv, variable=self.copy_stats)
        self.chk_stats.grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Label(self.adv, text="Keep shading:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.scale = ttk.Scale(self.adv, from_=0.0, to=1.0, variable=self.keeplum, length=180,
                               command=lambda v: self.keeplum_changed())
        self.scale.grid(row=2, column=1, sticky="w", pady=(4, 0))
        self.keeplum_lbl = ttk.Label(self.adv, text="0.50")
        self.keeplum_lbl.grid(row=2, column=2, sticky="w", padx=(6, 0), pady=(4, 0))
        ttk.Label(self.adv, text="(higher keeps the light and dark areas of the original; lower is brighter and flatter)",
                  foreground=GREY).grid(row=3, column=0, columnspan=3, sticky="w")
        ttk.Label(self.adv, text="Colours to change:").grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.cb_band = ttk.Combobox(self.adv, textvariable=self.band, state="readonly", width=32,
                                    values=[b[0] for b in BAND_CHOICES])
        self.cb_band.grid(row=4, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self.cb_band.bind("<<ComboboxSelected>>", lambda e: self.schedule_preview())
        self.chk_keep = ttk.Checkbutton(self.adv, text="Keep the working folder (for troubleshooting)", variable=self.keep_work)
        self.chk_keep.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.inputs += [self.chk_ui, self.chk_stats, self.scale, self.cb_band, self.chk_keep]
        self.adv.grid_remove()
        self.update_source_labels()
        r += 1

        # 6. save + create
        ttk.Label(left, text="4. Save the new game as", style="Step.TLabel").grid(row=r, column=0, sticky="w", pady=(10, 0))
        r += 1
        s = ttk.Frame(left)
        s.grid(row=r, column=0, sticky="ew")
        s.columnconfigure(0, weight=1)
        self.out_path = tk.StringVar()
        self.ent_out = ttk.Entry(s, textvariable=self.out_path)
        self.ent_out.grid(row=0, column=0, sticky="ew")
        self.btn_out = ttk.Button(s, text="Save as…", command=self.choose_output)
        self.btn_out.grid(row=0, column=1, padx=(6, 0))
        self.inputs += [self.ent_out, self.btn_out]
        r += 1
        c = ttk.Frame(left)
        c.grid(row=r, column=0, sticky="ew", pady=(12, 0))
        c.columnconfigure(1, weight=1)
        self.btn_go = ttk.Button(c, text="Create patched game", style="Big.TButton", command=self.create)
        self.btn_go.grid(row=0, column=0, sticky="w")
        self.inputs.append(self.btn_go)
        self.progress = ttk.Progressbar(c, mode="indeterminate", length=200)
        self.progress.grid(row=0, column=1, sticky="ew", padx=(12, 0))
        r += 1

        # preview (right column)
        ttk.Label(right, text="Preview", style="Step.TLabel").grid(row=0, column=0, sticky="w")
        self.preview_caption = ttk.Label(right, text="", foreground=GREY, wraplength=280, justify="left")
        self.preview_caption.grid(row=1, column=0, sticky="w")
        ttk.Label(right, text="Before").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.img_before = tk.Label(right, width=256, height=256, bg="#dddddd", text="", relief="flat")
        self.img_before.grid(row=3, column=0, sticky="w")
        ttk.Label(right, text="After").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.img_after = tk.Label(right, width=256, height=256, bg="#dddddd", text="")
        self.img_after.grid(row=5, column=0, sticky="w")

        # log
        lf = ttk.Frame(outer)
        lf.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        lf.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        self.log_text = tk.Text(lf, height=7, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lf, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=sb.set)

        self.set_enabled(False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)

    # ---------------------------------------------------------------- helpers
    def set_enabled(self, on):
        for w in self.inputs + [w for row in self.rows for w in row["widgets"]]:
            try:
                w.state(["!disabled"] if on else ["disabled"])
            except Exception:
                pass
        for w in (self.btn_iso, self.btn_dir):
            w.state(["disabled"] if self.busy else ["!disabled"])

    def log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def source(self):
        names = [pretty_name(c) for c in range(cc.NUM_CHARS)]
        return names.index(self.source_var.get()) if self.source_var.get() in names else 0

    def band_value(self):
        return dict(BAND_CHOICES).get(self.band.get(), BAND_REDS)

    def spec_for(self, colour):
        return colour_spec(colour, round(self.keeplum.get(), 2), self.band_value())

    def update_source_labels(self):
        n = pretty_name(self.source())
        self.chk_ui.configure(text="Show clones as %s in menus, on the HUD and by the announcer (recommended)" % n)
        self.chk_stats.configure(text="Give clones %s's stats" % n)

    def toggle_advanced(self):
        self.adv_open = not self.adv_open
        if self.adv_open:
            self.adv.grid()
        else:
            self.adv.grid_remove()
        self.btn_adv.configure(text=("▾" if self.adv_open else "▸") + " Advanced options")

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
        self.status.configure(text="Checking the game…", fg=GREY)
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
        self.status.configure(text=("✔ " if ok else "✖ ") + msg, fg=GREEN if ok else RED)
        if not ok:
            self.set_enabled(False)
            return
        self.out_path.set(str(default_output(path)))
        self.rebuild_rows(reset=True)
        self.set_enabled(True)
        self.schedule_preview()

    # ---------------------------------------------------------------- rows
    def rebuild_rows(self, reset=False):
        src = self.source()
        self.choices = donor_choices(self.game.dol, src)
        valid = {c.id for c in self.choices}
        old = [] if reset else [(r["slot"], r["colour"]) for r in self.rows]
        for row in self.rows:
            row["frame"].destroy()
        self.rows = []
        self.no_game_hint.grid_remove()
        wanted = default_rows(self.choices) if reset or not old else []
        used = set()
        for slot, colour in old:
            if slot not in valid or slot in used:
                slot = next_free_slot(self.choices, used)
            if slot is not None:
                wanted.append((slot, colour))
                used.add(slot)
        for slot, colour in wanted:
            self.add_row(slot, colour)
        if self.rows:
            self.preview_row.set(self.rows[0]["id"])

    def add_row(self, slot=None, colour=None):
        if self.game is None:
            return
        used = {r["slot"] for r in self.rows}
        if slot is None:
            slot = next_free_slot(self.choices, used)
            if slot is None:
                messagebox.showinfo(APP_TITLE, "Every available slot already has a clone.")
                return
        if colour is None:
            taken = {r["colour"] for r in self.rows}
            colour = next((c for c in ["blue", "green", "purple", "yellow", "orange", "cyan", "pink", "red"] if c not in taken), "blue")
        self._row_seq += 1
        row = {"id": self._row_seq, "slot": slot, "colour": colour}
        f = ttk.Frame(self.rows_frame)
        f.grid(row=len(self.rows) + 1, column=0, sticky="w", pady=1)
        row["frame"] = f
        rb = ttk.Radiobutton(f, variable=self.preview_row, value=row["id"], command=self.schedule_preview)
        rb.grid(row=0, column=0, padx=(18, 18))
        labels = [c.label for c in self.choices]
        row["donor"] = tk.StringVar(value=self.choice(slot).label)
        cb = ttk.Combobox(f, textvariable=row["donor"], values=labels, state="readonly", width=50)
        cb.grid(row=0, column=1, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda e, row=row: self.donor_changed(row))
        row["colour_var"] = tk.StringVar(value=colour_label(colour))
        cc_ = ttk.Combobox(f, textvariable=row["colour_var"], values=COLOUR_CHOICES, state="readonly", width=16)
        cc_.grid(row=0, column=2, sticky="w", padx=(6, 0))
        cc_.bind("<<ComboboxSelected>>", lambda e, row=row: self.colour_changed(row))
        sw = tk.Label(f, width=3, bg=colour_swatch(colour), relief="solid", borderwidth=1)
        sw.grid(row=0, column=3, padx=(6, 0))
        row["swatch"] = sw
        rm = ttk.Button(f, text="Remove", width=8, command=lambda row=row: self.remove_row(row))
        rm.grid(row=0, column=4, padx=(6, 0))
        row["widgets"] = [rb, cb, cc_, rm]
        self.rows.append(row)
        if len(self.rows) == 1:
            self.preview_row.set(row["id"])
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
        row["swatch"].configure(bg=colour_swatch(row["colour"]))
        self.preview_row.set(row["id"])
        self.schedule_preview()

    def source_changed(self):
        self.update_source_labels()
        if self.game is not None:
            self.rebuild_rows()
            self.set_enabled(not self.busy)
            self.schedule_preview()

    def keeplum_changed(self):
        self.keeplum_lbl.configure(text="%.2f" % self.keeplum.get())
        self.schedule_preview()

    # ---------------------------------------------------------------- preview
    def schedule_preview(self):
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(150, self.render_preview)

    def render_preview(self):
        self._preview_job = None
        row = next((r for r in self.rows if r["id"] == self.preview_row.get()), None)
        if self.game is None or row is None:
            return
        try:
            pv = self.game.preview(self.source())
            spec = self.spec_for(row["colour"])
            before, after = pv.before_png, pv.after_png(spec)
        except BaseException as e:  # noqa: B902
            self.preview_caption.configure(text="No preview: %s" % friendly_error(e)[0])
            return
        zoom = max(1, 256 // max(pv.w, pv.h))
        imgs = []
        for lbl, png in ((self.img_before, before), (self.img_after, after)):
            img = tk.PhotoImage(data=png_base64(png))
            if zoom > 1:
                img = img.zoom(zoom)
            lbl.configure(image=img, width=img.width(), height=img.height())
            lbl.image = img            # keep a reference or Tk shows nothing
            imgs.append(img)
        self.preview_caption.configure(text="%s's main texture as %s (%s)" % (
            pretty_name(self.source()), self.choice(row["slot"]).name, colour_label(row["colour"])))

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
                   ui_source=self.ui_source.get(), copy_stats=self.copy_stats.get(), keep_work=self.keep_work.get())

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
            messagebox.showerror(APP_TITLE, friendly_error(e)[0])
            return
        if job.output_iso.exists() and not messagebox.askyesno(APP_TITLE, "%s already exists. Replace it?" % job.output_iso.name):
            return
        self.busy = True
        self.set_enabled(False)
        self.progress.start(12)
        self.log("---- creating %s" % job.output_iso)
        self.log("this takes a few minutes (copying 1.4 GB twice and packing the new files)")
        threading.Thread(target=self._job_worker, args=(job,), daemon=True).start()

    def _job_worker(self, job):
        try:
            result = run_job(job, lambda line: self.q.put(("log", line)))
            hint = pick_hint(self.game.dol, job.source, [s for s, _ in job.slots], job.ui_source)
            self.q.put(("done", result, hint))
        except BaseException as e:  # noqa: B902 - SystemExit in a thread would vanish silently
            self.q.put(("error",) + friendly_error(e))

    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.log(msg[1])
                elif kind == "game":
                    self.game_loaded(*msg[1:])
                elif kind == "done":
                    self.finish()
                    result, hint = msg[1], msg[2]
                    extra = "\n\nThe working folder was kept: %s" % result["work"] if result.get("work") else ""
                    messagebox.showinfo("Done!", "Saved to %s.\n\nOpen it in Dolphin or Project Rio: File → Open…\n\n%s%s"
                                        % (result["iso"], hint, extra))
                elif kind == "error":
                    self.finish()
                    message, details = msg[1], msg[2]
                    self.log("ERROR: " + message)
                    if details:
                        self.log("details:\n" + details)
                    messagebox.showerror("Couldn't create the patched game", message)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def finish(self):
        self.busy = False
        self.progress.stop()
        self.set_enabled(self.game is not None)

    def on_close(self):
        if self.busy and not messagebox.askyesno(APP_TITLE, "The game is still being made. Quit anyway?\n\n"
                                                 "A half-finished working folder named MSSB-Clone-Maker-work-… "
                                                 "will be left next to the output; you can delete it."):
            return
        if self.game is not None:
            self.game.close()
        self.root.destroy()


def _dpi_aware():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def run_gui():
    _import_tk()
    _dpi_aware()
    root = tk.Tk()
    App(root)
    root.mainloop()


def selftest(argv):
    """Build the window, optionally load a game and render a preview, then close.  Exit code 0 = OK.
    Used by the Windows build (the .exe) and for screenshots:  clone_gui.py --selftest [--game P] [--hold S]"""
    game = hold = None
    if "--game" in argv:
        game = argv[argv.index("--game") + 1]
    if "--hold" in argv:
        hold = float(argv[argv.index("--hold") + 1])
    try:
        _import_tk()
        root = tk.Tk()
        app = App(root)
        root.update()
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
