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
        self.data.extend(struct.pack("<" + fmt * len(values), *values))
        view = {"buffer": 0, "byteOffset": start, "byteLength": len(self.data) - start}
        if target:
            view["target"] = target
        view_id = len(self.views)
        self.views.append(view)
        chunks = [values[i:i+count_per] for i in range(0, len(values), count_per)]
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
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    _, catalog = mssb_files.open_catalog(args.disc)
    body = catalog.data(catalog.find("subfile.%d" % (args.character * 19)))
    doc, _, layout, _, _ = skeleton(body, args.character)
    args.out.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False) + "\n",
                        encoding="utf-8")
    print("Exported character %d: %d bones, %d nodes -> %s" %
          (args.character, layout["num_bones"], len(doc["nodes"]), args.out))


if __name__ == "__main__":
    main()

