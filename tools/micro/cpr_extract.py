#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Micro CPR extractor (ASCARON_ARCHIVE V0.9, multi-segment).

Usage:  python cpr_extract.py <file.cpr> <out_dir>

Writes every entry to <out_dir>/<relative/path>. Unreadable / Windows-illegal
characters in names are replaced with '_'; every replacement, name collision
and read error is printed to console AND appended to <out_dir>/_warnings.log.
Stdlib only, no dependencies.
"""
import os
import struct
import sys

MAGIC = b"ASCARON_ARCHIVE V0.9"
BAD = set('<>:"|?*')  # Windows-illegal; controls/non-ASCII handled below


def segments(data):
    """Yield entry dicts {name, off, size} from every segment (first-wins)."""
    seen = set()
    heads = []
    if data[:20].startswith(MAGIC[:20]):
        heads.append(32)
        nxt = 32 + struct.unpack_from("<I", data, 32)[0] + \
            struct.unpack_from("<I", data, 44)[0]
    else:
        nxt = 0
    while nxt + 16 <= len(data):
        isz, dsz, nf, nrel = struct.unpack_from("<4I", data, nxt)
        ok = isz and not isz & 0x1F and 16 <= dsz <= isz and \
            1 <= nf <= 100000 and nrel > 0 and nxt + isz + nrel <= len(data)
        if not ok:  # scan forward a bit for the next plausible header
            nxt += 16
            continue
        heads.append(nxt)
        nxt = nxt + isz + nrel
    for base in heads:
        isz, dsz, nf, nrel = struct.unpack_from("<4I", data, base)
        pay = data[base + 16: base + dsz] if base else data[48: 48 + dsz - 16]
        off, got = 0, 0
        while off + 13 <= len(pay) and got < nf:
            e_off, e_len, _ = struct.unpack_from("<III", pay, off)
            off += 12
            z = pay.find(b"\x00", off)
            if z < 0:
                break
            name = pay[off:z].decode("latin-1", "replace")
            off = z + 1
            got += 1
            if name not in seen:
                seen.add(name)
                yield {"name": name, "off": e_off, "size": e_len}


def sanitize(name, warns):
    """Make name safe for the filesystem; log every change."""
    out = []
    for ch in name:
        if ch in BAD or ord(ch) < 32 or ord(ch) > 126:
            out.append("_")
        else:
            out.append(ch)
    clean = "".join(out).replace("/", os.sep)
    parts = [p.rstrip(" .") or "_" for p in clean.split(os.sep)]
    clean = os.sep.join(parts)
    if clean != name:
        warns.append("rename: %r -> %r" % (name, clean))
    return clean


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out_dir = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    os.makedirs(out_dir, exist_ok=True)
    warns, n_ok, n_err, total = [], 0, 0, 0
    used = set()
    for e in segments(data):
        rel = sanitize(e["name"], warns)
        base, i = rel, 2
        while rel.lower() in used:  # collision after sanitize
            rel = "%s_%d" % (base, i)
            i += 1
        if rel != base:
            warns.append("collision: %r -> %r" % (e["name"], rel))
        used.add(rel.lower())
        if e["off"] + e["size"] > len(data):
            warns.append("ERROR out of range: %r off=%d size=%d" %
                         (e["name"], e["off"], e["size"]))
            n_err += 1
            continue
        dst = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dst) or out_dir, exist_ok=True)
        try:
            with open(dst, "wb") as f:
                f.write(data[e["off"]: e["off"] + e["size"]])
            n_ok += 1
            total += e["size"]
        except OSError as ex:
            warns.append("ERROR write %r: %s" % (rel, ex))
            n_err += 1
    for w in warns:
        print(w)
    if warns:
        with open(os.path.join(out_dir, "_warnings.log"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(warns) + "\n")
    print("done: %d files, %d bytes, %d renamed/warnings, %d errors -> %s" %
          (n_ok, total, len(warns), n_err, out_dir))


if __name__ == "__main__":
    main()
