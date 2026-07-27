# -*- coding: utf-8 -*-
"""
aim_viewer.py — minimal local web viewer for extracted PR2 .aim files.

Pairs with cpr_extract.py: point it at an extracted CPR folder and browse
every .aim with previews for the formats we can decode.

    python tools/micro/aim_viewer.py "C:/GOG Games/p2 - cpr/pr2_arcd" [--port 8010]

Python 3.7+ stdlib only. Decoders live in the vendored modules next to this
script (imsld.py / dxt.py / tgares_codec.py); BMPRES is parsed inline below.

Decodable kinds:
  AIMRES2 class 17/18 with an IMSLD32 / IMSLDXT1|3|5 / IMDXT1|3|5 chunk
  TGARES (town ground textures)
  BMPRES  (embedded uncompressed 24/32-bit BMP)
Everything else (pure TILEDIM containers, unknown) is listed with
decodable=false — the atlasEditor web tool covers those.
"""
from __future__ import print_function

import argparse
import json
import os
import struct
import sys
import webbrowser
import zlib

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

# Make vendored codecs importable no matter where we are launched from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import imsld  # noqa: E402
import tgares_codec  # noqa: E402

# ---------------------------------------------------------------------------
# Tiny RGBA -> PNG encoder (stdlib only)
# ---------------------------------------------------------------------------


def rgba_to_png(rgba, w, h):
    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    row = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgba[y * row : (y + 1) * row]
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def bgra_to_rgba(bgra):
    out = bytearray(len(bgra))
    out[0::4] = bgra[2::4]
    out[1::4] = bgra[1::4]
    out[2::4] = bgra[0::4]
    out[3::4] = bgra[3::4]
    return bytes(out)


# ---------------------------------------------------------------------------
# BMPRES: tag 'BMPRES  ' + u32 w,h,size + embedded BMP (uncompressed 24/32)
# ---------------------------------------------------------------------------


def bmpres_sniff(head):
    """Return (w, h) from a BMPRES header, or None."""
    if not head.startswith(b"BMPRES  ") or len(head) < 20:
        return None
    w, h, _size = struct.unpack_from("<III", head, 8)
    if 0 < w <= 8192 and 0 < h <= 8192:
        return w, h
    return None


def bmpres_decode(data):
    """BMPRES bytes -> (bgra, w, h). Raises ValueError on anything odd."""
    wh = bmpres_sniff(data[:20])
    if wh is None:
        raise ValueError("not BMPRES")
    bmp = data[20:]
    if len(bmp) < 54 or bmp[:2] != b"BM":
        raise ValueError("BMPRES: no embedded BMP")
    data_off = struct.unpack_from("<I", bmp, 0x0A)[0]
    dib = struct.unpack_from("<I", bmp, 0x0E)[0]
    if dib < 40:
        raise ValueError("BMPRES: unsupported DIB %d" % dib)
    w = struct.unpack_from("<i", bmp, 0x12)[0]
    h = struct.unpack_from("<i", bmp, 0x16)[0]
    bpp = struct.unpack_from("<H", bmp, 0x1C)[0]
    comp = struct.unpack_from("<I", bmp, 0x1E)[0]
    if comp != 0 or bpp not in (24, 32) or w <= 0 or h == 0:
        raise ValueError("BMPRES: unsupported bmp (bpp=%d comp=%d)" % (bpp, comp))
    bottom_up = h > 0
    h = abs(h)
    if w > 8192 or h > 8192 or data_off + (w * bpp // 8 + 3 & ~3) * h > len(bmp) + 3:
        raise ValueError("BMPRES: bad geometry %dx%d" % (w, h))
    stride = (w * bpp // 8 + 3) & ~3
    bgra = bytearray(w * h * 4)
    for y in range(h):
        src_y = h - 1 - y if bottom_up else y
        row = bmp[data_off + src_y * stride : data_off + src_y * stride + w * (bpp // 8)]
        for x in range(w):
            o = (y * w + x) * 4
            p = x * (bpp // 8)
            bgra[o] = row[p]
            bgra[o + 1] = row[p + 1]
            bgra[o + 2] = row[p + 2]
            bgra[o + 3] = row[p + 3] if bpp == 32 else 255
    return bytes(bgra), w, h


# ---------------------------------------------------------------------------
# Header sniffing (no full decode — used for the listing)
# ---------------------------------------------------------------------------

_IMG_TAGS = (
    b"IMSLD32 ", b"IMSLD8  ",
    b"IMSLDXT1", b"IMSLDXT3", b"IMSLDXT5",
    b"IMDXT1  ", b"IMDXT3  ", b"IMDXT5  ",
)
_CLASS_NAMES = {17: "MIPM", 18: "TILEDIM"}


def _find_image_chunk(data):
    """Walk AIMRES2 chunk tags; return (tag, w, h) of first image chunk."""
    off = 20 if len(data) >= 28 else 16
    n = len(data)
    while off + 8 <= n and off < 4096:
        tag = data[off : off + 8]
        if tag == b"\x00" * 8:
            off += 8
            continue
        if not all(c == 0 or 32 <= c < 127 for c in tag):
            return None
        if tag in _IMG_TAGS:
            try:
                if tag.startswith((b"IMSLDXT", b"IMDXT")):
                    w, h = struct.unpack_from("<II", data, off + 8)
                else:  # IMSLD32 / IMSLD8: 6xu32 header, w/h at +16/+20
                    w, h = struct.unpack_from("<II", data, off + 16)
                if not (0 < w <= 8192 and 0 < h <= 8192):
                    w = h = None
            except struct.error:
                w = h = None
            return tag.decode("latin-1").strip(), w, h
        # skip container headers to reach the first sub-chunk
        if tag.startswith(b"MIPMCONT"):
            off += 8 + 8
        elif tag.startswith(b"TILEDIM"):
            off += 8 + 12
        else:
            return None
    return None


def sniff_file(path):
    """Return dict(kind, w, h, decodable) for one .aim file (header only)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return {"kind": "unknown", "w": None, "h": None, "decodable": False}

    wh = bmpres_sniff(head)
    if wh:
        return {"kind": "BMPRES", "w": wh[0], "h": wh[1], "decodable": True}

    if head.startswith(b"AIMRES2"):
        if b"TGARES" in head[:80]:
            hdr = tgares_codec.parse_header(head)
            w = hdr["w"] if hdr else None
            h = hdr["h"] if hdr else None
            return {"kind": "TGARES", "w": w, "h": h, "decodable": True}
        cls = struct.unpack_from("<I", head, 0x10)[0] if len(head) >= 20 else 0
        cls_name = _CLASS_NAMES.get(cls)
        found = _find_image_chunk(head)
        if found:
            tag, w, h = found
            kind = "%s/%s" % (cls_name, tag) if cls_name else tag
            return {"kind": kind, "w": w, "h": h, "decodable": True}
        if cls_name:
            return {"kind": cls_name, "w": None, "h": None, "decodable": False}
        return {"kind": "AIMRES2", "w": None, "h": None, "decodable": False}

    return {"kind": "unknown", "w": None, "h": None, "decodable": False}


# ---------------------------------------------------------------------------
# Full decode (lazy, per /api/png request)
# ---------------------------------------------------------------------------


def decode_file(path, kind):
    with open(path, "rb") as f:
        data = f.read()
    if kind == "BMPRES":
        bgra, w, h = bmpres_decode(data)
        return bgra, w, h
    if kind == "TGARES":
        bgra, wh = tgares_codec.decode_bgra(data)
        if bgra is None:
            raise ValueError("TGARES decode failed")
        return bgra, wh[0], wh[1]
    img = imsld.decode_aim_file(data)  # raises imsld.ImsldError
    return img.bgra, img.w, img.h


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def scan(root):
    files = []
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            if not fn.lower().endswith(".aim"):
                continue
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            info = sniff_file(full)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            folder = os.path.dirname(rel)
            files.append(
                {
                    "path": rel,
                    "folder": folder,
                    "name": os.path.basename(rel),
                    "size": size,
                    "kind": info["kind"],
                    "w": info["w"],
                    "h": info["h"],
                    "decodable": info["decodable"],
                }
            )
    files.sort(key=lambda f: f["path"].lower())
    return files


# ---------------------------------------------------------------------------
# Embedded frontend
# ---------------------------------------------------------------------------

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>PR2 AIM viewer</title>
<link rel="stylesheet" href="https://unpkg.com/element-plus@2.4.4/dist/index.css" />
<link rel="stylesheet" href="https://unpkg.com/element-plus@2.4.4/theme-chalk/dark/css-vars.css" />
<style>
  :root{
    --bg0:#0a0d12; --bg1:#0e1218; --panel:#131922; --panel2:#182029; --panel3:#1e2833;
    --line:#263140; --text:#e8eef7; --muted:#8b9bb0; --accent:#5b9fd4; --ok:#4ab07e;
    --mono:"Cascadia Mono",ui-monospace,Consolas,monospace;
  }
  html.dark{ --el-color-primary:#5b9fd4; --el-bg-color:#131922; --el-bg-color-page:#0e1218;
    --el-fill-color-blank:#131922; --el-fill-color:#1e2833; --el-border-color:#263140;
    --el-border-color-light:#1d2631; --el-text-color-primary:#e8eef7; --el-text-color-regular:#c4d0de; }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg0);color:var(--text);font:13px/1.45 "Segoe UI",system-ui,sans-serif}
  #app{display:flex;flex-direction:column;height:100vh}
  header{display:flex;align-items:center;gap:12px;padding:8px 14px;background:var(--bg1);
    border-bottom:1px solid var(--line);flex:0 0 auto}
  header .title{font-weight:600;color:var(--accent)}
  header .root{font-family:var(--mono);color:var(--muted);font-size:12px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  header .count{margin-left:auto;color:var(--muted);white-space:nowrap}
  .body{display:flex;flex:1;min-height:0}
  .side{width:260px;flex:0 0 260px;background:var(--panel);border-right:1px solid var(--line);
    display:flex;flex-direction:column;min-height:0}
  .side .search{padding:8px;border-bottom:1px solid var(--line)}
  .tree{flex:1;overflow:auto;padding:4px 0}
  .tnode{padding:3px 10px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
    color:var(--text);user-select:none}
  .tnode:hover{background:var(--panel3)}
  .tnode.sel{background:rgba(91,159,212,.16);color:var(--accent)}
  .tnode .cnt{color:var(--muted);font-size:11px;margin-left:6px}
  .main{flex:1;display:flex;min-width:0;min-height:0}
  .listpane{flex:1;overflow:auto;min-width:0;padding:6px 10px}
  .row{display:flex;align-items:center;gap:10px;padding:4px 8px;border-radius:5px;cursor:pointer}
  .row:hover{background:var(--panel2)}
  .row.sel{background:rgba(91,159,212,.14)}
  .row .name{font-family:var(--mono);font-size:12px;overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap;flex:1;min-width:0}
  .badge{font-size:10px;font-family:var(--mono);padding:1px 6px;border-radius:4px;
    background:var(--panel3);color:var(--muted);border:1px solid var(--line);white-space:nowrap}
  .badge.dec{color:var(--ok);border-color:rgba(74,176,126,.4)}
  .dims{color:var(--muted);font-size:11px;width:86px;text-align:right;white-space:nowrap}
  .sz{color:var(--muted);font-size:11px;width:64px;text-align:right;white-space:nowrap}
  .dot{width:8px;height:8px;border-radius:50%;flex:0 0 8px;background:#4a5563}
  .dot.ok{background:var(--ok)}
  .pager{display:flex;justify-content:center;padding:8px;border-top:1px solid var(--line);
    background:var(--bg1);flex:0 0 auto}
  .listwrap{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0}
  .preview{width:46%;min-width:340px;border-left:1px solid var(--line);background:var(--panel);
    display:flex;flex-direction:column;min-height:0}
  .pv-head{padding:10px 12px;border-bottom:1px solid var(--line)}
  .pv-head .fn{font-family:var(--mono);font-weight:600;font-size:13px;word-break:break-all}
  .pv-head .rel{font-family:var(--mono);color:var(--muted);font-size:11px;word-break:break-all;margin-top:2px}
  .pv-head .meta{color:var(--muted);font-size:11px;margin-top:6px;display:flex;gap:14px;flex-wrap:wrap}
  .pv-tools{display:flex;gap:8px;padding:8px 12px;border-bottom:1px solid var(--line);align-items:center}
  .pv-tools .zl{color:var(--muted);font-size:11px;margin-left:auto;font-family:var(--mono)}
  .stage{flex:1;position:relative;overflow:hidden;min-height:0;cursor:grab;
    background:
      linear-gradient(45deg,#1a222c 25%,transparent 25%,transparent 75%,#1a222c 75%),
      linear-gradient(45deg,#1a222c 25%,#12181f 25%,#12181f 75%,#1a222c 75%);
    background-size:16px 16px;background-position:0 0,8px 8px}
  .stage:active{cursor:grabbing}
  .stage img{position:absolute;left:0;top:0;image-rendering:pixelated;transform-origin:0 0;
    user-select:none;-webkit-user-drag:none}
  .pv-empty{flex:1;display:flex;align-items:center;justify-content:center;color:var(--muted);
    font-size:12px;text-align:center;padding:20px}
  .hint{color:var(--muted);font-size:11px;padding:6px 12px;border-top:1px solid var(--line)}
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:#263140;border-radius:5px}
  ::-webkit-scrollbar-track{background:transparent}
</style>
</head>
<body>
<div id="app">
  <header>
    <span class="title">PR2 AIM viewer</span>
    <span class="root" :title="root">{{ root }}</span>
    <span class="count">{{ files.length }} .aim files &middot; {{ decCount }} decodable</span>
  </header>
  <div class="body">
    <div class="side">
      <div class="search">
        <el-input v-model="query" size="small" clearable placeholder="search (2+ chars)"></el-input>
      </div>
      <div class="tree">
        <div v-for="n in treeRows" :key="n.path" class="tnode"
             :class="{sel: !searching && selFolder===n.path}"
             :style="{paddingLeft: (10 + n.depth*14) + 'px'}"
             @click="selectFolder(n.path)">
          {{ n.name }}<span class="cnt">{{ n.count }}</span>
        </div>
      </div>
    </div>
    <div class="main">
      <div class="listwrap">
        <div class="listpane" @click.self="closePreview">
          <div v-if="searching" class="hint" style="border:0">search: "{{ query }}" — {{ viewFiles.length }} hit(s)</div>
          <div v-for="f in pageFiles" :key="f.path" class="row" :class="{sel: sel && sel.path===f.path}"
               @click="openItem(f)">
            <span class="dot" :class="{ok:f.decodable}"></span>
            <span class="name" :title="f.path">{{ f.name }}</span>
            <span class="badge" :class="{dec:f.decodable}">{{ f.kind }}</span>
            <span class="dims">{{ f.w ? f.w + '×' + f.h : '?' }}</span>
            <span class="sz">{{ fmtSize(f.size) }}</span>
          </div>
          <div v-if="!pageFiles.length" class="pv-empty">no .aim files here</div>
        </div>
        <div class="pager" v-if="pageCount>1">
          <el-pagination layout="prev, pager, next" :total="viewFiles.length"
                         :page-size="pageSize" :current-page="page"
                         @current-change="p=>page=p" small background></el-pagination>
        </div>
      </div>
      <div class="preview" v-if="sel">
        <div class="pv-head">
          <div class="fn">{{ sel.name }}</div>
          <div class="rel">{{ sel.path }}</div>
          <div class="meta">
            <span>kind: {{ sel.kind }}</span>
            <span>dims: {{ sel.w ? sel.w+'×'+sel.h : '?' }}</span>
            <span>size: {{ fmtSize(sel.size) }}</span>
          </div>
        </div>
        <template v-if="sel.decodable">
          <div class="pv-tools">
            <el-button-group>
              <el-button size="small" @click="zoomFit">fit</el-button>
              <el-button size="small" @click="zoomSet(1)">100%</el-button>
              <el-button size="small" @click="zoomSet(2)">2x</el-button>
              <el-button size="small" @click="zoomSet(4)">4x</el-button>
            </el-button-group>
            <span class="zl">{{ Math.round(scale*100) }}%</span>
          </div>
          <div class="stage" ref="stage"
               @wheel.prevent="onWheel" @mousedown.prevent="dragStart"
               @mousemove="dragMove" @mouseup="dragEnd" @mouseleave="dragEnd"
               @dblclick="toggleFit">
            <img :src="pngUrl" :style="{transform:'translate('+tx+'px,'+ty+'px) scale('+scale+')'}"
                 @load="onImgLoad" draggable="false" alt="" />
          </div>
          <div class="hint">wheel = zoom &middot; drag = pan &middot; double-click = fit/100% &middot; &larr;/&rarr; = prev/next</div>
        </template>
        <div class="pv-empty" v-else>no preview ({{ sel.kind }})</div>
      </div>
    </div>
  </div>
</div>
<script src="https://unpkg.com/vue@3.4.21/dist/vue.global.prod.js"></script>
<script src="https://unpkg.com/element-plus@2.4.4/dist/index.full.min.js"></script>
<script>
const { createApp } = Vue;

createApp({
  data() {
    return {
      root: '', files: [], query: '',
      selFolder: '', sel: null,
      page: 1, pageSize: 100,
      scale: 1, tx: 0, ty: 0, fitScale: 1, fitted: true,
      natW: 0, natH: 0,
      dragging: false, dragX: 0, dragY: 0,
    };
  },
  computed: {
    decCount() { return this.files.filter(f => f.decodable).length; },
    searching() { return this.query.trim().length >= 2; },
    viewFiles() {
      if (this.searching) {
        const q = this.query.trim().toLowerCase();
        return this.files.filter(f => f.path.toLowerCase().includes(q));
      }
      return this.files.filter(f => f.folder === this.selFolder);
    },
    pageCount() { return Math.max(1, Math.ceil(this.viewFiles.length / this.pageSize)); },
    pageFiles() {
      const p = Math.min(this.page, this.pageCount);
      return this.viewFiles.slice((p - 1) * this.pageSize, p * this.pageSize);
    },
    treeRows() {
      const counts = {};
      for (const f of this.files) {
        const parts = f.folder ? f.folder.split('/') : [];
        let acc = '';
        counts[''] = (counts[''] || 0) + 1;
        for (const p of parts) {
          acc = acc ? acc + '/' + p : p;
          counts[acc] = (counts[acc] || 0) + 1;
        }
      }
      const rows = [{ path: '', name: '(root)', depth: 0, count: counts[''] || 0 }];
      const walk = (prefix, depth) => {
        const kids = new Set();
        for (const key of Object.keys(counts)) {
          if (!key || key === prefix) continue;
          if (prefix && !key.startsWith(prefix + '/')) continue;
          const rest = key.slice(prefix ? prefix.length + 1 : 0);
          if (rest.includes('/')) kids.add(rest.split('/')[0]);
          else if (rest) kids.add(rest);
        }
        for (const k of [...kids].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()))) {
          const full = prefix ? prefix + '/' + k : k;
          if (!counts[full]) continue;
          rows.push({ path: full, name: k, depth, count: counts[full] });
          walk(full, depth + 1);
        }
      };
      walk('', 1);
      return rows;
    },
    pngUrl() { return this.sel ? '/api/png?path=' + encodeURIComponent(this.sel.path) : ''; },
  },
  watch: {
    query() { this.page = 1; },
    viewFiles() { if (this.page > this.pageCount) this.page = this.pageCount; },
  },
  methods: {
    fmtSize(n) {
      if (n >= 1048576) return (n / 1048576).toFixed(1) + 'M';
      if (n >= 1024) return (n / 1024).toFixed(1) + 'K';
      return n + 'B';
    },
    selectFolder(p) { this.selFolder = p; this.page = 1; },
    openItem(f) { this.sel = f; this.fitted = true; },
    closePreview() { this.sel = null; },
    nav(d) {
      const list = this.viewFiles;
      if (!this.sel || !list.length) return;
      let i = list.findIndex(f => f.path === this.sel.path);
      if (i < 0) i = 0;
      i = (i + d + list.length) % list.length;
      this.page = Math.floor(i / this.pageSize) + 1;
      this.openItem(list[i]);
    },
    stageRect() { return this.$refs.stage.getBoundingClientRect(); },
    onImgLoad(e) {
      this.natW = e.target.naturalWidth; this.natH = e.target.naturalHeight;
      this.zoomFit();
    },
    zoomFit() {
      if (!this.natW) return;
      const r = this.stageRect();
      this.fitScale = Math.min(r.width / this.natW, r.height / this.natH);
      this.scale = this.fitScale;
      this.tx = (r.width - this.natW * this.scale) / 2;
      this.ty = (r.height - this.natH * this.scale) / 2;
      this.fitted = true;
    },
    zoomSet(s) {
      const r = this.stageRect();
      this.applyZoom(s, r.width / 2, r.height / 2);
      this.fitted = false;
    },
    toggleFit() { if (this.fitted) this.zoomSet(1); else this.zoomFit(); },
    applyZoom(ns, cx, cy) {
      ns = Math.min(64, Math.max(0.02, ns));
      const k = ns / this.scale;
      this.tx = cx - (cx - this.tx) * k;
      this.ty = cy - (cy - this.ty) * k;
      this.scale = ns;
    },
    onWheel(e) {
      const r = this.stageRect();
      const cx = e.clientX - r.left, cy = e.clientY - r.top;
      this.applyZoom(this.scale * (e.deltaY < 0 ? 1.25 : 0.8), cx, cy);
      this.fitted = false;
    },
    dragStart(e) { this.dragging = true; this.dragX = e.clientX; this.dragY = e.clientY; },
    dragMove(e) {
      if (!this.dragging) return;
      this.tx += e.clientX - this.dragX; this.ty += e.clientY - this.dragY;
      this.dragX = e.clientX; this.dragY = e.clientY;
    },
    dragEnd() { this.dragging = false; },
    onKey(e) {
      if (!this.sel) return;
      if (e.key === 'ArrowLeft') { e.preventDefault(); this.nav(-1); }
      else if (e.key === 'ArrowRight') { e.preventDefault(); this.nav(1); }
    },
  },
  mounted() {
    fetch('/api/list').then(r => r.json()).then(d => {
      this.root = d.root; this.files = d.files;
    });
    window.addEventListener('keydown', this.onKey);
  },
  unmounted() { window.removeEventListener('keydown', this.onKey); },
}).use(ElementPlus).mount('#app');
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class AimHandler(BaseHTTPRequestHandler):
    server_version = "AimViewer/1.0"
    files = []
    root = ""

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, code, msg):
        self._send(code, json.dumps({"error": msg}))

    def _resolve(self, rel):
        """Resolve a client-supplied rel path inside the scan root, or None."""
        rel = unquote(rel).replace("\\", "/").lstrip("/")
        full = os.path.realpath(os.path.join(self.root, rel))
        root_real = os.path.realpath(self.root)
        if os.path.commonpath([root_real, full]) != root_real:
            return None
        if not os.path.isfile(full):
            return None
        return full

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
            return
        if url.path == "/api/list":
            self._send(200, json.dumps({"root": os.path.abspath(self.root), "files": self.files}))
            return
        if url.path == "/api/png":
            qs = parse_qs(url.query)
            rel = (qs.get("path") or [""])[0]
            full = self._resolve(rel)
            if full is None:
                self._send_error_json(404, "not found")
                return
            entry = next((f for f in self.files if f["path"] == rel.replace("\\", "/")), None)
            if entry is None or not entry["decodable"]:
                self._send_error_json(404, "not decodable")
                return
            try:
                bgra, w, h = decode_file(full, entry["kind"])
                png = rgba_to_png(bgra_to_rgba(bgra), w, h)
            except Exception as e:  # decode failure must not kill the server
                self._send_error_json(404, "decode failed: %s" % e)
                return
            self._send(200, png, "image/png")
            return
        self._send_error_json(404, "unknown route")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local web viewer for extracted PR2 .aim files")
    ap.add_argument("folder", nargs="?", default=".", help="scan root (default: .)")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.folder)
    if not os.path.isdir(root):
        print("error: not a folder: %s" % root, file=sys.stderr)
        return 2

    print("scanning %s ..." % root)
    files = scan(root)
    dec = sum(1 for f in files if f["decodable"])
    print("found %d .aim files (%d decodable)" % (len(files), dec))

    AimHandler.files = files
    AimHandler.root = root
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), AimHandler)
    url = "http://127.0.0.1:%d/" % args.port
    print("serving at %s  (Ctrl+C to stop)" % url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
