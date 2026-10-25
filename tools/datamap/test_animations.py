"""Synthetic bank/layout tests; no game files required."""
import struct
import unittest

import animations as anim


def bank_fixture():
    """Two keys share a quaternion, with independently quantized translations."""
    data = bytearray(112)
    struct.pack_into(">IIHHHHII", data, 0, anim.VERSION, 24, 7, 1, 1, 2, 0, 0)
    struct.pack_into(">IIHH", data, 24, 110, 36, 1, 0)
    struct.pack_into(">fIHHBBBB", data, 36, 10, 52, 2, 46, 0x32, 9, 0x61, 1)
    struct.pack_into(">fII", data, 52, 0, 76, 0)
    struct.pack_into(">fII", data, 64, 10, 90, 0)
    struct.pack_into(">7h", data, 76, 0, 0, 0, 16384, -4, 8, 12)
    struct.pack_into(">7h", data, 90, 0, 0, 0, 16384, 4, -8, 16)
    data[110:] = b"s\0"
    return data


class AnimationTests(unittest.TestCase):
    def test_sequence_track_and_quantized_keys(self):
        bank = anim.parse_bank(bank_fixture(), keyframes=True)
        self.assertEqual(bank["bank_id"], 7)
        sequence = bank["sequences"][0]
        self.assertEqual(sequence["name"], "s")
        track = sequence["tracks"][0]
        self.assertEqual(track["bone_id"], 46)
        self.assertEqual(track["rotation_interpolation"], 6)
        self.assertEqual(track["translation_interpolation"], 1)
        self.assertEqual(track["keyframes"][0]["channels"], {
            "quaternion": [0, 0, 0, 1], "translation": [-1, 2, 3]})
        self.assertEqual(track["keyframes"][1]["channels"]["translation"], [1, -2, 4])
        self.assertNotIn("keyframes", anim.parse_bank(bank_fixture())["sequences"][0]["tracks"][0])

    def test_reject_corrupt_spans_and_times(self):
        for offset, fmt, value in [(4, "I", 0x80000018), (28, "I", 37),
                                   (40, "I", 53), (56, "I", 1000), (64, "f", -1)]:
            with self.subTest(offset=offset):
                data = bank_fixture()
                struct.pack_into(">" + fmt, data, offset, value)
                with self.assertRaises(ValueError):
                    anim.parse_bank(data, keyframes=True)
        with self.assertRaises(ValueError):
            anim.parse_bank(bank_fixture()[:60])

    def test_numeric_formats(self):
        for quant, fmt, value, expected in [(0x01, "B", 8, 4), (0x11, "b", -8, -4),
                                            (0x21, "H", 512, 256), (0x31, "h", -512, -256),
                                            (0x4F, "f", 1.5, 1.5)]:
            self.assertEqual(anim.Reader(struct.pack(">" + fmt, value)).numbers(0, 1, quant), [expected])

    def test_hierarchy_uses_ids_not_array_indices(self):
        data = bytearray(128)
        struct.pack_into(">I", data, 4, 32)
        struct.pack_into(">IHHIIIHHII", data, 32, anim.VERSION, 3, 2, 12, 32, 0, 0xFFFF, 0, 0, 0)
        struct.pack_into(">5IHHBBH", data, 64, 0, 0, 0, 0, 60, 0xFFFF, 71, 1, 0, 0)
        struct.pack_into(">5IHHBBH", data, 92, 0, 0, 0, 32, 0, 0xFFFF, 4, 1, 0, 0)
        layout = anim.parse_layout(data)
        self.assertEqual(layout["bones"][1]["parent_bone_id"], 71)
        self.assertEqual(layout["bones"][0]["child_index"], 1)
        struct.pack_into(">I", data, 104, 33)
        with self.assertRaises(ValueError):
            anim.parse_layout(data)


if __name__ == "__main__":
    unittest.main()
