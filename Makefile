.PHONY: install test lint typecheck quality benchmark app

install:
	python -m pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy src

quality: lint typecheck test

benchmark:
	retire benchmark --output-dir outputs/benchmark

app:
	retire app
