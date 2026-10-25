"""The C codecs (lzss.c) must give byte-identical output to the Python ones in clone_character.py.
Build first: gcc -O2 -shared -fPIC -o _lzss.so lzss.c   then: python3 test_lzss.py   (MSSB_DISC=<extracted disc> overrides orig/GYQE01)"""
import os
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import clone_character as cc  # noqa: E402

DISC = Path(os.environ.get("MSSB_DISC") or Path(__file__).resolve().parents[2] / "orig" / "GYQE01")


@unittest.skipIf(cc._fast_lzss is None, "C build of lzss.c not found")
class FastLzss(unittest.TestCase):
    def check(self, data):
        fast = cc.lzss_compress_optimal(data)
        self.assertEqual(fast, cc._py_lzss_compress_optimal(data))
        self.assertEqual(cc.lzss_decompress(fast, len(data)), cc._py_lzss_decompress(fast, len(data)))
        self.assertEqual(cc.lzss_decompress(fast, len(data))[0], data)

    def test_synthetic(self):
        rng = random.Random(1)
        for n in (0, 1, 2, 3, 17, 18, 4097):
            self.check(bytes(rng.randrange(4) for _ in range(n)))
        self.check(b"\0" * 70000)
        self.check(bytes(rng.randrange(256) for _ in range(5000)))

    def test_errors(self):
        with self.assertRaises(cc.LZSSError):
            cc.lzss_decompress(b"", 1)
        with self.assertRaises(cc.LZSSError):
            cc.lzss_decompress(b"\0\0\0\0", 1)     # first token is a match with nothing behind it

    @unittest.skipUnless((DISC / "files" / "aaaa.dat").exists(), "no extracted disc")
    def test_game_rel(self):
        disc = cc.Disc(DISC)
        dol = cc.Dol((disc.sys / "main.dol").read_bytes())
        rel = cc.load_game_rel(disc, dol)
        self.check(bytes(rel[:300000]))     # ponytail: a slice keeps the Python reference run to ~5 s


if __name__ == "__main__":
    unittest.main()
