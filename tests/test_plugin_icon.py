"""BUI-2161: the plugin ships a square listing icon the plugin directory accepts.

The directory looks for ``.claude-plugin/icon.png`` (unless ``plugin.json`` sets ``icon``) and
takes a square PNG or JPEG, 512 to 2048 px on each side, under 2 MB. It reads the icon only on
the first submission, so the file must be valid before then.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
ICON = REPOSITORY / ".claude-plugin" / "icon.png"
PLUGIN = REPOSITORY / ".claude-plugin" / "plugin.json"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MIN_SIDE, MAX_SIDE = 512, 2048
MAX_BYTES = 2 * 1024 * 1024
# PNG color type -> channels per pixel (8-bit depth).
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    assert data.startswith(PNG_SIGNATURE), "icon.png is not a PNG file"
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        assert len(body) == length, f"{kind!r} chunk is truncated"
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == crc, f"{kind!r} chunk has a bad CRC"
        chunks.append((kind, body))
        offset += 12 + length
    return chunks


def test_plugin_manifest_uses_the_default_icon_path() -> None:
    manifest = json.loads(PLUGIN.read_text(encoding="utf-8"))
    icon = REPOSITORY / manifest.get("icon", ".claude-plugin/icon.png")
    assert icon.resolve() == ICON, "plugin.json points `icon` away from .claude-plugin/icon.png"


def test_icon_is_a_square_png_the_directory_accepts() -> None:
    data = ICON.read_bytes()
    assert len(data) < MAX_BYTES, f"icon.png is {len(data)} bytes; the limit is 2 MB"
    chunks = _chunks(data)
    kinds = [kind for kind, _ in chunks]
    assert kinds[0] == b"IHDR" and kinds[-1] == b"IEND" and b"IDAT" in kinds

    width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    assert width == height, f"icon.png is {width}x{height}; it must be square"
    assert MIN_SIDE <= width <= MAX_SIDE, f"icon.png is {width} px; use {MIN_SIDE}-{MAX_SIDE} px"
    assert depth == 8 and color in CHANNELS and interlace == 0

    pixels = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    assert len(pixels) == height * (1 + width * CHANNELS[color]), "icon.png pixel data is short"


def test_icon_is_not_blank() -> None:
    chunks = _chunks(ICON.read_bytes())
    width, _, _, color, _, _, _ = struct.unpack(">IIBBBBB", chunks[0][1])
    pixels = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    stride = 1 + width * CHANNELS[color]
    middle = pixels[(width // 2) * stride : (width // 2 + 1) * stride]
    assert len(set(middle[1:])) > CHANNELS[color], "icon.png middle row is one flat color"
