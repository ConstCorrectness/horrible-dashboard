"""Generate the placeholder app icon (PNG + ICO) for the Tauri shell.

Pure-stdlib so it runs anywhere; replace with real art later and re-run
`uv run python scripts/make_icon.py` if the source design changes.
"""

import struct
import zlib
from pathlib import Path

SIZE = 256
BG = (20, 22, 26, 255)  # --bg
ACCENT = (110, 168, 254, 255)  # --accent


def build_png() -> bytes:
    rows = bytearray()
    lo, hi = SIZE * 3 // 16, SIZE * 13 // 16
    for y in range(SIZE):
        rows.append(0)  # filter: none
        for x in range(SIZE):
            rows += bytes(ACCENT if lo <= x < hi and lo <= y < hi else BG)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    png = build_png()
    out = Path(__file__).resolve().parents[1] / "apps/desktop/src-tauri/icons"
    out.mkdir(parents=True, exist_ok=True)
    (out / "icon.png").write_bytes(png)
    # ICO container with a single PNG entry (size byte 0 means 256).
    ico = (
        struct.pack("<HHH", 0, 1, 1)
        + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
        + png
    )
    (out / "icon.ico").write_bytes(ico)
    print(f"wrote {out / 'icon.ico'} ({len(png)} byte png)")


if __name__ == "__main__":
    main()
