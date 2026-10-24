#!/usr/bin/env python3
"""clone_character.py -- data-only character cloning for Mario Superstar Baseball (GYQE01).

Copies one character (the *source*, e.g. Mario) onto other roster slots (the *donors*, e.g. the
Toad colours) with recoloured textures, by patching the disc tables in sys/main.dol, the stat block
and model files in files/ZZZZ.dat, and a few gameplay tables in files/game.rel.  No code is
recompiled; the only .text edit is an 8-byte immediate (the ARAM blob size, see below).

Python 3.8+, standard library only.  Works on Windows:  py clone_character.py --help

Input is a disc folder extracted by Dolphin (<dir>/sys/main.dol, <dir>/files/ZZZZ.dat, ...).
The input is never modified: it is copied to --out and the copy is patched.

How a clone works (all verified against the matched decompilation, see .agent/character_modding_report.md):

  * Per-character disc subfiles (DOL lbl_800F1D78, 54 x 19 rows): tracks 0/1 are the hi/lo detail
    body .gpc files, tracks 2..18 animation banks and the skeleton.  A clone slot S gets the source's
    rows for 2..18 (that is exactly how the retail colour variants work) and two new, recoloured
    body files appended to ZZZZ.dat for tracks 0/1 (LZSS packed, 32-byte aligned).
  * Master descriptors (lbl_800EFD38, 516 rows, offsets inside the 4.9 MB "section 2" blob that is
    streamed into ARAM at boot): sub-items 54+S*7+k and the skeleton 432+S alias the source's rows.
  * In-game bodies come from ARAM: lbl_800F73B8[charID] = {bodyEntry, overlayEntry}.  The overlay is
    a replacement texture archive decompressed straight over the body's texture archive
    (fn_8002254C).  A clone gets {source body, O} where O is a master entry that only the donor used,
    and master[O] points at a recoloured archive appended to the blob.  Because other data follows
    the blob on disc, the whole blob is moved to the end of ZZZZ.dat; master[515] (the blob
    descriptor) and the hard-coded ARAM allocation size in fn_800227E4 are updated to match.
  * lbl_800EEAAC (glove bone list), lbl_800EEB18 (stadium-object model map), lbl_800F743C
    (sub-item row kind), lbl_800F5D98 (disc-path sub-item files), jumptable_800EEC40 (per-charID
    setup switch, cases 0..47) and lbl_800FB798 (15 per-character s16 animation parameters) are
    copied from the source.
  * Stats: the 54 x 0xA0 CharacterStats block at ZZZZ.dat+0x06CF8800 (loaded by menus.rel at boot).
    The clone gets the source's stat row (CharID stays S) and chemistry column/row.
  * game.rel .data: voice SFX base, batter hitbox, charge frames, runner timing and two more
    per-character tables are copied so the clone plays like the source.

What stays the donor's: menu name, portrait, announcer call-out, family/colour grouping on the
character-select grid (lbl_800E8558 / lbl_80108EDC).  Picking the donor in the menus gives you the
clone on the field.
"""
import argparse
import colorsys
import shutil
import struct
import sys
from pathlib import Path

# ----------------------------------------------------------------------------------------------
# Constants (RAM addresses in main.dol; everything is resolved through the DOL section table)
# ----------------------------------------------------------------------------------------------
NUM_CHARS = 54
SUBFILE_TRACKS = 19
SUBITEMS_PER_CHAR = 7
MASTER_SUBITEM_BASE = 54
MASTER_SKELETON_BASE = 432
MASTER_BLOB_ENTRY = 515

ADDR_SUBFILES = 0x800F1D78       # DiskFileDesc[54*19]
ADDR_MASTER = 0x800EFD38         # DiskFileDesc[516] (515 = lbl_800F1D68, the blob descriptor)
ADDR_PAIRS = 0x800F73B8          # s8[54][2] {bodyEntry, overlayEntry|-1}
ADDR_ROWKIND = 0x800F743C        # u8[54]
ADDR_SUBITEM_FILES = 0x800F5D98  # DiskFileDesc[54*6]
ADDR_GLOVE = 0x800EEAAC          # u16[54]
ADDR_MODELMAP = 0x800EEB18       # s16[54]
ADDR_JUMPTABLE = 0x800EEC40      # u32[48]
ADDR_ANIMPARAMS = 0x800FB798     # s16[15][54]
ADDR_FAMILY = 0x800E8558         # u8[54][6] {family, baseID, uniqueIdx, isBase, isCaptain, colour}
ADDR_BLOB_LIS = 0x800227EC       # fn_800227E4: lis  r3, 0x4d      (3C 60 00 4D)
ADDR_BLOB_ADDI = 0x800227F4      # fn_800227E4: addi r3, r3, 0x6600 (38 63 66 00)
JUMPTABLE_CASES = 48
ANIMPARAM_TABLES = 15

ZZZZ_STATS_OFFSET = 0x06CF8800   # CharacterStats[54], 0xA0 each (menus.rel lbl_2_data_C8)
STATS_ROW = 0xA0
STATS_CHARID = 0x24
STATS_CHEM = 0x3B

LZSS_FLAGS = 0x0000040B          # compressionConstants: repetition bits 4, look-back bits 11
LZSS_LOOKBACK_BITS = 11
LZSS_REPEAT_BITS = 4
SIZE_COMPRESSED_BIT = 0x40000000

# game.rel .data tables (section-relative offsets), indexed by charID
REL_VOICE = (0x8168, 2)          # u16 voice SFX base
REL_TABLES = [                   # (offset, row size)
    (0x6EF8, 0x24),              # BatterHitbox
    (0x7690, 2),                 # charge frames
    (0x7870, 6),                 # runner timing
    (0x7E34, 4),
    (0x17000, 4),
]
REL_VOICE_SIGNATURE = (324, 311, 298, 285)  # Mario, Luigi, DK, Diddy

CHAR_NAMES = [
    "Mario", "Luigi", "DK", "Diddy", "Peach", "Daisy", "Yoshi", "BabyMario", "BabyLuigi", "Bowser",
    "Wario", "Waluigi", "RedKoopa", "RedToad", "Boo", "Toadette", "RedShyGuy", "Birdo", "Monty",
    "BowserJr", "RedParatroopa", "BluePianta", "RedPianta", "YellowPianta", "BlueNoki", "RedNoki",
    "GreenNoki", "HammerBro", "Toadsworth", "BlueToad", "YellowToad", "GreenToad", "PurpleToad",
    "BlueMagikoopa", "RedMagikoopa", "GreenMagikoopa", "YellowMagikoopa", "KingBoo", "Petey",
    "Dixie", "Goomba", "Paragoomba", "GreenKoopa", "GreenParatroopa", "BlueShyGuy", "YellowShyGuy",
    "GreenShyGuy", "BlackShyGuy", "GrayDryBones", "GreenDryBones", "RedDryBones", "BlueDryBones",
    "FireBro", "BoomerangBro",
]
FAMILY_NAMES = ["-", "Koopa", "Toad", "ShyGuy", "Paratroopa", "Pianta", "Noki", "Bros", "Magikoopa", "DryBones"]
COLOUR_PRESETS = {
    "red": 0.0, "orange": 0.08, "yellow": 0.16, "green": 0.33, "cyan": 0.5, "blue": 0.62,
    "purple": 0.76, "pink": 0.9,
}


def norm_name(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def parse_char(s):
    s = s.strip()
    if s.isdigit():
        v = int(s)
        if not 0 <= v < NUM_CHARS:
            raise SystemExit("character id out of range: %d" % v)
        return v
    key = norm_name(s)
    for i, n in enumerate(CHAR_NAMES):
        if norm_name(n) == key:
            return i
    raise SystemExit("unknown character %r (use --list-slots)" % s)


def align(v, a):
    return (v + a - 1) & ~(a - 1)


# ----------------------------------------------------------------------------------------------
# Namco LZSS (DecompressDiskData / ReadBits): 32-bit big-endian words, bits consumed LSB first.
# token: 1 bit flag; 1 -> 8-bit literal; 0 -> (lookback-1 : 11 bits), (length-2 : 4 bits)
# ----------------------------------------------------------------------------------------------
class LZSSError(Exception):
    pass


def lzss_decompress(data, size, lb_bits=LZSS_LOOKBACK_BITS, rp_bits=LZSS_REPEAT_BITS):
    """Expand exactly `size` bytes; returns (bytes, words_consumed*4)."""
    out = bytearray()
    pos = 0
    buf = 0
    nbits = 0
    n = len(data)

    def read_bits(cnt):
        nonlocal pos, buf, nbits
        if nbits >= cnt:
            v = buf & ((1 << cnt) - 1)
            buf >>= cnt
            nbits -= cnt
            return v
        need = cnt - nbits
        v = (buf << need) & 0xFFFFFFFF
        if pos + 4 > n:
            raise LZSSError("compressed stream exhausted")
        w = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        rem = 32 - need
        v |= ((w << rem) & 0xFFFFFFFF) >> rem
        nbits = rem
        buf = w >> need
        return v

    left = size
    while left > 0:
        if read_bits(1):
            out.append(read_bits(8))
            left -= 1
        else:
            back = read_bits(lb_bits) + 1
            cnt = read_bits(rp_bits) + 2
            left -= cnt
            if back > len(out) or left < 0:
                raise LZSSError("bad back-reference")
            for _ in range(cnt):
                out.append(out[-back])
    return bytes(out), pos


class _BitWriter:
    __slots__ = ("words", "cur", "n")

    def __init__(self):
        self.words = []
        self.cur = 0
        self.n = 0

    def write(self, value, cnt):
        value &= (1 << cnt) - 1
        space = 32 - self.n
        if cnt <= space:
            self.cur |= value << self.n
            self.n += cnt
            if self.n == 32:
                self.words.append(self.cur)
                self.cur = 0
                self.n = 0
        else:
            # ReadBits refills by taking the high `space` bits of the value from the old word and the
            # low (cnt-space) bits from the new word.
            low = cnt - space
            self.cur |= (value >> low) << self.n
            self.words.append(self.cur)
            self.cur = value & ((1 << low) - 1)
            self.n = low

    def finish(self):
        if self.n:
            self.words.append(self.cur)
        return b"".join(struct.pack(">I", w) for w in self.words)


def lzss_compress(data, lb_bits=LZSS_LOOKBACK_BITS, rp_bits=LZSS_REPEAT_BITS, max_chain=48):
    """Greedy hash-chain encoder producing a stream DecompressDiskData accepts.  Output is padded
    with zero bytes to a multiple of 32 (the loader rounds reads up to 32 bytes anyway)."""
    data = bytes(data)
    n = len(data)
    bw = _BitWriter()
    max_back = 1 << lb_bits
    max_len = (1 << rp_bits) - 1 + 2
    heads = {}
    pos = 0
    while pos < n:
        best_len = 0
        best_back = 0
        if pos + 2 <= n:
            key = data[pos:pos + 2]
            chain = heads.get(key)
            if chain:
                limit = min(max_len, n - pos)
                tries = 0
                for cand in reversed(chain):
                    if pos - cand > max_back:
                        break
                    tries += 1
                    if tries > max_chain:
                        break
                    l = 2
                    while l < limit and data[cand + l] == data[pos + l]:
                        l += 1
                    if l > best_len:
                        best_len = l
                        best_back = pos - cand
                        if l == limit:
                            break
        if best_len >= 2:
            bw.write(0, 1)
            bw.write(best_back - 1, lb_bits)
            bw.write(best_len - 2, rp_bits)
            step = best_len
        else:
            bw.write(1, 1)
            bw.write(data[pos], 8)
            step = 1
        for p in range(pos, pos + step):
            if p + 2 <= n:
                k = data[p:p + 2]
                lst = heads.get(k)
                if lst is None:
                    heads[k] = [p]
                else:
                    lst.append(p)
                    if len(lst) > 256:
                        del lst[:128]
        pos += step
    out = bw.finish()
    pad = (-len(out)) % 32
    return out + b"\0" * pad


# ----------------------------------------------------------------------------------------------
# DOL
# ----------------------------------------------------------------------------------------------
class Dol:
    def __init__(self, data):
        self.data = bytearray(data)
        offs = struct.unpack(">18I", self.data[0:0x48])
        addrs = struct.unpack(">18I", self.data[0x48:0x90])
        sizes = struct.unpack(">18I", self.data[0x90:0xD8])
        self.sections = [(offs[i], addrs[i], sizes[i]) for i in range(18) if sizes[i]]

    def off(self, ram, size=1):
        for o, a, s in self.sections:
            if a <= ram and ram + size <= a + s:
                return o + (ram - a)
        raise ValueError("RAM address 0x%08X not inside any DOL section" % ram)

    def u8(self, ram):
        return self.data[self.off(ram)]

    def u16(self, ram):
        return struct.unpack_from(">H", self.data, self.off(ram, 2))[0]

    def s16(self, ram):
        return struct.unpack_from(">h", self.data, self.off(ram, 2))[0]

    def u32(self, ram):
        return struct.unpack_from(">I", self.data, self.off(ram, 4))[0]

    def read(self, ram, n):
        o = self.off(ram, n)
        return bytes(self.data[o:o + n])

    def write(self, ram, b):
        o = self.off(ram, len(b))
        self.data[o:o + len(b)] = b

    def desc(self, table, idx):
        return struct.unpack(">4I", self.read(table + idx * 16, 16))

    def set_desc(self, table, idx, d):
        self.write(table + idx * 16, struct.pack(">4I", *d))

    def pair(self, c):
        return struct.unpack(">2b", self.read(ADDR_PAIRS + c * 2, 2))

    def set_pair(self, c, body, overlay):
        self.write(ADDR_PAIRS + c * 2, struct.pack(">2b", body, overlay))

    def family(self, c):
        return tuple(self.read(ADDR_FAMILY + c * 6, 6))

    def blob_alloc_size(self):
        lis = self.u32(ADDR_BLOB_LIS)
        addi = self.u32(ADDR_BLOB_ADDI)
        if (lis >> 16) != 0x3C60 or (addi >> 16) != 0x3863:
            raise ValueError("fn_800227E4 ARAM allocation instructions not found (patched DOL?)")
        hi = lis & 0xFFFF
        lo = addi & 0xFFFF
        if lo & 0x8000:
            lo -= 0x10000
        return ((hi << 16) + lo) & 0xFFFFFFFF

    def set_blob_alloc_size(self, size):
        lo = size & 0xFFFF
        hi = (size >> 16) & 0xFFFF
        if lo & 0x8000:
            hi = (hi + 1) & 0xFFFF   # @ha carry: addi sign-extends its immediate
        self.write(ADDR_BLOB_LIS, struct.pack(">I", 0x3C600000 | hi))
        self.write(ADDR_BLOB_ADDI, struct.pack(">I", 0x38630000 | lo))


# ----------------------------------------------------------------------------------------------
# .gpc model files
# ----------------------------------------------------------------------------------------------
class Gpc:
    """Root header {texture, layout, geo, skn, extra} (file-relative offsets), texture archive
    {u16 count; u16 pad; TextureHeader[count] (0x20 each)} with archive-relative data offsets."""

    def __init__(self, data):
        self.data = bytearray(data)
        self.texture, self.layout, self.geo, self.skn, self.extra = struct.unpack_from(">5I", self.data, 0)
        if self.texture == 0 or self.layout == 0 or self.layout <= self.texture:
            raise ValueError("not a character .gpc (root header %08X %08X)" % (self.texture, self.layout))
        self.tex_count = struct.unpack_from(">H", self.data, self.texture)[0]
        self.textures = []
        for i in range(self.tex_count):
            h = self.texture + 4 + i * 0x20
            data_off, clut_off, height, width = struct.unpack_from(">IIHH", self.data, h)
            fmt = self.data[h + 0x17]
            self.textures.append({"index": i, "hdr": h, "data": self.texture + data_off,
                                  "clut": clut_off, "w": width, "h": height, "fmt": fmt})
        for t in self.textures:
            if t["fmt"] != 14:
                raise ValueError("texture %d is format %d, only CMPR (14) is supported" % (t["index"], t["fmt"]))
            t["size"] = t["w"] * t["h"] // 2
            if t["data"] + t["size"] > self.layout:
                raise ValueError("texture %d runs past the archive" % t["index"])

    @property
    def archive_size(self):
        return self.layout - self.texture

    def archive_bytes(self):
        return bytes(self.data[self.texture:self.layout])

    def layout_info(self):
        v, actor_id, total_bones, hier_off, hier_root, pal, skin_id = struct.unpack_from(">IHHIIIH", self.data, self.layout)
        return {"totalBones": total_bones, "skinFileID": skin_id}

    def geo_info(self):
        version, uds, ud, num, arr = struct.unpack_from(">5I", self.data, self.geo)
        return {"numDescriptors": num}

    def check(self):
        li = self.layout_info()
        gi = self.geo_info()
        if li["skinFileID"] != 0xFFFF and li["skinFileID"] >= gi["numDescriptors"]:
            raise ValueError("layout skinFileID %d >= numDescriptors %d (this is the 0x800233A0 crash)" %
                             (li["skinFileID"], gi["numDescriptors"]))
        if not (self.texture < self.layout < self.geo < self.skn <= len(self.data)):
            raise ValueError("root header offsets are not ordered")
        return li, gi


# ----------------------------------------------------------------------------------------------
# CMPR endpoint recolouring
# ----------------------------------------------------------------------------------------------
def rgb565_to_rgb(c):
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def rgb_to_rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


class Recolour:
    """Spec: comma-separated key=value.  Keys:
        hue=<0..1|preset>  target hue for the matched band (default band: reds, i.e. Mario's suit)
        from=<0..1> width=<0..1>  hue band to retarget (default from=0 width=0.05: Mario's red, not his skin)
        minsat=<0..1>      minimum saturation for a colour to be considered part of the band (0.5)
        all=1              retarget every saturated colour, not just the band
        sat=<mult> val=<mult>  multiply saturation / brightness (applied to matched colours)
        tint=RRGGBB[:strength]  blend every colour towards RRGGBB (strength 0..1, default 0.5)
        solid=RRGGBB       replace every colour (debug)
        tex=0/1/2          only touch these texture indices, slash-separated (default: all)
    A bare preset name (blue, green, ...) is shorthand for hue=<preset>."""

    def __init__(self, spec):
        self.hue = None
        self.band_from = 0.0
        self.band_width = 0.05
        self.minsat = 0.5
        self.all = False
        self.sat = 1.0
        self.val = 1.0
        self.tint = None
        self.tint_strength = 0.5
        self.solid = None
        self.tex = None
        self.spec = spec
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                if part.lower() in COLOUR_PRESETS:
                    self.hue = COLOUR_PRESETS[part.lower()]
                    continue
                raise SystemExit("bad recolour token %r" % part)
            k, v = part.split("=", 1)
            k = k.strip().lower()
            v = v.strip()
            if k == "hue":
                self.hue = COLOUR_PRESETS[v.lower()] if v.lower() in COLOUR_PRESETS else float(v)
            elif k == "from":
                self.band_from = float(v)
            elif k == "width":
                self.band_width = float(v)
            elif k == "minsat":
                self.minsat = float(v)
            elif k == "all":
                self.all = v not in ("0", "false", "no")
            elif k == "sat":
                self.sat = float(v)
            elif k == "val":
                self.val = float(v)
            elif k == "tint":
                col, _, strength = v.partition(":")
                self.tint = int(col, 16)
                if strength:
                    self.tint_strength = float(strength)
            elif k == "solid":
                self.solid = int(v, 16)
            elif k == "tex":
                self.tex = set(int(x) for x in v.split("/") if x)
            else:
                raise SystemExit("unknown recolour key %r" % k)
        self._cache = {}

    def _in_band(self, h, s):
        if s < self.minsat:
            return False
        if self.all:
            return True
        d = abs((h - self.band_from + 0.5) % 1.0 - 0.5)
        return d <= self.band_width

    def map565(self, c):
        r = self._cache.get(c)
        if r is not None:
            return r
        if self.solid is not None:
            out = rgb_to_rgb565((self.solid >> 16) & 0xFF, (self.solid >> 8) & 0xFF, self.solid & 0xFF)
            self._cache[c] = out
            return out
        r8, g8, b8 = rgb565_to_rgb(c)
        h, s, v = colorsys.rgb_to_hsv(r8 / 255.0, g8 / 255.0, b8 / 255.0)
        if self._in_band(h, s):
            if self.hue is not None:
                h = self.hue % 1.0
            s = min(1.0, s * self.sat)
            v = min(1.0, v * self.val)
        rf, gf, bf = colorsys.hsv_to_rgb(h, s, v)
        if self.tint is not None:
            t = self.tint_strength
            rf = rf * (1 - t) + ((self.tint >> 16) & 0xFF) / 255.0 * t
            gf = gf * (1 - t) + ((self.tint >> 8) & 0xFF) / 255.0 * t
            bf = bf * (1 - t) + (self.tint & 0xFF) / 255.0 * t
        out = rgb_to_rgb565(int(round(rf * 255)), int(round(gf * 255)), int(round(bf * 255)))
        self._cache[c] = out
        return out


def _swap01(idx):
    """Swap palette indices 0<->1 in a CMPR index word (keeps 2 and 3)."""
    out = 0
    for i in range(16):
        v = (idx >> (i * 2)) & 3
        if v < 2:
            v ^= 1
        out |= v << (i * 2)
    return out


def recolour_block(block, rc):
    c0, c1, idx = struct.unpack(">HHI", block)
    n0 = rc.map565(c0)
    n1 = rc.map565(c1)
    four = c0 > c1
    if four:
        if n0 == n1:
            # keep 4-colour mode: nudge the second endpoint down one blue step (or the first up)
            if n1 > 0:
                n1 -= 1
            else:
                n0 += 1
        if n0 < n1:
            n0, n1 = n1, n0
            idx ^= 0x55555555          # 0<->1, 2<->3
    else:
        if n0 > n1:
            n0, n1 = n1, n0
            idx = _swap01(idx)         # 0<->1, 2 (midpoint) and 3 (transparent) unchanged
    return struct.pack(">HHI", n0, n1, idx)


def recolour_gpc(gpc, rc):
    """Recolour the CMPR endpoints of every texture in place; headers and geometry untouched."""
    d = gpc.data
    for t in gpc.textures:
        if rc.tex is not None and t["index"] not in rc.tex:
            continue
        start, end = t["data"], t["data"] + t["size"]
        for o in range(start, end, 8):
            d[o:o + 8] = recolour_block(bytes(d[o:o + 8]), rc)


# ----------------------------------------------------------------------------------------------
# Disc folder
# ----------------------------------------------------------------------------------------------
class Disc:
    def __init__(self, root):
        self.root = Path(root)
        self.sys = self.root / "sys"
        self.files = self.root / "files"
        for p in (self.sys / "main.dol", self.files / "ZZZZ.dat", self.files / "game.rel", self.sys / "fst.bin"):
            if not p.exists():
                raise SystemExit("not an extracted GameCube disc folder (missing %s)" % p)

    def read_zzzz(self, off, size):
        with open(self.files / "ZZZZ.dat", "rb") as f:
            f.seek(off)
            return f.read(size)

    def zzzz_size(self):
        return (self.files / "ZZZZ.dat").stat().st_size


def load_subfile(disc, dol, idx):
    flags, sizeflags, off, comp = dol.desc(ADDR_SUBFILES, idx)
    raw = disc.read_zzzz(off, comp)
    if sizeflags & SIZE_COMPRESSED_BIT:
        return lzss_decompress(raw, sizeflags & 0x0FFFFFFF)[0]
    return raw[:sizeflags & 0x0FFFFFFF]


def load_master(disc, dol, idx):
    """Master entries live inside the blob (master[515])."""
    bflags, bsize, boff, bcomp = dol.desc(ADDR_MASTER, MASTER_BLOB_ENTRY)
    flags, sizeflags, off, comp = dol.desc(ADDR_MASTER, idx)
    raw = disc.read_zzzz(boff + off, comp)
    if sizeflags & SIZE_COMPRESSED_BIT:
        return lzss_decompress(raw, sizeflags & 0x0FFFFFFF)[0]
    return raw[:sizeflags & 0x0FFFFFFF]


def effective_body(disc, dol, c):
    """The hi-detail body the game shows for charID c: master[pair.body] with pair.overlay applied."""
    body, overlay = dol.pair(c)
    data = bytearray(load_master(disc, dol, body))
    if overlay != -1:
        ov = load_master(disc, dol, overlay)
        tex_off = struct.unpack_from(">I", data, 0)[0]
        data[tex_off:tex_off + len(ov)] = ov
    return bytes(data)


def private_entry(dol, s):
    """Master body-tier entry that only slot s references (its overlay, else its body), or None."""
    body, overlay = dol.pair(s)
    if overlay != -1:
        users = [c for c in range(NUM_CHARS) if overlay in dol.pair(c)]
        return overlay if users == [s] else None
    users = [c for c in range(NUM_CHARS) if body in dol.pair(c)]
    return body if users == [s] else None


def rel_data_section(rel):
    """Locate game.rel's .data section by its section table + the voice-table signature."""
    n = struct.unpack_from(">I", rel, 0x0C)[0]
    so = struct.unpack_from(">I", rel, 0x10)[0]
    for i in range(n):
        o, s = struct.unpack_from(">II", rel, so + i * 8)
        if s == 0 or (o & 1) or o == 0:
            continue
        base = o & ~1
        if base + REL_VOICE[0] + 8 <= len(rel):
            sig = struct.unpack_from(">4H", rel, base + REL_VOICE[0])
            if sig == REL_VOICE_SIGNATURE:
                return base, s
    raise SystemExit("game.rel: .data section with the voice table not found (already patched or wrong file?)")


# ----------------------------------------------------------------------------------------------
# FST / ISO
# ----------------------------------------------------------------------------------------------
def fst_entries(fst):
    count = struct.unpack_from(">I", fst, 8)[0]
    strings = fst[count * 12:]
    out = []
    for i in range(count):
        t = fst[i * 12]
        name_off = struct.unpack_from(">I", fst, i * 12)[0] & 0xFFFFFF
        a, b = struct.unpack_from(">II", fst, i * 12 + 4)
        name = "" if i == 0 else strings[name_off:strings.index(b"\0", name_off)].decode("ascii", "replace")
        out.append({"index": i, "dir": t == 1, "name": name, "offset": a, "size": b, "parent": a, "next": b})
    return out


def fst_paths(fst):
    """Yield (entry, relative path) for every file in the FST."""
    ents = fst_entries(fst)
    stack = []  # (end index, name)
    for e in ents[1:]:
        while stack and e["index"] >= stack[-1][0]:
            stack.pop()
        if e["dir"]:
            stack.append((e["next"], e["name"]))
        else:
            yield e, "/".join([s[1] for s in stack] + [e["name"]])


def patch_fst_size(fst, name, new_size):
    fst = bytearray(fst)
    for e, path in fst_paths(fst):
        if path == name:
            struct.pack_into(">I", fst, e["index"] * 12 + 8, new_size)
            return bytes(fst)
    raise SystemExit("%s not in fst.bin" % name)


def write_iso(disc_out, iso_path, log):
    sysd = disc_out.sys
    boot = (sysd / "boot.bin").read_bytes()
    bi2 = (sysd / "bi2.bin").read_bytes()
    apl = (sysd / "apploader.img").read_bytes()
    dol = (sysd / "main.dol").read_bytes()
    fst = (sysd / "fst.bin").read_bytes()
    dol_off, fst_off, fst_size = struct.unpack_from(">III", boot, 0x420)
    end = 0
    with open(iso_path, "wb") as f:
        f.write(boot)
        f.seek(0x440)
        f.write(bi2)
        f.seek(0x2440)
        f.write(apl)
        f.seek(dol_off)
        f.write(dol)
        f.seek(fst_off)
        f.write(fst)
        for e, path in fst_paths(fst):
            src = disc_out.files / path
            size = src.stat().st_size
            if size != e["size"]:
                raise SystemExit("fst.bin says %s is %d bytes but the file is %d" % (path, e["size"], size))
            f.seek(e["offset"])
            with open(src, "rb") as g:
                shutil.copyfileobj(g, f, 1 << 20)
            end = max(end, e["offset"] + size)
        end = align(end, 0x8000)
        f.truncate(end)
    log("wrote %s (%d bytes)" % (iso_path, end))


# ----------------------------------------------------------------------------------------------
# Listing
# ----------------------------------------------------------------------------------------------
def list_slots(disc):
    dol = Dol((disc.sys / "main.dol").read_bytes())
    default_case = dol.u32(ADDR_JUMPTABLE)  # Mario's entry is the switch default
    print("%3s %-16s %-10s %-5s %-10s %-8s %-8s %s" % ("id", "name", "family", "base", "pair", "private", "setup", "notes"))
    for c in range(NUM_CHARS):
        fam = dol.family(c)
        body, ov = dol.pair(c)
        pe = private_entry(dol, c)
        case = "-" if c >= JUMPTABLE_CASES or dol.u32(ADDR_JUMPTABLE + c * 4) == default_case else "yes"
        notes = []
        if pe is None:
            notes.append("body shared with its colour variants: donor only with --no-aram-overlay")
        if fam[0]:
            notes.append("colour %d of %s (auto-assigned when several %s are picked)" % (fam[5] + 1, FAMILY_NAMES[fam[0]], FAMILY_NAMES[fam[0]]))
        print("%3d %-16s %-10s %-5s %-10s %-8s %-8s %s" % (
            c, CHAR_NAMES[c], FAMILY_NAMES[fam[0]], "yes" if fam[3] else "", "%d,%d" % (body, ov),
            "" if pe is None else str(pe), case, "; ".join(notes)))


# ----------------------------------------------------------------------------------------------
# Cloning
# ----------------------------------------------------------------------------------------------
def parse_slot_spec(spec):
    slot, _, rc = spec.partition(":")
    return parse_char(slot), Recolour(rc if rc else "hue=blue")


def clone(args):
    log = print
    src_disc = Disc(args.disc)
    out = Path(args.out)
    if out.exists():
        if not args.force:
            raise SystemExit("%s exists; use --force to overwrite it" % out)
        shutil.rmtree(out)
    log("copying %s -> %s (this includes the 450 MB ZZZZ.dat)" % (src_disc.root, out))
    shutil.copytree(src_disc.root, out)
    disc = Disc(out)

    dol = Dol((disc.sys / "main.dol").read_bytes())
    C = parse_char(args.source)
    clones = [parse_slot_spec(s) for s in args.slot]
    if not clones:
        clones = [(29, Recolour("hue=blue")), (30, Recolour("hue=green")), (31, Recolour("hue=purple"))]
    slots = [s for s, _ in clones]
    if len(set(slots)) != len(slots):
        raise SystemExit("duplicate donor slot")
    if C in slots:
        raise SystemExit("the source cannot be one of its own donors")
    use_overlay = not args.no_aram_overlay
    default_case = dol.u32(ADDR_JUMPTABLE)
    src_case = dol.u32(ADDR_JUMPTABLE + C * 4) if C < JUMPTABLE_CASES else default_case

    # --- validate donors
    for S, rc in clones:
        if use_overlay and private_entry(dol, S) is None:
            raise SystemExit("slot %d (%s) shares its ARAM body with other slots; pick a colour variant "
                             "(Toads 29-32, Shy Guys 44-47, ...) or a stand-alone character, or use --no-aram-overlay"
                             % (S, CHAR_NAMES[S]))
        if S >= JUMPTABLE_CASES and src_case != default_case:
            raise SystemExit("slot %d is outside the per-character setup switch (0..47) but %s needs its own case"
                             % (S, CHAR_NAMES[C]))

    # --- source data
    log("source: %d %s" % (C, CHAR_NAMES[C]))
    src_tracks = [Gpc(load_subfile(disc, dol, C * SUBFILE_TRACKS + t)) for t in (0, 1)]
    src_body = Gpc(effective_body(disc, dol, C))
    for g in src_tracks + [src_body]:
        g.check()
    log("  track0 %d B, track1 %d B, ARAM body %d B, texture archive %d B (%d textures)" % (
        len(src_tracks[0].data), len(src_tracks[1].data), len(src_body.data), src_body.archive_size, src_body.tex_count))

    # --- new disc data: appended to ZZZZ.dat
    zz_path = disc.files / "ZZZZ.dat"
    append_at = align(disc.zzzz_size(), 0x800)
    chunks = []          # (offset, bytes)
    cursor = append_at

    def place(b, al=0x20):
        nonlocal cursor
        cursor = align(cursor, al)
        o = cursor
        chunks.append((o, b))
        cursor += len(b)
        return o

    blob_desc = dol.desc(ADDR_MASTER, MASTER_BLOB_ENTRY)
    blob = bytearray(disc.read_zzzz(blob_desc[2], blob_desc[3])) if use_overlay else None
    blob_used = len(blob) if blob is not None else 0
    report = []

    for S, rc in clones:
        log("clone -> slot %d %s  (%s)" % (S, CHAR_NAMES[S], rc.spec))
        # subfile rows: tracks 2..18 alias the source
        for t in range(2, SUBFILE_TRACKS):
            dol.set_desc(ADDR_SUBFILES, S * SUBFILE_TRACKS + t, dol.desc(ADDR_SUBFILES, C * SUBFILE_TRACKS + t))
        # tracks 0/1: recoloured copies
        for t in (0, 1):
            g = Gpc(bytes(src_tracks[t].data))
            recolour_gpc(g, rc)
            g.check()
            comp = lzss_compress(g.data)
            chk, used = lzss_decompress(comp, len(g.data))
            if chk != bytes(g.data):
                raise SystemExit("internal error: LZSS round trip failed")
            off = place(comp)
            dol.set_desc(ADDR_SUBFILES, S * SUBFILE_TRACKS + t, (LZSS_FLAGS, SIZE_COMPRESSED_BIT | len(g.data), off, len(comp)))
            log("  track %d -> ZZZZ.dat+0x%08X (%d -> %d B)" % (t, off, len(g.data), len(comp)))
        # master descriptors: sub-items and skeleton alias the source
        for k in range(SUBITEMS_PER_CHAR):
            dol.set_desc(ADDR_MASTER, MASTER_SUBITEM_BASE + S * SUBITEMS_PER_CHAR + k,
                         dol.desc(ADDR_MASTER, MASTER_SUBITEM_BASE + C * SUBITEMS_PER_CHAR + k))
        dol.set_desc(ADDR_MASTER, MASTER_SKELETON_BASE + S, dol.desc(ADDR_MASTER, MASTER_SKELETON_BASE + C))
        # ARAM body: source body + recoloured overlay in the donor's private master entry
        src_body_entry = dol.pair(C)[0]
        if use_overlay:
            O = private_entry(dol, S)
            g = Gpc(bytes(src_body.data))
            recolour_gpc(g, rc)
            archive = g.archive_bytes()
            comp = lzss_compress(archive)
            if lzss_decompress(comp, len(archive))[0] != archive:
                raise SystemExit("internal error: LZSS round trip failed")
            blob_used = align(blob_used, 0x20)
            blob[len(blob):] = b"\0" * (blob_used - len(blob))
            blob += comp
            dol.set_desc(ADDR_MASTER, O, (LZSS_FLAGS, SIZE_COMPRESSED_BIT | len(archive), blob_used, len(comp)))
            blob_used += len(comp)
            dol.set_pair(S, src_body_entry, O)
            log("  ARAM overlay -> master[%d] blob+0x%08X (%d -> %d B); pair = {%d, %d}" % (O, blob_used - len(comp), len(archive), len(comp), src_body_entry, O))
        else:
            dol.set_pair(S, *dol.pair(C))
            log("  ARAM body = source's (no recolour in the ARAM path)")
        # small per-character tables
        dol.write(ADDR_GLOVE + S * 2, dol.read(ADDR_GLOVE + C * 2, 2))
        dol.write(ADDR_MODELMAP + S * 2, dol.read(ADDR_MODELMAP + C * 2, 2))
        dol.write(ADDR_ROWKIND + S, dol.read(ADDR_ROWKIND + C, 1))
        for k in range(6):
            dol.set_desc(ADDR_SUBITEM_FILES, S * 6 + k, dol.desc(ADDR_SUBITEM_FILES, C * 6 + k))
        if S < JUMPTABLE_CASES:
            dol.write(ADDR_JUMPTABLE + S * 4, struct.pack(">I", src_case))
        if not args.no_gameplay:
            for k in range(ANIMPARAM_TABLES):
                dol.write(ADDR_ANIMPARAMS + (k * NUM_CHARS + S) * 2, dol.read(ADDR_ANIMPARAMS + (k * NUM_CHARS + C) * 2, 2))
        report.append((S, rc))

    # --- blob relocation
    if use_overlay:
        blob = bytes(blob) + b"\0" * ((-len(blob)) % 0x20)
        blob_off = place(blob, 0x800)
        dol.set_desc(ADDR_MASTER, MASTER_BLOB_ENTRY, (blob_desc[0], len(blob), blob_off, len(blob)))
        old_alloc = dol.blob_alloc_size()
        if old_alloc != blob_desc[3]:
            raise SystemExit("ARAM allocation (0x%X) does not match the blob descriptor (0x%X)" % (old_alloc, blob_desc[3]))
        dol.set_blob_alloc_size(len(blob))
        log("ARAM blob moved to ZZZZ.dat+0x%08X, size 0x%X -> 0x%X (fn_800227E4 immediates patched)" % (blob_off, blob_desc[3], len(blob)))

    # --- write ZZZZ.dat appendix
    with open(zz_path, "r+b") as f:
        for off, b in chunks:
            f.seek(off)
            f.write(b)
        f.seek(0, 2)
        end = f.tell()
        pad = align(end, 0x800) - end
        f.write(b"\0" * pad)
    new_zz_size = disc.zzzz_size()
    log("ZZZZ.dat: %d -> %d bytes" % (append_at, new_zz_size))

    # --- stats
    if not args.no_stats:
        with open(zz_path, "r+b") as f:
            f.seek(ZZZZ_STATS_OFFSET)
            stats = bytearray(f.read(NUM_CHARS * STATS_ROW))
            ids = [struct.unpack_from(">h", stats, i * STATS_ROW + STATS_CHARID)[0] for i in range(NUM_CHARS)]
            if ids != list(range(NUM_CHARS)):
                raise SystemExit("stat block at ZZZZ.dat+0x%X does not look right (CharIDs %r...)" % (ZZZZ_STATS_OFFSET, ids[:5]))
            src_row = bytes(stats[C * STATS_ROW:(C + 1) * STATS_ROW])
            for S, _ in report:
                row = bytearray(src_row)
                struct.pack_into(">h", row, STATS_CHARID, S)
                stats[S * STATS_ROW:(S + 1) * STATS_ROW] = row
            for S, _ in report:
                for j in range(NUM_CHARS):
                    stats[j * STATS_ROW + STATS_CHEM + S] = stats[j * STATS_ROW + STATS_CHEM + C]
            f.seek(ZZZZ_STATS_OFFSET)
            f.write(stats)
        log("stats: copied %s's row and chemistry to %s" % (CHAR_NAMES[C], ", ".join(CHAR_NAMES[S] for S, _ in report)))

    # --- game.rel gameplay tables
    if not args.no_gameplay:
        rel_path = disc.files / "game.rel"
        rel = bytearray(rel_path.read_bytes())
        base, size = rel_data_section(rel)
        for off, rs in [REL_VOICE] + REL_TABLES:
            if off + NUM_CHARS * rs > size:
                raise SystemExit("game.rel table at 0x%X is outside .data" % off)
            for S, _ in report:
                rel[base + off + S * rs:base + off + (S + 1) * rs] = rel[base + off + C * rs:base + off + (C + 1) * rs]
        rel_path.write_bytes(rel)
        log("game.rel: copied voice/hitbox/timing tables")

    # --- fst.bin and DOL
    fst = (disc.sys / "fst.bin").read_bytes()
    (disc.sys / "fst.bin").write_bytes(patch_fst_size(fst, "ZZZZ.dat", new_zz_size))
    (disc.sys / "main.dol").write_bytes(bytes(dol.data))
    log("main.dol and fst.bin written")

    if args.iso:
        write_iso(disc, Path(args.iso), log)

    log("")
    log("Done.  In the game, pick: " + ", ".join("%s (slot %d) -> %s" % (CHAR_NAMES[S], S, rc.spec) for S, rc in report))
    log("Boot: Dolphin -> File -> Open -> %s" % (out / "sys" / "main.dol"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="""examples:
  py clone_character.py --disc "C:\\mssb\\GYQE01" --list-slots
  py clone_character.py --disc "C:\\mssb\\GYQE01" --out "C:\\mssb\\GYQE01-marios" --source mario --slot BlueToad:blue --slot YellowToad:green --slot GreenToad:purple
  py clone_character.py --disc ... --out ... --source 0 --slot 29:hue=0.62,sat=1.1 --slot 30:tint=00FF00:0.6 --iso "C:\\mssb\\marios.iso"
recolour spec (after the slot and a colon): """ + Recolour.__doc__)
    ap.add_argument("--disc", required=True, help="extracted disc folder (contains sys/ and files/)")
    ap.add_argument("--out", help="output folder (created; never the input)")
    ap.add_argument("--source", default="mario", help="character to clone (name or id, default mario)")
    ap.add_argument("--slot", action="append", default=[], metavar="SLOT[:RECOLOUR]",
                    help="donor slot and recolour spec; repeatable.  Default: BlueToad:blue YellowToad:green GreenToad:purple")
    ap.add_argument("--iso", help="also write a bootable .iso (the disc grows past the standard size; Dolphin accepts it)")
    ap.add_argument("--no-aram-overlay", action="store_true",
                    help="do not recolour the ARAM (in-game) body; clones then look like the plain source in matches")
    ap.add_argument("--no-stats", action="store_true", help="keep the donor's stats instead of copying the source's")
    ap.add_argument("--no-gameplay", action="store_true", help="keep the donor's game.rel/DOL gameplay tables")
    ap.add_argument("--list-slots", action="store_true", help="print the slot table and exit")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    args = ap.parse_args(argv)
    if args.list_slots:
        list_slots(Disc(args.disc))
        return 0
    if not args.out:
        ap.error("--out is required")
    clone(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
