#!/usr/bin/env python3
"""Export a character's textured actor mesh, skeleton and clip to glTF 2.0."""
import argparse
import base64
import json
import math
from pathlib import Path
import struct
import sys
import zlib

import animations
import mssb_files
from stats import NAME_ID


# .agent/animations.md §1 in mssb-dtk maps gameplay IDs to bank sequence slots.
# The labels below are the reverse-engineered EnumCharacterAnimation_short names
# from mssb-dtk/in_game.h, not names stored in the retail ANIM sequences.
GAMEPLAY_NAME_GROUPS = (
    (0, """FielderStandStill FielderHoldBallStill FielderStandReady
FielderStandReadyToCatchOverhead FielderLookDissapointed FielderRunForwards
FielderRunForwardsLookUpLeft FielderWalkForwards FielderWalkForwardsLookUp
FielderWalkBackwards FielderSidleLeft FielderSidleRight FielderWalkForwardLow
FielderRunForwardsLookDownRight FielderRunForwardsLookDownLeft FielderTPose
FielderOnFire FielderCatchHighBall FielderCatchMidBall FielderCatchLowBall
FielderCatchHighRightBall FielderCatchMidRightBall FielderCatchLowRightBall
FielderCatchHighLeftBall FielderCatchMidLeftBall FielderCatchLowLeftBall
FielderPickUpBall FielderDive FielderBallKnockout FielderBallThrow
FielderBallThrow_Super FielderBallLob FielderTPose2 FielderTagBase
FielderRunToTagOut FielderStunned FielderEnvironmentKnockout
FielderRecoverFromKnockout FielderJump FielderLandFromJump FielderWallHang
FielderWallCatchBallAndRelease FielderWallClimb FielderWallSidleRight
FielderWallSidleLeft""".split()),
    (45, """RunnerRun Runner_Tpose RunnerWalk RunnerSkidStop RunnerTurnAround
RunnerDiveTurnAround Runner_BodyCheck RunnerDiveTurnSide
RunnerWalkAwayFrom2ndBase Runner_Tpose3 Runner_Tpose4 RunnerWait RunnerReady
RunnerExcited RunnerCelebration RunnerHomeRunCelebration""".split()),
    (61, "PitcherIdleLeft PitcherIdleRight".split()),
    (63, """PitcherIdle PitcherChargeThrow PitcherChargeThrowRecover
PitcherRegularThrow PitcherRegularThrowRecover PitcherResultHappy
PitcherResultSad PitcherTired PitcherStretch PitcherTaunt PitcherAwait
PitcherAwait_CockedHead""".split()),
    (75, """BatterIdle BatterReadyBat BatterSwingBat BatterResetAfterSwing
BatterUnsure_ResetAfterSwing BatterCoolDownAfterSwing BatterStrikePose
BatterHoldPose BatterSmallSwipe BatterSwipeCoolDown BatterHoldCharge
BatterHoldReady BatterGetReady BatterUnsure_Reset BatterReadyBunt
BatterHoldBunt BatterResetBunt BatterSwipeReset BatterBuntStrikeout
BatterSwingStrikeout BatterNoSwingStrikeout BatterChargeStrikout
BatterBallWalked BatterHitByBall BatterUnsure_Unhappy2
BatterUnsure_SwingLefty BatterPickOff3rdOut BatterReadyIntro
BatterWalkUpToPlate BatterUnsure_Recovery2""".split()),
    (107, ["WinningCelebration"]),
)

# Normal-match defaults are 2/3/4/5/15. Related mode variants use the same
# gameplay ID partition; 7/8 have no proven request path or ID mapping.
BANK_GAMEPLAY_BASE = {2: 75, 3: 45, 4: 0, 5: 63, 6: 61,
                      9: 75, 10: 75, 11: 45, 12: 45, 13: 0, 14: 45,
                      15: 105, 16: 105, 17: 105, 18: 63}
DEFAULT_ANIMATION_BANKS = (2, 3, 4, 5, 15)


def animation_name(bank_track, sequence_id):
    """Name a disc-bank sequence by the gameplay ID it occupies, if known."""
    prefix = "t%d_s%02d" % (bank_track, sequence_id)
    base = BANK_GAMEPLAY_BASE.get(bank_track)
    if base is None:
        return prefix
    gameplay_id = base + sequence_id
    prefix += "_id%d" % gameplay_id
    for start, names in GAMEPLAY_NAME_GROUPS:
        if start <= gameplay_id < start + len(names):
            return prefix + "_" + names[gameplay_id-start]
    return prefix


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
        count_per = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[shape]
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
                    "count": len(chunks), "type": shape}
        if shape != "MAT4":
            accessor["min"] = [min(v[i] for v in chunks) for i in range(count_per)]
            accessor["max"] = [max(v[i] for v in chunks) for i in range(count_per)]
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


def runtime_bone_order(layout):
    """The game's bone pointer array is filled by a depth-first tree walk."""
    bones = layout["bones"]
    order = []
    seen = set()

    def visit(index):
        while index is not None:
            if index in seen:
                raise ValueError("cycle in bone tree")
            seen.add(index)
            order.append(index)
            visit(bones[index]["child_index"])
            index = bones[index]["next_index"]

    visit(layout["root_index"])
    if len(order) != len(bones):
        raise ValueError("bone tree does not contain every bone")
    return order


def matmul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]


def translation_matrix(v):
    m = [[1.0, 0.0, 0.0, v[0]], [0.0, 1.0, 0.0, v[1]],
         [0.0, 0.0, 1.0, v[2]], [0.0, 0.0, 0.0, 1.0]]
    return m


def rotation_matrix(q):
    x, y, z, w = q
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), 0.0],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), 0.0],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def scale_matrix(v):
    return [[v[0], 0.0, 0.0, 0.0], [0.0, v[1], 0.0, 0.0],
            [0.0, 0.0, v[2], 0.0], [0.0, 0.0, 0.0, 1.0]]


def inverse_affine(m):
    a, b, c = m[0][:3]
    d, e, f = m[1][:3]
    g, h, i = m[2][:3]
    cof = [[e*i-f*h, c*h-b*i, b*f-c*e],
           [f*g-d*i, a*i-c*g, c*d-a*f],
           [d*h-e*g, b*g-a*h, a*e-b*d]]
    det = a*cof[0][0] + b*cof[1][0] + c*cof[2][0]
    if abs(det) < 1e-12:
        raise ValueError("singular bone rest transform")
    inv = [[x/det for x in row] for row in cof]
    offset = [-sum(inv[r][k]*m[k][3] for k in range(3)) for r in range(3)]
    return [inv[r]+[offset[r]] for r in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def inverse_bind_matrices(layout, poses):
    """Inverse of each joint's complete rest transform, including scale axis."""
    order = runtime_bone_order(layout)
    worlds = {}
    for index in order:
        pose = poses[index]
        local = translation_matrix(pose["translation"])
        for part in (rotation_matrix(pose["rotation"]),
                     rotation_matrix(pose["scale_quaternion"]),
                     scale_matrix(pose["scale"]),
                     rotation_matrix(inverse(pose["scale_quaternion"]))):
            local = matmul(local, part)
        parent = layout["bones"][index]["parent_index"]
        worlds[index] = matmul(worlds[parent], local) if parent is not None else local
    return [inverse_affine(worlds[index]) for index in order]


def skin_weights(body, layout, vertex_count):
    """Decode SK1, SK2 and accumulator influences for the skin position array."""
    reader = animations.Reader(body)
    skin, = reader.unpack("I", 12)
    if not skin:
        raise ValueError("actor has no skin section")
    n1, n2, na, shift = reader.unpack("HHHB", skin)
    if max(n1, n2, na) > 1024:
        raise ValueError("invalid skin list count")
    p1, p2, pa = reader.unpack("III", skin+8)
    reader.check(skin+p1, n1*0x40)
    reader.check(skin+p2, n2*0x74)
    reader.check(skin+pa, na*0x44)
    influences = {}

    def vertices(dst, offset, count):
        if (dst+offset) % 12:
            raise ValueError("unaligned skin destination")
        start = (dst+offset)//12
        if start+count > vertex_count:
            raise ValueError("skin destination exceeds position array")
        return range(start, start+count)

    for i in range(n1):
        src, dst, bone, count, offset = reader.unpack("IIHHB", skin+p1+i*0x40+0x30)
        for index in vertices(dst, offset, count):
            if index in influences:
                raise ValueError("overlapping primary skin lists")
            influences[index] = [(bone, 255)]
    for i in range(n2):
        src, weights, dst, bone0, bone1, count, offset = reader.unpack(
            "IIIHHHB", skin+p2+i*0x74+0x60)
        reader.check(skin+weights, 2*count)
        for j, index in enumerate(vertices(dst, offset, count)):
            if index in influences:
                raise ValueError("overlapping primary skin lists")
            w0, w1 = reader.unpack("BB", skin+weights+2*j)
            influences[index] = [(bone0, w0), (bone1, w1)]
    for i in range(na):
        src, indices, dst, weights, bone, count = reader.unpack(
            "IIIIHH", skin+pa+i*0x44+0x30)
        reader.check(skin+indices, count*2)
        reader.check(skin+weights, count)
        for j in range(count):
            index, = reader.unpack("H", skin+indices+2*j)
            weight, = reader.unpack("B", skin+weights+j)
            if index >= vertex_count:
                raise ValueError("accumulator vertex index exceeds position array")
            influences.setdefault(index, []).append((bone, weight))
    order = runtime_bone_order(layout)
    if any(bone >= len(order) for values in influences.values() for bone, _ in values):
        raise ValueError("skin joint index exceeds bone count")
    if any(len(values) > 8 for values in influences.values()):
        raise ValueError("more than eight skin influences")
    result = {}
    for index, values in influences.items():
        total = sum(weight for _, weight in values)
        if not total:
            raise ValueError("zero skin weight")
        result[index] = ((tuple(bone for bone, _ in values) + (0,)*8)[:8],
                         (tuple(weight/total for _, weight in values) + (0.0,)*8)[:8])
    return result


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


def add_animation(doc, buffer, bank, sequence_id, layout, handles, poses, name=None):
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
    time_accessor = None
    animation = {"name": name or "bank%d_sequence%d" % (bank["bank_id"], sequence_id),
                 "samplers": [], "channels": [],
                 "extras": {"raw_duration": duration, "sample_rate_hz": 30}}
    bone_by_id = {}
    for index, bone in enumerate(layout["bones"]):
        bone_by_id.setdefault(bone["bone_id"], index)

    def channel(node, path, values):
        nonlocal time_accessor
        if time_accessor is None:
            time_accessor = buffer.add(seconds, 5126, "SCALAR")
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
    if animation["channels"]:
        doc.setdefault("animations", []).append(animation)
        return animation
    return None


def gx_triangles(op, vertices):
    """Triangulate GX quads, triangles, strips and fans."""
    if op == 0x80:
        if len(vertices) % 4:
            raise ValueError("GX quad count is not a multiple of four")
        return [triangle for i in range(0, len(vertices), 4)
                for triangle in ((vertices[i], vertices[i+1], vertices[i+2]),
                                 (vertices[i], vertices[i+2], vertices[i+3]))]
    if op == 0x90:
        if len(vertices) % 3:
            raise ValueError("GX triangle count is not a multiple of three")
        return [tuple(vertices[i:i+3]) for i in range(0, len(vertices), 3)]
    if op == 0x98:
        return [(vertices[i-2], vertices[i-1], vertices[i]) if i % 2 == 0
                else (vertices[i-1], vertices[i-2], vertices[i])
                for i in range(2, len(vertices))]
    if op == 0xA0:
        return [(vertices[0], vertices[i-1], vertices[i])
                for i in range(2, len(vertices))]
    raise ValueError("unsupported GX primitive 0x%02X" % op)


def vertex_layout(vcd):
    """Byte widths of POS, NRM, CLR0, CLR1, TEX0 and TEX1 in a display list's vertices.

    Display state 2 holds the list's vertex descriptor: 2-bit fields from bit 0, the first
    (matrix index) unused, then POS, NRM, CLR0, CLR1, TEX0, TEX1; 0 none, 2 index8, 3 index16.
    Every retail body (54 characters, 938 lists) uses only these.
    """
    fields = [(vcd >> (2 * k)) & 3 for k in range(16)]
    if fields[0] or any(fields[7:]) or 1 in fields or not fields[1]:
        raise ValueError("unsupported vertex descriptor 0x%X" % vcd)
    return [{0: 0, 2: 1, 3: 2}[field] for field in fields[1:7]]


def decode_display_list(data, offset, size, widths, position_count):
    """Read GX FIFO position, normal and TEX0 indices laid out as `widths` (vertex_layout)."""
    reader = animations.Reader(data)
    reader.check(offset, size)
    end = offset + size
    stride = sum(widths)
    cursor = offset
    vertices = []
    triangles = []
    while cursor < end:
        op = data[cursor]
        if op == 0:  # 32-byte display-list padding
            cursor += 1
        elif op == 0x61:  # BP register write: opcode + u32
            reader.check(cursor, 5)
            cursor += 5
        elif op == 0x08:  # CP register write: opcode + u8 + u32
            reader.check(cursor, 6)
            cursor += 6
        elif op in (0x80, 0x90, 0x98, 0xA0):
            reader.check(cursor, 3)
            count, = reader.unpack("H", cursor+1)
            if cursor + 3 + count * stride > end:
                raise ValueError("GX vertex list exceeds its declared size")
            first = len(vertices)
            for i in range(count):
                address = cursor + 3 + i * stride
                indices = []
                for width in widths:
                    indices.append(None if not width else data[address] if width == 1
                                   else reader.unpack("H", address)[0])
                    address += width
                pos, normal, texcoord = indices[0], indices[1], indices[4]
                if pos >= position_count:
                    raise ValueError("GX position index %d >= %d" % (pos, position_count))
                vertices.append((pos, normal, texcoord))
            triangles.extend(gx_triangles(op, list(range(first, first+count))))
            cursor += 3 + count * stride
        else:
            raise ValueError("unsupported GX command 0x%02X at 0x%X" % (op, cursor))
    if not triangles:
        raise ValueError("display list has no triangles")
    return vertices, triangles


def geometry_descriptors(body):
    """Read unrelocated .gpc geo palette offsets (palette and layout relative)."""
    reader = animations.Reader(body)
    geo, = reader.unpack("I", 8)
    version, user_size, user, count, descriptors = reader.unpack("5I", geo)
    if version != 0x005BBC61 or count > 1024:
        raise ValueError("invalid geo palette")
    reader.check(geo + descriptors, count * 8)
    for i in range(count):
        layout_offset, name = reader.unpack("II", geo + descriptors + i*8)
        layout = geo + layout_offset
        pos, color, tex, light, display = reader.unpack("5I", layout)
        if not pos or not display:
            continue
        array, total, quant, components = reader.unpack("IHBB", layout + pos)
        kind, shift = quant >> 4, quant & 15
        if kind != 3 or components not in (3, 6):
            raise ValueError("unsupported position encoding 0x%02X/%d" %
                             (quant, components))
        reader.check(layout + array, total * components * 2)
        vertices = []
        for j in range(total):
            raw = reader.unpack("3h", layout + array + j*components*2)
            vertices.append([v / (1 << shift) for v in raw])
        normals = []
        if light:
            normal_array, normal_count, normal_quant, normal_components = reader.unpack(
                "IHBB", layout + light)
            if normal_quant >> 4 != 3 or normal_components not in (3, 6):
                raise ValueError("unsupported normal encoding")
            reader.check(layout + normal_array, normal_count * normal_components * 2)
            for j in range(normal_count):
                values = reader.unpack("3h", layout + normal_array +
                                       j*normal_components*2)
                xyz = [value / (1 << (normal_quant & 15)) for value in values]
                length = math.sqrt(sum(value*value for value in xyz))
                normals.append([value/length for value in xyz] if length else [0, 0, 1])
        texcoords = []
        if tex:
            channels = body[layout+0x14]
            if channels:
                uv_array, uv_count, uv_quant, uv_components = reader.unpack(
                    "IHBB", layout + tex)
                if uv_quant >> 4 != 3 or uv_components != 2:
                    raise ValueError("unsupported TEX0 encoding")
                reader.check(layout + uv_array, uv_count * 4)
                for j in range(uv_count):
                    uv = reader.unpack("2h", layout + uv_array + j*4)
                    texcoords.append([v / (1 << (uv_quant & 15)) for v in uv])
        primitive_bank, states, num_states = reader.unpack("IIH", layout + display)
        if num_states > 1024:
            raise ValueError("invalid display state count")
        groups = []
        formats = set()
        texture_id = None
        vcd = None
        for j in range(num_states):
            state_id, pad, pad16, setting, primitive, size = reader.unpack(
                "BBHIII", layout + states + j*16)
            # State 1 binds texture (low 13 bits) to texture map (bits 13-15). 0x1511 on map 0
            # is the primary texture; map 1 (0x2000, mostly 0x1500 settings) is an additional
            # TEV stage that the simple glTF material omits (King Boo's 0x1511200A is one).
            if state_id == 1 and setting >> 16 == 0x1511 and not setting & 0xE000:
                texture_id = setting & 0x1FFF
            if state_id == 2:
                vcd = setting
            if not primitive or not size:
                continue
            if vcd is None:
                raise ValueError("display list before its vertex descriptor")
            widths = vertex_layout(vcd)
            records, faces = decode_display_list(body, layout + primitive, size, widths, total)
            formats.add(sum(widths))
            for pos_id, normal_id, tex_id in records:
                if normal_id is not None and normal_id >= len(normals):
                    raise ValueError("GX normal index exceeds normal array")
                if tex_id is not None and tex_id >= len(texcoords):
                    raise ValueError("GX TEX0 index exceeds texture coordinate array")
            groups.append({"positions": [vertices[p] for p, _, _ in records],
                           "normals": [normals[n] for _, n, _ in records]
                           if widths[1] else [],
                           "uvs": [texcoords[t] for _, _, t in records]
                           if widths[4] else [],
                           "source_ids": [p for p, _, _ in records],
                           "triangles": faces, "texture_id": texture_id})
        if groups:
            yield i, groups, sorted(formats), total


def png_rgb(width, height, pixels):
    """Store decoded RGB pixels as a PNG without external dependencies."""
    if len(pixels) != width*height*3:
        raise ValueError("decoded texture size mismatch")

    def chunk(name, payload):
        return (struct.pack(">I", len(payload)) + name + payload +
                struct.pack(">I", zlib.crc32(name+payload) & 0xFFFFFFFF))

    rows = b"".join(b"\0" + pixels[y*width*3:(y+1)*width*3]
                    for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def add_textures(doc, body, textures_dir=None):
    """Embed every body texture, using the existing Clone Maker GX decoder."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modding"))
    import clone_character

    archive = clone_character.Gpc(body)
    wrap = {0: 33071, 1: 10497, 2: 33648}
    doc["images"] = []
    doc["samplers"] = []
    doc["textures"] = []
    doc["materials"] = []
    for item in archive.textures:
        index = item["index"]
        edited = (textures_dir / ("t%02d_%dx%d.png" %
                   (index, item["w"], item["h"]))) if textures_dir else None
        if edited and edited.is_file():
            png = edited.read_bytes()
            if (len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n" or
                    png[12:16] != b"IHDR" or
                    struct.unpack_from(">II", png, 16) != (item["w"], item["h"])):
                raise ValueError("%s is not a %dx%d PNG" %
                                 (edited, item["w"], item["h"]))
        else:
            data = clone_character.texture_bytes(archive, item)
            pixels = clone_character.decode_texture(data, item)
            png = png_rgb(item["w"], item["h"], pixels)
        doc["images"].append({"name": "texture_%02d" % index,
                              "uri": "data:image/png;base64," +
                                     base64.b64encode(png).decode("ascii")})
        hdr = item["hdr"]
        wrap_s, wrap_t, min_filter, mag_filter = body[hdr+12:hdr+16]
        if wrap_s not in wrap or wrap_t not in wrap:
            raise ValueError("unsupported texture wrap mode")
        doc["samplers"].append({"wrapS": wrap[wrap_s], "wrapT": wrap[wrap_t],
                                "minFilter": 9728 if min_filter == 0 else 9729,
                                "magFilter": 9728 if mag_filter == 0 else 9729})
        doc["textures"].append({"source": index, "sampler": index})
        doc["materials"].append({
            "name": "texture_%02d" % index, "doubleSided": True,
            "alphaMode": "MASK", "alphaCutoff": 0.5,
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": index},
                "metallicFactor": 0, "roughnessFactor": 1}})
    plain = len(doc["materials"])
    doc["materials"].append({"name": "untextured body", "doubleSided": True,
                             "pbrMetallicRoughness": {
                                 "baseColorFactor": [0.7, 0.7, 0.7, 1],
                                 "metallicFactor": 0, "roughnessFactor": 1}})
    return plain


def add_geometry(doc, buffer, body, layout, handles, poses, textures_dir=None):
    """Attach textured skin geometry and rigid geometry below its bone."""
    skin_id = layout["skin_file_id"]
    plain_material = add_textures(doc, body, textures_dir)
    rigid = {}
    for i, bone in enumerate(layout["bones"]):
        if bone["geometry_id"] != 0xFFFF:
            rigid.setdefault(bone["geometry_id"], i)
    counts = []
    for descriptor_id, groups, strides, total in geometry_descriptors(body):
        mesh_id = len(doc["meshes"])
        primitives = []
        influences = skin_weights(body, layout, total) if descriptor_id == skin_id else {}
        for group in groups:
            positions = group["positions"]
            source_ids = group["source_ids"]
            triangles = group["triangles"]
            if len(positions) > 65535:
                raise ValueError("geometry group needs u32 indices")
            attributes = {"POSITION": buffer.add(
                [x for point in positions for x in point], 5126, "VEC3", 34962)}
            if group["normals"]:
                attributes["NORMAL"] = buffer.add(
                    [x for normal in group["normals"] for x in normal],
                    5126, "VEC3", 34962)
            if group["uvs"]:
                attributes["TEXCOORD_0"] = buffer.add(
                    [x for uv in group["uvs"] for x in uv],
                    5126, "VEC2", 34962)
            if descriptor_id == skin_id:
                missing = set(source_ids) - influences.keys()
                if missing:
                    raise ValueError("display list references %d unweighted skin vertices: %s" %
                                     (len(missing), sorted(missing)[:10]))
                for set_id in range(1 + any(any(influences[index][1][4:])
                                            for index in source_ids)):
                    start = set_id * 4
                    joints = [joint for index in source_ids
                              for joint in influences[index][0][start:start+4]]
                    weights = [weight for index in source_ids
                               for weight in influences[index][1][start:start+4]]
                    attributes["JOINTS_%d" % set_id] = buffer.add(
                        joints, 5123, "VEC4", 34962)
                    attributes["WEIGHTS_%d" % set_id] = buffer.add(
                        weights, 5126, "VEC4", 34962)
            face_accessor = buffer.add(
                [i for triangle in triangles for i in triangle], 5123, "SCALAR", 34963)
            material = group["texture_id"]
            if material is None:
                material = plain_material
            elif material >= plain_material:
                raise ValueError("display state references texture %d >= %d" %
                                 (material, plain_material))
            primitives.append({"attributes": attributes, "indices": face_accessor,
                               "mode": 4, "material": material})
        doc["meshes"].append({"name": "geo_%d" % descriptor_id,
                              "primitives": primitives})
        node_id = len(doc["nodes"])
        doc["nodes"].append({"name": "geo_%d" % descriptor_id, "mesh": mesh_id,
                             "extras": {"descriptor": descriptor_id,
                                        "vertex_strides": strides}})
        if descriptor_id == skin_id:
            if "skins" not in doc:
                matrices = inverse_bind_matrices(layout, poses)
                gltf_matrices = [value for matrix in matrices
                                 for column in range(4) for value in
                                 (matrix[0][column], matrix[1][column],
                                  matrix[2][column], matrix[3][column])]
                ibm = buffer.add(gltf_matrices, 5126, "MAT4")
                order = runtime_bone_order(layout)
                doc["skins"] = [{"name": "actor skin",
                                 "inverseBindMatrices": ibm,
                                 "joints": [handles[index][3] for index in order],
                                 "skeleton": handles[layout["root_index"]][0]}]
            doc["nodes"][node_id]["skin"] = 0
            doc["scenes"][0]["nodes"].append(node_id)
        elif descriptor_id in rigid:
            doc["nodes"][handles[rigid[descriptor_id]][3]]["children"].append(node_id)
        else:
            raise ValueError("unowned rigid geometry descriptor %d" % descriptor_id)
        counts.append((descriptor_id,
                       sum(len(group["positions"]) for group in groups),
                       sum(len(group["triangles"]) for group in groups)))
    buffer.finish(doc)
    return counts


def character_id(value):
    if value.isdecimal():
        number = int(value)
        if 0 <= number < 54:
            return number
    name = value.lower().replace(" ", "").replace("-", "")
    if name in NAME_ID:
        return NAME_ID[name]
    raise argparse.ArgumentTypeError("unknown character: %s" % value)


def bank_tracks(value):
    try:
        tracks = tuple(int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--banks expects comma-separated tracks") from exc
    if not tracks or any(not 2 <= track <= 18 for track in tracks):
        raise argparse.ArgumentTypeError("--banks tracks must be 2..18")
    return tuple(dict.fromkeys(tracks))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disc", required=True, help="extracted game directory or ISO")
    parser.add_argument("--character", required=True, type=character_id)
    parser.add_argument("--bank", type=int, default=2, help="disc subfile track (default: 2 batting)")
    parser.add_argument("--sequence", type=int, default=2, help="bank sequence (default: 2 swing)")
    parser.add_argument("--all-animations", action="store_true",
                        help="include every nonempty sequence from normal-match banks")
    parser.add_argument("--banks", type=bank_tracks,
                        help="comma-separated tracks for --all-animations (default: 2,3,4,5,15)")
    parser.add_argument("--skeleton-only", action="store_true")
    parser.add_argument("--no-mesh", action="store_true", help="show joint markers only")
    parser.add_argument("--textures", type=Path,
                        help="preview painted PNGs from Clone Maker's Export Textures folder")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.textures and not args.textures.is_dir():
        parser.error("--textures must name an existing folder")
    if not 2 <= args.bank <= 18:
        parser.error("--bank must be 2..18")
    if args.banks and not args.all_animations:
        parser.error("--banks requires --all-animations")
    if args.skeleton_only and args.all_animations:
        parser.error("--skeleton-only conflicts with --all-animations")
    _, catalog = mssb_files.open_catalog(args.disc)
    body = catalog.data(catalog.find("subfile.%d" % (args.character * 19)))
    doc, buffer, layout, handles, poses = skeleton(body, args.character)
    if not args.no_mesh:
        counts = add_geometry(doc, buffer, body, layout, handles, poses, args.textures)
        print("Geometry: " + ", ".join("%d: %d vertices, %d triangles" % row
                                       for row in counts))
    if not args.skeleton_only:
        if args.all_animations:
            for track in args.banks or DEFAULT_ANIMATION_BANKS:
                bank_data = catalog.data(catalog.find(
                    "subfile.%d" % (args.character * 19 + track)))
                bank = animations.parse_bank(bank_data, keyframes=True)
                for sequence_id, sequence in enumerate(bank["sequences"]):
                    if sequence["tracks"]:
                        add_animation(doc, buffer, bank, sequence_id, layout,
                                      handles, poses, animation_name(track, sequence_id))
            print("Sampled %d animations at 30 fps" % len(doc.get("animations", [])))
        else:
            bank_data = catalog.data(catalog.find(
                "subfile.%d" % (args.character * 19 + args.bank)))
            bank = animations.parse_bank(bank_data, keyframes=True)
            clip = add_animation(doc, buffer, bank, args.sequence,
                                 layout, handles, poses,
                                 animation_name(args.bank, args.sequence))
            print("Sampled %d channels at 30 fps" %
                  (len(clip["channels"]) if clip else 0))
    buffer.finish(doc)
    args.out.write_text(json.dumps(doc, separators=(",", ":"), allow_nan=False) + "\n",
                        encoding="utf-8")
    print("Exported character %d: %d bones, %d nodes -> %s" %
          (args.character, layout["num_bones"], len(doc["nodes"]), args.out))


if __name__ == "__main__":
    main()

