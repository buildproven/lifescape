"""BUI-2161: the Claude plugin ships a square 512-2048 px icon at .claude-plugin/icon.png.

`claude plugin validate .` warns ICON_MISSING without it, and the plugin directory takes the icon
only on first submission.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
ICON = REPOSITORY / ".claude-plugin" / "icon.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    assert data[:8] == PNG_SIGNATURE, "icon.png must be a PNG file"
    chunks: list[tuple[bytes, bytes]] = []
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        assert zlib.crc32(kind + body) == crc, f"PNG chunk {kind!r} has a bad CRC"
        chunks.append((kind, body))
        offset += 12 + length
        if kind == b"IEND":
            break
    return chunks


def test_plugin_icon_is_a_valid_png() -> None:
    chunks = _png_chunks(ICON.read_bytes())

    assert chunks[0][0] == b"IHDR"
    assert chunks[-1][0] == b"IEND"
    assert any(kind == b"IDAT" for kind, _ in chunks)


def test_plugin_icon_is_square_between_512_and_2048_pixels() -> None:
    header = _png_chunks(ICON.read_bytes())[0][1]
    width, height = struct.unpack(">II", header[:8])

    assert width == height, f"icon must be square, got {width}x{height}"
    assert 512 <= width <= 2048, f"icon side must be 512-2048 px, got {width}"
