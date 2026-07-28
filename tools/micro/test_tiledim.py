# -*- coding: utf-8 -*-
"""Cross-validation: tiledim_codec (pure python) vs texpage_tool (PIL/numpy).

Reads the test files straight out of pr2_arcd.cpr (read-only, via
cpr_extract.segments), decodes each with both decoders and asserts
byte-identical RGBA output.

Run:  python tools/micro/test_tiledim.py
"""
from __future__ import print_function

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# PIL reference implementation lives in the game tree (test-only import).
SHIP_LAB = r"C:/GOG Games/Port Royale 2/loader/ship_lab"
sys.path.insert(0, SHIP_LAB)

import cpr_extract  # noqa: E402
import tiledim_codec  # noqa: E402
import texpage_tool  # noqa: E402  (PIL-based reference)

CPR = r"C:/GOG Games/Port Royale 2/pr2_arcd.cpr"

# basename -> expected (W, H) or None (assert ours == reference only)
TARGETS = {
    "TexPage_8_2.aim": (1024, 1024),   # IMHC4444
    "TexPage_1_10.aim": None,          # IMHC1555, dims printed for review
    "TexPage_0_11.aim": (1024, 1024),  # BMPRES tiles
}


def read_cpr_entry(want_base):
    with open(CPR, "rb") as f:
        data = f.read()
    for e in cpr_extract.segments(data):
        if os.path.basename(e["name"].replace("\\", "/")).lower() == \
                want_base.lower():
            return data[e["off"]: e["off"] + e["size"]]
    raise SystemExit("entry not found in %s: %s" % (CPR, want_base))


def main():
    n_fail = 0
    for base, expect in sorted(TARGETS.items()):
        blob = read_cpr_entry(base)

        t0 = time.perf_counter()
        rgba, w, h = tiledim_codec.decode_page_rgba(blob)
        dt = time.perf_counter() - t0

        ref_img = texpage_tool.decode_page(blob)
        ref = ref_img.convert("RGBA").tobytes()

        ok_dims = ref_img.size == (w, h)
        ok_bytes = ref == rgba
        ok_expect = expect is None or (w, h) == expect
        status = "OK" if (ok_dims and ok_bytes and ok_expect) else "FAIL"
        if status == "FAIL":
            n_fail += 1
        print("%s %-18s %dx%d (expect %s) ref %dx%d  bytes %s  %.2fs"
              % (status, base, w, h,
                 ("%dx%d" % expect) if expect else "?",
                 ref_img.size[0], ref_img.size[1],
                 "identical" if ok_bytes else "DIFFER", dt))
        if not ok_bytes:
            diff = sum(1 for a, b in zip(ref[:40000], rgba[:40000]) if a != b)
            print("  first-40k-byte diffs: %d" % diff)
    print("PASS" if n_fail == 0 else "FAILURES: %d" % n_fail)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
