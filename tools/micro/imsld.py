# -*- coding: utf-8 -*-
"""
AIMRES2 + IMSLD32 reader (Ascaron AIM20).

SLDCOMP bitstream reversed from AIM20.dll:
  - encoder  sub_10084000 / bit writer sub_1008484C (LSB-first dwords)
  - decoder  sub_1008491C  (called from sub_10010700)
  - multi-block SLDBCOMP walk: sub_10010170  ([u32 clen][SLDCOMP]*)

IMSLD32 on-disk (after 8-char tag)::

    u32 unk0, unk1, width, height, size_a, size_b
    u8  payload[size_a]   # often multi-block SLDCOMP

Payload starts with SLDCOMP (byte 0x01 + header). Further blocks are
length-prefixed. Decompressed size in the SLD header is either:

  * w*h*4  → packed BGRA
  * w*h    → one 8-bit plane (multiple IMSLD32 chunks → B,G,R,A)

MIP chains: take the largest (first) level. TGARES still via aim_codec.
"""
from __future__ import print_function

import struct
import zlib

from collections import namedtuple

AimChunk = namedtuple("AimChunk", "tag fields payload")
ImsldImage = namedtuple("ImsldImage", "w h bgra kind")

# (1<<n)-1 length masks used by AIM20 dword_100802F8[n]
_LEN_MASK = [
    0,
    1,
    3,
    7,
    0xF,
    0x1F,
    0x3F,
    0x7F,
    0xFF,
    0x1FF,
    0x3FF,
    0x7FF,
    0xFFF,
    0x1FFF,
    0x3FFF,
    0x7FFF,
    0xFFFF,
]


class ImsldError(ValueError):
    pass


class _BitReader(object):
    """LSB-first bit pack matching AIM20 sub_1008484C / sub_1008491C."""

    def __init__(self, data, off=0):
        self.data = data
        self.off = off
        self.cur = 0
        self.bits_left = 0
        self._fill()

    def _fill(self):
        if self.off + 4 <= len(self.data):
            self.cur = struct.unpack_from("<I", self.data, self.off)[0]
            self.off += 4
            self.bits_left = 32
            return
        rest = self.data[self.off :]
        self.off = len(self.data)
        if not rest:
            self.cur = 0
            self.bits_left = 0
            return
        self.cur = int.from_bytes(rest.ljust(4, b"\x00"), "little")
        self.bits_left = 8 * len(rest)

    def read(self, nbits):
        if nbits <= 0:
            return 0
        result = 0
        got = 0
        while got < nbits:
            if self.bits_left <= 0:
                self._fill()
                if self.bits_left <= 0:
                    raise ImsldError("bitstream exhausted")
            take = nbits - got
            if take > self.bits_left:
                take = self.bits_left
            mask = (1 << take) - 1
            result |= (self.cur & mask) << got
            self.cur >>= take
            self.bits_left -= take
            got += take
        return result


def parse_aimres2(data):
    """Return list of AimChunk from AIMRES2 file."""
    if not data.startswith(b"AIMRES2"):
        raise ImsldError("not AIMRES2")
    off = 16
    if len(data) >= 28 and data[20:28] in (
        b"MIPMCONT",
        b"TILEDIM ",
        b"IMSLD32 ",
        b"IMSLDXT1",
        b"IMSLDXT3",
        b"IMSLDXT5",
        b"IMDXT1  ",
        b"IMDXT3  ",
        b"IMDXT5  ",
        b"TGARES  ",
    ):
        off = 20
    chunks = []
    n = len(data)
    while off + 8 <= n:
        tag = data[off : off + 8]
        if tag == b"\x00" * 8:
            off += 8
            continue
        if not all(c == 0 or 32 <= c < 127 for c in tag):
            break
        tag_s = tag.decode("latin-1")
        off += 8
        if tag_s.startswith("MIPMCONT"):
            if off + 8 > n:
                break
            fields = struct.unpack_from("<2I", data, off)
            off += 8
            chunks.append(AimChunk(tag_s.strip(), fields, b""))
            continue
        if tag_s.startswith("TILEDIM"):
            if off + 12 > n:
                break
            fields = struct.unpack_from("<3I", data, off)
            off += 12
            chunks.append(AimChunk(tag_s.strip(), fields, b""))
            continue
        if tag_s.startswith("IMSLD32") or tag_s.startswith("IMSLD8"):
            # 6×u32 header; payload length = size_a (fields[4])
            if off + 24 > n:
                break
            fields = struct.unpack_from("<6I", data, off)
            off += 24
            size_a = fields[4]
            if size_a > n - off:
                size_a = n - off
            payload = data[off : off + size_a]
            off += size_a
            chunks.append(AimChunk(tag_s.strip(), fields, payload))
            continue
        if tag_s.startswith("IMSLDXT"):
            # w, h, size_a, size_b then size_a payload (SLD-compressed DXT)
            if off + 16 > n:
                break
            fields = struct.unpack_from("<4I", data, off)
            off += 16
            size_a = fields[2]
            if size_a > n - off:
                size_a = n - off
            payload = data[off : off + size_a]
            off += size_a
            chunks.append(AimChunk(tag_s.strip(), fields, payload))
            continue
        if tag_s.startswith("IMDXT"):
            # RAW DXT: header is only 3×u32 (w, h, size) — a 4th dword is
            # already DXT payload (verified on Hauptmenu12/PR2Ships textures:
            # reading 4 dwords shifts every block and scrambles the image).
            if off + 12 > n:
                break
            fields = struct.unpack_from("<3I", data, off)
            off += 12
            size_a = fields[2]
            if size_a > n - off:
                size_a = n - off
            payload = data[off : off + size_a]
            off += size_a
            chunks.append(AimChunk(tag_s.strip(), fields, payload))
            continue
        if tag_s.startswith("TGARES"):
            chunks.append(AimChunk(tag_s.strip(), (), data[off:]))
            break
        break
    return chunks


def sld_decompress(blob):
    """
    Decompress one SLDCOMP blob (optional leading 0x01) → raw bytes.
    Layout (after optional 0x01)::

        u32 size, u32 flags, u32 dist_table_nibbles, then bitstream dwords
    """
    if not blob:
        raise ImsldError("empty SLD blob")
    if blob[0] == 1 and len(blob) > 13:
        size_try = struct.unpack_from("<I", blob, 1)[0]
        if 0 < size_try <= 16 * 1024 * 1024:
            blob = blob[1:]
    if len(blob) < 12:
        raise ImsldError("SLD header too short")
    size, flags, table = struct.unpack_from("<III", blob, 0)
    if size == 0 or size > 64 * 1024 * 1024:
        raise ImsldError("bad SLD size %s" % size)

    # High bit set → raw or XOR path (encoder when raw < 32 uses 0xC0000000)
    if flags & 0x80000000:
        body = blob[12 : 12 + size]
        if len(body) < size:
            raise ImsldError("XOR/raw body short")
        if flags & 0x40000000:
            return bytes(b ^ 0x35 for b in body)
        return body

    # Distance bit-width schedule: 8 nibbles are *deltas* of widths
    # (encoder packs via ROR of successive differences); accumulate to widths.
    bits = []
    masks = []
    bases = []
    cumul = 0
    t = table
    for i in range(8):
        nib = t & 0xF
        t >>= 4
        cumul += nib
        if cumul <= 0 or cumul > 28:
            # corrupt / wrong framing
            raise ImsldError("bad dist width cumul=%s" % cumul)
        bits.append(cumul)
        masks.append((1 << cumul) - 1)
        if i == 0:
            bases.append(0)
        else:
            bases.append(bases[i - 1] + masks[i - 1] + 1)

    br = _BitReader(blob, 12)
    out = bytearray()
    remaining = size
    while remaining > 0:
        if br.read(1) == 0:
            out.append(br.read(8) & 0xFF)
            remaining -= 1
            continue
        level = br.read(3) & 7
        extra = br.read(bits[level])
        dist = bases[level] + (extra & masks[level]) + 1
        length = 2
        n = 1
        while True:
            n += 1
            if n >= len(_LEN_MASK):
                break
            v = br.read(n)
            length += v
            if v != _LEN_MASK[n]:
                break
        if dist <= 0 or dist > len(out):
            raise ImsldError("bad match dist=%s out=%s" % (dist, len(out)))
        for _ in range(length):
            if remaining <= 0:
                break
            out.append(out[-dist])
            remaining -= 1
    return bytes(out)


def _looks_like_sld(data, off):
    """True if data[off] looks like SLDCOMP (0x01 + size + flags)."""
    if off + 13 > len(data) or data[off] != 1:
        return False
    size, flags = struct.unpack_from("<II", data, off + 1)
    # size is decompressed length; flags: low values / 0x100 = LZ, high bit = raw/XOR
    if not (0 < size <= 16 * 1024 * 1024):
        return False
    if flags & 0x80000000:
        return True
    # LZ path: observed 1 and 0x100 (and small positives)
    if flags <= 0x10000:
        return True
    return False


def _find_next_len_prefixed_sld(payload, start):
    """
    Scan for SLDBCOMP-style ``u32 clen`` + SLDCOMP after *start*.
    Returns byte offset of the length prefix, or None.
    """
    n = len(payload)
    j = start
    while j + 16 < n:
        clen = struct.unpack_from("<I", payload, j)[0]
        if 12 <= clen <= n - j - 4 and _looks_like_sld(payload, j + 4):
            return j
        j += 1
    return None


def sld_decompress_stream(payload, expected=None):
    """
    Decompress SLDCOMP / SLDBCOMP payload to raw bytes.

    First block is bare SLDCOMP (leading 0x01). Further blocks are
    ``u32 compressed_len`` + SLDCOMP, possibly with zero padding between.
    Never scans inside a compressed bitstream for a bare 0x01 (false positives).
    """
    if not payload:
        raise ImsldError("empty payload")

    out = bytearray()
    pos = 0
    n = len(payload)

    while pos < n - 12 and (expected is None or len(out) < expected):
        while pos < n and payload[pos] == 0:
            pos += 1
        if pos >= n - 12:
            break

        # Length-prefixed block (SLDBCOMP tail)
        if pos + 16 <= n:
            clen = struct.unpack_from("<I", payload, pos)[0]
            if (
                12 <= clen <= n - pos - 4
                and _looks_like_sld(payload, pos + 4)
                and not _looks_like_sld(payload, pos)
            ):
                try:
                    raw = sld_decompress(payload[pos + 4 : pos + 4 + clen])
                except ImsldError:
                    pos += 1
                    continue
                out += raw
                pos += 4 + clen
                continue

        if not _looks_like_sld(payload, pos):
            pos += 1
            continue

        # Bare SLDCOMP head: slice ends at next length-prefixed block if any
        next_len_at = _find_next_len_prefixed_sld(payload, pos + 16)
        if next_len_at is not None:
            chunk = payload[pos:next_len_at]
        else:
            chunk = payload[pos:]

        try:
            raw = sld_decompress(chunk)
        except ImsldError:
            try:
                raw = sld_decompress(payload[pos:])
                out += raw
                break
            except ImsldError:
                pos += 1
                continue
        out += raw
        if next_len_at is None:
            break
        pos = next_len_at

    if not out:
        raise ImsldError("no SLD blocks decoded")
    if expected is not None and len(out) > expected:
        return bytes(out[:expected])
    return bytes(out)


def decode_imsld32_chunk(fields, payload, expect_planes=False):
    """
    fields: 6×u32 (unk0, unk1, w, h, size_a, size_b)
    Returns (raw_bytes, (w, h), layout) where layout is 'bgra' or 'plane'.
    """
    w, h = fields[2], fields[3]
    if w <= 0 or h <= 0 or w > 4096 or h > 4096:
        raise ImsldError("bad dimensions %sx%s" % (w, h))
    need_bgra = w * h * 4
    need_plane = w * h

    raw = sld_decompress_stream(payload, expected=need_bgra)
    if len(raw) >= need_bgra:
        return raw[:need_bgra], (w, h), "bgra"
    if len(raw) >= need_plane:
        return raw[:need_plane], (w, h), "plane"
    raise ImsldError(
        "IMSLD32 %dx%d got %d bytes (want %d or %d)"
        % (w, h, len(raw), need_bgra, need_plane)
    )


def _planes_to_bgra(planes, w, h):
    """Interleave up to 4 planes as B,G,R,A (missing → 0 / 255 for alpha)."""
    n = w * h
    bgra = bytearray(n * 4)
    for i in range(n):
        b = planes[0][i] if len(planes) > 0 and i < len(planes[0]) else 0
        g = planes[1][i] if len(planes) > 1 and i < len(planes[1]) else 0
        r = planes[2][i] if len(planes) > 2 and i < len(planes[2]) else 0
        a = planes[3][i] if len(planes) > 3 and i < len(planes[3]) else 255
        o = i * 4
        bgra[o] = b
        bgra[o + 1] = g
        bgra[o + 2] = r
        bgra[o + 3] = a
    return bytes(bgra)


def _dxt_fmt_from_tag(tag):
    t = tag.upper().replace(" ", "")
    if "DXT1" in t or t.endswith("XT1"):
        return "DXT1"
    if "DXT3" in t:
        return "DXT3"
    if "DXT5" in t:
        return "DXT5"
    return None


def decode_imsldxt_chunk(tag, fields, payload):
    """
    IMSLDXT* / IMDXT* chunk → BGRA.

    Header is 4×u32: w, h, size_a, size_b (size_a = payload length).
    IMDXT*: payload is raw DXT blocks.
    IMSLDXT*: payload is SLDCOMP stream expanding to DXT blocks.
    """
    from dxt import decode_dxt_to_bgra, dxt_image_size

    if len(fields) < 2:
        raise ImsldError("DXT chunk missing dimensions")
    w, h = fields[0], fields[1]
    if w <= 0 or h <= 0 or w > 4096 or h > 4096:
        raise ImsldError("bad DXT dimensions %sx%s" % (w, h))
    fmt = _dxt_fmt_from_tag(tag)
    if not fmt:
        raise ImsldError("unknown DXT tag %s" % tag)
    need = dxt_image_size(w, h, fmt)
    tag_u = tag.upper().replace(" ", "")
    if tag_u.startswith("IMDXT"):
        # Raw DXT blocks (no SLD)
        if len(payload) < need:
            raise ImsldError(
                "raw DXT short: have %s want %s (%s %dx%d)"
                % (len(payload), need, fmt, w, h)
            )
        dxt = payload[:need]
    else:
        # IMSLDXT* — SLDCOMP → DXT blocks
        dxt = sld_decompress_stream(payload, expected=need)
        if len(dxt) < need:
            dxt = sld_decompress_stream(payload)
        if len(dxt) < need:
            raise ImsldError(
                "DXT SLD short: got %s want %s (%s %dx%d)" % (len(dxt), need, fmt, w, h)
            )
        dxt = dxt[:need]
    bgra = decode_dxt_to_bgra(dxt, w, h, fmt)
    return bgra, (w, h), tag.strip()


def _import_aim_codec():
    """aim_codec живёт в loader/city_editor — добираемся отовсюду."""
    try:
        import aim_codec
        return aim_codec
    except ImportError:
        pass
    import os
    import sys

    cand = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "city_editor"
    )
    cand = os.path.normpath(cand)
    if os.path.isdir(cand) and cand not in sys.path:
        sys.path.insert(0, cand)
    import aim_codec
    return aim_codec


def decode_aim_file(data):
    """Decode AIMRES2 bytes → ImsldImage (BGRA) or raise ImsldError."""
    if data.startswith(b"AIMRES2") and b"TGARES" in data[:80]:
        try:
            aim_codec = _import_aim_codec()

            off, plen = aim_codec._find_bgra_payload(data)
            if off >= 0:
                hdr = aim_codec.parse_aim_header(data)
                raw = data[off : off + plen]
                return ImsldImage(hdr["w"], hdr["h"], raw, "TGARES")
        except Exception:
            pass

    chunks = parse_aimres2(data)

    # Prefer uncompressed / compressed DXT (common for ship hulls)
    dxt_chunks = [
        c
        for c in chunks
        if c.tag.startswith("IMSLDXT") or c.tag.startswith("IMDXT")
    ]
    if dxt_chunks:
        best = None
        best_area = -1
        for c in dxt_chunks:
            w, h = c.fields[0], c.fields[1]
            area = w * h
            if area > best_area:
                best_area = area
                best = c
        bgra, (w, h), kind = decode_imsldxt_chunk(best.tag, best.fields, best.payload)
        return ImsldImage(w, h, bgra, kind)

    imsld = [c for c in chunks if c.tag.startswith("IMSLD32")]
    if not imsld:
        raise ImsldError("no decodable image chunk (tags=%s)" % [c.tag for c in chunks])

    # Group by (w,h); prefer largest resolution (first mip)
    best_wh = None
    for c in imsld:
        wh = (c.fields[2], c.fields[3])
        if best_wh is None or wh[0] * wh[1] > best_wh[0] * best_wh[1]:
            best_wh = wh
    level = [c for c in imsld if (c.fields[2], c.fields[3]) == best_wh]
    w, h = best_wh

    decoded = []
    layouts = []
    for c in level:
        raw, _wh, layout = decode_imsld32_chunk(c.fields, c.payload)
        decoded.append(raw)
        layouts.append(layout)

    if len(decoded) == 1 and layouts[0] == "bgra":
        return ImsldImage(w, h, decoded[0], "IMSLD32")

    if all(L == "plane" for L in layouts) and len(decoded) >= 1:
        # 1–4 plane images at this resolution
        bgra = _planes_to_bgra(decoded[:4], w, h)
        return ImsldImage(w, h, bgra, "IMSLD32")

    if layouts[0] == "bgra":
        return ImsldImage(w, h, decoded[0][: w * h * 4], "IMSLD32")

    # plane-sized but only one chunk: expand grey+opaque
    plane = decoded[0][: w * h]
    bgra = bytearray(w * h * 4)
    for i, v in enumerate(plane):
        o = i * 4
        bgra[o] = bgra[o + 1] = bgra[o + 2] = v
        bgra[o + 3] = 255
    return ImsldImage(w, h, bytes(bgra), "IMSLD32")


def bgra_to_png(path, bgra, w, h):
    """Write PNG (RGBA) without external deps."""
    rgba = bytearray(len(bgra))
    for i in range(0, len(bgra), 4):
        rgba[i] = bgra[i + 2]
        rgba[i + 1] = bgra[i + 1]
        rgba[i + 2] = bgra[i]
        rgba[i + 3] = bgra[i + 3]

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b""
    row = w * 4
    for y in range(h):
        raw += b"\x00" + bytes(rgba[y * row : (y + 1) * row])
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


def bgra_to_rgba(bgra):
    out = bytearray(len(bgra))
    for i in range(0, len(bgra), 4):
        out[i] = bgra[i + 2]
        out[i + 1] = bgra[i + 1]
        out[i + 2] = bgra[i]
        out[i + 3] = bgra[i + 3]
    return bytes(out)


def load_aim_to_png(aim_path, png_path):
    with open(aim_path, "rb") as f:
        data = f.read()
    img = decode_aim_file(data)
    bgra_to_png(png_path, img.bgra, img.w, img.h)
    return img


def decode_aim_rgba(path_or_bytes):
    """Return (rgba, (w,h), kind) or raise ImsldError."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data = bytes(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as f:
            data = f.read()
    img = decode_aim_file(data)
    return bgra_to_rgba(img.bgra), (img.w, img.h), img.kind
