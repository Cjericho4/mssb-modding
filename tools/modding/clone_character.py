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

Presentation (--ui source, the default; see .agent/character_select.md section 6):

  * Names, portraits, the announcer call-out, the character voice bank and a few gameplay tables are
    keyed by the "unique index" (0..31, one per distinct character; colour variants share their
    base's).  Retail computes it two ways that always agree: lbl_800E8558[charID].uniqueIdx (the
    per-slot table) and fn_800698F8(charID) (a scan of mainCharArray / the variant table plus a
    switch).  The clone gets the source's uniqueIdx in lbl_800E8558, and fn_800698F8 is rewritten
    (16 instructions, DOL .text) to return lbl_800E8558[charID].uniqueIdx, so both routes agree
    again.  Family/base/colour in lbl_800E8558 are left alone: they drive the colour assignment on
    the select grid.
  * The in-game HUD portrait (batter/pitcher card) is a pane frame = charID into a 54-portrait
    texture file (ZZZZ.dat 0x1A6B3000, CI8 50x50 + RGB5A3 palette, one per charID).  The clone's
    portrait becomes a copy of the source's with its palette recoloured by the slot's spec; the
    file is re-packed in place (it only has to fit the retail compressed size).
  * Menu art keyed by charID (.agent/character_select.md section 8): the roster preview name and the
    52x60 mini picture (common UI file, ZZZZ.dat 0x1A9FE800) and the 50x50 select-grid portrait (ZZZZ.dat
    0x18AEE800).  The donor's texture headers are pointed at the source's pixel buffers; the two
    pictures get a private palette recoloured by the slot's spec, written into a buffer no header uses
    any more (the donor's own palette, or the donor's freed name/picture pixels), so no buffer another
    texture shares is ever written and the files keep their size.  Both are re-packed in place; if one
    ever does not fit it is appended and every copy of its descriptor (main.dol, menus/game/
    challenge.rel) is rewritten.
  * The colour word before the roster name ("Blue", "Purple") is a charID switch in menus.rel
    fn_2_74F40.  --colour-word none (default) points each donor's jump-table relocation at the label-0
    case (what single-colour characters show); --colour-word donor keeps it.  menus.rel is re-packed
    in place in aaaa.dat like game.rel.

What stays the donor's: the family/colour grouping on the character-select grid.  A main-grid
donor (e.g. Luigi) is itself the clone, so its grid cell shows the source's face and name; a colour
variant donor (e.g. Blue Toad) has no cell of its own, so the grid is unchanged and the roster
screen, lineup, HUD and announcer show the source.  --ui donor keeps every menu/HUD/announcer lookup as it was.
"""
import argparse
import colorsys
import copy
import hashlib
import math
import shutil
import struct
import sys
import tempfile
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
ADDR_REL_DESCS = (0x800E8AA8, 0x800E8AB8, 0x800E8AC8)  # DiskFileDesc for menus/game/challenge.rel
REL_NAMES = ("menus.rel", "game.rel", "challenge.rel")
ADDR_GAME_REL_DESC = ADDR_REL_DESCS[1]  # the RELs live LZSS-packed back to back in files/aaaa.dat, not in the FST
ADDR_BLOB_LIS = 0x800227EC       # fn_800227E4: lis  r3, 0x4d      (3C 60 00 4D)
ADDR_BLOB_ADDI = 0x800227F4      # fn_800227E4: addi r3, r3, 0x6600 (38 63 66 00)
JUMPTABLE_CASES = 48
ANIMPARAM_TABLES = 15
FAMILY_UNIQUE = 2                # lbl_800E8558 row byte: unique index (name/portrait/announcer key)

# fn_800698F8 (findCharacterID, DOL .text 0x800698F8, 0x170 bytes): charID -> unique index 0..31.
# Retail scans lbl_80108DB8 (32 base charIDs, returns the position) and then lbl_80108DDC (22
# variants, a switch maps each to its base's cell), so a variant can never resolve to another base's
# cell without new code.  --ui source replaces the head of the function with a lookup of
# lbl_800E8558[charID].uniqueIdx, which equals the retail result for every retail charID (checked by
# test_clone.py), keeping the Birdo contract (lbl_8034E9A0.useAltBirdoGrid: cell 0x11 reports 0x20
# once; the flag is cleared on every return).  The old body after the blr becomes dead code.
ADDR_FINDCHAR = 0x800698F8
FINDCHAR_PATCH = bytes.fromhex(
    "3C80800F"   # lis    r4, lbl_800E8558@ha
    "38848558"   # addi   r4, r4, lbl_800E8558@l
    "1C030006"   # mulli  r0, r3, 6
    "7C840214"   # add    r4, r4, r0
    "88640002"   # lbz    r3, 2(r4)              ; uniqueIdx
    "3C808035"   # lis    r4, lbl_8034E9A0@ha
    "3884E9A0"   # addi   r4, r4, lbl_8034E9A0@l
    "880448AE"   # lbz    r0, 0x48AE(r4)         ; useAltBirdoGrid
    "2C030011"   # cmpwi  r3, 0x11
    "40820010"   # bne    +0x10 (clear)
    "28000000"   # cmplwi r0, 0
    "41820008"   # beq    +0x08 (clear)
    "38600020"   # li     r3, 0x20
    "38000000"   # clear: li r0, 0
    "980448AE"   # stb    r0, 0x48AE(r4)
    "4E800020"   # blr
)
# the retail head (same length) as the preflight signature
FINDCHAR_RETAIL = bytes.fromhex(
    "9421FFF0" "7C0802A6" "3C808011" "3CA0800E" "90010014" "38000020" "38848DB8" "93E1000C"
    "3BE569B8" "38A00000" "7C0903A6" "88040000" "7C001800" "4082003C" "2C050011" "4082001C")

# 54-portrait texture file (HUD batter/pitcher card; pane frame = charID in game.rel fn_3_91D1C etc.).
# Descriptor rows that reference it: DOL lbl_800EEFD0+0x10 and lbl_800E8AA8+0x70; game.rel .data
# lbl_3_data_21EC8+0x10, lbl_3_data_F538+0x10, lbl_3_data_10598+0x10.  None of them is modified: the
# file is re-packed in place at the same ZZZZ.dat offset with the same uncompressed size.
ADDR_PORTRAIT_DESC = 0x800EEFE0
ADDR_PORTRAIT_DESC2 = 0x800E8B18
REL_PORTRAIT_DESCS = (0x21ED8, 0xF548, 0x105A8)
PORTRAIT_W = 50
PORTRAIT_H = 50
TEX_FMT_CI4 = 8
TEX_FMT_CI8 = 9
TEX_FMT_CMPR = 14
TLUT_FMT_RGB565 = 1
TLUT_FMT_RGB5A3 = 2

# Menu art keyed by charID (.agent/character_select.md section 8).  Both files are LZSS-packed in
# ZZZZ.dat; their texture headers are rewritten so the donor's frames show the source's art.
#   common file (boot-loaded UI slot 1): roster preview name, sequence 0x82, texture c<29 ? c : c+28
#     (111x18 CI4, one shared RGB565 TLUT); mini-screen picture, sequence 0x83, texture 153+c (52x60
#     CI8, RGB5A3 TLUT; some of them alias each other's buffers).
#   grid portrait file (UI slot 9): select-grid portrait, sequence 0x19, texture 4+c (50x50 CI8).
# The descriptor tuples also live in menus.rel (grid x9), game.rel (x5 each) and challenge.rel (x1
# each); they are only rewritten when a file has to be moved (see place_menu_file()).
ADDR_MENU_COMMON_DESC = 0x800E8BF8   # res table 0x800E8B08+0xF0 (also res_dol2 lbl_800EEFD0+0xF0)
ADDR_MENU_GRID_DESC = 0x800E8CD8     # res table 0x800E8B08+0x1D0 (also res_dol2 lbl_800EEFD0+0x1D0)
# retail copies of each descriptor tuple: (main.dol, menus.rel, game.rel, challenge.rel)
MENU_DESC_COPIES = {ADDR_MENU_COMMON_DESC: (2, 0, 5, 1), ADDR_MENU_GRID_DESC: (2, 9, 5, 1)}
MENU_COMMON_RETAIL = (0x40B, 0x40106568, 0x1A9FE800, 0x68C10)
MENU_GRID_RETAIL = (0x40B, 0x400ADFFC, 0x18AEE800, 0x4BAB0)
SEQ_NAME, SEQ_MINI, SEQ_GRID = 0x82, 0x83, 0x19

# Colour word ("Blue"/"Purple" before the preview name): menus.rel fn_2_74F40 switches on charID
# (cases 12..53, jumptable_2_data_2D844 in .data) to labels 0..6 of common sequence 0x7B.  The jump
# table is empty in the file; each entry is an R_PPC_ADDR32 relocation whose addend is the case's
# .text offset.  --colour-word none points a donor's entry at the label-0 case (what Mario and every
# other single-colour character shows): a 4-byte addend edit in the relocation table.
MENUS_TEXT_SEC, MENUS_DATA_SEC = 1, 5
MENUS_COLOUR_JT = 0x2D844
MENUS_COLOUR_FIRST = 12
MENUS_COLOUR_CASE = {1: 0x7556C, 2: 0x75574, 3: 0x7557C, 4: 0x75584, 5: 0x7558C, 6: 0x75594, 0: 0x7559C}
MENUS_COLOUR_LABEL = {}
for _lab, _ids in ((1, (13, 16, 20, 22, 25, 34, 42, 50, 52)), (2, (21, 24, 29, 33, 44, 51, 53)), (3, (23, 30, 36, 45)),
                   (4, (12, 26, 27, 31, 35, 43, 46, 49)), (5, (32,)), (6, (47,))):
    for _c in _ids:
        MENUS_COLOUR_LABEL[_c] = _lab
del _lab, _ids, _c

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
    "Wario", "Waluigi", "GreenKoopa", "RedToad", "Boo", "Toadette", "RedShyGuy", "Birdo", "Monty",
    "BowserJr", "RedParatroopa", "BluePianta", "RedPianta", "YellowPianta", "BlueNoki", "RedNoki",
    "GreenNoki", "HammerBro", "Toadsworth", "BlueToad", "YellowToad", "GreenToad", "PurpleToad",
    "BlueMagikoopa", "RedMagikoopa", "GreenMagikoopa", "YellowMagikoopa", "KingBoo", "Petey",
    "Dixie", "Goomba", "Paragoomba", "RedKoopa", "GreenParatroopa", "BlueShyGuy", "YellowShyGuy",
    "GreenShyGuy", "BlackShyGuy", "GrayDryBones", "GreenDryBones", "RedDryBones", "BlueDryBones",
    "FireBro", "BoomerangBro",
]
# Every character can be a source (Petey's CI4 textures are recoloured through their palettes).
UNSUPPORTED_SOURCES = frozenset()
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


def lzss_compress_optimal(data, lb_bits=LZSS_LOOKBACK_BITS, rp_bits=LZSS_REPEAT_BITS, max_chain=256):
    """Optimal-parse encoder (cheapest literal/match sequence, ~20 s for game.rel).  Packs tighter than
    Nintendo's encoder, which game.rel needs to fit back into its slot in aaaa.dat."""
    data = bytes(data)
    n = len(data)
    max_back = 1 << lb_bits
    max_len = (1 << rp_bits) - 1 + 2
    longest = [(0, 0)] * n   # (length, back) of the longest match at each position
    heads = {}
    for pos in range(n - 1):
        key = data[pos:pos + 2]
        chain = heads.get(key)
        if chain:
            limit = min(max_len, n - pos)
            best_len = best_back = tries = 0
            for cand in reversed(chain):
                if pos - cand > max_back or tries >= max_chain:
                    break
                tries += 1
                l = 2
                while l < limit and data[cand + l] == data[pos + l]:
                    l += 1
                if l > best_len:
                    best_len, best_back = l, pos - cand
                    if l == limit:
                        break
            longest[pos] = (best_len, best_back)
        lst = heads.setdefault(key, [])
        lst.append(pos)
        if len(lst) > 2 * max_chain:
            del lst[:max_chain]
    lit_bits, match_bits = 9, 1 + lb_bits + rp_bits
    cost = [0] * (n + 1)
    step = [1] * n
    for pos in range(n - 1, -1, -1):
        best = lit_bits + cost[pos + 1]
        for l in range(2, longest[pos][0] + 1):
            v = match_bits + cost[pos + l]
            if v < best:
                best, step[pos] = v, l
        cost[pos] = best
    bw = _BitWriter()
    pos = 0
    while pos < n:
        l = step[pos]
        if l == 1:
            bw.write(1, 1)
            bw.write(data[pos], 8)
        else:
            bw.write(0, 1)
            bw.write(longest[pos][1] - 1, lb_bits)
            bw.write(l - 2, rp_bits)
        pos += l
    out = bw.finish()
    return out + b"\0" * ((-len(out)) % 32)


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
            colours = struct.unpack_from(">H", self.data, h + 0x18)[0]
            self.textures.append({"index": i, "hdr": h, "data": self.texture + data_off,
                                  "clut": clut_off, "w": width, "h": height, "fmt": fmt,
                                  "tlut": self.texture + clut_off, "colours": colours, "tlut_fmt": self.data[h + 0x1A]})
        for t in self.textures:
            if t["fmt"] == TEX_FMT_CMPR:
                t["size"] = t["w"] * t["h"] // 2
                t["colours"] = 0
            elif t["fmt"] in (TEX_FMT_CI4, TEX_FMT_CI8) and t["tlut_fmt"] in (TLUT_FMT_RGB565, TLUT_FMT_RGB5A3):
                # Petey's body: CI4 textures with a small RGB5A3 palette inside the archive
                t["size"] = align(t["w"], 8) * align(t["h"], 8) // 2 if t["fmt"] == TEX_FMT_CI4 else align(t["w"], 8) * align(t["h"], 4)
                if not 0 < t["colours"] <= (16 if t["fmt"] == TEX_FMT_CI4 else 256) or t["tlut"] + 2 * t["colours"] > self.layout:
                    raise ValueError("texture %d: bad palette (%d colours at 0x%X)" % (t["index"], t["colours"], t["clut"]))
            else:
                raise ValueError("texture %d is format %d (palette format %d); only CMPR, CI4 and CI8 are supported"
                                 % (t["index"], t["fmt"], t["tlut_fmt"]))
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


def luma(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def match_luma(rgb, target):
    """Scale rgb to the target luma; if a channel would pass 1.0, blend toward white instead."""
    y = luma(*rgb)
    if y <= 0:
        return (target, target, target)
    out = [x * target / y for x in rgb]
    if max(out) > 1.0:
        top = [min(1.0, x) for x in out]
        yt = luma(*top)
        t = (target - yt) / (1.0 - yt) if yt < 1.0 else 0.0   # mix with white to recover the clipped luma
        out = [x + (1.0 - x) * t for x in top]
    return tuple(min(1.0, max(0.0, x)) for x in out)


class Recolour:
    """Spec: comma-separated key=value.  Keys:
        hue=<0..1|preset>  target hue for the matched band (default band: reds, i.e. Mario's suit)
        from=<0..1> width=<0..1>  hue band to retarget (default from=0 width=0.035: Mario's red, not his skin or ear shading)
        minsat=<0..1>      minimum saturation for a colour to be considered part of the band (0.5)
        all=1              retarget every saturated colour, not just the band
        sat=<mult> val=<mult>  multiply saturation / brightness (applied to matched colours)
        keeplum=<0..1>     how far to pull a recoloured colour toward the original's perceived brightness
                           (default 0.5; 0 = old look, greens/yellows washed-out and flat; 1 = exact
                           match, greens/yellows quite dark)
        tint=RRGGBB[:strength]  blend every colour towards RRGGBB (strength 0..1, default 0.5)
        solid=RRGGBB       replace every colour (debug)
        tex=0/1/2          only touch these texture indices, slash-separated (default: all)
        region=0/1         recolour these detected colour regions of the source (see --list-regions;
                           0 = its main colour).  With no from/width/minsat/region/all key, a slot
                           recolours the source's main colour region (Mario: his reds, as before)
        maxsat= minval= maxval=<0..1>  more limits for the from/width band (default 1, 0, 1)
        keepskin=1         never touch skin tones, even inside the band
    A bare preset name (blue, green, ...) is shorthand for hue=<preset>."""

    BAND_KEYS = ("from", "width", "minsat", "maxsat", "minval", "maxval", "keepskin")

    def __init__(self, spec):
        self.hue = None
        self.band_from = 0.0
        self.band_width = 0.035  # suit reds sit at hue <= 0.031; skin/ear shading starts at 0.038
        self.minsat = 0.5
        self.maxsat = 1.0
        self.minval = 0.0
        self.maxval = 1.0
        self.keepskin = False
        self.explicit_band = False   # a from/width/... key was given: never replaced by a region
        self.region = None           # [region indices] from region=, resolved by resolve()
        self.bands = None            # resolved [Region]; None = the single from/width band above
        self.regions = []            # every region of the source (a colour goes to its nearest one)
        self.skip_tex = frozenset()  # texture indices a resolved region leaves alone (the gloves)
        self.all = False
        self.sat = 1.0
        self.val = 1.0
        self.keeplum = 0.5
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
            if k in self.BAND_KEYS:
                self.explicit_band = True
            if k == "hue":
                self.hue = COLOUR_PRESETS[v.lower()] if v.lower() in COLOUR_PRESETS else float(v)
            elif k == "from":
                self.band_from = float(v)
            elif k == "width":
                self.band_width = float(v)
            elif k == "minsat":
                self.minsat = float(v)
            elif k == "maxsat":
                self.maxsat = float(v)
            elif k == "minval":
                self.minval = float(v)
            elif k == "maxval":
                self.maxval = float(v)
            elif k == "keepskin":
                self.keepskin = v not in ("0", "false", "no")
            elif k == "region":
                self.region = [int(x) for x in v.split("/") if x]
            elif k == "all":
                self.all = v not in ("0", "false", "no")
            elif k == "sat":
                self.sat = float(v)
            elif k == "val":
                self.val = float(v)
            elif k == "keeplum":
                self.keeplum = float(v)
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
        if self.region is not None and (self.explicit_band or self.all):
            raise SystemExit("recolour spec %r: region= cannot be combined with from/width/minsat/.../all" % spec)
        self._cache = {}
        self._texel_cache = {}

    @property
    def uses_default_band(self):
        """True when nothing says which colours to change (resolve() then picks the main region)."""
        return not (self.explicit_band or self.all or self.region is not None)

    def resolve(self, regions):
        """Bind region= (or, with no band keys at all, the main region regions[0]) to the source's
        detected regions.  Without regions (or with an explicit band) the spec is left as it is."""
        if self.region is not None:
            bad = [i for i in self.region if not 0 <= i < len(regions)]
            if bad:
                raise SystemExit("recolour spec %r: the source has %d colour regions (0..%d), not %s"
                                 % (self.spec, len(regions), len(regions) - 1, "/".join(map(str, bad))))
            picked = [regions[i] for i in self.region]
        elif self.uses_default_band and regions:
            picked = [r for r in regions if r.default] or [regions[0]]
        else:
            return self
        self.bands = picked
        self.regions = list(regions)
        self.skip_tex = frozenset().union(*[r.skip for r in picked])
        self._cache = {}
        self._texel_cache = {}
        return self

    def _band_for(self, h, s, v):
        """The selected Region a colour belongs to (None: not selected).  Only for resolved specs."""
        # a colour inside several regions' boxes belongs to the one whose hue centre is nearest
        # (and brightness/saturation): DK's fur box must not take his tie's reds
        inside = [r for r in self.regions if r.contains(h, s, v)]
        if not inside:
            return None
        best = min(inside, key=lambda r: r.distance(h, s, v))
        return best if any(best is b for b in self.bands) else None

    @property
    def tinting(self):
        """Every selected region is a tint region (white/grey/black body)."""
        return self.bands is not None and all(b.tint is not None for b in self.bands)

    def _in_band(self, h, s, v=0.0):
        if self.bands is not None:
            return self._band_for(h, s, v) is not None
        if s < self.minsat:
            return False
        if self.all:
            return True
        if s > self.maxsat or v < self.minval or v > self.maxval or (self.keepskin and is_skin(h, s, v)):
            return False
        d = abs((h - self.band_from + 0.5) % 1.0 - 0.5)
        return d <= self.band_width

    def describe(self):
        if self.bands is not None:
            return " + ".join(b.name for b in self.bands)
        if self.all:
            return "every strong colour"
        return "hue %.3f +- %.3f" % (self.band_from, self.band_width)

    def for_palette(self):
        """The spec for a whole-image palette (the HUD portrait): a portrait is a separate painting
        whose shading reaches past the boxes fitted to the body's texels, so detected regions are
        widened (more hue, any brightness, less saturation); Mario's original band stays as it is."""
        if not self.blockwise:
            return self
        rc = copy.copy(self)
        def widen(r):
            if r.tint is not None:   # a tint region is a brightness window: the portrait uses the same one
                return r
            return Region(r.name, r.hue, r.width + 0.03, max(0.15, r.minsat - 0.2), 1.0, 0.0, 1.0, "lips" if r.keepskin else False,
                          r.skip, r.weight, r.rgb, r.sat, r.val, boost=r.boost, lumk=r.lumk)
        rc.bands = [widen(b) for b in self.bands]
        rc.regions = [b for b in rc.bands] + [widen(r) for r in self.regions if not any(r is b for b in self.bands)]
        rc._cache, rc._texel_cache = {}, {}
        return rc

    def touches(self, tex_index):
        """Does this spec recolour texture `tex_index` of a body?"""
        return (self.tex is None or tex_index in self.tex) and tex_index not in self.skip_tex

    @property
    def blockwise(self):
        """Decide per CMPR block (by the colours its texels show) instead of per endpoint: true for
        detected regions; Mario's original band keeps the per-endpoint rule (byte-identical)."""
        return self.bands is not None and all(b.blockwise for b in self.bands)

    def texel_in_band(self, rgb):
        """Is a decoded texel colour (3 bytes) inside the selected region(s)?  Cached."""
        r = self._texel_cache.get(rgb)
        if r is None:
            r = self._in_band(*colorsys.rgb_to_hsv(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0))
            self._texel_cache[rgb] = r
        return r

    def map_rgb8(self, r8, g8, b8, force=False):
        """Map one 8-bit RGB colour through the spec (shared by every pixel format).  force: the
        colour is an endpoint of a block whose texels are mostly in the region, so it follows the
        region even when it lies outside it itself (unless it is skin or nearly grey)."""
        if self.solid is not None:
            return (self.solid >> 16) & 0xFF, (self.solid >> 8) & 0xFF, self.solid & 0xFF
        h, s, v = colorsys.rgb_to_hsv(r8 / 255.0, g8 / 255.0, b8 / 255.0)
        band = self._band_for(h, s, v) if self.bands is not None else None
        if band is not None and band.tint is not None:
            return self._colourise(band, r8, g8, b8, v)
        if band is None and force and self.tinting:
            # an endpoint of a block that shows mostly region colours (a 3-colour block whose texels
            # are all the midpoint of two endpoints just outside the box): follow if it is close
            for b in self.bands:
                if s <= b.maxsat + 0.15 and b.minval - 0.1 <= v <= b.maxval + 0.1:
                    return self._colourise(b, r8, g8, b8, v)
        if self.bands is not None:
            hit = band is not None or (force and not self.tinting and s >= 0.12 and not is_skin(h, s, v))
        else:
            hit = self._in_band(h, s, v) or (force and s >= 0.12 and not is_skin(h, s, v))
        if hit and band is None and self.bands:
            band = self.bands[0]          # a forced endpoint follows the (first) selected region's settings
        if hit:
            if self.hue is not None:
                h = self.hue % 1.0
            s = min(1.0, s * self.sat)
            if band is not None and band.boost != 1.0:
                s = min(1.0, s * band.boost)
        rf, gf, bf = colorsys.hsv_to_rgb(h, s, v)
        if hit:
            if self.keeplum and self.hue is not None:
                # same HSV value looks far brighter in green than in red: match the original's luma
                # instead, so the shading gradients survive the hue change
                lr, lg, lb = match_luma((rf, gf, bf), luma(r8 / 255.0, g8 / 255.0, b8 / 255.0))
                k = self.keeplum if band is None or band.lumk == 1.0 else self.keeplum * band.lumk
                rf, gf, bf = rf + (lr - rf) * k, gf + (lg - gf) * k, bf + (lb - bf) * k
            rf, gf, bf = (min(1.0, x * self.val) for x in (rf, gf, bf))
        if self.tint is not None:
            t = self.tint_strength
            rf = rf * (1 - t) + ((self.tint >> 16) & 0xFF) / 255.0 * t
            gf = gf * (1 - t) + ((self.tint >> 8) & 0xFF) / 255.0 * t
            bf = bf * (1 - t) + (self.tint & 0xFF) / 255.0 * t
        return int(round(rf * 255)), int(round(gf * 255)), int(round(bf * 255))

    def _colourise(self, band, r8, g8, b8, v):
        """A tint region's colour in the target hue: saturation band.tint at full strength from 80 %
        of the region's typical brightness up, fading to none at black, so shading and dark details
        (eye sockets, creases) survive.  A dark region (Black Shy Guy) is lifted a little so the hue shows."""
        if self.hue is None:
            return r8, g8, b8
        k = min(1.0, max(0.0, (v - 0.04) / max(0.05, band.val * 0.8 - 0.04)))
        s = min(1.0, band.tint * k * self.sat)
        if band.val < 0.4:
            v = min(1.0, v * (1.0 + 0.5 * k))
        rf, gf, bf = colorsys.hsv_to_rgb(self.hue % 1.0, s, v)
        rf, gf, bf = (min(1.0, x * self.val) for x in (rf, gf, bf))
        return int(round(rf * 255)), int(round(gf * 255)), int(round(bf * 255))

    def map565(self, c, force=False):
        key = c | 0x10000 if force else c
        r = self._cache.get(key)
        if r is not None:
            return r
        out = rgb_to_rgb565(*self.map_rgb8(*rgb565_to_rgb(c), force=force))
        self._cache[key] = out
        return out

    def map_rgb5a3(self, p):
        """One RGB5A3 TLUT/pixel value: bit 15 set = RGB555 opaque, clear = A3 RGB444."""
        if p & 0x8000:
            r = (p >> 10) & 0x1F
            g = (p >> 5) & 0x1F
            b = p & 0x1F
            r8, g8, b8 = self.map_rgb8((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2))
            return 0x8000 | ((r8 >> 3) << 10) | ((g8 >> 3) << 5) | (b8 >> 3)
        a = (p >> 12) & 7
        if a == 0 and self.tinting:
            return p                     # invisible (a portrait's background): a tint would only show in tools
        r8, g8, b8 = self.map_rgb8(((p >> 8) & 0xF) * 17, ((p >> 4) & 0xF) * 17, (p & 0xF) * 17)
        return (a << 12) | ((r8 >> 4) << 8) | ((g8 >> 4) << 4) | (b8 >> 4)


def _swap01(idx):
    """Swap palette indices 0<->1 in a CMPR index word (keeps 2 and 3)."""
    out = 0
    for i in range(16):
        v = (idx >> (i * 2)) & 3
        if v < 2:
            v ^= 1
        out |= v << (i * 2)
    return out


def block_band_share(c0, c1, idx, rc):
    """Fraction of a CMPR block's visible texels whose colour is inside the region(s).  A texel is
    often an interpolated mix of the two endpoints, so the endpoints alone mislead both ways: Baby
    Luigi's cap is dark olive + green with every texel at the 1/3 point (all in the region), a Red
    Paratroopa's yellow face is orange + lime with every texel at the midpoints (none in it)."""
    pal = cmpr_palette(c0, c1)
    n = [0, 0, 0, 0]
    for sh in (24, 16, 8, 0):
        cnt = _IDX_COUNTS[(idx >> sh) & 0xFF]
        for i in range(4):
            n[i] += cnt[i]
    inside = total = 0
    for i in range(4):
        if n[i] and pal[i] is not None:
            total += n[i]
            if rc.texel_in_band(pal[i]):
                inside += n[i]
    return inside / total if total else 0.0


def block_shows_other_colour(c0, c1, idx, rc, share):
    """Does a CMPR block show texels clearly unlike a tint region (strongly coloured, or far brighter
    than a dark region: the white of Black Shy Guy's mask around his eye holes)?  A tint region leaves
    such blocks alone when they are only partly region, so no ring is painted around an eye."""
    pal = cmpr_palette(c0, c1)
    n = [0, 0, 0, 0]
    for sh in (24, 16, 8, 0):
        cnt = _IDX_COUNTS[(idx >> sh) & 0xFF]
        for i in range(4):
            n[i] += cnt[i]
    top = max(b.maxval for b in rc.bands)
    strong = bright = 0
    for i in range(4):
        if n[i] and pal[i] is not None and not rc.texel_in_band(pal[i]):
            h, s, v = colorsys.rgb_to_hsv(pal[i][0] / 255.0, pal[i][1] / 255.0, pal[i][2] / 255.0)
            if s >= 0.4 and v >= 0.2:
                strong += n[i]
            elif v >= top + 0.35:
                bright += n[i]
    return strong >= 2 and share <= 0.5 or bright >= 2


def block_mostly_in_band(c0, c1, idx, rc):
    return block_band_share(c0, c1, idx, rc) >= 0.5


def recolour_block(block, rc):
    c0, c1, idx = struct.unpack(">HHI", block)
    force = False
    if rc.blockwise:
        share = block_band_share(c0, c1, idx, rc)
        if share == 0.0:
            return block                 # no texel shows a region colour: leave the block alone
        if rc.tinting and share < 1.0 and block_shows_other_colour(c0, c1, idx, rc, share):
            return block                 # an eye or a mouth with a little region colour: left whole
        force = share >= 0.5             # mostly region: both endpoints follow it
        if rc.tinting:
            force = share >= 0.999       # a tint only pulls in endpoints of blocks that show nothing else
    n0 = rc.map565(c0, force)
    n1 = rc.map565(c1, force)
    four = c0 > c1
    if four:
        if n0 == n1:
            # keep 4-colour mode: flip the blue LSB of one endpoint (n1 -= 1 borrowed into green when
            # blue was 0 and left bright blue specks); the swap below restores the c0 > c1 order
            n1 ^= 1
        if n0 < n1:
            n0, n1 = n1, n0
            idx ^= 0x55555555          # 0<->1, 2<->3
    else:
        if n0 > n1:
            n0, n1 = n1, n0
            idx = _swap01(idx)         # 0<->1, 2 (midpoint) and 3 (transparent) unchanged
    return struct.pack(">HHI", n0, n1, idx)


def recolour_gpc(gpc, rc):
    """Recolour the CMPR endpoints (or the palette of a CI texture) of every texture in place; headers
    and geometry untouched."""
    d = gpc.data
    for t in gpc.textures:
        if not rc.touches(t["index"]):
            continue
        if t["fmt"] != TEX_FMT_CMPR:
            o = t["tlut"]
            for k in range(t["colours"]):
                p = struct.unpack_from(">H", d, o + 2 * k)[0]
                q = rc.map_rgb5a3(p) if t["tlut_fmt"] == TLUT_FMT_RGB5A3 else rc.map565(p)
                struct.pack_into(">H", d, o + 2 * k, q)
            continue
        start, end = t["data"], t["data"] + t["size"]
        for o in range(start, end, 8):
            d[o:o + 8] = recolour_block(bytes(d[o:o + 8]), rc)


# ----------------------------------------------------------------------------------------------
# CMPR decoding (preview images, region detection, the test contact sheet)
# ----------------------------------------------------------------------------------------------
_RGB565 = None


def _rgb565_table():
    global _RGB565
    if _RGB565 is None:
        _RGB565 = [bytes(rgb565_to_rgb(c)) for c in range(0x10000)]
    return _RGB565


def cmpr_palette(c0, c1):
    """The four colours of a CMPR (DXT1) block as 3-byte strings; index 3 is None (transparent)
    in 3-colour mode (c0 <= c1)."""
    t = _rgb565_table()
    a, b = t[c0], t[c1]
    if c0 > c1:
        return (a, b, bytes((2 * x + y) // 3 for x, y in zip(a, b)), bytes((x + 2 * y) // 3 for x, y in zip(a, b)))
    return (a, b, bytes((x + y) // 2 for x, y in zip(a, b)), None)


CHECKER = (b"\x99\x99\x99", b"\xcc\xcc\xcc")


def decode_cmpr(data, w, h, checker=CHECKER):
    """GameCube CMPR (8x8 tiles of four 4x4 DXT1 blocks, indices MSB first) -> RGB bytes, w*h*3.
    Transparent texels become checker[(x>>3 ^ y>>3) & 1] (a grey checkerboard)."""
    out = bytearray(w * h * 3)
    stride = w * 3
    o = 0
    unpack = struct.unpack_from
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for by, bx in ((0, 0), (0, 4), (4, 0), (4, 4)):
                c0, c1, idx = unpack(">HHI", data, o)
                o += 8
                y0, x0 = ty + by, tx + bx
                if y0 >= h or x0 >= w:
                    continue
                pal = cmpr_palette(c0, c1)
                if pal[3] is None:
                    pal = pal[:3] + (checker[((x0 >> 3) ^ (y0 >> 3)) & 1],)
                n = min(4, w - x0)
                for r in range(min(4, h - y0)):
                    row = (idx >> (24 - 8 * r)) & 0xFF
                    px = pal[row >> 6] + pal[(row >> 4) & 3] + pal[(row >> 2) & 3] + pal[row & 3]
                    p = (y0 + r) * stride + x0 * 3
                    out[p:p + n * 3] = px[:n * 3]
    return bytes(out)


_IDX_COUNTS = [tuple(sum(1 for k in range(4) if (b >> (2 * k)) & 3 == i) for i in range(4)) for b in range(256)]


def cmpr_colour_counts(data, counts=None):
    """{(r, g, b): texel count} over a CMPR texture (transparent texels left out)."""
    counts = {} if counts is None else counts
    unpack = struct.unpack_from
    for o in range(0, len(data), 8):
        c0, c1, idx = unpack(">HHI", data, o)
        pal = cmpr_palette(c0, c1)
        n = [0, 0, 0, 0]
        for sh in (24, 16, 8, 0):
            cnt = _IDX_COUNTS[(idx >> sh) & 0xFF]
            n[0] += cnt[0]
            n[1] += cnt[1]
            n[2] += cnt[2]
            n[3] += cnt[3]
        for i in range(4):
            if n[i] and pal[i] is not None:
                counts[pal[i]] = counts.get(pal[i], 0) + n[i]
    return counts


def texture_bytes(gpc, t):
    """A texture's pixel data; for a CI texture followed by its palette (both change the look)."""
    b = bytes(gpc.data[t["data"]:t["data"] + t["size"]])
    if t["colours"]:
        b += bytes(gpc.data[t["tlut"]:t["tlut"] + 2 * t["colours"]])
    return b


def tlut_rgba(tlut, tlut_fmt):
    """[(r, g, b, a8)] of an RGB565 or RGB5A3 palette."""
    out = []
    for o in range(0, len(tlut), 2):
        v = (tlut[o] << 8) | tlut[o + 1]
        if tlut_fmt == TLUT_FMT_RGB565:
            out.append(rgb565_to_rgb(v) + (255,))
        elif v & 0x8000:
            r, g, b = (v >> 10) & 31, (v >> 5) & 31, v & 31
            out.append(((r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2), 255))
        else:
            out.append((((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17, ((v >> 12) & 7) * 255 // 7))
    return out


def ci_indices(data, t):
    """Palette index of every texel of a CI4 (8x8 tiles) or CI8 (8x4 tiles) texture, row by row."""
    w, h = t["w"], t["h"]
    tw, th = (8, 8) if t["fmt"] == TEX_FMT_CI4 else (8, 4)
    tiles_x = align(w, 8) // 8
    out = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            k = ((y // th) * tiles_x + x // tw) * tw * th + (y % th) * tw + (x % tw)
            out[y * w + x] = (data[k >> 1] >> (4 * (1 - (k & 1)))) & 15 if t["fmt"] == TEX_FMT_CI4 else data[k]
    return out


def decode_texture(data, t, checker=CHECKER):
    """texture_bytes() of texture t -> RGB bytes, w*h*3 (transparent texels show a grey checkerboard)."""
    if t["fmt"] == TEX_FMT_CMPR:
        return decode_cmpr(data, t["w"], t["h"], checker)
    pal = tlut_rgba(data[t["size"]:t["size"] + 2 * t["colours"]], t["tlut_fmt"])
    w = t["w"]
    out = bytearray()
    for i, k in enumerate(ci_indices(data, t)):
        r, g, b, a = pal[k]
        c = checker[(((i % w) >> 3) ^ ((i // w) >> 3)) & 1]
        out += bytes(((r * a + c[0] * (255 - a)) // 255, (g * a + c[1] * (255 - a)) // 255, (b * a + c[2] * (255 - a)) // 255))
    return bytes(out)


def texture_colour_counts(data, t, counts=None):
    """{(r, g, b): texel count} of texture_bytes() of texture t (transparent texels left out)."""
    if t["fmt"] == TEX_FMT_CMPR:
        return cmpr_colour_counts(data, counts)
    counts = {} if counts is None else counts
    pal = tlut_rgba(data[t["size"]:t["size"] + 2 * t["colours"]], t["tlut_fmt"])
    n = [0] * len(pal)
    for k in ci_indices(data, t):
        n[k] += 1
    for (r, g, b, a), c in zip(pal, n):
        if c and a >= 128:
            key = bytes((r, g, b))
            counts[key] = counts.get(key, 0) + c
    return counts


# ----------------------------------------------------------------------------------------------
# Colour regions: which colours of a character are "its" colours (Mario's red, Luigi's green, ...)
# ----------------------------------------------------------------------------------------------
def is_skin(h, s, v):
    """Skin tones (peach/tan, every character's face and hands): orange hue, light, not too vivid."""
    return h <= 0.13 and 0.08 <= s <= 0.62 and v >= 0.5


def is_skin_or_lips(h, s, v):
    """is_skin plus the light pinks of lips and cheeks (portraits: Peach's lips are her dress pink)."""
    return is_skin(h, s, v) or ((h >= 0.93 or h <= 0.02) and s <= 0.5 and v >= 0.8)


# Every character's body carries two baseball-glove textures (orange-brown leather, the same few
# images shared between characters).  Their colours sit right on top of brown fur, orange dresses
# and skin, so a detected region never touches them.  md5(texture data)[:12] of all of them in the
# retail bodies (test_matrix.py checks each cloneable character has exactly two).
GLOVE_TEXTURES = frozenset("""
8da02ffb3ea9 3d8f3fc12a04 14a8da07943c 799c1d92d00e 3f6017a6f8cd 37468e06f34c 4d342940c114
c5181b821b6c 56a1383eff40 0b988894d1eb 0414e07d8d63 5ebe98e4d4ed 05853bc37f33 ccdc45f00ee3
3bfbc4bc3abc 9439d82f2d70 b33278673130 57bd06c2f345 8e13c338ed31 625961e30213 b1a6ae25a138
be99c0e257ad 77d9b7b1a3dc 4cd06b1fd065 9f4cc4b5fd1b 12d3a56c160c e68703ece83c 916129f18959
0796c1fd528f 927163b674ca eb51c8b77a37 f1a1cdd1c589 63292930f0a5 2a28084a4a2a
""".split())


def glove_textures(gpc):
    return frozenset(t["index"] for t in gpc.textures
                     if hashlib.md5(texture_bytes(gpc, t)).hexdigest()[:12] in GLOVE_TEXTURES)


HUE_NAMES = ((15, "red"), (40, "orange"), (70, "yellow"), (165, "green"), (195, "cyan"), (255, "blue"),
             (320, "purple"), (345, "pink"), (360, "red"))


class Region:
    """A box in HSV space: hue within +-width of `hue`, saturation and value inside their limits,
    optionally never skin.  `skip` = texture indices left alone (the gloves).
    tint=<0..1>: a white, grey or black (or very pale) region that has no hue to shift: its colours are
    painted in the target hue at this saturation instead, scaled by brightness, so the shading stays
    and the darkest parts stay dark (Boo, King Boo, Black Shy Guy, Gray Dry Bones).  A width of 0.5
    or more means any hue.  boost: saturation multiplier for a pale region (Birdo's pink); lumk scales
    keeplum for it (a light pink's brightness pushed onto blue or green washes it out to pastel)."""
    __slots__ = ("name", "hue", "width", "minsat", "maxsat", "minval", "maxval", "keepskin", "skip",
                 "weight", "rgb", "sat", "val", "default", "blockwise", "tint", "boost", "lumk")

    def __init__(self, name, hue, width, minsat=0.5, maxsat=1.0, minval=0.0, maxval=1.0, keepskin=True,
                 skip=frozenset(), weight=0.0, rgb=None, sat=0.8, val=0.7, blockwise=True, tint=None, boost=1.0, lumk=1.0):
        self.tint, self.boost, self.lumk = tint, boost, lumk
        self.name, self.hue, self.width = name, hue % 1.0, width
        self.minsat, self.maxsat, self.minval, self.maxval = minsat, maxsat, minval, maxval
        self.keepskin, self.skip, self.weight = keepskin, frozenset(skip), weight
        self.sat, self.val = sat, val          # the cluster's typical colour (median s, v)
        self.default = False                   # recoloured when a slot names no region
        self.blockwise = blockwise
        self.rgb = rgb if rgb is not None else tuple(int(x * 255) for x in colorsys.hsv_to_rgb(self.hue, 0.8, 0.85))

    def contains(self, h, s, v):
        if s < self.minsat or s > self.maxsat or v < self.minval or v > self.maxval:
            return False
        if self.width < 0.5 and abs((h - self.hue + 0.5) % 1.0 - 0.5) > self.width:
            return False
        return not (self.keepskin and (is_skin_or_lips if self.keepskin == "lips" else is_skin)(h, s, v))

    def distance(self, h, s, v):
        """How far a colour is from this region's typical colour (hue counts 4x)."""
        dh = abs((h - self.hue + 0.5) % 1.0 - 0.5) * 4 if self.width < 0.5 else 0.0
        return dh * dh + (s - self.sat) ** 2 + (v - self.val) ** 2

    @property
    def swatch(self):
        return "#%02x%02x%02x" % tuple(self.rgb)

    def __repr__(self):
        return ("Region(%r, hue=%.3f, width=%.3f, sat %.2f-%.2f, val %.2f-%.2f%s, %.1f%%)"
                % (self.name, self.hue, self.width, self.minsat, self.maxsat, self.minval, self.maxval,
                   ", keepskin" if self.keepskin else "", self.weight * 100))


def hue_name(h, s, v):
    deg = (h % 1.0) * 360
    name = next(n for top, n in HUE_NAMES if deg < top)
    if name in ("orange", "yellow") and v < 0.6:
        return "brown"
    if name == "red" and v < 0.45:
        return "dark red"
    if s < 0.5 and v > 0.7:
        return "light " + name
    return name


# Hand-tuned regions where the detector alone is ambiguous, keyed by charID: a list of
# (name, overrides) applied to the detected regions in order, or full Region lists.  Mario's first
# region is exactly the band clone_character used before regions existed (byte-identical output).
REGION_OVERRIDES = {
    0: [Region("red (cap, shirt)", 0.0, 0.035, 0.5, keepskin=False, blockwise=False)],
    # Luigi's big cap emblem letter is a bluer green (hue 0.36-0.39) than his cap and shirt (0.29-0.33);
    # it has too few texels to become a region of its own, so the detected box (0.28-0.34) left it green
    1: [Region("green (cap, shirt)", 0.335, 0.07, 0.25, rgb=(30, 140, 30))],
    # Wario's tan skin (hue 30 deg, more saturated than most skin) touches his yellow cap's hues
    # (34 deg); the box starts in the valley at 39-40 deg, so the dark-gold brim shading at 37-38 deg is
    # left whole instead of being split texel by texel (specks)
    10: [Region("yellow (cap, shirt)", 0.136, 0.025, 0.6, minval=0.5, rgb=(230, 190, 20))],
    # Red Magikoopa's robe shades through orange into his yellow face without a gap in hue
    34: [Region("red (robe)", 0.005, 0.045, 0.8, rgb=(150, 20, 10))],
    # Birdo's hot pink is light and only half saturated: a hue shift alone gives pastel blues/greens
    17: [Region("pink", 0.901, 0.024, 0.54, minval=0.35, maxval=0.97, rgb=(220, 80, 170), sat=0.65, val=0.85, boost=1.25, lumk=0.25)],
    # White, black and grey characters have no hue to shift: tint regions (see Region).  Boo and
    # King Boo: the white body and face.  Their fangs are a faintly cyan white (saturation 0.05-0.1,
    # also the solid 16x16 teeth texture) where the body is neutral (< 0.045), so the fangs, the dark
    # eyes and the red mouth stay
    14: [Region("white (body)", 0.0, 0.5, 0.0, 0.045, 0.6, 1.0, False, rgb=(236, 236, 240), sat=0.01, val=0.93, tint=0.55)],
    37: [Region("white (body)", 0.0, 0.5, 0.0, 0.045, 0.6, 1.0, False, rgb=(236, 236, 240), sat=0.01, val=0.93, tint=0.55)],
    # Black Shy Guy: the near-black robe (v 0.1-0.35); the white mask and the black eye holes stay
    47: [Region("black (robe)", 0.0, 0.5, 0.0, 0.25, 0.07, 0.38, False, rgb=(52, 52, 56), sat=0.05, val=0.22, tint=0.8)],
    # Gray Dry Bones: the very pale cyan shell (hue 185-200 deg, saturation 0.1-0.3); the bluish-grey
    # bone around it (225-260 deg, saturation < 0.09) stays
    48: [Region("light cyan (shell)", 0.535, 0.035, 0.09, 0.6, 0.3, 1.0, False, rgb=(190, 225, 230), sat=0.18, val=0.8, tint=0.6)],
}
# Which detected regions a slot recolours by default (words of their names, a|b = either; main
# one first),
# where the biggest coloured cluster is not the character's own colour: the turtles' yellow skin,
# the colour variants' shared base colours (a Blue Toad's brown vest), Pianta palm hair.  Every
# entry is checked against the contact sheet of test_matrix.py.
# Friendlier names for the main characters' regions (what the colour is on the model).
REGION_NAMES = {
    1: {"green": "green (cap, shirt)", "blue": "blue (overalls)"},
    2: {"brown": "brown (fur)", "red": "red (tie)"},
    3: {"red": "red (cap, shirt)", "brown": "brown (fur)"},
    4: {"light pink": "pink (dress)", "red": "dark pink (dress)", "yellow": "yellow (hair)"},
    5: {"orange": "orange/yellow (dress)"},
    6: {"green": "green (body)", "orange": "orange (shoes)"},
    7: {"red": "red (cap, shirt)", "blue": "blue (overalls)"},
    8: {"green": "green (cap, shirt)", "blue": "blue (overalls)"},
    9: {"green": "green (shell)", "yellow": "yellow (belly)"},
    10: {"purple": "purple (overalls)"},
    11: {"purple": "purple (cap, shirt)"},
    12: {"green": "green (shell)"}, 42: {"red": "red (shell)"}, 20: {"red": "red (shell)"}, 43: {"green": "green (shell)"},
}
# Textures no region touches for one character, beyond the gloves (same colours as the outfit):
KEEP_TEXTURES = {
    5: {8: "hair (the same orange as the dress)"},
    7: {11: "mouth (the same red as the cap)"},
    20: {12: "mouth"}, 42: {12: "mouth"},
    39: {13: "mouth"},
    50: {0: "baseball", 8: "baseball"},           # red stitches
    52: {32: "flame (red shading into orange)"},
    14: {11: "baseball"}, 37: {0: "baseball"},     # off-white like the tinted body
}
REGION_DEFAULTS = {
    3: ("red",),                                   # Diddy: cap and shirt (fur is region 1)
    4: ("light pink",),                            # Peach: dress (her lips are its darker pink)
    5: ("orange", "yellow"),                       # Daisy: the whole dress
    9: ("green",), 19: ("green", "green 2"),                 # Bowser, Bowser Jr.: shell
    12: ("green",), 42: ("red",), 20: ("red",), 43: ("green",),   # Koopas / Paratroopas: shell
    21: ("cyan",), 22: ("pink|purple",), 23: ("yellow",),   # Piantas: body
    26: ("green", "green 2"),                      # Green Noki: shell and its dark lower stripe
    27: ("green",), 52: ("red",), 53: ("blue",),  # Bros: helmet and shell
    29: ("blue",), 30: ("yellow",), 31: ("green",), 32: ("purple",),  # Toad colours: cap spots
    28: ("purple", "purple 2"),                    # Toadsworth: cap spots and coat
    39: ("pink",),                                 # Dixie: beret and top
    44: ("blue",),                                 # Blue Shy Guy: robe
}


def _wpercentile(pairs, q):
    """Weighted percentile of [(value, weight)]."""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= q * total:
            return v
    return pairs[-1][0] if pairs else 0.0


def _bin_stats(cand, bins_):
    """(median saturation, median value) of the candidate colours in these hue bins."""
    sel = set(bins_)
    members = [c for c in cand if c[0] in sel]
    if not members:
        return 0.0, 0.0
    return (_wpercentile([(c[2], c[4]) for c in members], 0.5), _wpercentile([(c[3], c[4]) for c in members], 0.5))


def family_shared_textures(disc, dol, c):
    """Texture indices whose data is the same in every character that shares c's ARAM body entry
    (a colour family: Toads, Magikoopas, Shy Guys, ...).  Those are not the family colour (mouths,
    wands, buttons), so no region of a family member touches them."""
    body = dol.pair(c)[0]
    members = [m for m in range(NUM_CHARS) if dol.pair(m)[0] == body]
    if len(members) < 2:
        return frozenset()
    bodies = [Gpc(effective_body(disc, dol, m)) for m in members]
    n = bodies[0].tex_count
    if any(b.tex_count != n for b in bodies):
        return frozenset()
    return frozenset(i for i in range(n)
                     if len({texture_bytes(b, b.textures[i]) for b in bodies}) == 1)


def source_regions(disc, dol, c, gpc=None):
    """detect_regions() for charID c's in-match body, with its colour family's shared textures."""
    gpc = gpc or Gpc(effective_body(disc, dol, c))
    return detect_regions(gpc, c, family_shared_textures(disc, dol, c))


def detect_regions(gpc, char=None, keep=frozenset(), bins=72, min_share=0.015):
    """The character's recolourable colour regions, main one first: clusters of saturated hues over
    every body texture (weighted by texel count), leaving out the gloves, `keep` (texture indices,
    see family_shared_textures), skin tones, eyes and near-greys/whites/blacks.
    REGION_OVERRIDES[char] replaces or extends the result, REGION_DEFAULTS[char] picks the default."""
    skip = glove_textures(gpc) | frozenset(KEEP_TEXTURES.get(char, ())) | frozenset(keep)
    counts = {}
    for t in gpc.textures:
        if t["index"] not in skip:
            texture_colour_counts(texture_bytes(gpc, t), t, counts)
    total = float(sum(counts.values())) or 1.0
    cand = []
    hist = [0.0] * bins
    for rgb, n in counts.items():
        h, s, v = colorsys.rgb_to_hsv(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)
        if s < 0.2 or v < 0.15 or is_skin(h, s, v):
            continue
        b = int(h * bins) % bins
        cand.append((b, h, s, v, n, rgb))
        hist[b] += n
    sm = hist
    on = [x >= 0.0015 * total for x in sm]
    # contiguous runs of populated bins (wrapping around red), split at deep valleys
    runs = []
    if all(on):
        runs = [list(range(bins))]
    elif any(on):
        start = next(i for i in range(bins) if not on[i])
        cur = []
        for k in range(1, bins + 1):
            i = (start + k) % bins
            if on[i]:
                cur.append(i)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
    clusters = []
    for run in runs:
        parts, cur = [], [run[0]]
        for j in range(1, len(run) - 1):
            i = run[j]
            left = max(sm[k] for k in cur)
            right = max(sm[k] for k in run[j + 1:])
            if sm[i] < sm[run[j - 1]] and sm[i] <= sm[run[j + 1]] and sm[i] < 0.5 * min(left, right):
                parts.append(cur)
                cur = []
            cur.append(i)
        cur += run[len(run) - 1:] if len(run) > 1 else []
        parts.append(cur)
        # a valley between two shades of one colour (Wario's yellow and its orange shading) is not a
        # border: merge neighbours whose typical saturation and brightness are alike; keep DK's dark
        # fur and bright red tie apart
        merged = [parts[0]]
        for part in parts[1:]:
            a, b = _bin_stats(cand, merged[-1]), _bin_stats(cand, part)
            gap = (len(merged[-1]) + len(part)) / 2.0 / bins      # distance of the parts' centres
            if abs(a[0] - b[0]) < 0.22 and abs(a[1] - b[1]) < 0.22 and gap < 0.07:
                merged[-1] = merged[-1] + part
            else:
                merged.append(part)
        clusters += merged
    regions = []
    for bins_ in clusters:
        members = [c for c in cand if c[0] in set(bins_)]
        weight = sum(c[4] for c in members)
        if weight < min_share * total:
            continue
        # circular weighted mean hue
        sx = sum(math.cos(2 * math.pi * c[1]) * c[4] for c in members)
        sy = sum(math.sin(2 * math.pi * c[1]) * c[4] for c in members)
        hue = (math.atan2(sy, sx) / (2 * math.pi)) % 1.0
        width = _wpercentile([(abs((c[1] - hue + 0.5) % 1.0 - 0.5), c[4]) for c in members], 0.99) + 0.015
        s_lo = _wpercentile([(c[2], c[4]) for c in members], 0.01) - 0.04
        v_lo = _wpercentile([(c[3], c[4]) for c in members], 0.01) - 0.04
        s_med = _wpercentile([(c[2], c[4]) for c in members], 0.5)
        v_med = _wpercentile([(c[3], c[4]) for c in members], 0.5)
        v_hi = _wpercentile([(c[3], c[4]) for c in members], 0.99)
        rgb = tuple(int(sum(c[5][k] * c[4] for c in members) / weight) for k in range(3))
        regions.append(Region(hue_name(hue, s_med, v_med), hue, width, max(0.2, s_lo - 0.04), 1.0,
                              max(0.1, v_lo - 0.08), min(1.0, v_hi + 0.08), True, skip, weight / total, rgb, s_med, v_med))
    regions.sort(key=lambda r: -r.weight)
    seen = {}
    for r in regions:
        seen[r.name] = seen.get(r.name, 0) + 1
        if seen[r.name] > 1:
            r.name = "%s %d" % (r.name, seen[r.name])
    ov = REGION_OVERRIDES.get(char)
    if ov:
        ov = [copy.copy(o) for o in ov]
        for o in ov:
            o.skip = o.skip | skip if o.keepskin or o.tint is not None else o.skip   # Mario's band stays exactly as it was
            o.weight = sum(n for rgb, n in counts.items()
                           if o.contains(*colorsys.rgb_to_hsv(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0))) / total
        regions = ov + [r for r in regions if not any(o.width < 0.5 and _overlaps(r, o) for o in ov)]
    want = REGION_DEFAULTS.get(char, ())
    picked = []
    for prefix in want:
        r = next((r for r in regions if any(w in r.name for w in prefix.split("|")) and r not in picked), None)
        if r is not None:
            picked.append(r)
    if not picked and regions:
        picked = [regions[0]]
    for r in picked:
        r.default = True
    names = REGION_NAMES.get(char, {})
    for r in regions:
        r.name = names.get(r.name, r.name)
    return picked + [r for r in regions if r not in picked]


def _overlaps(a, b):
    return abs((a.hue - b.hue + 0.5) % 1.0 - 0.5) <= a.width + b.width


# ----------------------------------------------------------------------------------------------
# UI texture files (TextureArchiveHeader at the root; CI8 textures with a TLUT)
# ----------------------------------------------------------------------------------------------
class TextureFile:
    """A UI resource whose root word is the texture archive offset (the HUD portrait file).
    Each 0x20 header: {u32 dataOff, u32 tlutOff, u16 h, u16 w, ..., u8 fmt @0x17, u16 colours @0x18,
    u8 tlutFmt @0x1A}; offsets are archive-relative.  CI8 index data is stored in 8x4 tiles."""

    def __init__(self, data):
        self.data = bytearray(data)
        self.archive = struct.unpack_from(">I", self.data, 0)[0]
        self.count = struct.unpack_from(">H", self.data, self.archive)[0]
        self.textures = []
        for i in range(self.count):
            h = self.archive + 4 + i * 0x20
            data_off, tlut_off, height, width = struct.unpack_from(">IIHH", self.data, h)
            fmt = self.data[h + 0x17]
            colours = struct.unpack_from(">H", self.data, h + 0x18)[0]
            tlut_fmt = self.data[h + 0x1A]
            size = None
            if fmt == TEX_FMT_CI8:
                size = align(width, 8) * align(height, 4)
            elif fmt == TEX_FMT_CI4:
                size = align(width, 8) * align(height, 8) // 2
            self.textures.append({"index": i, "hdr": h, "data": self.archive + data_off, "tlut": self.archive + tlut_off,
                                  "w": width, "h": height, "fmt": fmt, "colours": colours, "tlut_fmt": tlut_fmt, "size": size})

    def portraits(self):
        """The per-charID 50x50 CI8 portraits, in charID order (54 of them)."""
        out = [t for t in self.textures if (t["w"], t["h"], t["fmt"]) == (PORTRAIT_W, PORTRAIT_H, TEX_FMT_CI8)]
        if len(out) != NUM_CHARS:
            raise ValueError("portrait file: expected %d 50x50 CI8 textures, found %d" % (NUM_CHARS, len(out)))
        for t in out:
            if t["colours"] != 256 or t["tlut_fmt"] != TLUT_FMT_RGB5A3:
                raise ValueError("portrait %d is not CI8 with a 256-entry RGB5A3 palette" % t["index"])
        return out

    def tlut(self, t):
        return bytes(self.data[t["tlut"]:t["tlut"] + t["colours"] * 2])

    def pixels(self, t):
        return bytes(self.data[t["data"]:t["data"] + t["size"]])


def recolour_tlut(tlut, rc):
    out = bytearray(tlut)
    for o in range(0, len(out), 2):
        struct.pack_into(">H", out, o, rc.map_rgb5a3(struct.unpack_from(">H", out, o)[0]))
    return bytes(out)


def copy_portrait(tf, S, C, rc):
    """Portrait S := portrait C with its palette recoloured (index data copied as is)."""
    por = tf.portraits()
    src, dst = por[C], por[S]
    if src["size"] != dst["size"]:
        raise ValueError("portrait sizes differ")
    tf.data[dst["data"]:dst["data"] + dst["size"]] = tf.pixels(src)
    tf.data[dst["tlut"]:dst["tlut"] + 512] = recolour_tlut(tf.tlut(src), rc.for_palette())


def portrait_desc(dol):
    """The 54-portrait file descriptor; all DOL rows that name it must agree (and look retail)."""
    d = dol.desc(ADDR_PORTRAIT_DESC, 0)
    if dol.desc(ADDR_PORTRAIT_DESC2, 0) != d:
        raise SystemExit("main.dol: the two portrait-file descriptor rows disagree (patched DOL?)")
    if d[0] != LZSS_FLAGS or not (d[1] & SIZE_COMPRESSED_BIT):
        raise SystemExit("main.dol: portrait file descriptor is not an LZSS file")
    return d


# ----------------------------------------------------------------------------------------------
# Menu art (character_select.md section 8): charID-keyed names, mini pictures and grid portraits
# ----------------------------------------------------------------------------------------------
def name_texture(c):
    """Common-file texture of charID c's roster preview name."""
    return c if c < 29 else c + 28


def frame_textures(data, sequence):
    """{frame: texture index} of a UI sprite-bank sequence with one image list (the character
    name/picture/portrait lists).  The bank is root word 1; bank+8 is a relative sequence table;
    each sequence is {count, header list, image list}.  Offsets in each table are relative to that
    table.  Records are type 0 (a full template) or type 2 (word deltas over the template, indices
    counted after the record's first word), stored in descending frame order; the texture index is
    the expanded record's halfword at +6."""
    u16 = lambda o: struct.unpack_from(">H", data, o)[0]   # noqa: E731
    u32 = lambda o: struct.unpack_from(">I", data, o)[0]   # noqa: E731
    bank = u32(4)
    table = bank + u32(bank + 8)
    if sequence >= u32(table):
        raise ValueError("sequence outside sprite bank")
    seq = table + u32(table + 8 + sequence * 4)
    if (u32(seq) & 0x7FFFFFFF) != 2:
        raise ValueError("expected header list plus one image list")
    group = seq + u32(seq + 12)
    count, stride = struct.unpack_from(">HH", data, group)
    pos = group + 4
    template = data[pos:pos + stride]
    frames = {}
    for _ in range(count):
        kind = u16(pos) & 0x7FFF
        frame = u16(pos + 2)
        record = bytearray(template)
        if kind == 0:
            size = stride
        elif kind == 2:
            record[:4] = data[pos:pos + 4]
            changes, words = struct.unpack_from(">HH", data, pos + 4)
            size = words * 4
            values = pos + size - changes * 4
            for i in range(changes):
                dst = 4 + u16(pos + 8 + i * 2) * 4
                if dst + 4 > stride:
                    raise ValueError("delta outside expanded record")
                record[dst:dst + 4] = data[values + i * 4:values + i * 4 + 4]
        else:
            raise ValueError("unexpected record type %d in a character list" % kind)
        if frame in frames:
            raise ValueError("duplicate frame")
        frames[frame] = struct.unpack_from(">H", record, 6)[0]
        pos += size
    if set(frames) != set(range(count)):
        raise ValueError("character frames are not contiguous")
    return frames


def load_menu_file(disc, dol, addr):
    """(unpacked bytes, descriptor) of an LZSS-packed ZZZZ.dat UI file named by a DOL descriptor."""
    d = dol.desc(addr, 0)
    if d[0] != LZSS_FLAGS or not d[1] & SIZE_COMPRESSED_BIT:
        raise SystemExit("main.dol: descriptor at %08X is not an LZSS file" % addr)
    return lzss_decompress(disc.read_zzzz(d[2], d[3]), d[1] & 0x0FFFFFFF)[0], d


def menu_art_map(common, grid):
    """charID -> texture index for the three charID-keyed menu images, read from the files' own
    sprite banks (frame 54 is the empty-slot placeholder and is dropped)."""
    m = {"name": frame_textures(common, SEQ_NAME), "mini": frame_textures(common, SEQ_MINI),
         "grid": frame_textures(grid, SEQ_GRID)}
    for k in m:
        m[k] = {c: m[k][c] for c in range(NUM_CHARS)}
    if m["name"] != {c: name_texture(c) for c in range(NUM_CHARS)} or m["mini"] != {c: 153 + c for c in range(NUM_CHARS)} \
            or m["grid"] != {c: 4 + c for c in range(NUM_CHARS)}:
        raise SystemExit("menu art: the name/picture/portrait frame lists are not the retail ones (patched disc?)")
    return m


class MenuTextures:
    """Edits a UI texture file by rewriting texture headers.  retarget(dst, src) makes texture dst
    use src's pixel buffer and either src's palette or a private copy of a new one.  No buffer that
    another header still references is ever written: a private palette goes into a buffer that no
    header references any more (dst's old palette, its old pixels, or buffers freed by earlier
    retargets, e.g. a donor's old name pixels), and only if there is none is it appended (the file
    then grows)."""

    def __init__(self, data):
        self.tf = TextureFile(data)
        self.free = []          # candidate (start, end) ranges freed by retargets
        self.grown = 0
        for t in self.tf.textures:
            if t["size"] is None:
                raise SystemExit("menu art: texture %d has unsupported format %d" % (t["index"], t["fmt"]))

    @property
    def data(self):
        return self.tf.data

    def tex(self, i):
        return self.tf.textures[i]

    def tlut(self, i):
        return self.tf.tlut(self.tex(i))

    def _referenced(self, start, end):
        for t in self.tf.textures:
            for s, n in ((t["data"], t["size"]), (t["tlut"], t["colours"] * 2)):
                if s < end and start < s + n:
                    return True
        return False

    def _set(self, i, data=None, tlut=None):
        t = self.tex(i)
        if data is not None:
            struct.pack_into(">I", self.tf.data, t["hdr"], data - self.tf.archive)
            t["data"] = data
        if tlut is not None:
            struct.pack_into(">I", self.tf.data, t["hdr"] + 4, tlut - self.tf.archive)
            t["tlut"] = tlut

    def _alloc(self, n):
        for k, (s, e) in enumerate(self.free):
            if e - s >= n and not self._referenced(s, e):
                rest = align(s + n, 0x20)
                self.free[k] = (rest, e)
                return s
        off = align(len(self.tf.data), 0x20)
        self.tf.data[len(self.tf.data):] = b"\0" * (off + n - len(self.tf.data))
        self.grown += n
        return off

    def retarget(self, dst, src, tlut=None):
        d, s = self.tex(dst), self.tex(src)
        if (d["w"], d["h"], d["fmt"], d["colours"], d["tlut_fmt"]) != (s["w"], s["h"], s["fmt"], s["colours"], s["tlut_fmt"]):
            raise SystemExit("menu art: textures %d and %d differ in size/format" % (dst, src))
        old = [(d["tlut"], d["tlut"] + d["colours"] * 2), (d["data"], d["data"] + d["size"])]
        self._set(dst, data=s["data"], tlut=s["tlut"])
        self.free = old + self.free     # dst's own buffers first: an unshared palette is rewritten in place
        if tlut is None or tlut == self.tlut(src):
            return
        if len(tlut) != s["colours"] * 2:
            raise ValueError("palette size")
        off = self._alloc(len(tlut))
        self.tf.data[off:off + len(tlut)] = tlut
        self._set(dst, tlut=off)


def patch_menu_art(common, grid, C, clones):
    """Donor S's preview name, mini picture and grid portrait := the source C's (the two images with
    their palettes recoloured by the slot's spec).  Returns the two MenuTextures editors."""
    amap = menu_art_map(common, grid)
    ce, ge = MenuTextures(common), MenuTextures(grid)
    for S, _ in clones:             # names first: each frees the donor's own name pixels
        ce.retarget(amap["name"][S], amap["name"][C])
    for S, rc in clones:
        pal = rc.for_palette()
        ce.retarget(amap["mini"][S], amap["mini"][C], recolour_tlut(ce.tlut(amap["mini"][C]), pal))
        ge.retarget(amap["grid"][S], amap["grid"][C], recolour_tlut(ge.tlut(amap["grid"][C]), pal))
    return ce, ge


def rel_header(rel):
    """(module id, [(file offset, size)] per section, [(module id, relocation offset)])."""
    mid, = struct.unpack_from(">I", rel, 0)
    nsec, secoff = struct.unpack_from(">II", rel, 0x0C)
    impoff, impsize = struct.unpack_from(">II", rel, 0x28)
    secs = [struct.unpack_from(">II", rel, secoff + i * 8) for i in range(nsec)]
    imps = [struct.unpack_from(">II", rel, impoff + k * 8) for k in range(impsize // 8)]
    return mid, [(o & ~1, n) for o, n in secs], imps


def rel_self_relocs(rel):
    """Yield (entry file offset, section, offset in section, type, target section, addend) for every
    relocation a REL applies against itself."""
    mid, _, imps = rel_header(rel)
    for m, p in imps:
        if m != mid:
            continue
        sec = None
        cur = 0
        while True:
            delta, kind, tsec, addend = struct.unpack_from(">HBBI", rel, p)
            if kind == 203:                 # R_DOLPHIN_END
                break
            if kind == 202:                 # R_DOLPHIN_SECTION
                sec, cur = tsec, 0
            else:
                cur += delta
                if kind != 201:             # R_DOLPHIN_NOP only advances
                    yield p, sec, cur, kind, tsec, addend
            p += 8


def colour_word_entries(rel):
    """{charID: relocation entry file offset} for fn_2_74F40's colour-word jump table, checked
    against the retail switch (each addend is the case of its retail label; each case is li r0, N)."""
    _, secs, _ = rel_header(rel)
    text = secs[MENUS_TEXT_SEC][0]
    for lab, case in MENUS_COLOUR_CASE.items():
        if struct.unpack_from(">I", rel, text + case)[0] != 0x38000000 | lab:
            raise SystemExit("menus.rel: fn_2_74F40's colour-word case %d is not li r0, %d" % (lab, lab))
    found = {}
    for p, sec, off, kind, tsec, addend in rel_self_relocs(rel):
        if sec == MENUS_DATA_SEC and MENUS_COLOUR_JT <= off < MENUS_COLOUR_JT + 4 * (NUM_CHARS - MENUS_COLOUR_FIRST):
            c = MENUS_COLOUR_FIRST + (off - MENUS_COLOUR_JT) // 4
            if kind != 1 or tsec != MENUS_TEXT_SEC or addend != MENUS_COLOUR_CASE[MENUS_COLOUR_LABEL.get(c, 0)]:
                raise SystemExit("menus.rel: colour-word jump-table entry for charID %d is not retail" % c)
            found[c] = p
    if sorted(found) != list(range(MENUS_COLOUR_FIRST, NUM_CHARS)):
        raise SystemExit("menus.rel: colour-word jump table not found")
    return found


def patch_colour_word(rel, donors):
    """Donor charIDs show label 0 (no colour word).  Returns the edited relocation entry offsets."""
    found = colour_word_entries(rel)
    out = []
    for S in donors:
        if MENUS_COLOUR_LABEL.get(S, 0):
            struct.pack_into(">I", rel, found[S] + 4, MENUS_COLOUR_CASE[0])
            out.append(found[S] + 4)
    return out


def replace_desc(buf, old, new):
    """Replace every copy of descriptor tuple `old` in buf; returns how many."""
    a, b = struct.pack(">4I", *old), struct.pack(">4I", *new)
    n = 0
    i = buf.find(a)
    while i >= 0:
        buf[i:i + 16] = b
        n += 1
        i = buf.find(a, i + 16)
    return n


# ----------------------------------------------------------------------------------------------
# Disc folder
# ----------------------------------------------------------------------------------------------
class Disc:
    def __init__(self, root):
        self.root = Path(root)
        self.sys = self.root / "sys"
        self.files = self.root / "files"
        for p in (self.sys / "main.dol", self.files / "ZZZZ.dat", self.sys / "fst.bin"):
            if not p.exists():
                raise SystemExit("not an extracted GameCube disc folder (missing %s)" % p)

    def read_zzzz(self, off, size):
        with open(self.files / "ZZZZ.dat", "rb") as f:
            f.seek(off)
            return f.read(size)

    def read_aaaa(self, off, size):
        with open(self.files / "aaaa.dat", "rb") as f:
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


def load_rel(disc, dol, idx):
    """menus.rel (0), game.rel (1) or challenge.rel (2) as the game loads it: LZSS-packed in
    aaaa.dat via its DOL descriptor."""
    flags, sizeflags, off, comp = dol.desc(ADDR_REL_DESCS[idx], 0)
    raw = disc.read_aaaa(off, comp)
    if sizeflags & SIZE_COMPRESSED_BIT:
        return lzss_decompress(raw, sizeflags & 0x0FFFFFFF)[0]
    return raw[:sizeflags & 0x0FFFFFFF]


def load_game_rel(disc, dol):
    return load_rel(disc, dol, 1)


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


def dol_size(dol_hdr):
    """End of the furthest section in a DOL header (7 text + 11 data sections)."""
    offs = struct.unpack_from(">18I", dol_hdr, 0x00)
    sizes = struct.unpack_from(">18I", dol_hdr, 0x90)
    return max(o + s for o, s in zip(offs, sizes) if s)


def extract_iso(iso_path, dest, log):
    """Unpack a GameCube .iso into Dolphin's extracted layout (dest/sys + dest/files)."""
    dest = Path(dest)
    with open(iso_path, "rb") as f:
        def read(off, n):
            f.seek(off)
            b = f.read(n)
            if len(b) != n:
                raise SystemExit("%s is truncated or not a GameCube disc image" % iso_path)
            return b

        boot = read(0, 0x440)
        if struct.unpack_from(">I", boot, 0x1C)[0] != 0xC2339F3D:
            raise SystemExit("%s is not a GameCube disc image (no disc magic); .rvz/.nkit images must be "
                             "converted to .iso first (Dolphin: right-click the game -> Convert File -> ISO)" % iso_path)
        dol_off, fst_off, fst_size = struct.unpack_from(">III", boot, 0x420)
        apl_hdr = read(0x2440, 0x20)
        apl_size = 0x20 + sum(struct.unpack_from(">II", apl_hdr, 0x14))
        fst = read(fst_off, fst_size)
        sysd, filesd = dest / "sys", dest / "files"
        sysd.mkdir(parents=True)
        filesd.mkdir()
        (sysd / "boot.bin").write_bytes(boot)
        (sysd / "bi2.bin").write_bytes(read(0x440, 0x2000))
        (sysd / "apploader.img").write_bytes(read(0x2440, apl_size))
        (sysd / "main.dol").write_bytes(read(dol_off, dol_size(read(dol_off, 0x100))))
        (sysd / "fst.bin").write_bytes(fst)
        log("extracting %s -> %s" % (iso_path, dest))
        for e, path in fst_paths(fst):
            out = filesd / path
            out.parent.mkdir(parents=True, exist_ok=True)
            f.seek(e["offset"])
            left = e["size"]
            with open(out, "wb") as g:
                while left:
                    b = f.read(min(left, 1 << 20))
                    if not b:
                        raise SystemExit("%s is truncated (%s)" % (iso_path, path))
                    g.write(b)
                    left -= len(b)


def open_disc(path, log, scratch):
    """--disc may be an extracted folder or a .iso; an ISO is unpacked into `scratch` first."""
    path = Path(path)
    if path.is_file():
        extract_iso(path, scratch, log)
        return Disc(scratch)
    return Disc(path)


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
def list_regions(disc, c):
    dol = Dol((disc.sys / "main.dol").read_bytes())
    print("colour regions of %d %s (use region=N in a recolour spec; default = the ones marked *):" % (c, CHAR_NAMES[c]))
    for i, r in enumerate(source_regions(disc, dol, c)):
        print("  %s%d  %-22s %s  %5.1f%% of the body texels   from=%.3f width=%.3f minsat=%.2f minval=%.2f maxval=%.2f"
              % ("*" if r.default else " ", i, r.name, r.swatch, 100 * r.weight, r.hue, r.width, r.minsat, r.minval, r.maxval))


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


RETAIL_DOL_SIZE = 1139968
RETAIL_ZZZZ_SIZE = 450041856
RETAIL_BLOB_DESC = (0, 0x004D6600, 0x1A15E800, 0x004D6600)


def preflight(disc, dol, log):
    """Make sure --disc is an unmodified GYQE01 dump; explains the most likely mistakes."""
    problems = []
    dol_size = (disc.sys / "main.dol").stat().st_size
    if dol_size != RETAIL_DOL_SIZE:
        problems.append("sys/main.dol is %d bytes, retail GYQE01 is %d (wrong game/region or modified DOL)" % (dol_size, RETAIL_DOL_SIZE))
    zz = disc.zzzz_size()
    if zz != RETAIL_ZZZZ_SIZE:
        problems.append("files/ZZZZ.dat is %d bytes, retail is %d (%s)" % (zz, RETAIL_ZZZZ_SIZE,
                        "already patched: point --disc at the clean extraction" if zz > RETAIL_ZZZZ_SIZE else "truncated extraction"))
    try:
        if dol.desc(ADDR_MASTER, MASTER_BLOB_ENTRY) != RETAIL_BLOB_DESC:
            problems.append("main.dol blob descriptor is not retail (already patched DOL?)")
        if dol.blob_alloc_size() != RETAIL_BLOB_DESC[3]:
            problems.append("main.dol ARAM allocation already patched")
        if dol.desc(ADDR_SUBFILES, 0)[2] != 0x09439000 or dol.pair(0) != (0, -1):
            problems.append("main.dol character tables do not look like retail GYQE01")
        if dol.desc(ADDR_MENU_COMMON_DESC, 0) != MENU_COMMON_RETAIL or dol.desc(ADDR_MENU_GRID_DESC, 0) != MENU_GRID_RETAIL:
            problems.append("main.dol menu art descriptors are not retail (already patched DOL?)")
        if dol.read(ADDR_FINDCHAR, len(FINDCHAR_RETAIL)) != FINDCHAR_RETAIL:
            problems.append("main.dol fn_800698F8 (findCharacterID) is not retail (already patched DOL?)")
    except ValueError as e:
        problems.append(str(e))
    boot = disc.sys / "boot.bin"
    if boot.exists() and boot.read_bytes()[:6] != b"GYQE01":
        problems.append("sys/boot.bin game id is %r, expected GYQE01 (USA)" % boot.read_bytes()[:6])
    if problems:
        for p in problems:
            log("preflight: " + p)
        raise SystemExit("--disc must be a clean, complete Dolphin extraction of the USA disc (GYQE01)")
    log("preflight: clean retail GYQE01 dump")


def clone(args, log=print):
    """Run the patch described by `args` (see main()); `log` gets every progress line.  Returns
    {"source": C, "slots": [(S, spec), ...], "out": Path, "iso": Path|None, "dry_run": bool}.
    Errors are raised as SystemExit(<plain message>), as on the command line."""
    dry = args.dry_run
    out = Path(args.out)
    from_iso = Path(args.disc).is_file()
    if from_iso and dry:
        # ponytail: a dry run from an ISO still unpacks it (to a temp folder) to read the tables
        tmp = tempfile.TemporaryDirectory()
        src_disc = open_disc(args.disc, log, Path(tmp.name) / "disc")
    elif from_iso:
        if out.exists():
            if not args.force:
                raise SystemExit("%s exists; use --force to overwrite it (or delete it to start over)" % out)
            shutil.rmtree(out)
        src_disc = open_disc(args.disc, log, out)  # unpack straight into --out, then patch in place
    else:
        src_disc = Disc(args.disc)
    preflight(src_disc, Dol((src_disc.sys / "main.dol").read_bytes()), log)
    if dry:
        log("DRY RUN: nothing will be written")
        disc = src_disc
    elif from_iso:
        disc = src_disc
    else:
        if out.exists():
            if not args.force:
                raise SystemExit("%s exists; use --force to overwrite it (or delete it to start over)" % out)
            shutil.rmtree(out)
        log("copying %s -> %s (this includes the 450 MB ZZZZ.dat)" % (src_disc.root, out))
        shutil.copytree(src_disc.root, out)
        disc = Disc(out)

    dol = Dol((disc.sys / "main.dol").read_bytes())
    C = parse_char(args.source)
    clones = [parse_slot_spec(s) for s in args.slot]
    if not clones:
        clones = [(29, Recolour("hue=blue")), (30, Recolour("hue=green")), (31, Recolour("hue=purple")), (32, Recolour("hue=yellow"))]
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
    if C in UNSUPPORTED_SOURCES:
        raise SystemExit("%s can't be cloned" % CHAR_NAMES[C])
    src_tracks = [Gpc(load_subfile(disc, dol, C * SUBFILE_TRACKS + t)) for t in (0, 1)]
    src_body = Gpc(effective_body(disc, dol, C))
    for g in src_tracks + [src_body]:
        g.check()
    log("  track0 %d B, track1 %d B, ARAM body %d B, texture archive %d B (%d textures)" % (
        len(src_tracks[0].data), len(src_tracks[1].data), len(src_body.data), src_body.archive_size, src_body.tex_count))
    regions = source_regions(disc, dol, C, src_body)
    log("  colour regions: " + "; ".join("%d %s%s" % (i, r.name, " (default)" if r.default else "")
                                         for i, r in enumerate(regions)))
    for S, rc in clones:
        rc.resolve(regions)

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
    inplace = []         # (offset, bytes) rewritten inside the existing ZZZZ.dat
    rel_edits = {0: [], 1: [], 2: []}   # REL index -> [(label, fn(rel bytearray) -> log line)]
    desc_moves = []      # (label, DOL address, old descriptor, new descriptor) of moved menu files

    # --- presentation: unique index (names/portraits/announcer/voice bank) and the HUD portrait
    ui_source = args.ui == "source"
    if ui_source:
        src_unique = dol.u8(ADDR_FAMILY + C * 6 + FAMILY_UNIQUE)
        for S, rc in clones:
            dol.write(ADDR_FAMILY + S * 6 + FAMILY_UNIQUE, bytes([src_unique]))
        dol.write(ADDR_FINDCHAR, FINDCHAR_PATCH)
        log("ui: unique index of %s -> %d (%s's); fn_800698F8 rewritten to read lbl_800E8558[charID].uniqueIdx" % (
            ", ".join(CHAR_NAMES[S] for S, _ in clones), src_unique, CHAR_NAMES[C]))
        pdesc = portrait_desc(dol)
        pfile = TextureFile(lzss_decompress(disc.read_zzzz(pdesc[2], pdesc[3]), pdesc[1] & 0x0FFFFFFF)[0])
        for S, rc in clones:
            copy_portrait(pfile, S, C, rc)
        pcomp = lzss_compress_optimal(bytes(pfile.data))
        if lzss_decompress(pcomp, len(pfile.data))[0] != bytes(pfile.data):
            raise SystemExit("internal error: LZSS round trip failed (portraits)")
        if len(pcomp) > pdesc[3]:
            raise SystemExit("the re-packed HUD portrait file is %d B but only %d B fit at its disc offset; rerun with --ui donor"
                             % (len(pcomp), pdesc[3]))
        inplace.append((pdesc[2], pcomp + b"\0" * (pdesc[3] - len(pcomp))))
        log("ui: HUD portraits %s := %s's (palette recoloured); file re-packed in place at ZZZZ.dat+0x%08X (%d of %d B)" % (
            ", ".join(CHAR_NAMES[S] for S, _ in clones), CHAR_NAMES[C], pdesc[2], len(pcomp), pdesc[3]))
        # menu art (character_select.md section 8): preview name, mini picture, grid portrait
        common, cdesc = load_menu_file(disc, dol, ADDR_MENU_COMMON_DESC)
        grid, gdesc = load_menu_file(disc, dol, ADDR_MENU_GRID_DESC)
        ce, ge = patch_menu_art(common, grid, C, clones)
        log("ui: menus: preview name, mini picture and grid portrait of %s := %s's (pictures recoloured)" % (
            ", ".join(CHAR_NAMES[S] for S, _ in clones), CHAR_NAMES[C]))
        for label, addr, desc, ed in (("common menu file", ADDR_MENU_COMMON_DESC, cdesc, ce), ("grid portrait file", ADDR_MENU_GRID_DESC, gdesc, ge)):
            data = bytes(ed.data)
            comp = lzss_compress_optimal(data)
            if lzss_decompress(comp, len(data))[0] != data:
                raise SystemExit("internal error: LZSS round trip failed (%s)" % label)
            if len(data) == desc[1] & 0x0FFFFFFF and len(comp) <= desc[3] and not getattr(args, "relocate_menu_art", False):
                inplace.append((desc[2], comp + b"\0" * (desc[3] - len(comp))))
                log("ui: %s re-packed in place at ZZZZ.dat+0x%08X (%d of %d B, %d B to spare)" % (
                    label, desc[2], len(comp), desc[3], desc[3] - len(comp)))
            else:
                new = (desc[0], SIZE_COMPRESSED_BIT | len(data), place(comp, 0x800), len(comp))
                n = replace_desc(dol.data, desc, new)
                if n != MENU_DESC_COPIES[addr][0]:
                    raise SystemExit("main.dol: expected %d copies of the %s descriptor, found %d" % (MENU_DESC_COPIES[addr][0], label, n))
                desc_moves.append((label, addr, desc, new))
                log("ui: %s (%d -> %d B%s) moved to ZZZZ.dat+0x%08X; %d DOL descriptor copies updated, REL copies below" % (
                    label, len(data), len(comp), ", grew %d B" % ed.grown if ed.grown else "", new[2], n))
        if getattr(args, "colour_word", "none") == "none":
            rel_edits[0].append(("colour word", lambda rel: "colour word: label 0 for charIDs %s (relocation addends at %s)" % (
                [S for S, _ in clones], ", ".join("0x%X" % o for o in patch_colour_word(rel, [S for S, _ in clones])) or "none needed")))
        else:
            log("ui: colour word: the donors keep theirs (--colour-word donor)")
    else:
        log("ui: donor (menus, HUD and announcer keep the donor's name/portrait/call-out)")

    for S, rc in clones:
        log("clone -> slot %d %s  (%s; recolours %s)" % (S, CHAR_NAMES[S], rc.spec, rc.describe()))
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

    # --- game.rel gameplay tables (the game never reads a files/game.rel; that file only exists in
    # decomp setups)
    if not args.no_gameplay:
        def gameplay_tables(rel):
            base, size = rel_data_section(rel)
            for off, rs in [REL_VOICE] + REL_TABLES:
                if off + NUM_CHARS * rs > size:
                    raise SystemExit("game.rel table at 0x%X is outside .data" % off)
                for S, _ in report:
                    rel[base + off + S * rs:base + off + (S + 1) * rs] = rel[base + off + C * rs:base + off + (C + 1) * rs]
            return "copied voice/hitbox/timing tables"
        rel_edits[1].append(("gameplay tables", gameplay_tables))
    for label, addr, old, new in desc_moves:
        for i in range(3):
            def move(rel, label=label, addr=addr, old=old, new=new, i=i):
                n = replace_desc(rel, old, new)
                if n != MENU_DESC_COPIES[addr][1 + i]:
                    raise SystemExit("%s: expected %d copies of the %s descriptor, found %d" % (REL_NAMES[i], MENU_DESC_COPIES[addr][1 + i], label, n))
                return "%d %s descriptor copies updated" % (n, label)
            if MENU_DESC_COPIES[addr][1 + i]:
                rel_edits[i].append((label, move))

    # --- RELs: each edited one is re-packed in place inside aaaa.dat (growing aaaa.dat would shift
    # other disc files)
    rel_patches = []
    rel_ends = sorted(dol.desc(a, 0)[2] for a in ADDR_REL_DESCS) + [(disc.files / "aaaa.dat").stat().st_size]
    for i, edits in rel_edits.items():
        if not edits:
            continue
        old = dol.desc(ADDR_REL_DESCS[i], 0)
        rel = bytearray(load_rel(disc, dol, i))
        lines = [fn(rel) for _, fn in edits]
        comp = lzss_compress_optimal(bytes(rel))
        if lzss_decompress(comp, len(rel))[0] != bytes(rel):
            raise SystemExit("internal error: LZSS round trip failed (%s)" % REL_NAMES[i])
        room = min(e for e in rel_ends if e > old[2]) - old[2]
        if len(comp) > room:
            raise SystemExit("patched %s packs to %d B but only %d B fit in aaaa.dat; rerun with %s" % (
                REL_NAMES[i], len(comp), room, "--no-gameplay" if i == 1 else "--ui donor"))
        rel_patches.append((old[2], comp + b"\0" * max(0, old[3] - len(comp))))
        dol.set_desc(ADDR_REL_DESCS[i], 0, (old[0], SIZE_COMPRESSED_BIT | len(rel), old[2], len(comp)))
        log("%s: %s; re-packed in place at aaaa.dat+0x%08X (%d B, room %d B)" % (REL_NAMES[i], "; ".join(lines), old[2], len(comp), room))

    if dry:
        log("DRY RUN summary: %d B in %d chunks appended to ZZZZ.dat from 0x%08X; %d in-place rewrite(s); main.dol/stats/game.rel rows for slots %s" % (
            cursor - append_at, len(chunks), append_at, len(inplace), [S for S, _ in report]))
        log("DRY RUN: no files written")
        return {"source": C, "slots": [(S, rc.spec) for S, rc in report], "out": out, "iso": None, "dry_run": True}

    # --- write ZZZZ.dat: in-place rewrites, then the appendix
    with open(zz_path, "r+b") as f:
        for off, b in inplace + chunks:
            f.seek(off)
            f.write(b)
        f.seek(0, 2)
        end = f.tell()
        pad = align(end, 0x8000) - end
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

    if rel_patches:
        with open(disc.files / "aaaa.dat", "r+b") as f:
            for off, b in rel_patches:
                f.seek(off)
                f.write(b)

    # --- fst.bin and DOL
    fst = (disc.sys / "fst.bin").read_bytes()
    (disc.sys / "fst.bin").write_bytes(patch_fst_size(fst, "ZZZZ.dat", new_zz_size))
    (disc.sys / "main.dol").write_bytes(bytes(dol.data))
    log("main.dol and fst.bin written")

    if args.iso:
        write_iso(disc, Path(args.iso), log)

    log("")
    log("Done.  In the game, pick: " + ", ".join("%s (slot %d) -> %s" % (CHAR_NAMES[S], S, rc.spec) for S, rc in report))
    if ui_source:
        log("Menus (preview name, mini picture, grid portrait), HUD and the announcer show/say %s for these slots%s." % (
            CHAR_NAMES[C], "" if getattr(args, "colour_word", "none") == "none" else "; the donors keep their colour word"))
    log("Boot: Dolphin -> File -> Open -> %s" % (out / "sys" / "main.dol"))
    return {"source": C, "slots": [(S, rc.spec) for S, rc in report], "out": out,
            "iso": Path(args.iso) if args.iso else None, "dry_run": False}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="""examples:
  py clone_character.py --disc "C:\\mssb\\Mario Superstar Baseball (US).iso" --out "C:\\mssb\\marios" --iso "C:\\mssb\\marios.iso"
  py clone_character.py --disc "C:\\mssb\\GYQE01" --list-slots
  py clone_character.py --disc "C:\\mssb\\GYQE01" --list-regions --source luigi
  py clone_character.py --disc "C:\\mssb\\GYQE01" --out "C:\\mssb\\GYQE01-luigi" --source mario --slot Luigi:green
  py clone_character.py --disc "C:\\mssb\\GYQE01" --out "C:\\mssb\\GYQE01-marios" --source mario --slot BlueToad:blue --slot YellowToad:green --slot GreenToad:purple --slot PurpleToad:yellow
  py clone_character.py --disc ... --out ... --source 0 --slot 29:hue=0.62,sat=1.1 --slot 30:tint=00FF00:0.6 --iso "C:\\mssb\\marios.iso"
recolour spec (after the slot and a colon): """ + Recolour.__doc__)
    ap.add_argument("--disc", required=True, help="the game: a .iso file, or an extracted disc folder (contains sys/ and files/)")
    ap.add_argument("--out", help="output folder (created; never the input)")
    ap.add_argument("--source", default="mario", help="character to clone (name or id, default mario)")
    ap.add_argument("--slot", action="append", default=[], metavar="SLOT[:RECOLOUR]",
                    help="donor slot and recolour spec; repeatable.  Default: all four Toad colours (29:blue 30:green 31:purple 32:yellow)")
    ap.add_argument("--iso", help="also write a bootable .iso (the disc grows past the standard size; Dolphin accepts it)")
    ap.add_argument("--no-aram-overlay", action="store_true",
                    help="do not recolour the ARAM (in-game) body; clones then look like the plain source in matches")
    ap.add_argument("--ui", choices=("source", "donor"), default="source",
                    help="whose name/portrait/announcer call-out/voice the clone slots get in menus and in-game (default: source)")
    ap.add_argument("--colour-word", choices=("none", "donor"), default="none",
                    help="with --ui source: the colour word shown before a clone's name on the roster screen; "
                         "none (default: the clone is the source, e.g. no 'Purple') or donor (keep e.g. 'Purple')")
    ap.add_argument("--relocate-menu-art", action="store_true", help=argparse.SUPPRESS)  # tests: force the append path
    ap.add_argument("--no-stats", action="store_true", help="keep the donor's stats instead of copying the source's")
    ap.add_argument("--no-gameplay", action="store_true", help="keep the donor's game.rel/DOL gameplay tables")
    ap.add_argument("--list-slots", action="store_true", help="print the slot table and exit")
    ap.add_argument("--list-regions", action="store_true",
                    help="print the colour regions of --source (for region=) and exit")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    ap.add_argument("--dry-run", action="store_true", help="print every planned edit (offsets, sizes, entries) without copying or writing anything")
    args = ap.parse_args(argv)
    if args.list_slots or args.list_regions:
        with tempfile.TemporaryDirectory() as tmp:
            disc = open_disc(args.disc, print, Path(tmp) / "disc")
            if args.list_slots:
                list_slots(disc)
            else:
                list_regions(disc, parse_char(args.source))
        return 0
    if not args.out and not args.dry_run:
        ap.error("--out is required (a new folder; the input is never modified)")
    if args.out and Path(args.out).resolve() == Path(args.disc).resolve():
        ap.error("--out must be a different folder from --disc")
    if not args.out:
        args.out = str(Path(args.disc).parent / "dry-run")
    clone(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
