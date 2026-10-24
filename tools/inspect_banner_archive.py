#!/usr/bin/env python3
"""Read-only metadata/sequence inspection of the retail ZZZZ+0x19135800 UI file.

No pixels, palettes or decompressed assets are exported. Imports the existing
local disc/LZSS/texture readers without invoking the patcher's write path.
"""
import argparse
from collections import Counter
from pathlib import Path
import struct
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "modding"))
import clone_character as cc

DESCRIPTOR = (0x40B, 0x4023491C, 0x19135800, 0x82928)


def u16(data, offset):
    return struct.unpack_from(">H", data, offset)[0]


def u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def rel_data(rel):
    """Retail game/menu/challenge .data is section 5; section 4 is .rodata."""
    table = u32(rel, 0x10)
    offset, size = struct.unpack_from(">II", rel, table + 5 * 8)
    if not offset or offset & 1 or offset + size > len(rel):
        raise ValueError("invalid menus .data section")
    return rel[offset:offset + size]


def expand_list(data, group, expanded_bytes):
    """The four record cases of fn_8000CEF0, before pointer relocation.

    Return (descending ordinal, stored frame, flags, texture/sequence index).
    This is the query table; renderer timing/interpolation is a separate table.
    """
    count, stride = struct.unpack_from(">HH", data, group)
    if not 4 <= stride <= expanded_bytes:
        raise ValueError("invalid image-list template size")
    pos = group + 4
    template = data[pos:pos + stride]
    rows = []
    for ordinal in reversed(range(count)):
        kind = u16(data, pos) & 0x7FFF
        record = bytearray(expanded_bytes)
        if kind == 0:
            record[:stride] = template
            size = (stride >> 2) * 4
        elif kind == 1:
            record[:] = data[pos:pos + expanded_bytes]
            size = expanded_bytes
        elif kind in (2, 3):
            record[4:stride] = template[4:]
            record[:4] = data[pos:pos + 4]
            changes, words = struct.unpack_from(">HH", data, pos + 4)
            size = words * 4
            if kind == 2:
                values = pos + size - changes * 4
                if pos + 8 + changes * 2 > values:
                    raise ValueError("overlapping word-delta indices and values")
                for i in reversed(range(changes)):
                    dst = 4 + u16(data, pos + 8 + i * 2) * 4
                    if dst + 4 > expanded_bytes:
                        raise ValueError("word delta exceeds output")
                    record[dst:dst + 4] = data[values + i * 4:values + i * 4 + 4]
            else:
                values = pos + 8 + changes * 4
                for i in range(changes):
                    dst, length = struct.unpack_from(">HH", data, pos + 8 + i * 4)
                    dst += 4
                    if dst + length > expanded_bytes or values + length > pos + size:
                        raise ValueError("range delta exceeds output/record")
                    record[dst:dst + length] = data[values:values + length]
                    values += length
        else:
            raise ValueError("unknown record type %d" % kind)
        if size <= 0 or pos + size > len(data):
            raise ValueError("invalid packed record size")
        rows.append((ordinal, u16(record, 2), u16(record, 4), u16(record, 6)))
        pos += size
    return rows


def sequences(data):
    bank = u32(data, 4)
    table = bank + u32(data, bank + 8)
    expanded = data[bank + 5] * 4
    for sequence in range(u32(data, table)):
        seq = table + u32(data, table + 8 + sequence * 4)
        layers = []
        for layer in range((u32(data, seq) & 0x7FFFFFFF) - 1):
            group = seq + u32(data, seq + 12 + layer * 4)
            layers.append(expand_list(data, group, expanded))
        yield sequence, layers


def runs(indices):
    """Compact sorted consecutive integer runs for metadata output."""
    result = []
    for index in sorted(set(indices)):
        if result and result[-1][1] + 1 == index:
            result[-1][1] = index
        else:
            result.append([index, index])
    return ",".join(str(a) if a == b else "%d..%d" % (a, b) for a, b in result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disc", type=Path, help="extracted USA Rev 0 disc folder")
    parser.add_argument("--sequences", action="store_true", help="print each list's flags/index references")
    args = parser.parse_args()
    disc = cc.Disc(args.disc)
    menus = rel_data((disc.files / "menus.rel").read_bytes())
    descriptor = struct.unpack_from(">4I", menus, 0x132DC)
    if descriptor != DESCRIPTOR:
        raise ValueError("menus .data+0x132DC is not the retail banner descriptor")
    flags, size, offset, packed = descriptor
    data, _ = cc.lzss_decompress(disc.read_zzzz(offset, packed), size & 0x0FFFFFFF)
    archive = cc.TextureFile(data)
    print("menus .data+132DC -> slot17: flags=%X unpacked=%X ZZZZ=%08X packed=%X" %
          (flags, len(data), offset, packed))
    if archive.count != 427:
        raise ValueError("unexpected retail texture count")
    needle = struct.pack(">4I", *DESCRIPTOR)
    for module in ("menus", "game", "challenge"):
        section = rel_data((disc.files / (module + ".rel")).read_bytes())
        aliases = [i for i in range(0, len(section) - 15, 4) if section[i:i + 16] == needle]
        print("%s.rel descriptor copies in .data: %s" %
              (module, ", ".join("0x%X" % i for i in aliases) or "none"))
    print("textures:", archive.count)
    formats = Counter((t["w"], t["h"], t["fmt"], t["colours"], t["tlut_fmt"]) for t in archive.textures)
    for metadata, count in sorted(formats.items()):
        indices = [t["index"] for t in archive.textures if
                   (t["w"], t["h"], t["fmt"], t["colours"], t["tlut_fmt"]) == metadata]
        print("  %d x %dx%d GX=%d colours=%d TLUT=%d; indices=%s" %
              ((count,) + metadata + (runs(indices),)))
    decoded = list(sequences(data))
    print("sequences:", len(decoded), "; all image lists expanded with output bounds checked")
    # In the query format flags high byte 3 is a nested sequence, 4 a texture.
    # fn_8000BA3C's recursion at 8000C104..8000C13C uses precisely that field.
    reachable, textures = set(), set()
    pending = [0x64]
    while pending:
        sequence = pending.pop()
        if sequence in reachable:
            continue
        if not 0 <= sequence < len(decoded):
            raise ValueError("nested sequence outside bank")
        reachable.add(sequence)
        for rows in decoded[sequence][1]:
            for _, _, flags, index in rows:
                if flags >> 8 == 3:
                    pending.append(index)
                elif flags >> 8 == 4:
                    if index >= archive.count:
                        raise ValueError("texture outside archive")
                    textures.add(index)
    strips = {t["index"] for t in archive.textures if t["h"] == 20 and t["w"] in (400, 420)}
    if len(decoded) != 101 or len(strips) != 139 or not strips <= textures:
        raise ValueError("retail timeline/strip coverage changed")
    bank = u32(data, 4)
    table = bank + u32(data, bank + 8)
    seq = table + u32(data, table + 8 + 0x64 * 4)
    timing = seq + u32(data, seq + 8)
    events = [struct.unpack_from(">HH", data, timing + 4 + i * 12) for i in range(u16(data, timing))]
    print("sequence64 timing (frame, mode):", events)
    print("sequence64 fixed graph: sequences=%s; %d textures; %d/%d long strips" %
          (runs(reachable), len(textures), len(textures & strips), len(strips)))
    print("long strips outside sequence64 graph:", runs(strips - textures) or "none")
    print("textures outside sequence64 graph:", runs(set(range(archive.count)) - textures) or "none")
    if args.sequences:
        for seq, layers in decoded:
            refs = ["L%d:%s" % (i, ";".join("flags=%04X indices=%s" % (flag, runs(r[3] for r in rows if r[2] == flag))
                                          for flag in sorted({r[2] for r in rows})))
                    for i, rows in enumerate(layers)]
            print("  seq%02X %s" % (seq, " ".join(refs)))


if __name__ == "__main__":
    main()
