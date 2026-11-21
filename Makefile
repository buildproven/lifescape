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

# Pack the Claude Desktop extension (the mcpb devDependency, pinned in package.json) and the
# skill ZIP; .mcpbignore keeps local files out. A clean checkout installs the locked dependencies.
mcpb:
	mkdir -p dist
	rm -f dist/lifescape.mcpb dist/lifescape-skill.zip
	if [ ! -x node_modules/.bin/mcpb ] \
		|| [ package.json -nt node_modules/.bin/mcpb ] \
		|| [ package-lock.json -nt node_modules/.bin/mcpb ]; then \
		npm ci --ignore-scripts --no-audit --no-fund; \
	fi
	npm run --silent mcpb
	cd skills && zip -qr ../dist/lifescape-skill.zip lifescape -x '*/__pycache__/*' '*/.DS_Store'
