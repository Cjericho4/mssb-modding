"""Synthetic interpolation tests; no retail game files."""
import math
import struct
import unittest

import export_gltf as gltf


def key(time, name, value, inside=None, out=None, ease=None):
    auxiliary = {}
    if inside is not None:
        auxiliary[name] = {"in": inside, "out": out}
        if ease is not None:
            auxiliary[name]["ease"] = ease
    return {"time": time, "channels": {name: value}, "auxiliary": auxiliary}


def track(keys, name, mode):
    shift = 4 if name in ("euler", "quaternion") else 2 if name == "scale" else 0
    return {"keyframes": keys, "interpolation": mode << shift,
            "scale_interpolation": mode if name == "scale" else 0,
            "rotation_interpolation": mode if name in ("euler", "quaternion") else 0}


class CurveTests(unittest.TestCase):
    def test_hold_lerp_bezier(self):
        keys = [key(0, "translation", [0, 0, 0], [0]*3, [2, 0, 0]),
                key(10, "translation", [10, 0, 0], [-2, 0, 0], [0]*3)]
        self.assertEqual(gltf.sample_track(track(keys, "translation", 0), 5)["translation"],
                         [0, 0, 0])
        self.assertEqual(gltf.sample_track(track(keys, "translation", 1), 5)["translation"],
                         [5, 0, 0])
        self.assertEqual(gltf.sample_track(track(keys, "translation", 2), 5)["translation"],
                         [5, 0, 0])
        self.assertEqual(gltf.sample_track(track(keys, "translation", 2), 2.5)["translation"][0],
                         gltf.bezier([0], [10], [2], [-2], 0.25)[0])

    def test_eased_hermite_and_retail_coefficient(self):
        keys = [key(0, "translation", [0, 0, 0], [0]*3, [4, 0, 0], [0, 0.25]),
                key(10, "translation", [10, 0, 0], [2, 0, 0], [0]*3, [0.25, 0])]
        self.assertEqual(gltf.ease_time(0.5, 0.25, 0.25), -1.125)
        result = gltf.sample_track(track(keys, "translation", 3), 5)["translation"]
        self.assertAlmostEqual(result[0], gltf.hermite([0], [10], [4], [2], -1.125)[0])
        self.assertEqual(gltf.ease_time(0, 0.25, 0.25), 0)
        self.assertEqual(gltf.ease_time(1, 0.25, 0.25), 1)
        self.assertEqual(gltf.ease_time(0.5, 0, 0), 0.5)

    def test_euler_wrap(self):
        keys = [key(0, "euler", [170, 0, 0]),
                key(10, "euler", [-170, 0, 0])]
        result = gltf.sample_track(track(keys, "euler", 1), 5)["euler"]
        self.assertEqual(result, [-180, 0, 0])

    def test_quaternion_modes_and_antipodal(self):
        a = [0, 0, 0, 1]
        b = [0, 0, 1, 0]
        keys = [key(0, "quaternion", a, a, a, [0, 0.25]),
                key(10, "quaternion", b, b, b, [0.25, 0])]
        self.assertEqual(gltf.sample_track(track(keys, "quaternion", 0), 5)["quaternion"], a)
        midpoint = gltf.sample_track(track(keys, "quaternion", 6), 5)["quaternion"]
        self.assertAlmostEqual(midpoint[2], math.sqrt(.5))
        self.assertAlmostEqual(midpoint[3], math.sqrt(.5))
        squad = gltf.sample_track(track(keys, "quaternion", 4), 5)["quaternion"]
        self.assertAlmostEqual(squad[2], midpoint[2])
        eased = gltf.sample_track(track(keys, "quaternion", 5), 5)["quaternion"]
        self.assertTrue(all(math.isfinite(x) for x in eased))
        opposite = gltf.slerp(a, [0, 0, 0, -1], 0.5)
        self.assertAlmostEqual(opposite[2], math.sqrt(.5))
        self.assertAlmostEqual(opposite[3], math.sqrt(.5))

    def test_synthetic_gpc_geometry_and_gx_triangles(self):
        body = bytearray(256)
        struct.pack_into(">I", body, 8, 0x20)  # .gpc root geo
        struct.pack_into(">5I", body, 0x20, 0x005BBC61, 0, 0, 1, 0x14)
        struct.pack_into(">2I", body, 0x34, 0x1C, 0)
        struct.pack_into(">5IBBHI", body, 0x3C, 0x20, 0, 0, 0, 0x60, 0, 0, 0, 0)
        struct.pack_into(">IHBB", body, 0x5C, 0x30, 3, 0x3E, 3)
        struct.pack_into(">9h", body, 0x6C, 0, 0, 0, 16384, 0, 0, 0, 16384, 0)
        struct.pack_into(">IIH", body, 0x9C, 0, 0x6C, 1)
        display = bytes([0x90, 0, 3]) + b"".join(
            struct.pack(">3H", index, index, index) for index in range(3))
        display += bytes(32-len(display))
        struct.pack_into(">BBHIII", body, 0xA8, 2, 0, 0, 0, 0x80, len(display))
        body[0xBC:0xBC+len(display)] = display
        descriptors = list(gltf.geometry_descriptors(body))
        self.assertEqual(len(descriptors), 1)
        number, positions, faces, strides, source_ids, total = descriptors[0]
        self.assertEqual((number, len(positions), faces, strides),
                         (0, 3, [(0, 1, 2)], [6]))
        self.assertEqual((source_ids, total), ([0, 1, 2], 3))
        self.assertEqual(positions[1], [1, 0, 0])
        self.assertEqual(gltf.gx_triangles(0x98, [0, 1, 2, 3]),
                         [(0, 1, 2), (2, 1, 3)])
        self.assertEqual(gltf.gx_triangles(0x80, [0, 1, 2, 3]),
                         [(0, 1, 2), (0, 2, 3)])
        compact = bytes([0xA0, 0, 3, 0, 0, 1, 0, 2, 0]) + bytes(7)
        self.assertEqual(gltf.infer_vertex_stride(compact, 0, len(compact), 3)[0], 2)

    def test_scale_axis_and_buffer_bounds(self):
        keys = [key(0, "scale_quaternion", [0, 0, 0, 1]),
                key(10, "scale_quaternion", [0, 0, 1, 0])]
        animated = track(keys, "scale", 1)
        animated["scale_interpolation"] = 1
        self.assertAlmostEqual(gltf.sample_track(animated, 5)["scale_quaternion"][2],
                               math.sqrt(.5))
        buf = gltf.Buffer()
        accessor = buf.add([0, 1, 2, 3, 4, 5], 5126, "VEC3")
        self.assertEqual(buf.accessors[accessor]["min"], [0, 1, 2])
        self.assertEqual(buf.accessors[accessor]["max"], [3, 4, 5])
        rounded = buf.add([0, 0.2], 5126, "SCALAR")
        self.assertEqual(buf.accessors[rounded]["max"],
                         [0.20000000298023224])

    def test_skin_lists_and_depth_first_joint_order(self):
        body = bytearray(768)
        struct.pack_into(">I", body, 12, 0x20)
        struct.pack_into(">HHHBxIII", body, 0x20, 1, 1, 1, 11,
                         0x24, 0x64, 0xD8)
        struct.pack_into(">IIHHB", body, 0x20+0x24+0x30, 0, 0, 0, 1, 0)
        struct.pack_into(">IIIHHHB", body, 0x20+0x64+0x60,
                         0, 0x1E0, 12, 1, 2, 1, 0)
        body[0x20+0x1E0:0x20+0x1E2] = bytes([100, 100])
        struct.pack_into(">IIIIHH", body, 0x20+0xD8+0x30,
                         0, 0x1E4, 0, 0x1E8, 0, 1)
        struct.pack_into(">H", body, 0x20+0x1E4, 1)
        body[0x20+0x1E8] = 55
        layout = {"root_index": 0, "bones": [
            {"parent_index": None, "child_index": 2, "next_index": None},
            {"parent_index": 0, "child_index": None, "next_index": None},
            {"parent_index": 0, "child_index": None, "next_index": 1}]}
        self.assertEqual(gltf.runtime_bone_order(layout), [0, 2, 1])
        weights = gltf.skin_weights(body, layout, 2)
        self.assertEqual(weights[0][0][:1], (0,))
        self.assertEqual(weights[1][0][:3], (1, 2, 0))
        self.assertAlmostEqual(sum(weights[1][1]), 1)
        self.assertAlmostEqual(weights[1][1][2], 55/255)

        poses = [{"translation": translation, "rotation": [0, 0, 0, 1],
                  "scale": [1, 1, 1], "scale_quaternion": [0, 0, 0, 1]}
                 for translation in ([1, 0, 0], [0, 2, 0], [0, 0, 3])]
        matrices = gltf.inverse_bind_matrices(layout, poses)
        self.assertEqual([m[2][3] for m in matrices], [0, -3, 0])
        self.assertEqual([m[1][3] for m in matrices], [0, 0, -2])


if __name__ == "__main__":
    unittest.main()

