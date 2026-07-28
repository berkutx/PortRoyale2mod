# -*- coding: utf-8 -*-
"""
Pure-python TILEDIM atlas-page decoder (stdlib only).

Port of the decode side of loader/ship_lab/texpage_tool.py (parse_atlas /
_decode_tile) with the tolerant grid logic of loader/city_editor/aim_api.py
(_tiledim_parse / _tiledim_pil):

  * first tile tag strictly at "TILEDIM " + 20 (else None — DXT single-image
    TILEDIMs like the nation flags are NOT pages and must fall through)
  * tile tags: IMHC4444 / IMHC1555 / IMHC565 (u16 formats),
    BMPRES  (embedded BMP, 24-bit with black colour key or 32-bit BGRA),
    IMSLD32 native five-field (2,2,w,h,size) raw-SLD BGRA,
    MIPMCONT-wrapped IMSLD32 six-field, (0,2)=B/G/R/A planes, (2,1)=BGRA
  * page W,H from the IHHW trailer (rfind(b"IHHW")-12), grid
    ceil(W/tw) x ceil(H/th), tiles pasted COLUMN-MAJOR, clipped to W x H
  * all tiles must share the same tw x th (same restriction as the editor)

Pixel math mirrors texpage_tool._decode_tile exactly (cross-validated
byte-for-byte against its PIL decode in test_tiledim.py).
"""
from __future__ import print_function

import math
import struct

import imsld

TILE_TAGS = ((b"IMHC4444", "IMHC4444"), (b"IMHC1555", "IMHC1555"),
             (b"IMHC565 ", "IMHC565"), (b"BMPRES  ", "BMPRES"),
             (b"IMSLD32 ", "IMSLD32N"))

# Tags that may start a page (used by the caller for a cheap head check).
PAGE_FIRST_TAGS = (b"IMHC4444", b"IMHC1555", b"IMHC565 ", b"BMPRES  ",
                   b"MIPMCONT")


class TiledimError(ValueError):
    pass


def trailer_dims(data):
    """(W, H) from the IHHW trailer, else None."""
    f = data.rfind(b"IHHW")
    if f >= 12:
        try:
            w, h = struct.unpack_from("<2I", data, f - 12)
            if 0 < w <= 8192 and 0 < h <= 8192:
                return w, h
        except struct.error:
            pass
    return None


def parse_page(data):
    """Tolerant TILEDIM page parse.

    Returns dict(tiles=[(kind, payload_off, pitch, tw, th, size)], w, h,
    cols, rows, tw, th) or None when this is not a decodable page container.
    """
    if not data.startswith(b"AIMRES2"):
        return None
    off = data.find(b"TILEDIM ")
    if off < 0 or off + 20 > len(data):
        return None
    _hdr, count, _tz = struct.unpack_from("<3I", data, off + 8)
    n = len(data)
    tiles = []
    i = off + 20
    while i + 8 <= n and (not count or len(tiles) < count):
        tag = data[i:i + 8]
        kind = None
        for t, k in TILE_TAGS:
            if tag == t:
                kind = k
                break
        if kind is None and tag != b"MIPMCONT":
            break  # strict sequence: first unknown tag ends the tile list
        if kind == "BMPRES":
            if i + 20 > n:
                break
            w, h, size = struct.unpack_from("<3I", data, i + 8)
            po = i + 20
            if po + size > n or size < 54:
                break
            bpp = struct.unpack_from("<H", data, po + 28)[0]
            tiles.append(("BMP32" if bpp == 32 else "BMPRES", po, 0, w, h, size))
            i = po + size
        elif kind == "IMSLD32N":
            if i + 28 > n:
                break
            sld_mode, sld_variant, tw, th, size = \
                struct.unpack_from("<5I", data, i + 8)
            po = i + 28
            if (sld_mode, sld_variant) != (2, 2) or not tw or not th or \
                    size < 16 or po + size > n:
                break
            tiles.append(("IMSLD32N", po, 5, tw, th, size))
            i = po + size
        elif tag == b"MIPMCONT":
            if i + 48 > n:
                break
            mip_count, _one = struct.unpack_from("<2I", data, i + 8)
            if mip_count != 1 or data[i + 16:i + 24] != b"IMSLD32 ":
                break
            sld_mode, sld_variant, tw, th, size, _raw = \
                struct.unpack_from("<6I", data, i + 24)
            po = i + 48
            if (sld_mode, sld_variant) == (0, 2):
                layout = 1
            elif (sld_mode, sld_variant) == (2, 1):
                layout = 4
            else:
                break
            if not tw or not th or size < 13 or po + size > n:
                break
            tiles.append(("MIPMSLD", po, layout, tw, th, size))
            i = po + size
        else:  # IMHC4444 / IMHC1555 / IMHC565
            if i + 24 > n:
                break
            _c0, pitch, _c2, size = struct.unpack_from("<4I", data, i + 8)
            po = i + 24
            if po + size + 8 > n:
                break
            tw, th = struct.unpack_from("<2I", data, po + size)
            if not tw or not th or pitch < tw * 2:
                break
            tiles.append((kind, po, pitch, tw, th, size))
            i = po + size + 8
    if not tiles or (count and len(tiles) != count):
        return None
    tw, th = tiles[0][3], tiles[0][4]
    if not tw or not th or any(t[3] != tw or t[4] != th for t in tiles):
        return None
    dims = trailer_dims(data)
    if dims:
        W, H = dims
        cols = (W + tw - 1) // tw
        rows = (H + th - 1) // th
    else:
        cols = int(math.ceil(math.sqrt(len(tiles))))
        rows = int(math.ceil(float(len(tiles)) / cols))
        W, H = cols * tw, rows * th
    if cols * rows != len(tiles):
        return None
    return {"tiles": tiles, "w": W, "h": H,
            "cols": cols, "rows": rows, "tw": tw, "th": th}


def sniff(data):
    """Header-level check for /api/list.

    Returns (kind_string, w, h) when *data* is a decodable TILEDIM page,
    else None.  kind_string lists the distinct tile kinds, e.g.
    "IMHC4444" or "BMPRES+BMP32".
    """
    info = parse_page(data)
    if info is None:
        return None
    kinds = []
    for t in info["tiles"]:
        if t[0] not in kinds:
            kinds.append(t[0])
    return "+".join(kinds), info["w"], info["h"]


# ---------------------------------------------------------------------------
# u16 tile formats — 64K lookup tables (built once, per format)
# ---------------------------------------------------------------------------

_LUTS = {}


def _lut(kind):
    tab = _LUTS.get(kind)
    if tab is not None:
        return tab
    out = []
    ap = out.append
    if kind == "IMHC4444":
        for v in range(65536):
            ap(bytes((((v >> 8) & 0xF) * 17, ((v >> 4) & 0xF) * 17,
                      (v & 0xF) * 17, ((v >> 12) & 0xF) * 17)))
    elif kind == "IMHC1555":
        for v in range(65536):
            ap(bytes((((v >> 10) & 0x1F) << 3, ((v >> 5) & 0x1F) << 3,
                      (v & 0x1F) << 3, 255 if (v & 0x8000) else 0)))
    else:  # IMHC565
        for v in range(65536):
            ap(bytes((((v >> 11) & 0x1F) << 3, ((v >> 5) & 0x3F) << 2,
                      (v & 0x1F) << 3, 255)))
    _LUTS[kind] = out
    return out


def _decode_imhc(data, po, pitch, tw, th, size, kind):
    tab = _lut(kind)
    row_b = tw * 2
    if pitch == row_b and size >= row_b * th:
        pix = struct.unpack_from("<%dH" % (tw * th), data, po)
        return b"".join(tab[v] for v in pix)
    # padded rows: decode row by row
    rows = []
    for y in range(th):
        pix = struct.unpack_from("<%dH" % tw, data, po + y * pitch)
        rows.append(b"".join(tab[v] for v in pix))
    return b"".join(rows)


def _decode_bmp(data, po, tw, th, size):
    """Embedded BMP tile -> RGBA.  24-bit: black colour key (sum==0 -> a=0);
    32-bit: straight BGRA.  Bottom-up rows (mirrors texpage_tool)."""
    data_off = struct.unpack_from("<I", data, po + 10)[0]
    bpp = struct.unpack_from("<H", data, po + 28)[0]
    if bpp not in (24, 32):
        raise TiledimError("BMP tile bpp=%d" % bpp)
    bytes_pp = bpp // 8
    row_b = (tw * bytes_pp + 3) & ~3
    start = po + data_off
    if start + row_b * th > po + size:
        raise TiledimError("BMP tile pitch/size mismatch")
    out = bytearray(tw * th * 4)
    for y in range(th):
        ro = start + (th - 1 - y) * row_b
        row = data[ro:ro + tw * bytes_pp]
        o = y * tw * 4
        if bpp == 32:
            out[o:o + tw * 4:4] = row[2::4]
            out[o + 1:o + tw * 4:4] = row[1::4]
            out[o + 2:o + tw * 4:4] = row[0::4]
            out[o + 3:o + tw * 4:4] = row[3::4]
        else:
            b = row[0::3]
            g = row[1::3]
            r = row[2::3]
            out[o:o + tw * 4:4] = r
            out[o + 1:o + tw * 4:4] = g
            out[o + 2:o + tw * 4:4] = b
            out[o + 3:o + tw * 4:4] = bytes(
                0 if (bb | gg | rr) == 0 else 255
                for bb, gg, rr in zip(b, g, r))
    return bytes(out)


def _imsld32_raw(data, po, size, tw, th):
    """Raw-SLD multi-block stream (first block bare, rest length-prefixed;
    blocks must be uncompressed: flags 0x80000000).  Port of
    texpage_tool._imsld32_raw."""
    end = po + size
    need = tw * th * 4
    if size < 13 or end > len(data):
        raise TiledimError("invalid raw IMSLD32 tile")
    raw = bytearray()
    pos = po
    first = True
    while len(raw) < need:
        is_first = first
        if is_first:
            blob_at = pos
            first = False
        else:
            if pos + 4 > end:
                raise TiledimError("truncated raw IMSLD32 block length")
            blob_len = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            blob_at = pos
            if blob_len < 13 or blob_at + blob_len > end:
                raise TiledimError("invalid raw IMSLD32 block length")
        if blob_at + 13 > end or data[blob_at] != 1:
            raise TiledimError("IMSLD32 tile is compressed (raw SLD only)")
        raw_len, flags, table = struct.unpack_from("<3I", data, blob_at + 1)
        body_at = blob_at + 13
        actual_len = 13 + raw_len
        if (raw_len == 0 or flags != 0x80000000 or table != 0 or
                body_at + raw_len > end):
            raise TiledimError("invalid raw IMSLD32 tile")
        if not is_first and blob_len != actual_len:
            raise TiledimError("raw IMSLD32 block length mismatch")
        raw += data[body_at:body_at + raw_len]
        pos = blob_at + actual_len
    if len(raw) != need:
        raise TiledimError("raw IMSLD32 size mismatch")
    return bytes(raw)


def _bgra_to_rgba(raw):
    out = bytearray(len(raw))
    out[0::4] = raw[2::4]
    out[1::4] = raw[1::4]
    out[2::4] = raw[0::4]
    out[3::4] = raw[3::4]
    return bytes(out)


def _decode_imsld(data, po, layout, tw, th, size, kind):
    """IMSLD32N (native five-field, SLD stream -> BGRA) and MIPMSLD
    (MIPMCONT-wrapped six-field: layout 1 = B/G/R/A planes, 4 = BGRA)."""
    if kind == "IMSLD32N":
        try:
            raw = imsld.sld_decompress_stream(data[po:po + size],
                                              expected=tw * th * 4)
        except Exception as exc:
            raise TiledimError("cannot decode native IMSLD32 tile: %s" % exc)
        if len(raw) != tw * th * 4:
            raise TiledimError("native IMSLD32 size mismatch")
        return _bgra_to_rgba(raw)
    raw = _imsld32_raw(data, po, size, tw, th)
    if layout == 4:
        return _bgra_to_rgba(raw)
    plane = tw * th
    out = bytearray(plane * 4)
    out[0::4] = raw[plane * 2:plane * 3]  # R
    out[1::4] = raw[plane:plane * 2]      # G
    out[2::4] = raw[:plane]               # B
    out[3::4] = raw[plane * 3:]           # A
    return bytes(out)


def decode_tile_rgba(data, tile):
    """One tile -> RGBA bytes (tw*th*4)."""
    kind, po, pitch, tw, th, size = tile
    if kind in ("IMHC4444", "IMHC1555", "IMHC565"):
        return _decode_imhc(data, po, pitch, tw, th, size, kind)
    if kind in ("BMPRES", "BMP32"):
        return _decode_bmp(data, po, tw, th, size)
    return _decode_imsld(data, po, pitch, tw, th, size, kind)


def decode_page_rgba(data):
    """TILEDIM page -> (rgba_bytes, W, H).  Raises TiledimError."""
    info = parse_page(data)
    if info is None:
        raise TiledimError("not a decodable TILEDIM page container")
    tw, th, rows = info["tw"], info["th"], info["rows"]
    gw, gh = info["cols"] * tw, info["rows"] * th
    canvas = bytearray(gw * gh * 4)
    for ti, tile in enumerate(info["tiles"]):
        cx, cy = (ti // rows) * tw, (ti % rows) * th
        tile_rgba = decode_tile_rgba(data, tile)
        for y in range(th):
            src = y * tw * 4
            dst = ((cy + y) * gw + cx) * 4
            canvas[dst:dst + tw * 4] = tile_rgba[src:src + tw * 4]
    W, H = info["w"], info["h"]
    if (W, H) != (gw, gh):  # clip transparent padding to the trailer dims
        out = bytearray(W * H * 4)
        for y in range(H):
            src = y * gw * 4
            out[y * W * 4:(y + 1) * W * 4] = canvas[src:src + W * 4]
        return bytes(out), W, H
    return bytes(canvas), W, H
