"""The public landing page shows a recorded tour of the local app (PRD G1, G2)."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from lifescape.web import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "lifescape" / "static"
MAX_VIDEO_BYTES = 3_000_000  # Vercel caps a Python function response near 4.5 MB.


def hosted(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "output", hosted_demo=True)
    return TestClient(app, base_url="https://lifescape.buildproven.ai")


def test_landing_page_embeds_the_tour_with_poster_and_text_steps(tmp_path: Path) -> None:
    html = hosted(tmp_path).get("/").text
    video = re.search(r"<video[^>]*>", html)
    assert video is not None
    assert 'poster="/static/tour-poster.jpg"' in video.group(0)
    assert "controls" in video.group(0)
    assert '<source src="/static/tour.mp4" type="video/mp4"' in html
    assert "Steps shown:" in html


def test_tour_video_is_served_and_small_enough_for_the_hosted_function(tmp_path: Path) -> None:
    assert (STATIC / "tour.mp4").stat().st_size <= MAX_VIDEO_BYTES
    client = hosted(tmp_path)
    full = client.get("/static/tour.mp4")
    assert full.status_code == 200
    assert full.headers["content-type"] == "video/mp4"
    assert full.content[4:8] == b"ftyp"
    assert client.get("/static/tour-poster.jpg").status_code == 200


def test_tour_video_supports_range_requests_for_seeking(tmp_path: Path) -> None:
    response = hosted(tmp_path).get("/static/tour.mp4", headers={"Range": "bytes=0-99"})
    assert response.status_code == 206
    assert len(response.content) == 100


def test_demo_page_links_to_the_current_repository(tmp_path: Path) -> None:
    html = hosted(tmp_path).get("/demo").text
    assert "github.com/buildproven/lifescape-engine" not in html
    assert "github.com/buildproven/lifescape" in html


def test_demo_page_embeds_the_tour(tmp_path: Path) -> None:
    html = hosted(tmp_path).get("/demo").text
    assert 'poster="/static/tour-poster.jpg"' in html
    assert '<source src="/static/tour.mp4" type="video/mp4"' in html
