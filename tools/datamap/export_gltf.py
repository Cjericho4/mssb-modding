#!/usr/bin/env python3
"""Export a character's actor skeleton to self-contained glTF 2.0."""
import argparse
import base64
import json
import math
from pathlib import Path
import struct

import animations
import mssb_files
from stats import NAME_ID


def unit(q):
    length = math.sqrt(sum(x * x for x in q))
    if not math.isfinite(length) or length == 0:
        raise ValueError("invalid rest quaternion")
    return [x / length for x in q]


def multiply(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw*bx + ax*bw + ay*bz - az*by,
            aw*by - ax*bz + ay*bw + az*bx,
            aw*bz + ax*by - ay*bx + az*bw,
            aw*bw - ax*bx - ay*by - az*bz]


def euler_xyz(degrees):
    x, y, z = (math.radians(v) / 2 for v in degrees)
    qx = [math.sin(x), 0, 0, math.cos(x)]
    qy = [0, math.sin(y), 0, math.cos(y)]
    qz = [0, 0, math.sin(z), math.cos(z)]
    return unit(multiply(multiply(qz, qy), qx))


def inverse(q):
    x, y, z, w = q
    return [-x, -y, -z, w]


def read_control(body, layout_offset, bone):
    """Read the file's ControlExt rest pose, whose pointer is layout-relative."""
    pose = {"translation": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0],
            "scale": [1.0, 1.0, 1.0], "scale_quaternion": [0.0, 0.0, 0.0, 1.0]}
    pointer = bone["control_offset"]
    if not pointer:
        return pose
    reader = animations.Reader(body)
    offset = layout_offset + pointer
    kind, = reader.unpack("B", offset)
    if kind & 0x10:
        raise ValueError("rest matrix control unsupported on bone %d" % bone["bone_id"])
    if kind & 1:
        pose["scale"] = list(reader.unpack("3f", offset + 4))
    if kind & 4:
        pose["rotation"] = unit(reader.unpack("4f", offset + 16))
    elif kind & 2:
        pose["rotation"] = euler_xyz(reader.unpack("3f", offset + 16))
    if kind & 8:
        pose["translation"] = list(reader.unpack("3f", offset + 32))
    if kind & 0x20:
        pose["scale_quaternion"] = unit(reader.unpack("4f", offset + 44))
    return pose


class Buffer:
    def __init__(self):
        self.data = bytearray()
        self.views = []
        self.accessors = []

    def add(self, values, component, shape, target=None):
        fmt, size, code = {5126: ("f", 4, 5126), 5123: ("H", 2, 5123)}[component]
        count_per = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[shape]
        if len(values) % count_per:
            raise ValueError("accessor component count mismatch")
        while len(self.data) % 4:
            self.data.append(0)
        start = len(self.data)
        packed = struct.pack("<" + fmt * len(values), *values)
        self.data.extend(packed)
        stored = struct.unpack("<" + fmt * len(values), packed)
        view = {"buffer": 0, "byteOffset": start, "byteLength": len(self.data) - start}
        if target:
            view["target"] = target
        view_id = len(self.views)
        self.views.append(view)
        # Bounds describe the stored f32 values, not their higher-precision Python inputs.
        chunks = [stored[i:i+count_per] for i in range(0, len(stored), count_per)]
        accessor = {"bufferView": view_id, "componentType": code,
                    "count": len(chunks), "type": shape,
                    "min": [min(v[i] for v in chunks) for i in range(count_per)],
                    "max": [max(v[i] for v in chunks) for i in range(count_per)]}
        accessor_id = len(self.accessors)
        self.accessors.append(accessor)
        return accessor_id

    def finish(self, doc):
        doc["buffers"] = [{"byteLength": len(self.data),
                           "uri": "data:application/octet-stream;base64," +
                                  base64.b64encode(self.data).decode("ascii")}]
        doc["bufferViews"] = self.views
        doc["accessors"] = self.accessors


def cube(buffer):
    # A 5 cm marker shared by every bone, sufficient to inspect the hierarchy.
    vertices = [-.025,-.025,-.025, .025,-.025,-.025, .025,.025,-.025, -.025,.025,-.025,
                -.025,-.025,.025, .025,-.025,.025, .025,.025,.025, -.025,.025,.025]
    indices = [0,2,1,0,3,2, 4,5,6,4,6,7, 0,1,5,0,5,4,
               3,7,6,3,6,2, 0,4,7,0,7,3, 1,2,6,1,6,5]
    positions = buffer.add(vertices, 5126, "VEC3", 34962)
    faces = buffer.add(indices, 5123, "SCALAR", 34963)
    return {"name": "bone markers", "primitives": [{"attributes": {"POSITION": positions},
                                                    "indices": faces, "mode": 4}]}


def skeleton(body, character):
    layout = animations.parse_layout(body)
    buffer = Buffer()
    doc = {"asset": {"version": "2.0", "generator": "mssb export_gltf.py"},
           "scene": 0, "scenes": [{"nodes": []}], "nodes": [],
           "meshes": [cube(buffer)],
           "extras": {"character_id": character, "bone_count": layout["num_bones"],
                      "coordinate_system": "game and glTF: right-handed, +Y up"}}
    handles = []
    poses = []
    for bone in layout["bones"]:
        pose = read_control(body, layout["layout_offset"], bone)
        poses.append(pose)
        bone_id = bone["bone_id"]
        main = len(doc["nodes"])
        axis = main + 1
        scaled = main + 2
        leaf = main + 3
        doc["nodes"].extend([
            {"name": "bone_%d" % bone_id, "translation": pose["translation"],
             "rotation": pose["rotation"], "mesh": 0,
             "children": [axis], "extras": {"bone_id": bone_id}},
            {"name": "bone_%d_scale_axis" % bone_id,
             "rotation": pose["scale_quaternion"], "children": [scaled]},
            {"name": "bone_%d_scale" % bone_id, "scale": pose["scale"],
             "children": [leaf]},
            {"name": "bone_%d_scale_axis_inverse" % bone_id,
             "rotation": inverse(pose["scale_quaternion"]), "children": []}
        ])
        handles.append((main, axis, scaled, leaf))
    for i, bone in enumerate(layout["bones"]):
        parent = bone["parent_index"]
        if parent is None:
            doc["scenes"][0]["nodes"].append(handles[i][0])
        else:
            doc["nodes"][handles[parent][3]]["children"].append(handles[i][0])
    buffer.finish(doc)
    return doc, buffer, layout, handles, poses


def lerp(a, b, u):
    return [(1-u)*x + u*y for x, y in zip(a, b)]


def ease_time(u, a, b):
    """The retail ease remap, including its discontinuous coefficient."""
    if u == 0 or u == 1 or a + b == 0:
        return u
    total = a + b
    if total > 1:
        a /= total
        b /= total
    coefficient = 0.5 - 1 / total
    if u < a:
        return u*u*coefficient/a
    if u < 1-b:
        return coefficient*(2*u-a)
    return 1-(1-u)**2*coefficient/b


def bezier(a, b, out_offset, in_offset, u):
    return [(1-u)**3*x + 3*u*(1-u)**2*(x+out) +
            3*u*u*(1-u)*(y+inside) + u**3*y
            for x, y, out, inside in zip(a, b, out_offset, in_offset)]


def hermite(a, b, out_tangent, in_tangent, u):
    return [(2*u**3-3*u*u+1)*x + (3*u*u-2*u**3)*y +
            (u**3-2*u*u+u)*out + (u**3-u*u)*inside
            for x, y, out, inside in zip(a, b, out_tangent, in_tangent)]


def slerp(a, b, u):
    dot = sum(x*y for x, y in zip(a, b))
    if 1 + dot <= 1e-6:
        perp = [-b[1], b[0], -b[3], b[2]]
        return [math.sin((1-u)*math.pi/2)*x + math.sin(u*math.pi/2)*y
                for x, y in zip(a, perp)]
    if 1 - dot <= 1e-6:
        return lerp(a, b, u)
    angle = math.acos(max(-1, min(1, dot)))
    divisor = math.sin(angle)
    return [(math.sin((1-u)*angle)*x + math.sin(u*angle)*y)/divisor
            for x, y in zip(a, b)]


def squad(a, out_control, in_control, b, u):
    return slerp(slerp(a, b, u), slerp(out_control, in_control, u), 2*u*(1-u))


def key_pair(track, time):
    keys = track["keyframes"]
    if not keys:
        raise ValueError("track has no keys")
    if len(keys) == 1 or time <= keys[0]["time"]:
        return keys[0], keys[0], 0.0
    for i in range(len(keys)-1):
        current, following = keys[i], keys[i+1]
        if time < following["time"]:
            span = following["time"]-current["time"]
            return current, following, ((time-current["time"])/span if span else 0.0)
    return keys[-1], keys[-1], 0.0


def sample_vector(track, channel, mode, current, following, u):
    a = current["channels"][channel]
    b = following["channels"][channel]
    if mode == 0 or current is following:
        return a
    if channel == "euler" and mode == 1:
        if mode == 3:
            raise ValueError("Euler Hermite is unsupported by the game")
        # Retail wraps before lerp or Bezier interpolation.
        a, b = list(a), list(b)
        for i in range(3):
            if a[i]-b[i] > 180:
                a[i] -= 360
            elif a[i]-b[i] < -180:
                b[i] -= 360
    if mode == 1:
        return lerp(a, b, u)
    out = current["auxiliary"][channel]["out"]
    inside = following["auxiliary"][channel]["in"]
    if mode == 2:
        return bezier(a, b, out, inside, u)
    if mode == 3:
        ease_in, ease_out = animations.segment_ease(current, following, channel)
        return hermite(a, b, out, inside, ease_time(u, ease_in, ease_out))
    raise ValueError("unsupported %s interpolation mode %d" % (channel, mode))


def sample_track(track, time):
    """Sample a bone track at raw ANIM time; return only its animated channels."""
    current, following, u = key_pair(track, time)
    if current is following:
        return dict(current["channels"])
    channels = current["channels"]
    result = {}
    for name, shift, mask in (("scale", 2, 3), ("euler", 4, 7),
                              ("translation", 0, 3)):
        if name in channels:
            mode = (track["interpolation"] >> shift) & mask
            result[name] = sample_vector(track, name, mode, current, following, u)
    if "scale_quaternion" in channels:
        # The scale axis follows the scale channel's time; retail scale-quat
        # tracks use scale mode 1. No fixed s16 decode is applied here.
        mode = track["scale_interpolation"]
        result["scale_quaternion"] = (channels["scale_quaternion"] if mode == 0 else
            slerp(channels["scale_quaternion"],
                  following["channels"]["scale_quaternion"], u))
    if "quaternion" in channels:
        mode = track["rotation_interpolation"]
        a, b = channels["quaternion"], following["channels"]["quaternion"]
        if mode == 0:
            result["quaternion"] = a
        elif mode == 6:
            result["quaternion"] = slerp(a, b, u)
        elif mode in (4, 5):
            if mode == 5:
                ease_in, ease_out = animations.segment_ease(current, following, "quaternion")
                u = ease_time(u, ease_in, ease_out)
            result["quaternion"] = squad(
                a, current["auxiliary"]["quaternion"]["out"],
                following["auxiliary"]["quaternion"]["in"], b, u)
        else:
            raise ValueError("unsupported quaternion interpolation mode %d" % mode)
    return result


def add_animation(doc, buffer, bank, sequence_id, layout, handles, poses):
    if not 0 <= sequence_id < bank["num_sequences"]:
        raise ValueError("sequence %d is outside bank" % sequence_id)
    sequence = bank["sequences"][sequence_id]
    duration = max((t["duration"] for t in sequence["tracks"]), default=0)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("invalid animation duration")
    frames = list(range(math.floor(duration)+1))
    if not frames or frames[-1] != duration:
        frames.append(duration)
    seconds = [t/30 for t in frames]
    time_accessor = buffer.add(seconds, 5126, "SCALAR")
    animation = {"name": "bank%d_sequence%d" % (bank["bank_id"], sequence_id),
                 "samplers": [], "channels": [],
                 "extras": {"raw_duration": duration, "sample_rate_hz": 30}}
    bone_by_id = {}
    for index, bone in enumerate(layout["bones"]):
        bone_by_id.setdefault(bone["bone_id"], index)

    def channel(node, path, values):
        width = 4 if path == "rotation" else 3
        flattened = [x for value in values for x in value]
        output = buffer.add(flattened, 5126, "VEC4" if width == 4 else "VEC3")
        sampler = len(animation["samplers"])
        animation["samplers"].append({"input": time_accessor, "output": output,
                                      "interpolation": "LINEAR"})
        animation["channels"].append({"sampler": sampler,
                                      "target": {"node": node, "path": path}})

    for track in sequence["tracks"]:
        index = bone_by_id.get(track["bone_id"])
        if index is None:
            continue  # Aux tracks such as 0xFFFF have no actor bone.
        samples = [sample_track(track, t) for t in frames]
        if not samples:
            continue
        main, axis, scaled, leaf = handles[index]
        rest = poses[index]
        names = samples[0]
        if "translation" in names:
            channel(main, "translation", [s["translation"] for s in samples])
        if "quaternion" in names or "euler" in names:
            rotations = [unit(s["quaternion"]) if "quaternion" in s
                         else euler_xyz(s["euler"]) for s in samples]
            channel(main, "rotation", rotations)
        if "scale" in names:
            channel(scaled, "scale", [s["scale"] for s in samples])
        if "scale_quaternion" in names:
            axes = [unit(s["scale_quaternion"]) for s in samples]
            channel(axis, "rotation", axes)
            channel(leaf, "rotation", [inverse(q) for q in axes])
    doc["animations"] = [animation]
    buffer.finish(doc)
    return animation


def character_id(value):
    if value.isdecimal():
        number = int(value)
        if 0 <= number < 54:
            return number
    name = value.lower().replace(" ", "").replace("-", "")
    if name in NAME_ID:
        return NAME_ID[name]
    raise argparse.ArgumentTypeError("unknown character: %s" % value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disc", required=True, help="extracted game directory or ISO")
    parser.add_argument("--character", required=True, type=character_id)
    parser.add_argument("--bank", type=int, default=2, help="disc subfile track (default: 2 batting)")
    parser.add_argument("--sequence", type=int, default=2, help="bank sequence (default: 2 swing)")
    parser.add_argument("--skeleton-only", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.bank <= 18:
        parser.error("--bank must be 2..18")
    _, catalog = mssb_files.open_catalog(args.disc)
    body = catalog.data(catalog.find("subfile.%d" % (args.character * 19)))
    doc, buffer, layout, handles, poses = skeleton(body, args.character)
    if not args.skeleton_only:
        bank_data = catalog.data(catalog.find("subfile.%d" % (args.character * 19 + args.bank)))
        bank = animations.parse_bank(bank_data, keyframes=True)
        clip = add_animation(doc, buffer, bank, args.sequence, layout, handles, poses)
        print("Sampled %d channels at 30 fps" % len(clip["channels"]))
    args.out.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False) + "\n",
                        encoding="utf-8")
    print("Exported character %d: %d bones, %d nodes -> %s" %
          (args.character, layout["num_bones"], len(doc["nodes"]), args.out))


if __name__ == "__main__":
    main()

