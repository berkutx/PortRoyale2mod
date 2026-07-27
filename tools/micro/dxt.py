# -*- coding: utf-8 -*-
"""
DXT1 / DXT3 / DXT5 block decompress → BGRA8 (no external deps).

Used by IMSLDXT* (SLD → DXT) and IMDXT* (raw DXT) AIM chunks.
"""
from __future__ import print_function

import struct


def dxt_block_bytes(fmt):
    """Bytes per 4×4 block."""
    f = fmt.upper()
    if f in ("DXT1", "BC1"):
        return 8
    if f in ("DXT3", "DXT5", "BC2", "BC3"):
        return 16
    raise ValueError("unknown DXT format %s" % fmt)


def dxt_image_size(w, h, fmt):
    bw = max(1, (w + 3) // 4)
    bh = max(1, (h + 3) // 4)
    return bw * bh * dxt_block_bytes(fmt)


def _rgb565(c):
    r = ((c >> 11) & 0x1F) * 255 // 31
    g = ((c >> 5) & 0x3F) * 255 // 63
    b = (c & 0x1F) * 255 // 31
    return r, g, b


def _lerp(a, b, na, nb, d):
    return (a * na + b * nb) // d


def _decode_color_block(block8, opaque_only=False):
    """Return 16 (r,g,b,a) for a DXT1/color half (8 bytes)."""
    c0, c1 = struct.unpack_from("<HH", block8, 0)
    bits = struct.unpack_from("<I", block8, 4)[0]
    r0, g0, b0 = _rgb565(c0)
    r1, g1, b1 = _rgb565(c1)
    colors = [(r0, g0, b0, 255), (r1, g1, b1, 255)]
    if c0 > c1 or opaque_only:
        colors.append(
            (
                _lerp(r0, r1, 2, 1, 3),
                _lerp(g0, g1, 2, 1, 3),
                _lerp(b0, b1, 2, 1, 3),
                255,
            )
        )
        colors.append(
            (
                _lerp(r0, r1, 1, 2, 3),
                _lerp(g0, g1, 1, 2, 3),
                _lerp(b0, b1, 1, 2, 3),
                255,
            )
        )
    else:
        colors.append(
            (
                _lerp(r0, r1, 1, 1, 2),
                _lerp(g0, g1, 1, 1, 2),
                _lerp(b0, b1, 1, 1, 2),
                255,
            )
        )
        colors.append((0, 0, 0, 0))
    out = []
    for i in range(16):
        idx = (bits >> (2 * i)) & 3
        out.append(colors[idx])
    return out


def _decode_dxt3_alpha(block8):
    """16 alpha bytes from DXT3 explicit alpha (8 bytes)."""
    alphas = []
    for i in range(8):
        b = block8[i]
        a0 = (b & 0xF) * 17
        a1 = (b >> 4) * 17
        alphas.append(a0)
        alphas.append(a1)
    return alphas


def _decode_dxt5_alpha(block8):
    """16 alpha bytes from DXT5 interpolated alpha (8 bytes)."""
    a0 = block8[0]
    a1 = block8[1]
    # 48-bit lookup table, 3 bits per pixel, little-endian across 6 bytes
    bits = 0
    for i in range(6):
        bits |= block8[2 + i] << (8 * i)
    palette = [a0, a1]
    if a0 > a1:
        for i in range(1, 7):
            palette.append(((7 - i) * a0 + i * a1) // 7)
    else:
        for i in range(1, 5):
            palette.append(((5 - i) * a0 + i * a1) // 5)
        palette.append(0)
        palette.append(255)
    out = []
    for i in range(16):
        idx = (bits >> (3 * i)) & 7
        out.append(palette[idx] & 0xFF)
    return out


def decode_dxt_to_bgra(data, w, h, fmt):
    """
    Decompress DXT buffer to BGRA bytes (w*h*4).
    fmt: 'DXT1' | 'DXT3' | 'DXT5'
    """
    fmt = fmt.upper()
    if fmt.startswith("BC1"):
        fmt = "DXT1"
    elif fmt.startswith("BC2"):
        fmt = "DXT3"
    elif fmt.startswith("BC3"):
        fmt = "DXT5"
    need = dxt_image_size(w, h, fmt)
    if len(data) < need:
        raise ValueError("DXT buffer short: have %s want %s" % (len(data), need))

    bgra = bytearray(w * h * 4)
    bw = max(1, (w + 3) // 4)
    bh = max(1, (h + 3) // 4)
    bsz = dxt_block_bytes(fmt)
    off = 0
    for by in range(bh):
        for bx in range(bw):
            block = data[off : off + bsz]
            off += bsz
            if fmt == "DXT1":
                colors = _decode_color_block(block, opaque_only=False)
                alphas = [c[3] for c in colors]
            elif fmt == "DXT3":
                alphas = _decode_dxt3_alpha(block[:8])
                colors = _decode_color_block(block[8:16], opaque_only=True)
            else:  # DXT5
                alphas = _decode_dxt5_alpha(block[:8])
                colors = _decode_color_block(block[8:16], opaque_only=True)
            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x >= w or y >= h:
                        continue
                    i = py * 4 + px
                    r, g, b, _a = colors[i]
                    a = alphas[i]
                    o = (y * w + x) * 4
                    bgra[o] = b
                    bgra[o + 1] = g
                    bgra[o + 2] = r
                    bgra[o + 3] = a
    return bytes(bgra)
