"""Native SLD bit-boundary checks; synthetic fixtures only."""
import struct
import unittest
import warnings
import imsld


def blob(size, fields, lookahead=False):
    value = 0
    bits = 0
    for item, width in fields:
        value |= item << bits
        bits += width
    payload = value.to_bytes(((bits + 31) // 32) * 4, "little")
    if lookahead:
        payload += b"\0" * 4
    return b"\x01" + struct.pack("<III", size, 1, 0x11111111) + payload


class StrictSldTests(unittest.TestCase):
    def test_exact_word_boundary_requires_lookahead(self):
        raw = bytes(range(128))
        fields = [(field, width) for byte in raw for field, width in ((0, 1), (byte, 8))]
        short = blob(len(raw), fields)
        with self.assertWarnsRegex(RuntimeWarning, "missing native DWORD"):
            self.assertEqual(imsld.sld_decompress(short), raw)
        with self.assertRaisesRegex(imsld.ImsldError, "lookahead"):
            imsld.sld_decompress(short, strict_native=True)
        self.assertEqual(imsld.sld_decompress(blob(len(raw), fields, True),
                                              strict_native=True), raw)

    def test_partial_dword_is_not_zero_padded_in_strict_mode(self):
        data = blob(1, [(0, 1), (65, 8)])
        self.assertEqual(imsld.sld_decompress(data, strict_native=True), b"A")
        with self.assertWarnsRegex(RuntimeWarning, "partial DWORD"):
            self.assertEqual(imsld.sld_decompress(data[:-2]), b"A")
        with self.assertRaisesRegex(imsld.ImsldError, "lookahead"):
            imsld.sld_decompress(data[:-2], strict_native=True)

    def test_match_must_not_cross_declared_output(self):
        # Literal A, then distance=1 and length=5 with only 2 bytes remaining.
        data = blob(3, [(0, 1), (65, 8), (1, 1), (0, 3), (0, 1), (3, 2), (0, 3)])
        with self.assertWarnsRegex(RuntimeWarning, "truncating match"):
            self.assertEqual(imsld.sld_decompress(data), b"AAA")
        with self.assertRaisesRegex(imsld.ImsldError, "exceeds declared output"):
            imsld.sld_decompress(data, strict_native=True)

    def test_native_mip_lengths_preserve_every_following_level(self):
        data = bytearray(b"AIMRES2.00" + b"\0" * 6 + struct.pack("<I", 17) +
                         b"MIPMCONT" + struct.pack("<2I", 3, 1))
        rasters = []
        for size in (4, 2, 1):
            raw = bytes(range(size * size * 4))
            fields = [(field, width) for byte in raw
                      for field, width in ((0, 1), (byte, 8))]
            block = blob(len(raw), fields, True)
            data += (b"IMSLD32 " + struct.pack("<6I", 2, 1, size, size,
                                             len(block) + 4, len(block)) + block)
            rasters.append(raw)
        with warnings.catch_warnings(record=True) as recovered:
            warnings.simplefilter("always")
            chunks = imsld.parse_aimres2(bytes(data))
            levels = [c for c in chunks if c.tag == "IMSLD32"]
            self.assertEqual(len(levels), 3)
            self.assertEqual([c.fields[2:4] for c in levels], [(4, 4), (2, 2), (1, 1)])
            for level, raw in zip(levels, rasters):
                self.assertEqual(imsld.decode_imsld32_chunk(level.fields, level.payload)[0], raw)
            self.assertEqual(recovered, [], "valid framing must not require recovery")


if __name__ == "__main__":
    unittest.main()
