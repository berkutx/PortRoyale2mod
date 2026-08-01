# -*- coding: utf-8 -*-
"""Regression checks for exact AIM on-disk layout identification."""
from __future__ import print_function

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import aim_viewer


def fixture(cls, outer, child):
    header = b"AIMRES2.00" + b"\x00" * 6 + struct.pack("<I", cls)
    if outer == b"TILEDIM ":
        return header + outer + struct.pack("<III", 8, 1, 0) + child
    if outer == b"MIPMCONT":
        return header + outer + struct.pack("<II", 1, 0) + child
    return header + outer + child


def main():
    nation = fixture(18, b"TILEDIM ", b"IMSLDXT1")
    mipm = fixture(17, b"MIPMCONT", b"IMSLD32 ")
    atlas = fixture(18, b"TILEDIM ", b"IMHC4444")
    assert aim_viewer.aim_layout_signature(nation) == (
        "AIMRES2/class18/TILEDIM/IMSLDXT1"
    )
    assert aim_viewer.aim_layout_signature(mipm) == (
        "AIMRES2/class17/MIPMCONT/IMSLD32"
    )
    assert aim_viewer.aim_layout_signature(atlas) == (
        "AIMRES2/class18/TILEDIM/IMHC4444"
    )
    assert aim_viewer.aim_layout_signature(b"BMPRES  " + b"\x00" * 24) == "BMPRES"
    assert aim_viewer.aim_layout_signature(b"bad") == "unknown"
    print("ALL OK: AIM layout signatures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
