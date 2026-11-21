# Lifescape

## Use Lifescape in Claude

Lifescape is free for everyone under the [MIT License](LICENSE), with no paid tier. It runs on
your computer: you need only your own Claude account, and your searches, evidence, and reports stay
on your machine. Claude gets a skill with the decision workflow (discover towns, build a shortlist,
gather evidence, approve each value yourself, and compare finalists) and a local MCP server with the
Lifescape tools. The engines find, gate, and rank; Claude only explains the tool results.

### Install in Claude Desktop

1. Download the two files from the latest GitHub release:
   - the desktop extension,
     [`lifescape.mcpb`](https://github.com/buildproven/lifescape/releases/latest/download/lifescape.mcpb)
   - the skill ZIP,
     [`lifescape-skill.zip`](https://github.com/buildproven/lifescape/releases/latest/download/lifescape-skill.zip)
2. **Extension:** in Claude Desktop, go to Settings > Desktop app, select "Manage" next to
   Extensions (older layout: Settings > Extensions), open "Advanced settings", click "Install
   Extension…", and select `lifescape.mcpb`. The extension runs `lifescape mcp` (the Python stdio
   server) on the `uv` runtime: Claude Desktop manages Python and installs the dependencies pinned
   in `uv.lock`. Reports go to `~/Lifescape/mcp`.
3. **Skill:** in Claude, go to Customize > Skills, add a skill, choose "Upload a skill", and upload
   `lifescape-skill.zip`.

**Claude Code:** add the server straight from GitHub, and unzip `lifescape-skill.zip` into
`~/.claude/skills` (it holds one `lifescape` folder):

```bash
claude mcp add --transport stdio --scope user lifescape \
  -- uvx --from git+https://github.com/buildproven/lifescape lifescape mcp
```

The tools are `find_place`, `discover_towns`, `explain_town`, `inspect_evidence`,
`fetch_live_evidence`, `approve_evidence`, `reject_evidence`, `compare_finalists`, and
`export_report`. Fetched values stay proposals until you approve or reject each one by name in
chat; the server refuses an AI or model name as the reviewer.

### Local web app

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, open the guided
browser workspace at `http://127.0.0.1:8765`, with no clone or build:

```bash
uvx --from git+https://github.com/buildproven/lifescape lifescape app
```

See [Web app quick start](#web-app-quick-start) for the journey. The web UI is frozen: it gets
fixes but no new features.

### Command line

The same package gives the `lifescape` CLI. Run the synthetic ten-town benchmark, or compare your
own reviewed evidence with `lifescape run` (see [Evidence contract](#evidence-contract)):

```bash
uvx --from git+https://github.com/buildproven/lifescape lifescape benchmark --output-dir benchmark
uvx --from git+https://github.com/buildproven/lifescape lifescape run --help
```

## Hosted server (the author's instance)

`https://lifescape.buildproven.ai/api/mcp` is the author's own instance. It serves the same tools
over Streamable HTTP for Claude on the web and mobile, but only the owner can sign in: Claude signs
in through GitHub, and only the GitHub login in `OWNER_GITHUB_LOGIN` gets a token. The plugin
marketplace that installs it is private too. To use Lifescape from claude.ai on the web or mobile,
[deploy your own](#deploy-your-own) instance.

The repository root is the plugin: `.claude-plugin/plugin.json` names it, `.mcp.json` points at
the hosted server, and `skills/lifescape` is the skill. Check it with `claude plugin validate .`.
Point `.mcp.json` at your own deployment before you install it from your own marketplace.

[`/privacy`](https://lifescape.buildproven.ai/privacy) states what the hosted server stores (the
research packets and report indexes in the owner's database), that only the owner's GitHub
account can sign in, and how to delete the data. The plugin manifest names it as
`privacyPolicyUrl`.

### Deploy your own

Deploy the repository to Vercel (`api/index.py` is the entry). You need a Postgres database (the
Neon integration sets `DATABASE_URL`) and a GitHub OAuth app whose callback is
`<origin>/oauth/github/callback`. `.env.example` lists every variable; the hosted server needs
these:

| Variable                               | Holds                                                               |
| -------------------------------------- | ------------------------------------------------------------------- |
| `OWNER_GITHUB_LOGIN`                   | your GitHub login, the only account that can sign in                |
| `LIFESCAPE_GITHUB_OAUTH_CLIENT_ID`     | the GitHub OAuth app (callback `<origin>/oauth/github/callback`)    |
| `LIFESCAPE_GITHUB_OAUTH_CLIENT_SECRET` | that app's client secret                                            |
| `LIFESCAPE_OAUTH_SIGNING_KEY`          | at least 32 characters, such as `openssl rand -base64 48`           |
| `LIFESCAPE_STORE`                      | `database`                                                          |
| `DATABASE_URL`                         | the Postgres URL (the Neon integration sets it); psycopg 3 connects |

If one is missing or unusable, `/api/mcp` answers 503 with the names to set or fix (never the
values), and the landing, demo, and privacy pages still serve.

## What Lifescape does

Lifescape helps a U.S. household find places where they might want to live. Start with one or two
towns you like or supported qualities you want. Lifescape finds explainable town matches, helps
you shape a shortlist, and then uses verified evidence to test the finalists against hard
requirements and household priorities.

The governing rule is: **preferences and examples discover; gates eliminate; weights rank;
evidence decides; uncertainty stays visible.** Discovery data can suggest a town but cannot clear
a gate or affect an evidence-backed score. Unknown critical evidence blocks a finalist. Lifescape
never guesses a missing value or produces a purchase recommendation.

See it first: a [40-second tour of the app](https://lifescape.buildproven.ai/#tour) and a
[finished synthetic decision](https://lifescape.buildproven.ai/demo). The public site is static and
runs no app code; the app itself runs only on your computer.

The approved [place-discovery PRD](docs/prd/lifescape-place-discovery.md) is the product source of
truth. Product work must cite its goal, functional requirement, non-functional requirement, or
acceptance criterion. The [delivery tasks](docs/prd/lifescape-place-discovery-tasks.md) define the
current vertical implementation sequence.

## Web app quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (one command to install:
`curl -LsSf https://astral.sh/uv/install.sh | sh`). Then run Lifescape straight from GitHub, with
no clone or build:

```bash
uvx --from git+https://github.com/buildproven/lifescape lifescape app
```

Your browser opens a private workspace at `http://127.0.0.1:8765`. Reports you generate are saved
in a `Lifescape` folder in your home directory (`--output-dir` changes it). Nothing leaves your
computer: the place catalog is packaged with the app, searches run locally, and your shortlist is
saved only in your browser. See the [user guide](docs/user-guide.md) for a five-minute walkthrough.

To keep it installed instead: `uv tool install git+https://github.com/buildproven/lifescape`, then
run `lifescape app`.

The journey is:

1. **Start.** Type a town you like, tap **Try it with Traverse City, MI**, or pick a style such as
   "Lively and walkable". Fine-tune is optional.
2. **Limits** (optional). Set a price ceiling, regions to avoid, or other limits.
3. **Matches.** Read ten explainable town matches with their reasons, biggest trade-off, and
   unknowns. Mark each Keep, Not for me, or Unsure.
4. **Shortlist.** Keep at least three towns, add any town by hand, and export your search.
5. **Verify.** Move finalists into evidence review. The comparison runs only when reviewed
   evidence exists for at least two towns, and every missing critical value stays visible.
6. Download the Markdown report, ranking CSV, sensitivity CSV, and SQLite provenance database.

The bundled evidence is synthetic and exists only to demonstrate and test the method. It must not
be used as retirement research. Hosted mode likewise accepts no inputs and shows only a finished
synthetic example.

### Develop from a checkout

```bash
uv sync --locked --extra dev --python 3.12
uv run playwright install chromium
npm ci          # Node.js 24.18.x
uv run lifescape app
```

### Build the desktop packages

Each release attaches `lifescape.mcpb` and `lifescape-skill.zip`. To build them yourself from a
checkout (needs Node.js and `zip`), run the command below. It installs the `@anthropic-ai/mcpb`
version pinned in `package.json` when needed; `npm run mcpb` builds just the extension when the
dependencies are already installed:

```bash
make mcpb
```

This writes `dist/lifescape.mcpb` (the desktop extension, packed from `manifest.json`) and
`dist/lifescape-skill.zip` (the skill ZIP), and replaces any earlier copies. `.mcpbignore` keeps
the local `.venv`, `node_modules`, `outputs`, `.git`, and caches out of the extension, so you can
pack from a working checkout.

## What discovery uses

Discovery runs offline against a catalog of 32,041 U.S. places built from the 2024 Census Gazetteer
and ACS 2020–2024 five-year files. It compares fifteen qualities: population, median home value,
population density, car-light commute share, college-educated share, older-adult share, four
NOAA 1991–2020 climate normals (freezing nights, 90°F+ days, precipitation, snowfall), and four
place-character qualities (elevation, distance to an ocean or Great Lake, distance to a large
metro, large-metro suburb), and straight-line miles to the nearest CMS emergency-capable hospital
(not drive time). It
recommends from the 10,215 places with 2,500 or more people. `scripts/build_place_catalog.py`
rebuilds the catalog from pinned official files and a manifest of hashes. The Census Geocoder
response that locates the hospitals is committed in `data/place-catalog/`, because the geocoder
can match the same addresses differently on another day; only `--refresh-geocoder` asks Census
again. Nature and
walkability are not claimed, and hospital distance never decides a gate. See [known limitations](docs/limitations.md).

## Traceability

Every requirement in the PRD traces to design, code, and tests in
[`docs/traceability.md`](docs/traceability.md); `tests/test_traceability.py` fails when any link
breaks. Product changes cite the PRD in their PR trace.

## Evidence contract

Use `data/benchmarks/evidence.csv` as the column contract, not as real evidence. Identity and
source columns precede one column per configured metric. Blank cells remain missing. Every real
row must retain its source, geography, retrieval date, observation period, confidence, and
synthetic status.

For real comparisons, use configuration whose `research_brief.yaml` sets
`benchmark_only: false`:

```bash
lifescape run \
  --evidence path/to/reviewed-evidence.csv \
  --profile path/to/user-profile.yaml \
  --config-dir path/to/config \
  --database outputs/run.sqlite \
  --output-dir outputs/run
```

The app and CLI call the same `execute_run` authority. Tier C material cannot affect a gate or
score. Failed and unknown critical gates stay visible and unranked.

## Use it from Claude Desktop

`lifescape mcp` serves the engine as a local MCP (Model Context Protocol) server over stdio. Add it
to Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lifescape": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/buildproven/lifescape", "lifescape", "mcp"]
    }
  }
}
```

| Tool                  | Does                                                                                         |
| --------------------- | -------------------------------------------------------------------------------------------- |
| `find_place`          | Looks up towns by name in the packaged catalog and returns their place IDs.                  |
| `discover_towns`      | Finds towns like up to two example towns, with priorities, limits, and state filters.        |
| `compare_finalists`   | Runs two or more finalists from reviewed evidence through gates, ranking, and sensitivity.   |
| `explain_town`        | Returns one town's research card: unresolved critical evidence and the next action for each. |
| `inspect_evidence`    | Checks evidence: validation errors, and each town's audit status per metric.                 |
| `fetch_live_evidence` | Fetches Census ACS and NOAA values for towns into a research packet, all unapproved.         |
| `approve_evidence`    | Promotes one fetched value after the owner confirms it; the reviewer must be a named human.  |
| `reject_evidence`     | Records the owner's rejection of one fetched value with a reason; approval is then refused.  |
| `export_report`       | Returns the Markdown and CSV report text of a `compare_finalists` run, and the report paths. |

Discovery results are leads, not evidence. `compare_finalists` and `explain_town` use the live
research brief (`benchmark_only: false`), compare only the named place IDs, and write reports under
`~/Lifescape/mcp` (`--output-dir` changes it). A finalist with failed or unknown critical evidence
is returned as `blocked` and never ranked. Every input schema rejects unknown arguments.

Evidence approval stays a human act. `fetch_live_evidence` returns proposals with their sources and
stores them in a research packet under `research-workspace/`. `approve_evidence` is annotated as
destructive, so Claude Desktop asks the owner before each call. It refuses an empty reviewer or one
named `ai`, `claude`, `codex`, or `model`, and then promotes nothing. `reject_evidence` is also
annotated as destructive. It records the named owner's rejection and reason (at least eight
characters) in the packet, refuses the same reviewers, and `approve_evidence` then refuses that
value. Census ACS geography comes
from the place ID; NOAA needs a station-year that the operator sets per place ID in
`LIFESCAPE_RESEARCH_GEOGRAPHIES` (for example `{"3746340":{"noaa_gsoy":"USW00013754:2024"}}`).
Without one the fetch lists snowfall as missing instead of choosing a station. `export_report` reads the report text from the store, so the hosted server returns it from any
instance; the local server also checks the report files and the SQLite run database, and refuses a
run whose reports a later run overwrote. A run saved before report text was kept must be compared
again.

`compare_finalists`, `explain_town`, and `inspect_evidence` take exactly one evidence input:
`packet_id`, a research packet whose approved values they export with the web app's evidence
export (`require_complete_packet_evidence`, then `export_approved_evidence`), or `evidence_path`, a
local evidence CSV. A packet is read only when every candidate has approved critical evidence. The
hosted server (`LIFESCAPE_STORE=database`) has no owner files, so it refuses `evidence_path`; pass
`packet_id` there. The local store accepts both.

## Verification

```bash
uv run lifescape benchmark --output-dir outputs/benchmark
npm run quality:check
npm run security:check
uv run python scripts/build_place_catalog.py --verify   # rebuild the catalog from Census files
```

`--verify` sends nothing to the Census Geocoder: it reads the committed response, fails when that
file's SHA-256 is not the one the manifest records, and fails unless the rebuilt catalog and
manifest match the committed files byte for byte.

The benchmark covers ten synthetic towns and must produce repeatable artifacts. Quality includes
Python tests and coverage, Ruff, mypy, browser journeys, package construction, frontend linting,
dependency audits, and secret scanning.

## Experimental research tools

The repository retains research-packet APIs, AI discovery, ACS and NOAA connectors, evidence
review and promotion, provenance auditing, and conditional research reports for continued
experimentation. They are not part of the supported version-one journey and are not required for
version-one completion.

Commercial routing, FCC broadband aggregation, property and parcel providers, neighborhood
verification, hosted accounts, remote persistence, and purchase recommendations are deferred.
Their absence does not weaken the version-one contract because reviewed evidence enters through
the same strict CSV boundary and missing critical evidence continues to block.

See the [place-discovery contract](docs/decisions/ADR-place-discovery-contract.md), the
[superseded CSV-only boundary](docs/decisions/ADR-v1-product-boundary.md),
[local-app specification](docs/local-app-spec.md), [source policy](docs/source-policy.md), and
[known limitations](docs/limitations.md).

## Contributing and license

Lifescape is open source under the [MIT License](LICENSE). Read [CONTRIBUTING.md](CONTRIBUTING.md)
before opening a pull request, and report security problems privately as described in
[SECURITY.md](SECURITY.md). Everyone taking part follows the
[Code of Conduct](CODE_OF_CONDUCT.md).
