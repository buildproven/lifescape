"""BUI-2161: the plugin ships a square listing icon at ``.claude-plugin/icon.png``.

The plugin directory takes the icon only on first submission, so it must be in the repo before
that: a PNG, square, 512 to 2048 px on each side, under 2 MB (``claude plugin validate`` reports
ICON_MISSING otherwise).
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
ICON = REPOSITORY / ".claude-plugin" / "icon.png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Bytes per pixel for 8-bit PNG colour types: grey, RGB, grey+alpha, RGBA.
BYTES_PER_PIXEL = {0: 1, 2: 3, 4: 2, 6: 4}


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """Split a PNG into (type, payload) chunks, checking each CRC."""
    assert data.startswith(PNG_SIGNATURE), "icon.png is not a PNG file"
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        assert zlib.crc32(kind + payload) & 0xFFFFFFFF == crc, f"bad CRC in {kind!r} chunk"
        chunks.append((kind, payload))
        offset += 12 + length
        if kind == b"IEND":
            break
    return chunks


def test_bui_2161_icon_is_a_square_png_the_directory_accepts() -> None:
    assert ICON.is_file(), "missing .claude-plugin/icon.png"
    data = ICON.read_bytes()
    assert len(data) < 2 * 1024 * 1024, "icon.png must be under 2 MB"

    chunks = _chunks(data)
    assert chunks[0][0] == b"IHDR"
    assert chunks[-1][0] == b"IEND"
    width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", chunks[0][1])

    assert width == height, f"icon is {width}x{height}, not square"
    assert 512 <= width <= 2048, f"icon side {width} px is outside 512-2048"

    # The image data must decode to exactly one filtered row per pixel row.
    assert depth == 8
    assert interlace == 0
    assert colour in BYTES_PER_PIXEL
    pixels = zlib.decompress(b"".join(payload for kind, payload in chunks if kind == b"IDAT"))
    assert len(pixels) == height * (1 + width * BYTES_PER_PIXEL[colour])


def test_bui_2161_claude_plugin_validate_finds_the_icon() -> None:
    claude = shutil.which("claude")
    if claude is None:
        pytest.skip("BUI-2161 claude plugin validate skipped: the claude CLI is not on PATH")
    probe = subprocess.run(
        [claude, "--version"], capture_output=True, text=True, timeout=60, check=False
    )
    if probe.returncode != 0:
        pytest.skip(f"BUI-2161 claude plugin validate skipped: claude --version failed: {probe}")

    completed = subprocess.run(
        [claude, "plugin", "validate", "."],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert "ICON_MISSING" not in output, output
