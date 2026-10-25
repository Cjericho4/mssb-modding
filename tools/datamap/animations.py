#!/usr/bin/env python3
"""Inspect unrelocated GYQE01 charPipeline banks and body actor layouts.

Stdlib only. Extract with mssb_files.py first. JSON output is intended for local
analysis; it can contain game animation data and must not be redistributed.
See .agent/animations.md for the retail code evidence and export limitations.
"""
import argparse
import json
import math
from pathlib import Path
import struct

VERSION = 0x007B7960
QUANT_FORMATS = {0: "B", 1: "b", 2: "H", 3: "h", 4: "f"}


class Reader:
    """Read bounded, big-endian records relative to one file section."""

    def __init__(self, data):
        self.data = data

    def check(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise ValueError("out-of-range span 0x%X + 0x%X" % (offset, size))

    def unpack(self, fmt, offset):
        self.check(offset, struct.calcsize(">" + fmt))
        values = struct.unpack_from(">" + fmt, self.data, offset)
        if any(isinstance(x, float) and not math.isfinite(x) for x in values):
            raise ValueError("non-finite number at 0x%X" % offset)
        return values

    def string(self, offset):
        self.check(offset, 1)
        end = self.data.find(b"\0", offset)
        if end < 0:
            raise ValueError("unterminated sequence name at 0x%X" % offset)
        return self.data[offset:end].decode("ascii", errors="backslashreplace")

    def numbers(self, offset, count, quantization):
        kind, shift = quantization >> 4, quantization & 15
        if kind not in QUANT_FORMATS:
            raise ValueError("unsupported quantization 0x%02X" % quantization)
        values = self.unpack(str(count) + QUANT_FORMATS[kind], offset)
        # GetData copies floats directly; integer cases multiply by 2**-shift.
        return [float(x) if kind == 4 else x / (1 << shift) for x in values]


def _settings(reader, offset, quantization, anim_type):
    """Decode setting channels in the order used by Do*Animation."""
    if anim_type & ~0x3F:
        raise ValueError("unsupported animation type 0x%02X" % anim_type)
    if quantization >> 4 not in QUANT_FORMATS:
        raise ValueError("unsupported quantization 0x%02X" % quantization)
    width = struct.calcsize(QUANT_FORMATS[quantization >> 4])
    result = {}
    if anim_type & 0x10:
        # ANIMTick's matrix branch reads twelve floats, independent of kind.
        return {"matrix": [x / (1 << (quantization & 15))
                           for x in reader.unpack("12f", offset)]}
    if anim_type & 2:
        result["scale"] = reader.numbers(offset, 3, quantization)
        offset += 3 * width
        if anim_type & 0x20:
            result["scale_quaternion"] = reader.numbers(offset, 4, 0x3E)
            offset += 8
    if anim_type & 8:
        # Retail DoQuatAnimation uses fixed s16 / 16384, not quantizeInfo.
        result["quaternion"] = reader.numbers(offset, 4, 0x3E)
        offset += 8
    elif anim_type & 4:
        result["euler"] = reader.numbers(offset, 3, quantization)
        offset += 3 * width
    if anim_type & 1:
        result["translation"] = reader.numbers(offset, 3, quantization)
    return result


def parse_bank(data, keyframes=False):
    """List every sequence and bone track; optionally decode each key's setting."""
    reader = Reader(data)
    version, sequences, bank_id, ns, nt, nk, user_size, user_offset = reader.unpack("IIHHHHII", 0)
    if version != VERSION:
        raise ValueError("unsupported ANIM version 0x%08X" % version)
    # ANIMGet relocates contiguous tables beginning at +0x18, regardless of +4.
    if sequences != 0x18:
        raise ValueError("sequence table must begin at 0x18 for retail ANIMGet")
    track_base = 0x18 + ns * 12
    key_base = track_base + nt * 16
    reader.check(0x18, ns * 12 + nt * 16 + nk * 12)
    if user_size:
        reader.check(user_offset, user_size)
    result = {"version": version, "bank_id": bank_id, "num_sequences": ns,
              "num_tracks": nt, "num_keyframes": nk, "user_data_size": user_size,
              "user_data_offset": user_offset, "sequences": []}
    for sequence_id in range(ns):
        name, tracks, count, pad = reader.unpack("IIHH", sequences + sequence_id * 12)
        if tracks < track_base or (tracks - track_base) % 16 or tracks + count * 16 > key_base:
            raise ValueError("sequence track span is outside track table")
        sequence = {"id": sequence_id, "name": reader.string(name), "tracks": []}
        for index in range(count):
            duration, keys, total, bone_id, quant, kind, interp, replace = reader.unpack(
                "fIHHBBBB", tracks + index * 16)
            if keys < key_base or (keys - key_base) % 12 or keys + total * 12 > key_base + nk * 12:
                raise ValueError("track keyframe span is outside keyframe table")
            track = {"bone_id": bone_id, "duration": duration, "num_keyframes": total,
                     "quantization": quant, "anim_type": kind, "interpolation": interp,
                     "translation_interpolation": interp & 3,
                     "scale_interpolation": (interp >> 2) & 3,
                     "rotation_interpolation": (interp >> 4) & 7,
                     "replace_hierarchy_control": replace}
            previous = -math.inf
            frames = []
            for k in range(total):
                time, setting, auxiliary = reader.unpack("fII", keys + 12 * k)
                if time < previous:
                    raise ValueError("keyframe times decrease")
                previous = time
                reader.check(setting, 1)
                if auxiliary:
                    reader.check(auxiliary, 1)
                if keyframes:
                    frames.append({"time": time, "setting_offset": setting,
                                   "interpolation_offset": auxiliary,
                                   "channels": _settings(reader, setting, quant, kind)})
            if keyframes:
                track["keyframes"] = frames
            sequence["tracks"].append(track)
        result["sequences"].append(sequence)
    return result


def parse_layout(data):
    """Read a body's .gpc layout, including stable bone IDs and parent IDs."""
    body = Reader(data)
    layout_offset, = body.unpack("I", 4)
    body.check(layout_offset, 0x20)
    reader = Reader(data[layout_offset:])
    version, actor_id, count, tree_offset, root, palette, skin, pad, size, user = reader.unpack(
        "IHHIIIHHII", 0)
    if version != VERSION:
        raise ValueError("unsupported actor-layout version")
    reader.check(0x20, count * 0x1C)
    offsets = {0x20 + i * 0x1C: i for i in range(count)}

    def bone_index(pointer):
        if pointer == 0:
            return None
        if pointer not in offsets:
            raise ValueError("hierarchy pointer does not name an ActorBone")
        return offsets[pointer]

    bones = []
    for i in range(count):
        control, prev, nxt, parent, child, geo_id, bone_id, inherit, priority, pad = reader.unpack(
            "5IHHBBH", 0x20 + i * 0x1C)
        bone = {"index": i, "bone_id": bone_id, "parent_index": bone_index(parent),
                "previous_index": bone_index(prev), "next_index": bone_index(nxt),
                "child_index": bone_index(child), "geometry_id": geo_id,
                "inheritance": inherit, "priority": priority, "control_offset": control}
        if control:
            # CTRLControl is a one-byte type, three padding bytes, then floats.
            ctrl_type, = reader.unpack("B", control)
            bone["control_type"] = ctrl_type
            reader.check(control, 0x44)
        bones.append(bone)
    for bone in bones:
        parent = bone["parent_index"]
        bone["parent_bone_id"] = None if parent is None else bones[parent]["bone_id"]
    return {"layout_offset": layout_offset, "actor_id": actor_id, "num_bones": count,
            "tree_offset": tree_offset, "root_index": bone_index(root), "skin_file_id": skin,
            "bones": bones}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--layout", action="store_true", help="inspect a body .gpc instead of an ANIM bank")
    parser.add_argument("--keyframes", action="store_true", help="include decoded setting values (large)")
    parser.add_argument("--sequence", type=int, help="only show this zero-based sequence")
    parser.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args()
    try:
        result = parse_layout(args.file.read_bytes()) if args.layout else parse_bank(
            args.file.read_bytes(), args.keyframes)
        if args.sequence is not None:
            if args.layout or not 0 <= args.sequence < result["num_sequences"]:
                raise ValueError("sequence outside bank")
            result["sequences"] = [result["sequences"][args.sequence]]
        output = json.dumps(result, indent=2, allow_nan=False) + "\n"
        if args.out:
            args.out.write_text(output, encoding="utf-8")
        else:
            print(output, end="")
    except (ValueError, OSError) as error:
        parser.exit(2, "error: %s\n" % error)


if __name__ == "__main__":
    main()
