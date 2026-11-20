"""The hosted site must reach the app with the visitor's own path (BUI-1970)."""

import json
import tomllib
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def test_vercel_preset_loads_the_hosted_app_without_a_rewrite() -> None:
    vercel = json.loads((REPOSITORY / "vercel.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))

    # A rewrite to the function arrives as the literal path /api/index, which the
    # hosted boundary refuses, so every page answered 404 on Git deploys.
    assert "rewrites" not in vercel
    assert "routes" not in vercel
    assert pyproject["tool"]["vercel"]["entrypoint"] == "api.index:app"
    module, attribute = pyproject["tool"]["vercel"]["entrypoint"].split(":")
    assert (REPOSITORY / Path(*module.split("."))).with_suffix(".py").is_file()
    assert attribute == "app"
