"""Disc access, module images (main.dol + the three RELs) and descriptor scanning for GYQE01.

Standard library only (Python 3.8+, Windows-safe).  The LZSS codec, ISO extraction and FST helpers
are imported read-only from tools/modding/clone_character.py.

Disk file descriptor (DiskFileDesc, 16 bytes, big endian):

    u32 flags      0 = stored, else (repeatBits << 8) | lookbackBits of the LZSS stream (0x40B, 0x50E)
    u32 sizeWord   bit 30 = compressed, low 28 bits = unpacked size
    u32 offset     byte offset inside the container (ZZZZ.dat, aaaa.dat, or the ARAM blob)
    u32 packed     bytes read from disc (the LZSS stream length; stored files round up to 4)
"""
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "modding"))
try:
    import clone_character as cc  # noqa: E402
except ImportError as e:  # pragma: no cover
    raise SystemExit("tools/modding/clone_character.py is needed next to tools/datamap (%s)" % e)

SIZE_COMPRESSED = 0x40000000
SIZE_MASK = 0x0FFFFFFF
SECTOR = 0x800

# DOL boot file table entries 0..2: the three RELs, LZSS-packed inside aaaa.dat (loaded by fn_800097A0)
REL_DESC_ADDRS = (("menus", 0x800E8AA8), ("game", 0x800E8AB8), ("challenge", 0x800E8AC8))
MASTER_ADDR = 0x800EFD38          # master descriptors (blob-relative), 516 rows
BLOB_DESC_ADDR = 0x800F1D68       # master[515]: the ARAM blob itself (ZZZZ-absolute, stored)
BLOB_LIS = 0x800227EC             # fn_800227E4: lis r3, hi(ARAM allocation)
BLOB_ADDI = 0x800227F4            # fn_800227E4: addi r3, r3, lo(ARAM allocation)


def u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def align(v, a):
    return (v + a - 1) // a * a


def lzss_params(flags):
    """(lookbackBits, repeatBits) from a descriptor's flags word."""
    return flags & 0xFF, (flags >> 8) & 0xFF


def lzss_head(data, limit, lb_bits, rp_bits):
    """First `limit` bytes of an LZSS stream (the full decoder rejects a match that runs past the
    requested size, so a partial decode needs its own loop)."""
    out = bytearray()
    pos = buf = nbits = 0
    n = len(data)
    while len(out) < limit:
        if nbits == 0:
            if pos + 4 > n:
                break
            buf = u32(data, pos)
            pos += 4
            nbits = 32
        flag = buf & 1
        buf >>= 1
        nbits -= 1
        fields = (8,) if flag else (lb_bits, rp_bits)
        vals = []
        for cnt in fields:
            if nbits >= cnt:
                v = buf & ((1 << cnt) - 1)
                buf >>= cnt
                nbits -= cnt
            else:
                if pos + 4 > n:
                    return bytes(out)
                need = cnt - nbits
                v = (buf << need) & 0xFFFFFFFF
                w = u32(data, pos)
                pos += 4
                rem = 32 - need
                v |= ((w << rem) & 0xFFFFFFFF) >> rem
                nbits = rem
                buf = w >> need
            vals.append(v)
        if flag:
            out.append(vals[0])
        else:
            back, cnt = vals[0] + 1, vals[1] + 2
            if back > len(out):
                raise cc.LZSSError("bad back-reference")
            for _ in range(cnt):
                out.append(out[-back])
    return bytes(out[:limit])


def unpack(desc, raw, limit=None):
    """Decode a file's bytes as the game does.  limit: decode only the first `limit` bytes."""
    flags, sizew, off, packed = desc
    size = sizew & SIZE_MASK
    if sizew & SIZE_COMPRESSED:
        lb, rp = lzss_params(flags)
        if limit is not None and limit < size:
            return lzss_head(raw, limit, lb, rp)
        out, _ = cc.lzss_decompress(raw, size, lb_bits=lb, rp_bits=rp)
        return out
    data = raw[:size]
    return data if limit is None else data[:limit]


def pack(data, flags):
    lb, rp = lzss_params(flags)
    return cc.lzss_compress_optimal(data, lb_bits=lb, rp_bits=rp)


# ----------------------------------------------------------------------------------------------
# Module images
# ----------------------------------------------------------------------------------------------
class Image:
    """main.dol or a decompressed REL.  `sections` = [(index, fileOff, size, exec)] (non-empty only).
    Locations of things inside an image are (module, section, offset-in-section); for the DOL the
    section offset is turned into a RAM address, which is what symbols.txt and the docs use."""

    def __init__(self, name, data):
        self.name = name
        self.data = bytearray(data)
        self.sections = []
        if name == "dol":
            offs = struct.unpack_from(">18I", self.data, 0x00)
            addrs = struct.unpack_from(">18I", self.data, 0x48)
            sizes = struct.unpack_from(">18I", self.data, 0x90)
            self.addrs = {}
            for i in range(18):
                if sizes[i]:
                    self.sections.append((i, offs[i], sizes[i], i < 7))
                    self.addrs[i] = addrs[i]
        else:
            n = u32(self.data, 0x0C)
            so = u32(self.data, 0x10)
            for i in range(n):
                o, s = struct.unpack_from(">II", self.data, so + i * 8)
                if s and (o & ~1):
                    self.sections.append((i, o & ~1, s, bool(o & 1)))

    def data_sections(self):
        return [s for s in self.sections if not s[3]]

    def loc(self, file_off):
        """(section, offset in section, display address)."""
        for i, o, s, ex in self.sections:
            if o <= file_off < o + s:
                if self.name == "dol":
                    return i, file_off - o, self.addrs[i] + file_off - o
                return i, file_off - o, file_off - o
        return None, None, None

    def file_off(self, section, sect_off):
        for i, o, s, ex in self.sections:
            if i == section:
                return o + sect_off
        raise KeyError(section)

    def ram_off(self, ram):
        """DOL only: RAM address -> file offset."""
        for i, o, s, ex in self.sections:
            a = self.addrs[i]
            if a <= ram < a + s:
                return o + ram - a
        raise ValueError("0x%08X is not inside main.dol" % ram)

    def where(self, file_off):
        sec, so, disp = self.loc(file_off)
        if self.name == "dol":
            return "dol 0x%08X" % disp
        return "%s.rel s%d+0x%X" % (self.name, sec, so)

    def desc_at(self, file_off):
        return struct.unpack_from(">4I", self.data, file_off)

    def set_desc(self, file_off, d):
        struct.pack_into(">4I", self.data, file_off, *d)


# ----------------------------------------------------------------------------------------------
# Disc folder
# ----------------------------------------------------------------------------------------------
class Disc:
    """A GYQE01 disc: an extracted folder (Dolphin layout: sys/ + files/) or a .iso read in place.
    Read-only; `replace` works on a copy."""

    def __init__(self, root):
        self.root = Path(root)
        self.iso = self.root.is_file()
        self._zf = None
        if self.iso:
            self._iso = open(self.root, "rb")
            boot = self._iso_read(0, 0x440)
            if len(boot) < 0x440 or u32(boot, 0x1C) != 0xC2339F3D:
                raise SystemExit("%s is not a GameCube disc image (.rvz/.nkit must be converted to .iso "
                                 "first: Dolphin -> right-click -> Convert File -> ISO)" % self.root)
            self.boot = boot
            dol_off, fst_off, fst_size = struct.unpack_from(">III", boot, 0x420)
            dol = self._iso_read(dol_off, cc.dol_size(self._iso_read(dol_off, 0x100)))
            self.fst = self._iso_read(fst_off, fst_size)
            self._fst_map = {path: (e["offset"], e["size"]) for e, path in cc.fst_paths(self.fst)}
            for need in ("ZZZZ.dat", "aaaa.dat"):
                if need not in self._fst_map:
                    raise SystemExit("%s has no %s: not Mario Superstar Baseball" % (self.root, need))
            self.zzzz_size = self._fst_map["ZZZZ.dat"][1]
            ao, asz = self._fst_map["aaaa.dat"]
            self.aaaa = self._iso_read(ao, asz)
        else:
            self.sys = self.root / "sys"
            self.files = self.root / "files"
            for p in (self.sys / "main.dol", self.sys / "fst.bin", self.files / "ZZZZ.dat", self.files / "aaaa.dat"):
                if not p.exists():
                    raise SystemExit("not an extracted GYQE01 disc folder or .iso (missing %s)" % p)
            self.boot = (self.sys / "boot.bin").read_bytes() if (self.sys / "boot.bin").exists() else b"GYQE01"
            dol = (self.sys / "main.dol").read_bytes()
            self.fst = (self.sys / "fst.bin").read_bytes()
            self.zzzz_size = (self.files / "ZZZZ.dat").stat().st_size
            self.aaaa = (self.files / "aaaa.dat").read_bytes()
        if self.boot[:6] != b"GYQE01":
            raise SystemExit("%s is %r, not Mario Superstar Baseball USA (GYQE01)" % (self.root, bytes(self.boot[:6])))
        self.dol = Image("dol", dol)
        self.rels = {}
        self.rel_desc = {}
        for name, addr in REL_DESC_ADDRS:
            d = self.dol.desc_at(self.dol.ram_off(addr))
            self.rel_desc[name] = d
            self.rels[name] = Image(name, unpack(d, self.aaaa[d[2]:d[2] + d[3]]))

    def _iso_read(self, off, n):
        self._iso.seek(off)
        return self._iso.read(n)

    def images(self):
        return [self.dol] + [self.rels[n] for n, _ in REL_DESC_ADDRS]

    def image(self, name):
        return self.dol if name == "dol" else self.rels[name]

    def read_zzzz(self, off, n):
        if self.iso:
            base, size = self._fst_map["ZZZZ.dat"]
            n = max(0, min(n, size - off))
            return self._iso_read(base + off, n)
        if self._zf is None:
            self._zf = open(self.files / "ZZZZ.dat", "rb")
        self._zf.seek(off)
        return self._zf.read(n)

    def read_fst_file(self, path, off=0, n=None):
        """Bytes of any FST file (e.g. snd/mario_01_h.adp)."""
        if self.iso:
            base, size = self._fst_map[path]
            n = size - off if n is None else min(n, size - off)
            return self._iso_read(base + off, n)
        with open(self.files / path, "rb") as f:
            f.seek(off)
            return f.read() if n is None else f.read(n)

    def blob_desc(self):
        return self.dol.desc_at(self.dol.ram_off(BLOB_DESC_ADDR))

    def read(self, container, off, n):
        if container == "ZZZZ":
            return self.read_zzzz(off, n)
        if container == "aaaa":
            return self.aaaa[off:off + n]
        if container == "blob":
            return self.read_zzzz(self.blob_desc()[2] + off, n)
        raise ValueError(container)

    def container_size(self, container):
        if container == "ZZZZ":
            return self.zzzz_size
        if container == "aaaa":
            return len(self.aaaa)
        if container == "blob":
            return self.blob_desc()[1] & SIZE_MASK
        raise ValueError(container)

    def fst_files(self):
        return list(cc.fst_paths(self.fst))

    def close(self):
        if self._zf:
            self._zf.close()
            self._zf = None
        if self.iso and self._iso:
            self._iso.close()
            self._iso = None


# ----------------------------------------------------------------------------------------------
# Descriptor scanning
# ----------------------------------------------------------------------------------------------
def plausible(d, container_size, absolute=True):
    """Is this 16-byte record a real descriptor?  Rules derived from every retail table."""
    flags, sizew, off, packed = d
    if sizew & 0xB0000000:
        return False
    size = sizew & SIZE_MASK
    if size < 0x20 or packed == 0 or size > 0x8000000:
        return False
    if sizew & SIZE_COMPRESSED:
        lb, rp = lzss_params(flags)
        if flags >> 16 or not (8 <= lb <= 16 and 2 <= rp <= 8):
            return False
        if packed > size + size // 8 + 0x40:
            return False
    else:
        if flags != 0 or packed < size or packed > align(size, 0x20):
            return False
        if off == size == packed:          # repeated-word noise (0x10000/0x10000/0x10000 ...)
            return False
    if absolute and (off == 0 or off % SECTOR):
        return False
    if not absolute and off % 0x20:
        return False
    extent = packed if sizew & SIZE_COMPRESSED else max(size, packed)
    return off + extent <= container_size


def scan_image(img, container_size):
    """Every 4-aligned 16-byte record in the image's data sections that passes `plausible`
    (ZZZZ-absolute rules).  Yields file offsets."""
    for i, o, s, ex in img.data_sections():
        buf = img.data
        for p in range(o, o + s - 15, 4):
            d = struct.unpack_from(">4I", buf, p)
            if d[1] & 0xB0000000 or not d[3]:
                continue
            if plausible(d, container_size):
                yield p
