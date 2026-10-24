"""Format sniffers for GYQE01 data files (standard library only).

sniff(data, size, stored) -> (format_id, one-line summary) works on the first HEAD bytes of a file
(cheap: `list` decodes only that much).  describe(fmt, data) -> list of detail lines for `info`,
given the whole decoded file.

Almost every packed file is a *sectioned file*: word0 is the byte length of a header made of u32
section offsets (ascending, 0 = absent), followed by the sections.  The pointers are file-relative
and get relocated on load (e.g. fn_80017814 for character models, fn_3_5EC0 for stadiums).
"""
import struct

HEAD = 0x800

GX_FORMATS = {0: "I4", 1: "I8", 2: "IA4", 3: "IA8", 4: "RGB565", 5: "RGB5A3", 6: "RGBA8",
              8: "CI4", 9: "CI8", 10: "CI14X2", 14: "CMPR"}
_BPP = {0: 4, 1: 8, 2: 8, 3: 16, 4: 16, 5: 16, 6: 32, 8: 4, 9: 8, 10: 16, 14: 4}
_BLOCK = {4: (8, 8), 8: (8, 4), 16: (4, 4), 32: (4, 4)}

ANIM_VERSION = 0x007B7960
TEXT_MAGIC = 0x0131
REL_IDS = {1: "challenge.rel", 2: "menus.rel", 3: "game.rel"}

FORMAT_NAMES = {
    "model": ".gpc model: {texture, layout, geo, skin, extra}",
    "stadium": "stadium model (16-section root: layout, geo, textures, ...)",
    "ui": "UI resource: texture archive + sprite bank (sequences)",
    "texarc": "texture archive (TextureArchiveHeader)",
    "anim": "charPipeline ANIM bank",
    "sectioned": "sectioned data file (u32 offset header)",
    "musyx": "MusyX sound group (proj/pool/sdir/samp)",
    "text": "text bank (glyph-coded strings)",
    "hvqm": "HVQM4 1.3 movie",
    "rel": "REL module",
    "stats": "CharacterStats block",
    "script": "keyed event script (count, 0x28-byte header, offset table)",
    "table": "fixed-record table",
    "image": "stored raw image",
    "unknown": "unknown",
}


def u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def tex_bytes(fmt, w, h):
    bpp = _BPP.get(fmt)
    if bpp is None:
        return None
    bw, bh = _BLOCK[bpp]
    return ((w + bw - 1) // bw * bw) * ((h + bh - 1) // bh * bh) * bpp // 8


def texture_archive(b, base, limit):
    """Parse {u16 count; u16 pad; TextureHeader[count] (0x20)}; archive-relative data offsets.
    Returns a list of (w, h, fmt, dataOff, tlutOff, colours, tlutFmt) or None if implausible."""
    if base + 4 > len(b):
        return None
    n = u16(b, base)
    if not 0 < n <= 2048 or base + 4 + n * 0x20 > len(b):
        return None
    out = []
    for i in range(n):
        h = base + 4 + i * 0x20
        doff, toff, hh, ww = struct.unpack_from(">IIHH", b, h)
        fmt = b[h + 0x17]
        if fmt not in GX_FORMATS or not (0 < ww <= 1024 and 0 < hh <= 1024):
            return None
        sz = tex_bytes(fmt, ww, hh)
        if doff < 4 + n * 0x20 or base + doff + sz > limit:
            return None
        out.append((ww, hh, fmt, doff, toff, u16(b, h + 0x18), b[h + 0x1A]))
    return out


def tex_summary(texs, total=None):
    fmts = {}
    for t in texs:
        fmts[GX_FORMATS[t[2]]] = fmts.get(GX_FORMATS[t[2]], 0) + 1
    big = max(texs, key=lambda t: t[0] * t[1])
    part = "" if total is None or total == len(texs) else " in the first %d" % len(texs)
    return "%d textures (%s%s; largest %dx%d)" % (total or len(texs), ", ".join("%s %d" % kv for kv in sorted(fmts.items())),
                                                  part, big[0], big[1])


def section_header(b, size):
    """Offsets of a sectioned file's header, or None."""
    if len(b) < 8:
        return None
    hdr = u32(b, 0)
    if hdr % 4 or not 8 <= hdr <= 0x400 or hdr > len(b) or hdr >= size:
        return None
    offs = list(struct.unpack_from(">%dI" % (hdr // 4), b, 0))
    last = 0
    nonzero = 0
    for o in offs:
        if o == 0:
            continue
        if o < hdr or o > size or o < last:
            return None
        last = o
        nonzero += 1
    if nonzero < 2:
        return None
    return offs


def musyx_chunks(b, size):
    if len(b) < 0x20 or u32(b, 0) != 0x20:
        return None
    pairs = [struct.unpack_from(">II", b, i * 8) for i in range(4)]
    pos = 0x20
    for o, s in pairs:
        if o != (pos + 0x1F) // 0x20 * 0x20:
            return None
        pos = o + s
    if pos != size:
        return None
    return pairs


def sniff(b, size, stored=False):
    """(format, summary) from the head bytes `b` of a file whose unpacked size is `size`."""
    if b[:5] == b"HVQM4":
        return "hvqm", "HVQM4 1.3 movie (stored, 0x%X bytes)" % size
    if len(b) >= 0x40 and 1 <= u32(b, 0) <= 3 and u32(b, 0x0C) in (17, 18, 19) and u32(b, 0x10) in (0x40, 0x4C):
        return "rel", "%s (REL id %d, %d sections)" % (REL_IDS.get(u32(b, 0), "?"), u32(b, 0), u32(b, 0x0C))
    if len(b) >= 4 and u32(b, 0) == ANIM_VERSION:
        return "anim", "ANIM bank: %d sequences, %d tracks, %d keyframes" % (u16(b, 0xA), u16(b, 0xC), u16(b, 0xE))
    if len(b) >= 8 and u16(b, 2) == TEXT_MAGIC and u16(b, 0) and 4 + u16(b, 0) * 4 <= size:
        return "text", "text bank: %d strings" % u16(b, 0)
    m = musyx_chunks(b, size)
    if m:
        return "musyx", "MusyX group: proj 0x%X, pool 0x%X, sdir 0x%X, samp 0x%X bytes" % tuple(s for o, s in m)
    if len(b) >= 0x30 and u32(b, 4) == 0x28 and 0 < u32(b, 0) <= 0x400 and u32(b, 8) <= size:
        n = u32(b, 0)
        offs = struct.unpack_from(">%dI" % min(n, (len(b) - 0x28) // 4), b, 0x28)
        if all(offs[i] <= offs[i + 1] for i in range(len(offs) - 1)) and (not offs or offs[-1] <= size):
            return "script", "keyed event script: %d entries" % n
    t = texture_archive(b, 0, size) if size <= len(b) else _texarc_head(b, size)
    if t:
        return "texarc", "texture archive: " + tex_summary(t, u16(b, 0))
    offs = section_header(b, size)
    if offs:
        n = len(offs)
        if n == 8 and offs[0] == 0x20 and offs[1] and offs[2] and offs[3]:
            ta = _texarc_head(b, size, 0x20)
            if ta is not None:
                return "model", ".gpc model: " + tex_summary(ta, u16(b, 0x20)) + (", layout 0x%X, geo 0x%X" % (offs[1], offs[2]))
        if n == 8 and offs[0] == 0x20 and offs[1] and not any(offs[2:]):
            ta = _texarc_head(b, size, 0x20)
            if ta is not None:
                return "ui", "UI resource: %s, sprite bank at 0x%X" % (tex_summary(ta, u16(b, 0x20)), offs[1])
        if n == 16:
            return "stadium", "stadium/scene model: %d sections" % sum(1 for o in offs if o)
        return "sectioned", "sectioned file: %d-word header, %d sections" % (n, sum(1 for o in offs if o))
    if stored and size >= 0x4000:
        return "unknown", "stored binary"
    return "unknown", "unknown (%s)" % b[:8].hex()


def _texarc_head(b, size, base=0):
    """Texture archive check when only the head is available (bounds against `size`)."""
    if base + 4 > len(b):
        return None
    n = u16(b, base)
    if not 0 < n <= 2048:
        return None
    need = base + 4 + n * 0x20
    if need > len(b):
        n = (len(b) - base - 4) // 0x20
        if n <= 0:
            return None
    out = []
    for i in range(n):
        h = base + 4 + i * 0x20
        doff, toff, hh, ww = struct.unpack_from(">IIHH", b, h)
        fmt = b[h + 0x17]
        if fmt not in GX_FORMATS or not (0 < ww <= 1024 and 0 < hh <= 1024):
            return None
        if base + doff + tex_bytes(fmt, ww, hh) > size:
            return None
        out.append((ww, hh, fmt, doff, toff, u16(b, h + 0x18), b[h + 0x1A]))
    return out


# ----------------------------------------------------------------------------------------------
# Details for `info` (whole file)
# ----------------------------------------------------------------------------------------------
# Glyph codes of the text banks, decoded from the retail strings (lossless: anything else is {XXXX}).
TEXT_GLYPHS = {0x00: "!", 0x01: '"', 0x06: "'", 0x07: "(", 0x08: ")", 0x0B: ",", 0x0C: "-", 0x0D: ".", 0x1E: "?"}
TEXT_GLYPHS.update({0x0F + i: chr(ord("0") + i) for i in range(10)})
TEXT_GLYPHS.update({0x20 + i: chr(ord("A") + i) for i in range(26)})
TEXT_GLYPHS.update({0x3E + i: chr(ord("a") + i) for i in range(26)})
TEXT_CODES = {v: k for k, v in TEXT_GLYPHS.items()}
TEXT_END, TEXT_NEWLINE, TEXT_SPACE = 0x4000, 0x4001, 0x4002


def _text_char(c):
    return TEXT_GLYPHS.get(c, "{%04X}" % c)


def decode_text(b, start, end):
    """One string as editable text: 0x4001 -> \\n, 0x4002 -> space, other 0x40xx -> <xx>
    (colour/voice/box codes), unknown glyphs -> {XXXX}.  Stops at the 0x4000 terminator."""
    out = []
    for o in range(start, end - 1, 2):
        c = u16(b, o)
        if c == TEXT_END:
            break
        if c == TEXT_NEWLINE:
            out.append("\\n")
        elif c == TEXT_SPACE:
            out.append(" ")
        elif c & 0xFF00 == 0x4000:
            out.append("<%02X>" % (c & 0xFF))
        else:
            out.append(_text_char(c))
    return "".join(out)


def encode_text(s):
    codes = []
    i = 0
    while i < len(s):
        ch = s[i]
        if s.startswith("\\n", i):
            codes.append(TEXT_NEWLINE)
            i += 2
        elif ch == " ":
            codes.append(TEXT_SPACE)
            i += 1
        elif ch == "<" and s[i + 3:i + 4] == ">":
            codes.append(0x4000 | int(s[i + 1:i + 3], 16))
            i += 4
        elif ch == "{" and s[i + 5:i + 6] == "}":
            codes.append(int(s[i + 1:i + 5], 16))
            i += 6
        elif ch in TEXT_CODES:
            codes.append(TEXT_CODES[ch])
            i += 1
        else:
            raise ValueError("character %r at column %d has no glyph (use {XXXX} for raw codes)" % (ch, i + 1))
    codes.append(TEXT_END)
    return struct.pack(">%dH" % len(codes), *codes)


def text_export(b):
    """Text bank -> UTF-8 text: a header line, then one '<index>\t<string>' line per string."""
    n = u16(b, 0)
    offs = struct.unpack_from(">%dI" % n, b, 4)
    lines = ["#mssb-text strings=%d magic=0x%04X" % (n, u16(b, 2))]
    for i in range(n):
        end = offs[i + 1] if i + 1 < n else len(b)
        lines.append("%d\t%s" % (i, decode_text(b, offs[i], end)))
    return "\n".join(lines) + "\n"


def text_import(text):
    """Inverse of text_export (strings may change length; the offset table is rebuilt)."""
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    head = [l for l in text.splitlines() if l.startswith("#mssb-text")]
    magic = TEXT_MAGIC
    if head and "magic=" in head[0]:
        magic = int(head[0].split("magic=")[1].split()[0], 16)
    strings = {}
    for l in lines:
        idx, _, body = l.partition("\t")
        strings[int(idx)] = encode_text(body)
    n = len(strings)
    if sorted(strings) != list(range(n)):
        raise ValueError("string indices must be 0..%d with no gaps" % (n - 1))
    out = bytearray(struct.pack(">HH", n, magic))
    pos = 4 + 4 * n
    blob = bytearray()
    for i in range(n):
        out += struct.pack(">I", pos + len(blob))
        blob += strings[i]
    return bytes(out + blob)


def describe(fmt, b):
    lines = []
    size = len(b)
    if fmt in ("model", "ui", "stadium", "sectioned"):
        offs = section_header(b, size) or []
        lines.append("sections: " + ", ".join("0x%X" % o for o in offs))
        tex_base = None
        if fmt in ("model", "ui"):
            tex_base = 0x20
        elif fmt == "stadium" and len(offs) > 5 and offs[5]:
            tex_base = offs[5]
        if tex_base:
            texs = texture_archive(b, tex_base, size)
            if texs:
                lines.append("texture archive at 0x%X: %s" % (tex_base, tex_summary(texs)))
                for i, t in enumerate(texs):
                    lines.append("  tex %3d: %4dx%-4d %-6s data 0x%X%s" % (
                        i, t[0], t[1], GX_FORMATS[t[2]], tex_base + t[3],
                        (", %d-colour TLUT fmt %d" % (t[5], t[6])) if t[2] in (8, 9, 10) else ""))
        if fmt == "ui" and len(offs) > 1:
            bank = offs[1]
            if bank + 12 <= size:
                table = bank + u32(b, bank + 8)
                if table + 4 <= size:
                    lines.append("sprite bank at 0x%X: %d sequences" % (bank, u32(b, table)))
        if fmt == "model" and len(offs) > 2 and offs[1] + 0x16 <= size:
            lines.append("layout: totalBones %d, skinFileID %d" % (u16(b, offs[1] + 6), u16(b, offs[1] + 0x14)))
    elif fmt == "texarc":
        texs = texture_archive(b, 0, size) or []
        lines.append(tex_summary(texs) if texs else "no textures")
        for i, t in enumerate(texs):
            lines.append("  tex %3d: %4dx%-4d %-6s data 0x%X" % (i, t[0], t[1], GX_FORMATS[t[2]], t[3]))
    elif fmt == "anim":
        lines.append("bankID %d, %d sequences, %d tracks, %d keyframes, userData 0x%X bytes" % (
            u16(b, 8), u16(b, 0xA), u16(b, 0xC), u16(b, 0xE), u32(b, 0x10)))
        seq = u32(b, 4)
        for i in range(min(u16(b, 0xA), 64)):
            so = seq + i * 0xC
            if so + 0xC > size:
                break
            name_off, tracks, ntr = u32(b, so), u32(b, so + 4), u16(b, so + 8)
            name = ""
            if 0 < name_off < size:
                end = b.find(b"\0", name_off)
                name = b[name_off:end].decode("ascii", "replace") if end > 0 else ""
            lines.append("  seq %2d: %-24s %d tracks" % (i, name or "-", ntr))
    elif fmt == "musyx":
        names = ("proj", "pool", "sdir", "samp")
        pairs = musyx_chunks(b, size) or []
        for n, (o, s) in zip(names, pairs):
            lines.append("%s: offset 0x%X size 0x%X" % (n, o, s))
        if len(pairs) == 4:
            o, s = pairs[2]
            cnt = 0
            while o + cnt * 0x20 + 2 <= size and u16(b, o + cnt * 0x20) != 0xFFFF and cnt * 0x20 < s:
                cnt += 1
            lines.append("sdir: %d samples (0x20-byte SDIR entries)" % cnt)
            lines.append("group id (proj word): 0x%X" % u32(b, 0x24) if size > 0x28 else "")
    elif fmt == "text":
        n = u16(b, 0)
        offs = struct.unpack_from(">%dI" % n, b, 4)
        lines.append("%d strings (16-bit glyph codes; 0x40xx = control codes: 4001 newline, 4002 space); "
                     "edit with text-export / text-import" % n)
        for i in range(n):
            end = offs[i + 1] if i + 1 < n else size
            lines.append("  %4d: %s" % (i, decode_text(b, offs[i], end)[:120]))
    elif fmt == "hvqm":
        lines.append("header: " + b[:0x40].hex())
    elif fmt == "rel":
        n = u32(b, 0x0C)
        so = u32(b, 0x10)
        for i in range(n):
            o, s = struct.unpack_from(">II", b, so + i * 8)
            if s:
                lines.append("  section %2d: offset 0x%X size 0x%X%s" % (i, o & ~1, s, " (exec)" if o & 1 else ""))
    return [l for l in lines if l]
