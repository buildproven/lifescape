# Contributing to Lifescape

Thanks for helping. Lifescape finds U.S. places from towns you like and tests finalists against
reviewed evidence. It runs on your own computer and sends nothing about your profile anywhere.

## Ground rules

- The [place-discovery PRD](docs/prd/lifescape-place-discovery.md) is the product source of truth.
  Every change should cite a PRD goal, requirement, or acceptance criterion. A new scored quality
  or a new feature needs a PRD amendment first, so open an issue before you write code.
- Keep the governing rule: preferences and examples discover; gates eliminate; weights rank;
  evidence decides; uncertainty stays visible.
- Never invent or impute a missing value. Discovery data cannot clear a gate or change an
  evidence-backed score.
- Benchmark data is synthetic and must never be presented as real evidence.

## Set up

You need Python 3.12 and [uv](https://docs.astral.sh/uv/), plus Node.js (see `.nvmrc`).

```bash
make install     # uv sync, Playwright Chromium, npm ci
make app         # run the app on http://127.0.0.1:8765 (uv run lifescape app)
```

## Before you open a pull request

```bash
make lint        # ruff format + check, prettier, eslint, stylelint
make typecheck   # mypy --strict
make test        # pytest
make quality     # full gate: formatters, linters, mypy, pytest with the 90% coverage floor, build
```

Run `tests/test_user_journey.py` on its own; concurrent Playwright runs time out. Add a test that
fails without your change. Use `feat:`, `fix:`, `docs:`, or `chore:` commit messages
(commitlint checks them). Methodology changes need tests for gates, normalization, scoring,
sensitivity, source policy, and missing-data behavior.

## Reporting problems

Open a GitHub issue with steps to reproduce. For a security problem, do not open a public issue;
follow [SECURITY.md](SECURITY.md). Never include credentials or personal data.
