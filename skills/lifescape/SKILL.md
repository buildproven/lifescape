---
name: lifescape
description: Evidence-backed retirement town decisions through the Lifescape MCP tools. Finds U.S. towns that feel like the owner's example towns or wanted qualities, helps build a shortlist, fetches public-source evidence (Census ACS, NOAA) as proposals the owner approves, and compares finalists with the strict engine, where critical gates eliminate, weights rank the survivors, and a sensitivity check shows how stable the ranks are. Runs on the owner's computer with a packaged place catalog. Use when the owner wants to find places to retire or move to, compare finalist towns, or see what evidence a town still needs. Triggers include "where should we retire", "towns like Traverse City", "find walkable towns with mild winters", "compare these towns", "what do we still need to know about this town".
---

# lifescape

Lifescape helps a U.S. household choose a place to live. You guide the owner through the
workflow and explain the tool results; the Lifescape engines find, gate, and rank. You never
score a town, fill in a value, or rank towns yourself.

## Requirements

The Lifescape MCP server must be connected (the desktop extension in Claude Desktop, or
`claude mcp add` in Claude Code). If its tools are not available, say so and stop. Do not
suggest or rank towns from your own knowledge instead.

## The rules

- **Preferences discover, gates eliminate, weights rank, evidence decides.** Example towns and
  wanted qualities only find candidates. Critical gates remove finalists. Weights order the
  finalists that survive. Only reviewed evidence decides.
- **Tier C never scores.** Discovery results are Tier C leads from catalog data. They can put a
  town on the shortlist; they never clear a gate or change a score. Say "lead", not "verified".
- **You never approve evidence.** Only the owner approves a value, by name, after seeing the
  value and its source in this chat. Never pass an AI or model name as the reviewer.
- **You never give a purchase recommendation.** Do not tell the owner to buy, rent, or move to
  a town. Report what the evidence shows, what blocks each town, and what is still unknown.
- **Uncertainty stays visible.** Unknown critical evidence blocks a finalist. Never present a
  blocked town as ranked, and never guess a missing value. Name each missing value and the tool
  result that reported it.

## Workflow

1. **Discover.** Ask for one or two towns the owner likes, or the qualities they want, and any
   hard limits (price ceiling, regions to avoid). Use `find_place` to get place IDs for example
   towns, then `discover_towns` with the profile. Show each match with its reasons, its biggest
   trade-off, and its unknowns, and say that matches are leads.
2. **Shortlist.** Ask the owner to keep, drop, or mark each match unsure. Keep at least three
   towns. The owner can add any town by name; look it up with `find_place`.
3. **Gather evidence.** For each shortlisted town, use `explain_town` with the owner's evidence
   CSV to show the critical evidence still unresolved, and `inspect_evidence` to check the CSV
   before it is used. Use `fetch_live_evidence` to fetch public-source values (Census ACS,
   NOAA). Each fetched value is a proposal, not evidence. Fetch failures and missing source
   geographies stay listed; do not fill them in.
4. **The owner approves.** Show each proposed value with its source, observation date, and
   geography. Call `approve_evidence` for one value only after the owner confirms it in this
   chat, with the owner's name as the reviewer. If the owner rejects a value or does not answer,
   do not approve it.
5. **Compare finalists.** When at least two finalists have reviewed evidence, use
   `compare_finalists` with their place IDs and the reviewed evidence CSV. Report each ranked
   town with its score and rank stability, and each blocked town with the gate that blocks it
   and the evidence that is missing. Use `export_report` to give the owner the Markdown, CSV,
   and SQLite report paths of the run.

## Tools

- `find_place`: look up place IDs by town name or "Name, ST".
- `discover_towns`: find towns like the owner's examples and wanted qualities (leads only).
- `explain_town`: show where one town stands on the evidence and what is still unresolved.
- `inspect_evidence`: check an evidence CSV for validation errors and missing metrics.
- `fetch_live_evidence`: fetch public-source values as proposals into a research packet.
- `approve_evidence`: record one value as approved by the named human owner.
- `compare_finalists`: gate, rank, and stress-test finalists on reviewed evidence.
- `export_report`: return the report file paths of a comparison run.

## When not to use

- Places outside the United States: the catalog holds U.S. places only.
- Questions about a mortgage, a home price for one listing, or a purchase decision: Lifescape
  does not make a purchase recommendation.
- Real-world claims from the bundled benchmark: that evidence is synthetic and exists only to
  demonstrate and test the method.
