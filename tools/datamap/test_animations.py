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
                                            (0x4F, "f", 1.5, 1.5 / 32768)]:
            self.assertEqual(anim.Reader(struct.pack(">" + fmt, value)).numbers(0, 1, quant), [expected])

    def test_track_quantized_scale_quaternion(self):
        # s16/256 is deliberately different from the fixed rotation-quat s16/16384.
        reader = anim.Reader(struct.pack(">7h", 256, 512, 768, 256, -256, 128, 64))
        channels = anim._settings(reader, 0, 0x38, 0x22)
        self.assertEqual(channels["scale"], [1, 2, 3])
        self.assertEqual(channels["scale_quaternion"], [1, -1, 0.5, 0.25])
        # A one-byte scale quaternion must advance by four bytes before rotation/translation.
        compact = anim.Reader(struct.pack(">7b4h3b", 2, 4, 6, 2, -2, 1, 0,
                                          0, 0, 0, 16384, 2, 4, 6))
        compact_channels = anim._settings(compact, 0, 0x11, 0x2B)
        self.assertEqual(compact_channels["scale_quaternion"], [1, -1, 0.5, 0])
        self.assertEqual(compact_channels["quaternion"], [0, 0, 0, 1])
        self.assertEqual(compact_channels["translation"], [1, 2, 3])

    def test_all_auxiliary_layouts_and_order(self):
        # Scale Hermite, quaternion eased Squad, translation Hermite, in that order.
        values = (16384, 0, -16384, 4096, 8192, 12288, 1024, 2048,
                  0, 0, 0, 16384, 8192, 0, 0, 8192, 3072, 4096,
                  256, 512, 768, -256, -512, -768, 5120, 6144)
        data = b"ABCD" + struct.pack(">" + "h" * len(values), *values)
        aux = anim._auxiliary(anim.Reader(data), 4, 0x3E, 0x2B, 0x5F)
        self.assertEqual(list(aux), ["scale", "quaternion", "translation"])
        self.assertEqual(aux["scale"]["in"], [1, 0, -1])
        self.assertEqual(aux["scale"]["out"], [0.25, 0.5, 0.75])
        self.assertEqual(aux["scale"]["ease"], [0.0625, 0.125])
        self.assertEqual(aux["quaternion"]["in"], [0, 0, 0, 1])
        self.assertEqual(aux["quaternion"]["out"], [0.5, 0, 0, 0.5])
        self.assertEqual(aux["quaternion"]["ease"], [0.1875, 0.25])
        self.assertEqual(aux["translation"]["in"], [0.015625, 0.03125, 0.046875])
        self.assertEqual(aux["translation"]["out"], [-0.015625, -0.03125, -0.046875])
        self.assertEqual(aux["translation"]["ease"], [0.3125, 0.375])

        # Mode-2 Bezier offsets use the track's own s8/2 quantization.
        offsets = anim._auxiliary(anim.Reader(b"X" + struct.pack(">18b", *range(18))),
                                      1, 0x11, 0x7, 0x2A)
        self.assertEqual(list(offsets), ["scale", "euler", "translation"])
        self.assertEqual(offsets["scale"]["in"], [0, 0.5, 1])
        self.assertEqual(offsets["euler"]["out"], [4.5, 5, 5.5])
        self.assertEqual(offsets["translation"]["out"], [7.5, 8, 8.5])
        self.assertNotIn("ease", offsets["euler"])

        # Mode-4 quaternion controls are fixed s16/16384 and have no ease pair.
        quat = anim._auxiliary(anim.Reader(b"X" + struct.pack(">8h", *range(8))),
                               1, 0x38, 0x8, 0x40)
        self.assertEqual(quat["quaternion"]["out"], [x / 16384 for x in range(4, 8)])
        self.assertNotIn("ease", quat["quaternion"])

        # Euler mode 3 reserves bytes but is unsupported by retail playback.
        reserved = anim._auxiliary(anim.Reader(b"X" + b"\0" * 16 +
                                               struct.pack(">6h", 1, 2, 3, 4, 5, 6)),
                                   1, 0x3E, 0x5, 0x32)
        self.assertEqual(list(reserved), ["translation"])
        self.assertEqual(reserved["translation"]["in"], [x / 16384 for x in (1, 2, 3)])

    def test_parsed_auxiliary_and_segment_ease_order(self):
        data = bank_fixture()
        data.extend(b"\0" * 32)
        data[50] = 0x63  # quaternion slerp, translation Hermite
        struct.pack_into(">I", data, 60, 112)
        struct.pack_into(">I", data, 72, 128)
        struct.pack_into(">8h", data, 112, 1, 2, 3, 4, 5, 6, 1024, 2048)
        struct.pack_into(">8h", data, 128, 7, 8, 9, 10, 11, 12, 3072, 4096)
        keys = anim.parse_bank(data, keyframes=True)["sequences"][0]["tracks"][0]["keyframes"]
        self.assertEqual(keys[0]["auxiliary"]["translation"]["out"], [1, 1.25, 1.5])
        # Current ease[1] and next ease[0], not the reverse.
        self.assertEqual(anim.segment_ease(keys[0], keys[1], "translation"), (0.125, 0.1875))
        self.assertEqual(keys[0]["interpolation_offset"], 112)

    def test_auxiliary_rejects_truncation_and_missing_pointer(self):
        with self.assertRaises(ValueError):
            anim._auxiliary(anim.Reader(b"X" + b"\0" * 5), 1, 0x3E, 1, 3)
        with self.assertRaises(ValueError):
            anim._auxiliary(anim.Reader(b"X" * 32), 0, 0x3E, 1, 3)

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
