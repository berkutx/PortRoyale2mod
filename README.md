# PortRoyale2mod

Modding tools for Port Royale 2 (GOG 1.1.2.3): CPR archive extraction and
AIM image viewing/editing. Everything is pure Python 3.7+ stdlib unless
noted otherwise.

## Tools

| Tool | What it does |
|---|---|
| `tools/micro/cpr_extract.py` | Extracts every file from a CPR archive (`ASCARON_ARCHIVE V0.9`, multi-segment). Illegal/unreadable characters in names are replaced with `_` and logged to `_warnings.log` + console. |
| `tools/micro/aim_viewer.py` | Local web viewer (Vue 3 + Element Plus from CDN) for extracted `.aim` files: folder tree, search, pan/zoom preview. `python tools/micro/aim_viewer.py <folder> [--port 8010]` |

```
python tools/micro/cpr_extract.py pr2_arcd.cpr out/pr2_arcd
python tools/micro/aim_viewer.py out/pr2_arcd
```

## Where game resources live

Almost all assets are packed inside `pr2_arcd.cpr` (~198 MB, 4147 entries)
— not loose files next to the game. `pr2_arcs.cpr` = scripts/config/Lua,
`pr2_arct.cpr` = 3D models (`lcr`), `pr2_loca.cpr` = fonts + UI screens.
A loose file placed next to `PR2.exe` under the same relative path
**overrides** the archived copy (e.g. `images\interface\nation00.aim`).

Many `imagesSD\*.aim` names are **virtual sprites**: they exist only as
rectangles inside shared atlas pages. Resolution chain:
`.anim` → virtual name → `scripts\Partmap.007` (hash → TOC + part index) →
`scripts\TexPageN.tex` (rect + atlas name) → `imagesSD\TexPage_X_Y.aim`.

## Supported AIM formats

`.aim` = `AIMRES2` container. Two classes with different loaders:

| Class / tag | Pixel format | Decoding | Notes |
|---|---|---|---|
| 17 `MIPMCONT → IMSLD32` | BGRA raw or SLDCOMP LZ; `mode==0` = 4 packed B/G/R/A planes, `mode==2` = interleaved | yes | 3D textures: pirate flags, ship textures, city-map flags |
| 17 `MIPMCONT → IMSLDXT1/3/5` | SLD-compressed DXT1/3/5 | yes | compressed 3D textures |
| 17 `IMDXT1/3/5` | raw DXT blocks (3-dword chunk header) | yes | `Hauptmenu12` menu ship textures |
| 18 `TILEDIM → IMHC4444/1555/565` | 16-bit UI atlas tiles 256×256, col-major | yes | `imagesSD\TexPage_8_*.aim` UI sprite atlases |
| 18 `TILEDIM → BMPRES` | BMP24 tiles (black = transparent key) / BMP32 | yes | `TexPage_0_11.aim` (UI flags live here) |
| 18 `TILEDIM → native IMSLD32` | lossless BGRA tiles, five-field header; clipped pages via `IHHW` trailer | yes | `schiffstypen\*` ship icons (400×400 from 256 tiles) |
| 18 `TILEDIM → IMSLDXT1/3/5` | SLD-compressed DXT1/3/5 single images; optional `IHHW` trailer | yes | `nation00..03.aim` world-map flag sources (`IMSLDXT1`) |
| 18 `TILEDIM → IMJPG24/IMJPG32` | 256×256 JPEG tiles, col/row-major | atlasEditor (needs PIL) | `#Waitscreen*`, `#Menu\*`, Fechtkampf backgrounds |
| `TGARES` | town ground splats | yes | `towns\*.aim` |
| `BMPRES` standalone | embedded BMP 24/32 | yes | rare singles |
| `SH_ANIM` (.anim) | animation header + virtual sprite paths (frames stride 0xC18) | parsed, not rendered here | `scripts\Pr2_Flagge_*.anim` |
| `.screen` | compiled UI layout (CScreen) — **not an image** | not parsed | `scripts\*_800x600.screen` |

Encoding on write (city_editor / atlasEditor) preserves the input disk layout
by default: AIM class, outer container, image chunk codec, mip geometry,
SLD-compressed/raw mode, and trailers such as `IHHW`. Thus a stock
`TILEDIM/IMSLDXT1` nation flag stays `TILEDIM/IMSLDXT1`; it is never silently
expanded to an `IMSLD32` file. Explicit format conversion is a separate
operation. Unsupported layouts are rejected instead of being rewritten as a
different format. TGARES and atlas pages likewise retain their original
layout.

## Flag cheat-sheet (where each flag actually is)

| Flag | Location |
|---|---|
| Waving town flag, world map | `images\interface\nation00..03.aim`, `nation_piraten.aim` (standalone; game warps them into 64 frames at runtime) |
| Named pirates / player-pirate | `images\interface\Piraten\pirat00..29.aim`, `spielerpirat.aim` |
| Flags on city buildings | `images\Module_Stadtkarte\Flagge_{England,Frankreich,Holland,Spanien}.aim` (128×512, 4 frames) |
| Flags on 3D ships, in port and battle | `run\Segel.aim` — shared 256×256 `MIPMCONT/IMSLDXT1` sail sheet; flag swatches are in its lower area, so preserve the complete UV layout |
| Convoy flags, world map | atlas sprites `Seekarte_Schiff_Flagge_*` (TexPage8 → `TexPage_8_7.aim`) |
| UI nation flags | atlas sprites `Pr2_Flagge_*` (TexPage12 → `TexPage_0_11.aim`) |
| Player's own flag | `<user dir>\0.bmp` (special `-userflag-`), defaults `DefaultLogos\Logo_*.bmp` |
