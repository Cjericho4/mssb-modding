#!/usr/bin/env python3
"""Read-only verification of the menu character-image map (character_select.md §8).

Only prints descriptors, texture metadata and alias counts. Never exports game assets.
Run with an extracted USA Rev 0 disc directory, e.g. orig/GYQE01, or a disc patched by
clone_character.py (the menu files may then have been re-packed or moved, and donor headers
point at the source's buffers, so only the retail frame->texture map and formats are required).
"""
import argparse
from collections import Counter
from pathlib import Path

import clone_character as cc

COMMON_DESC = cc.ADDR_MENU_COMMON_DESC
GRID_DESC = cc.ADDR_MENU_GRID_DESC
name_texture = cc.name_texture
frame_textures = cc.frame_textures


def load_resource(disc, dol, address, log=print):
    data, desc = cc.load_menu_file(disc, dol, address)
    log("descriptor %08X: flags=%X unpacked=%X ZZZZ=%08X packed=%X" % (address, desc[0], len(data), desc[2], desc[3]))
    return data, cc.TextureFile(data)


def verify_set(label, archive, indices, expected, log=print):
    textures = [archive.textures[i] for i in indices]
    actual = {(t["w"], t["h"], t["fmt"], t["colours"], t["tlut_fmt"]) for t in textures}
    if actual != {expected}:
        raise ValueError("%s: unexpected texture metadata %r" % (label, actual))
    for t in textures:
        if max(t["data"] + t["size"], t["tlut"] + t["colours"] * 2) > len(archive.data):
            raise ValueError("%s: texture %d buffers run past the end of the file" % (label, t["index"]))
    pixels = Counter(t["data"] for t in textures)
    palettes = Counter(t["tlut"] for t in textures)
    log("%s: %d textures; %dx%d format=%d colours=%d TLUT=%d; %d pixel / %d palette buffers" %
        ((label, len(textures)) + expected + (len(pixels), len(palettes))))


def validate(disc_path, log=print):
    """Parse both menu files of a disc and check all 162 mappings (raises on any problem).
    Returns (common TextureFile, grid TextureFile, {kind: {charID: texture index}})."""
    disc = cc.Disc(disc_path)
    dol = cc.Dol((disc.sys / "main.dol").read_bytes())
    common, ct = load_resource(disc, dol, COMMON_DESC, log)
    grid, gt = load_resource(disc, dol, GRID_DESC, log)
    names = frame_textures(common, cc.SEQ_NAME)
    sprites = frame_textures(common, cc.SEQ_MINI)
    portraits = frame_textures(grid, cc.SEQ_GRID)
    if names != {c: name_texture(c) for c in range(54)}:
        raise ValueError("name texture mapping differs from retail")
    # Frame54 is the separate empty-slot image.
    if sprites != {**{c: 153 + c for c in range(54)}, 54: 434}:
        raise ValueError("small sprite mapping differs from retail")
    if portraits != {**{c: 4 + c for c in range(54)}, 54: 118}:
        raise ValueError("grid portrait mapping differs from retail")
    verify_set("names: c<29 ? c : c+28", ct, [names[c] for c in range(54)], (111, 18, 8, 16, 0), log)
    verify_set("small sprites: 153+c", ct, [sprites[c] for c in range(54)], (52, 60, 9, 256, 2), log)
    verify_set("grid portraits: 4+c", gt, [portraits[c] for c in range(54)], (50, 50, 9, 256, 2), log)
    log("Verified all 162 character mappings; placeholders: common=434, grid=118.")
    return ct, gt, cc.menu_art_map(common, grid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disc", type=Path, help="extracted USA Rev 0 disc directory (retail or patched)")
    validate(parser.parse_args().disc)


if __name__ == "__main__":
    main()
