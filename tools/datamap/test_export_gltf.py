"""Synthetic interpolation tests; no retail game files."""
import math
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


if __name__ == "__main__":
    unittest.main()

