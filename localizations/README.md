# Port Royale 2 localization packages v1.0

These packages target only the English GOG Port Royale 2 1.1.2.3 executable:

```text
SHA-256 394CE2A48BC6B21708B81A232C7C88459BE7F3C8082CC593AD83B0B8E4C168EC
```

Steam and other executable hashes are not supported. Do not replace
`PR2.exe` with an executable taken from a retail localization.

## Install

1. Close Port Royale 2.
2. Run `PR2 Addon Configurator.exe` next to the supported `PR2.exe`.
3. Select German, Spanish, Polish, or Russian under **Online language**.
4. Press **Download & install**, select the installed package, then press
   **Activate**.
5. Start the normal `PR2.exe`.

For offline installation, download the desired `*.pr2loc.zip` from the
[v1.0 release](https://github.com/berkutx/PortRoyale2mod/releases/tag/v1.0),
choose **Install local...**, then **Activate**.

## Inspect and verify

`*.pr2loc.zip` is deliberately an ordinary uncompressed, non-ZIP64 archive.
It can be inspected without the Configurator. Every package contains:

- `manifest.json` — exact target, string-table, review-file, and asset hashes;
- `strings.jsonl` — human-readable UTF-8 translation review;
- `strings.res` — the UTF-16LE string table consumed by the addon;
- only the font/audio assets declared by the manifest.

The Configurator verifies the exact v1.0 release byte count and SHA-256, then
revalidates ZIP structure, CRC-32, every declared member hash and size, safe
Windows paths, and the target executable hash. Package contents are never
executed or loaded as DLLs.

The authoritative machine-readable release index is
[`catalog-v1.json`](catalog-v1.json); standalone checksums are in
[`SHA256SUMS.txt`](SHA256SUMS.txt).

## Languages

| Locale | Package | Size | SHA-256 |
|---|---|---:|---|
| German / Deutsch | `de-retail-1.0.0.pr2loc.zip` | 155,302,506 | `D9EFC86B7A561F3BB4B998C9B8A292D37DCACA01766DC6FC391F208AAECFB7AB` |
| Spanish / Español | `es-retail-1.0.0.pr2loc.zip` | 145,620,368 | `6FF9039CE91C78DE5E5D1D199ECAF48C7D0DA4752153DA0D3B955CFC28969864` |
| Polish / Polski | `pl-retail-1.0.0.pr2loc.zip` | 2,635,483 | `1509941E56C0EAFF5B0D6C8106BBBA7686FBFEF6D7FFDC0949D403C8415964F1` |
| Russian / Русский | `ru-retail-1.0.0.pr2loc.zip` | 134,556,347 | `C32DEEF8B51EF9FF464863A09488343C8723168ADF12C8B9802F940FFC9B51CA` |

Community: [Discord](https://discord.com/channels/608039137265582100/1429799720007503892) ·
[Patreon](https://www.patreon.com/collection/2278934?view=expanded)
