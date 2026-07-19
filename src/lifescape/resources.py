"""Access packaged benchmark assets in wheels and editable checkouts."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from importlib import resources
from pathlib import Path


@contextmanager
def bundled_benchmark() -> Iterator[tuple[Path, Path]]:
    """Yield the benchmark evidence and configuration as filesystem paths."""
    package_root = resources.files("lifescape").joinpath("resources")
    package_evidence = package_root.joinpath("benchmark-evidence.csv")
    package_config = package_root.joinpath("config")
    if package_evidence.is_file() and package_config.is_dir():
        with ExitStack() as stack:
            evidence = stack.enter_context(resources.as_file(package_evidence))
            config = stack.enter_context(resources.as_file(package_config))
            yield evidence, config
        return

    repository = Path(__file__).resolve().parents[2]
    evidence = repository / "data/benchmarks/evidence.csv"
    config = repository / "config"
    if not evidence.is_file() or not config.is_dir():
        raise FileNotFoundError("packaged benchmark resources are unavailable")
    yield evidence, config
