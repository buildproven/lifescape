"""`.env.example` names every variable the hosted MCP endpoint needs (BUI-2041)."""

from __future__ import annotations

from pathlib import Path

from lifescape.mcp_http import HOSTED_ENV
from lifescape.oauth import GITHUB_CLIENT_SECRET_ENV, SIGNING_KEY_ENV
from lifescape.store import DATABASE_URL_ENV, STORE_ENV, LocalStore, store_from_env

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _entries() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, value = stripped.partition("=")
        assert separator, f".env.example line is not NAME=value: {line!r}"
        assert name not in entries, f".env.example names {name} twice"
        entries[name] = value
    return entries


def test_env_example_names_every_hosted_variable() -> None:
    missing = [name for name in HOSTED_ENV if name not in _entries()]
    assert missing == []


def test_env_example_holds_no_hosted_secret_values() -> None:
    entries = _entries()
    for name in (GITHUB_CLIENT_SECRET_ENV, SIGNING_KEY_ENV, DATABASE_URL_ENV):
        assert entries[name] == "", f"{name} must stay empty in .env.example"


def test_env_example_store_value_keeps_the_local_adapter(tmp_path: Path) -> None:
    entries = _entries()
    store = store_from_env(tmp_path, {STORE_ENV: entries[STORE_ENV]})
    assert isinstance(store, LocalStore)
