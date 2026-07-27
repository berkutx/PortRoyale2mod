# PortRoyale2mod

## tools/micro/cpr_extract.py

Minimal CPR extractor (ASCARON_ARCHIVE V0.9, multi-segment), stdlib only:

```
python tools/micro/cpr_extract.py <file.cpr> <out_dir>
```

Extracts every entry into `<out_dir>/<relative/path>`. Names with
unreadable / Windows-illegal characters are replaced with `_`; all
renames, collisions and read errors go to console and
`<out_dir>/_warnings.log`.