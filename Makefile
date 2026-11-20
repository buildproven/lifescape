.PHONY: install test lint typecheck quality benchmark app mcpb

install:
	uv sync --locked --extra dev --python 3.12
	uv run playwright install chromium
	npm ci

test:
	uv run pytest

lint:
	uv run ruff format --check .
	uv run ruff check .
	npm run quality:frontend

typecheck:
	uv run mypy src

quality:
	npm run quality:check

benchmark:
	uv run lifescape benchmark --output-dir outputs/benchmark

app:
	uv run lifescape app

# Pack the Claude Desktop extension and the skill ZIP; .mcpbignore keeps local files out.
mcpb:
	mkdir -p dist
	rm -f dist/lifescape.mcpb dist/lifescape-skill.zip
	npx --yes @anthropic-ai/mcpb@2.1.2 pack . dist/lifescape.mcpb
	cd skills && zip -qr ../dist/lifescape-skill.zip lifescape -x '*/__pycache__/*' '*/.DS_Store'
