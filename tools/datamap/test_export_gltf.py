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
        body = bytearray(320)
        struct.pack_into(">I", body, 8, 0x20)  # .gpc root geo
        struct.pack_into(">5I", body, 0x20, 0x005BBC61, 0, 0, 1, 0x14)
        struct.pack_into(">2I", body, 0x34, 0x1C, 0)
        struct.pack_into(">5IBBHI", body, 0x3C, 0x20, 0, 0xC0, 0xA0, 0x60, 1, 0, 0, 0)
        struct.pack_into(">IHBB", body, 0x5C, 0x30, 3, 0x3E, 3)
        struct.pack_into(">9h", body, 0x6C, 0, 0, 0, 16384, 0, 0, 0, 16384, 0)
        struct.pack_into(">IIH", body, 0x9C, 0, 0x6C, 2)
        display = bytes([0x90, 0, 3]) + b"".join(
            struct.pack(">3H", index, index, index) for index in range(3))
        display += bytes(32-len(display))
        # vertex descriptor 0xC3C: POS, NRM and TEX0 as 16-bit indices
        struct.pack_into(">BBHIII", body, 0xA8, 2, 0, 0, 0xC3C, 0, 0)
        struct.pack_into(">BBHIII", body, 0xB8, 1, 0, 0, 0x15110002, 0xE4, len(display))
        body[0x120:0x120+len(display)] = display
        struct.pack_into(">IHBB", body, 0xDC, 0xA8, 3, 0x3E, 3)
        struct.pack_into(">9h", body, 0xE4, *(0, 0, 16384)*3)
        struct.pack_into(">IHBBII", body, 0xFC, 0xD0, 3, 0x3E, 2, 0, 0)
        struct.pack_into(">6h", body, 0x10C, 0, 0, 16384, 0, 0, 16384)
        descriptors = list(gltf.geometry_descriptors(body))
        self.assertEqual(len(descriptors), 1)
        number, groups, strides, total = descriptors[0]
        positions = groups[0]["positions"]
        self.assertEqual((number, len(positions), groups[0]["triangles"], strides),
                         (0, 3, [(0, 2, 1)], [6]))
        self.assertEqual((groups[0]["source_ids"], total), ([0, 1, 2], 3))
        self.assertEqual(positions[1], [1, 0, 0])
        self.assertEqual(groups[0]["normals"], [[0, 0, 1]]*3)
        self.assertEqual(groups[0]["uvs"], [[0, 0], [1, 0], [0, 1]])
        self.assertEqual(groups[0]["texture_id"], 2)
        # GX winds front faces clockwise; glTF triangles are counter-clockwise.
        self.assertEqual(gltf.gx_triangles(0x98, [0, 1, 2, 3]),
                         [(0, 2, 1), (2, 3, 1)])
        self.assertEqual(gltf.gx_triangles(0x80, [0, 1, 2, 3]),
                         [(0, 2, 1), (0, 3, 2)])
        self.assertEqual(gltf.gx_triangles(0xA0, [0, 1, 2, 3]),
                         [(0, 2, 1), (0, 3, 2)])
        compact = bytes([0xA0, 0, 3, 0, 0, 1, 0, 2, 0]) + bytes(7)
        self.assertEqual(gltf.vertex_layout(0x28), [1, 1, 0, 0, 0, 0])
        self.assertEqual(gltf.vertex_layout(0x2CBC), [2, 2, 1, 0, 2, 1])
        self.assertEqual(gltf.decode_display_list(compact, 0, len(compact), [1, 1, 0, 0, 0, 0], 3),
                         ([(0, 0, None), (1, 0, None), (2, 0, None)], [(0, 2, 1)]))
        with self.assertRaises(ValueError):
            gltf.vertex_layout(0x2D)  # a direct (not indexed) attribute

    def test_cmpr_texture_to_embedded_png(self):
        import base64
        from pathlib import Path
        import tempfile
        import zlib
        body = bytearray(256)
        struct.pack_into(">2I", body, 0, 0x20, 0x100)
        struct.pack_into(">H", body, 0x20, 1)
        struct.pack_into(">IIHH", body, 0x24, 0x40, 0, 8, 8)
        body[0x24+0x17] = 14  # CMPR
        body[0x60:0x80] = struct.pack(">HHI", 0xF800, 0x07E0, 0)*4
        doc = {}
        self.assertEqual(gltf.add_textures(doc, bytes(body)), 1)
        self.assertEqual(len(doc["images"]), 1)
        png = base64.b64decode(doc["images"][0]["uri"].split(",", 1)[1])
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack_from(">II", png, 16), (8, 8))
        size, = struct.unpack_from(">I", png, 33)
        self.assertEqual(png[37:41], b"IDAT")
        rows = zlib.decompress(png[41:41+size])
        self.assertEqual(rows, (b"\0" + bytes([255, 0, 0])*8)*8)
        with tempfile.TemporaryDirectory() as directory:
            painted = gltf.png_rgb(8, 8, bytes([0, 0, 255])*64)
            (Path(directory) / "t00_8x8.png").write_bytes(painted)
            preview = {}
            gltf.add_textures(preview, bytes(body), Path(directory))
            self.assertEqual(base64.b64decode(
                preview["images"][0]["uri"].split(",", 1)[1]), painted)

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

        # A turned root stands the actor up: its children's +Y (game down) becomes -Y.
        poses[0]["turn"] = True
        worlds = gltf.rest_worlds(layout, poses)
        self.assertEqual([worlds[i][1][3] for i in range(3)], [0, -2, 0])
        self.assertEqual([worlds[i][2][3] for i in range(3)], [0, 0, -3])

    def test_scale_axis_bones_need_a_turned_axis(self):
        layout = {"bones": [{"bone_id": 7}, {"bone_id": 8}]}

        def scale_axis(q):
            return {"bone_id": 7, "keyframes": [
                {"channels": {"scale_quaternion": q, "scale": [1, 2, 1]}}]}

        self.assertEqual(gltf.scale_axis_bones(layout, [[scale_axis([0, 0, 0, 1])]]), set())
        self.assertEqual(gltf.scale_axis_bones(layout, [[scale_axis([0, .6, 0, .8])]]), {0})

    def test_named_animations_share_skeleton_nodes(self):
        self.assertEqual(gltf.animation_name(2, 2), "t2_s02_id77_BatterSwingBat")
        self.assertEqual(gltf.animation_name(4, 17), "t4_s17_id17_FielderCatchHighBall")
        self.assertEqual(gltf.animation_name(15, 2), "t15_s02_id107_WinningCelebration")
        self.assertEqual(gltf.animation_name(15, 0), "t15_s00_id105")
        self.assertEqual(gltf.animation_name(7, 0), "t7_s00")
        self.assertEqual(gltf.animation_name(4, 44),
                         "t4_s44_id44_FielderWallSidleLeft")
        self.assertEqual(gltf.animation_name(5, 11),
                         "t5_s11_id74_PitcherAwait_CockedHead")
        self.assertEqual(gltf.animation_name(2, 29),
                         "t2_s29_id104_BatterUnsure_Recovery2")
        self.assertEqual(gltf.bank_tracks("2,4,2,15"), (2, 4, 15))

        def move(distance):
            return {"bone_id": 5, "duration": 1, "interpolation": 1,
                    "keyframes": [key(0, "translation", [0, 0, 0]),
                                  key(1, "translation", [distance, 0, 0])]}

        bank = {"bank_id": 0, "num_sequences": 2,
                "sequences": [{"tracks": [move(1)]}, {"tracks": [move(2)]}]}
        doc = {"nodes": [{"name": "bone_5"}]}
        buffer = gltf.Buffer()
        layout = {"bones": [{"bone_id": 5}]}
        handles = [(0, 1, 2, 3)]
        gltf.add_animation(doc, buffer, bank, 0, layout, handles, [{}],
                           gltf.animation_name(2, 0))
        gltf.add_animation(doc, buffer, bank, 1, layout, handles, [{}],
                           gltf.animation_name(2, 1))
        buffer.finish(doc)
        self.assertEqual(len(doc["animations"]), 2)
        self.assertEqual(len(doc["nodes"]), 1)
        self.assertEqual([a["channels"][0]["target"]["node"]
                          for a in doc["animations"]], [0, 0])
        self.assertEqual([a["name"] for a in doc["animations"]],
                         ["t2_s00_id75_BatterIdle", "t2_s01_id76_BatterReadyBat"])


if __name__ == "__main__":
    unittest.main()

