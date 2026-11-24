"""BUI-2161: the plugin ships a square listing icon at ``.claude-plugin/icon.png``.

``claude plugin validate .`` warns ICON_MISSING without it, and the plugin directory takes the
icon only on first submission. The directory accepts a square PNG of 512 to 2048 px per side,
under 2 MB.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
ICON = REPOSITORY / ".claude-plugin" / "icon.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 2 * 1024 * 1024
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}  # PNG colour type -> samples per pixel


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    assert data.startswith(PNG_SIGNATURE), "icon.png must be a PNG file"
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == crc, f"bad CRC in {kind!r} chunk"
        chunks.append((kind, body))
        offset += 12 + length
    return chunks


def test_icon_is_a_square_png_the_directory_accepts() -> None:
    data = ICON.read_bytes()
    assert len(data) < MAX_BYTES

    chunks = _chunks(data)
    assert chunks[0][0] == b"IHDR"
    assert chunks[-1][0] == b"IEND"
    width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    assert width == height, "the icon must be square"
    assert 512 <= width <= 2048

    # The pixel data decodes to exactly one filtered row per scanline.
    assert depth == 8 and interlace == 0
    pixels = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    assert len(pixels) == height * (1 + width * CHANNELS[colour])
