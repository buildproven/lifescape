.PHONY: install test lint typecheck quality benchmark app

install:
	uv sync --locked --extra dev --python 3.12
	uv run playwright install chromium
	npm ci

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

quality:
	npm run quality:check

benchmark:
	uv run lifescape benchmark --output-dir outputs/benchmark

app:
	uv run lifescape app
