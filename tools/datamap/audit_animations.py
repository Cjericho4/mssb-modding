#!/usr/bin/env python3
"""Verify every character bank/layout locally; emit metadata, never key values.

Usage: python audit_animations.py <extracted-disc-or-iso> <report.json>
May take several minutes because every distinct bank is decompressed and decoded.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import struct

import animations
import mssb_files


def audit(disc_path):
    """Check all 918 ANIM references, 108 bodies, and 54 ARAM counterparts."""
    disc, catalog = mssb_files.open_catalog(disc_path)
    cache = {}
    hashes = defaultdict(list)
    descriptor_families = defaultdict(list)
    layouts = []
    keys = 0
    for character in range(54):
        descriptors = []
        for track in range(2, 19):
            file = catalog.find("subfile.%d" % (character * 19 + track))
            descriptors.append(file.desc)
            if file.id not in cache:
                data = catalog.data(file)
                bank = animations.parse_bank(data, keyframes=True)
                hashes[hashlib.sha256(data).hexdigest()].append(file.id)
                keys += bank["num_keyframes"]
                cache[file.id] = {"num_sequences": bank["num_sequences"],
                                  "num_tracks": bank["num_tracks"],
                                  "num_keyframes": bank["num_keyframes"]}
        descriptor_families[tuple(descriptors)].append(character)
        for track in (0, 1):
            file = catalog.find("subfile.%d" % (character * 19 + track))
            layout = animations.parse_layout(catalog.data(file))
            layouts.append({"character": character, "track": track,
                            "num_bones": layout["num_bones"],
                            "signature": [(b["bone_id"], b["parent_bone_id"]) for b in layout["bones"]]})
    for character in range(54):
        master = catalog.data(catalog.find("master.%d" % (432 + character)))
        subfile = catalog.data(catalog.find("subfile.%d" % (character * 19 + 15)))
        if master != subfile:
            raise ValueError("master/disc track 15 mismatch for character %d" % character)
    windows_offset = disc.dol.ram_off(0x800FB798)
    windows = [{"character": c, "values": [struct.unpack_from(
        ">h", disc.dol.data, windows_offset + table * 108 + c * 2)[0]
        for table in range(15)]} for c in range(54)]
    return {"unique_disc_banks": len(cache), "decoded_keyframes": keys,
            "master_disc_equal": 54, "descriptor_families": list(descriptor_families.values()),
            "identical_distinct_files": [v for v in hashes.values() if len(v) > 1],
            "banks": cache, "layouts": layouts, "event_windows": windows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("disc")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    result = audit(args.disc)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Verified %d distinct banks, %d keys, 108 bodies, 54 master/disc pairs" % (
        result["unique_disc_banks"], result["decoded_keyframes"]))


if __name__ == "__main__":
    main()
