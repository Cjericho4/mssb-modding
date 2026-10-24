#!/usr/bin/env python3
"""Read-only verification of the retail menu character-image map (character_select.md §8).

Only prints descriptors, texture metadata and alias counts. Never exports game assets.
Run with an extracted USA Rev 0 disc directory, e.g. orig/GYQE01.
"""
import argparse
from collections import Counter
from pathlib import Path
import struct

import clone_character as cc

COMMON_DESC = 0x800E8BF8
GRID_DESC = 0x800E8CD8


def u16(data, offset):
    return struct.unpack_from(">H", data, offset)[0]


def u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def name_texture(char_id):
    return char_id if char_id < 29 else char_id + 28


def frame_textures(data, sequence):
    """Expand the retail type-0/type-2 list-0 records used by these three sequences.

    Offsets in each table are relative to that table, not the resource root.
    Delta word indices start AFTER the record's first word. Records are stored
    in descending frame order; the frame halfword is not a texture index.
    """
    bank = u32(data, 4)
    table = bank + u32(data, bank + 8)
    if sequence >= u32(data, table):
        raise ValueError("sequence outside sprite bank")
    seq = table + u32(data, table + 8 + sequence * 4)
    if (u32(data, seq) & 0x7FFFFFFF) != 2:
        raise ValueError("expected header list plus one image list")
    group = seq + u32(data, seq + 12)
    count, stride = struct.unpack_from(">HH", data, group)
    pos = group + 4
    template = data[pos:pos + stride]
    frames = {}
    for _ in range(count):
        kind = u16(data, pos) & 0x7FFF
        frame = u16(data, pos + 2)
        if kind == 0:
            record = bytearray(template)
            size = stride
        elif kind == 2:
            record = bytearray(template)
            record[:4] = data[pos:pos + 4]
            changes, words = struct.unpack_from(">HH", data, pos + 4)
            size = words * 4
            values = pos + size - changes * 4
            for i in range(changes):
                dst = 4 + u16(data, pos + 8 + i * 2) * 4
                if dst + 4 > stride:
                    raise ValueError("delta outside expanded record")
                record[dst:dst + 4] = data[values + i * 4:values + i * 4 + 4]
        else:
            raise ValueError("unexpected record type %d in retail character list" % kind)
        if frame in frames:
            raise ValueError("duplicate frame")
        frames[frame] = u16(record, 6)
        pos += size
    if set(frames) != set(range(count)):
        raise ValueError("character frames are not contiguous")
    return frames


def load_resource(disc, dol, address, expected_offset):
    desc = dol.desc(address, 0)
    flags, size, offset, packed = desc
    if flags != cc.LZSS_FLAGS or not size & cc.SIZE_COMPRESSED_BIT or offset != expected_offset:
        raise ValueError("descriptor at %08X does not describe the retail resource" % address)
    data, _ = cc.lzss_decompress(disc.read_zzzz(offset, packed), size & 0x0FFFFFFF)
    print("descriptor %08X: flags=%X unpacked=%X ZZZZ=%08X packed=%X" %
          (address, flags, len(data), offset, packed))
    return data, cc.TextureFile(data)


def verify_set(label, archive, indices, expected):
    textures = [archive.textures[i] for i in indices]
    actual = {(t["w"], t["h"], t["fmt"], t["colours"], t["tlut_fmt"]) for t in textures}
    if actual != {expected}:
        raise ValueError("%s: unexpected texture metadata %r" % (label, actual))
    pixels = Counter(t["data"] for t in textures)
    palettes = Counter(t["tlut"] for t in textures)
    print("%s: %d textures; %dx%d format=%d colours=%d TLUT=%d; %d pixel / %d palette buffers" %
          ((label, len(textures)) + expected + (len(pixels), len(palettes))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disc", type=Path, help="extracted USA Rev 0 disc directory")
    args = parser.parse_args()
    disc = cc.Disc(args.disc)
    dol = cc.Dol((disc.sys / "main.dol").read_bytes())
    common, ct = load_resource(disc, dol, COMMON_DESC, 0x1A9FE800)
    grid, gt = load_resource(disc, dol, GRID_DESC, 0x18AEE800)
    names = frame_textures(common, 0x82)
    sprites = frame_textures(common, 0x83)
    portraits = frame_textures(grid, 0x19)
    if names != {c: name_texture(c) for c in range(54)}:
        raise ValueError("name texture mapping differs from retail")
    # Frame54 is the separate empty-slot image.
    if sprites != {**{c: 153 + c for c in range(54)}, 54: 434}:
        raise ValueError("small sprite mapping differs from retail")
    if portraits != {**{c: 4 + c for c in range(54)}, 54: 118}:
        raise ValueError("grid portrait mapping differs from retail")
    verify_set("names: c<29 ? c : c+28", ct, [names[c] for c in range(54)], (111, 18, 8, 16, 0))
    verify_set("small sprites: 153+c", ct, [sprites[c] for c in range(54)], (52, 60, 9, 256, 2))
    verify_set("grid portraits: 4+c", gt, [portraits[c] for c in range(54)], (50, 50, 9, 256, 2))
    print("Verified all 162 retail character mappings; placeholders: common=434, grid=118.")


if __name__ == "__main__":
    main()
