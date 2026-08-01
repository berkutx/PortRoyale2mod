# Micro tools

Small single-purpose helpers for Port Royale 2 modding. No dependencies —
Python 3.7+ standard library only (decoders are vendored next to the scripts).

## cpr_extract.py

Extract a CPR resource archive into a folder:

```
python tools/micro/cpr_extract.py "C:/GOG Games/Port Royale 2/PR2_Arcd.cpr" "C:/GOG Games/p2 - cpr/pr2_arcd"
```

## aim_viewer.py

Browse the extracted `.aim` files in a local web page with previews and zoom:

```
python tools/micro/aim_viewer.py "C:/GOG Games/p2 - cpr/pr2_arcd" [--port 8010] [--no-browser]
```

Decodes and previews: AIMRES2 MIPM/TILEDIM files with IMSLD32 or
IMSLDXT1/3/5 / IMDXT1/3/5 chunks, TILEDIM atlas pages (IMHC4444/1555/565
tiles, BMPRES tiles, native and MIPMCONT-wrapped IMSLD32 tiles), TGARES town
ground textures, and BMPRES embedded bitmaps. IMJPG24/32 tile containers and
unknown formats are listed but shown as "no preview" — those are covered by
the atlasEditor web tool, which is also where any `.aim` editing happens.

`test_tiledim.py` cross-validates the pure-python TILEDIM decoder
byte-for-byte against the PIL reference (`loader/ship_lab/texpage_tool.py`)
on stock TexPage files — needs Pillow + numpy installed, it is a dev check,
not a viewer dependency.

The viewer is read-only: it scans headers once at startup and decodes lazily
per click, so it stays fast on multi-thousand-file trees. The details panel
also shows the exact on-disk layout signature (for example
`AIMRES2/class18/TILEDIM/IMSLDXT1`). This is the format identity that editors
must preserve on a normal save.
