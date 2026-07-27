# -*- coding: utf-8 -*-
"""
TGARES-кодек AIMRES2 (минимальный порт loader/city_editor/aim_codec.py).

Только то, что нужно standalone-.aim редактору: распознать заголовок,
достать BGRA-payload, переписать payload поверх (header+footer сохраняются).
Городская atlas-стitching машинерия оригинала сюда не перенесена.

В отличие от оригинала функции работают с BYTES (файл лежит внутри
сессионного CPR), а не с путями на диске.

Раскладка TGARES (по стоку): заголовок 78 байт, далее w*h*4 байта BGRA8,
опциональный 8-байтовый футер (обычно w,h двумя u32).
"""
from __future__ import print_function

import struct

TILE = 256
TGARES_PAYLOAD_OFF = 78


def parse_header(data):
    """dict {kind, w, h, payload_off, payload_len, footer} или None, если не
    AIMRES2. kind: 'TGARES' | 'IMSLD32' | 'IMSLDXT1' | 'UNKNOWN'."""
    if not data.startswith(b"AIMRES2"):
        return None
    info = {"kind": None, "w": TILE, "h": TILE,
            "payload_off": -1, "payload_len": 0, "footer": 0,
            "raw_size": len(data)}
    if b"TGARES" in data[:80]:
        i = data.find(b"TGARES")
        w = struct.unpack_from("<I", data, i + 8)[0]
        h = struct.unpack_from("<I", data, i + 12)[0]
        if w == 0 or w > 4096:
            w = TILE
        if h == 0 or h > 4096:
            h = TILE
        need = w * h * 4
        if len(data) >= TGARES_PAYLOAD_OFF + need:
            off = TGARES_PAYLOAD_OFF
            footer = len(data) - off - need
            if footer < 0 or footer > 16:
                # trailing-payload fallback (старые файлы со смещением 86)
                off = len(data) - need
                footer = 0
        else:
            off = max(0, len(data) - need)
            footer = 0
        info.update({"kind": "TGARES", "w": w, "h": h,
                     "payload_off": off, "payload_len": need,
                     "footer": footer})
        return info
    if b"IMSLD32" in data[:100]:
        i = data.find(b"IMSLD32")
        try:
            w = struct.unpack_from("<I", data, i + 16)[0]
            h = struct.unpack_from("<I", data, i + 20)[0]
        except Exception:
            w, h = TILE, TILE
        info.update({"kind": "IMSLD32", "w": w, "h": h})
        return info
    if b"IMSLDXT1" in data[:100]:
        info["kind"] = "IMSLDXT1"
        return info
    info["kind"] = "UNKNOWN"
    return info


def decode_bgra(data):
    """AIMRES2/TGARES bytes → (bgra, (w, h)) или (None, None)."""
    hdr = parse_header(data)
    if not hdr or hdr["kind"] != "TGARES":
        return None, None
    off, plen = hdr["payload_off"], hdr["payload_len"]
    if off < 0 or plen <= 0 or off + plen > len(data):
        return None, None
    return data[off:off + plen], (hdr["w"], hdr["h"])


def write_bgra_payload(data, bgra):
    """Переписать TGARES BGRA-payload поверх (header+footer сохраняются).
    Возвращает новые bytes; None, если раскладка/размер не сошлись."""
    hdr = parse_header(data)
    if not hdr or hdr["kind"] != "TGARES":
        return None
    off, plen = hdr["payload_off"], hdr["payload_len"]
    if off < 0 or len(bgra) != plen or off + plen > len(data):
        return None
    out = bytearray(data)
    out[off:off + plen] = bgra
    return bytes(out)


def bgra_to_rgba(bgra):
    out = bytearray(len(bgra))
    for i in range(0, len(bgra), 4):
        out[i] = bgra[i + 2]
        out[i + 1] = bgra[i + 1]
        out[i + 2] = bgra[i]
        out[i + 3] = bgra[i + 3]
    return bytes(out)


def rgba_to_bgra(rgba):
    out = bytearray(len(rgba))
    for i in range(0, len(rgba), 4):
        out[i] = rgba[i + 2]
        out[i + 1] = rgba[i + 1]
        out[i + 2] = rgba[i]
        out[i + 3] = rgba[i + 3]
    return bytes(out)
