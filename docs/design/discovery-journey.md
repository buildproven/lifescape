# Design: discovery journey

PRD: `docs/prd/lifescape-place-discovery.md` (FR1–FR16). Contract: `docs/decisions/ADR-place-discovery-contract.md`.

## Stages

| Stage | User question | Element | PRD |
|---|---|---|---|
| Start | Where might I want to live? | Example-town lookup (max two), **Try it with Traverse City** shortcut, four one-tap styles, collapsed *Fine-tune* with six quality rows, live readiness line | FR1, FR2, FR2b, FR4, FR5 |
| Limits | What must be true? | Min/max limits per quality, region filter, skipped states | FR3 |
| Matches | Which towns deserve a look? | Ten cards: reasons, biggest trade-off, unknowns, Keep / Not for me / Unsure, **Why this place?** | FR7, FR8, FR10, FR11 |
| Shortlist | What do I keep? | Kept and unsure towns, manual add, export, start over | FR12 |
| Verify | Do the finalists meet my requirements? | Per-town evidence state first, research checklist download, collapsed comparison settings, run | FR13, FR14, FR15 |

**Find places** is the primary action on the first screen (FR1). Limits and fine-tuning are optional.

## Ease of use

A first-time visitor reaches ten matches in one click (**Try it**) or two (a style, then **Find places**). The first screen shows its main choices without scrolling at 390 px. Technical controls (six quality rows, budget, age, household) are collapsed until wanted. The disabled-button reason is shown beside the inputs. Reports save to `~/Lifescape`. Install is one `uvx` command.

## State model

One scenario object in `localStorage` key `lifescape.scenario`, `schema_version: 1`:
`profile` (examples with their catalog values, custom targets, importance, limits, regions,
skipped states), `result` (the complete API response snapshot, including catalog, algorithm and
normalization versions), `decisions`, `shortlist` (each kept recommendation is stored with its full
snapshot so it stays explainable after a rerun or a catalog change), `previous_ranks`.

Failure paths (all implemented in `scenario.js`, none partly load): unparseable JSON, missing or
mistyped fields, bad enum values, or an unsupported `schema_version` copy the raw text to
`lifescape.scenario.backup`, show a banner with **Download backup JSON** and **Start fresh**, and
load an empty scenario. A matching-schema snapshot from an older catalog loads unchanged and shows
**Search again**; it is never rescored. Blocked storage shows a "Not saved" banner and the journey
continues in memory.

## Why-this-place and trust labels

Every value is labelled **Discovery data** or **Missing**, and every row carries **not verified
evidence**. Clipped values show the raw value, the catalog range, and that edge similarity is not
an exact match. Unknown limit values show **Needs verification**. Reasons are fixed templates over
component data; they cannot add a fact.

## Evidence handoff

The Verify stage matches each shortlist town to an evidence place by folded name and state. A town
without evidence shows every metric as Missing, with critical metrics tagged. **Run comparison** is
enabled only when at least two towns have reviewed evidence (FR13, FR14). Imported CSV towns can be
added directly (FR15). Synthetic evidence keeps its warning in Verify and Results.

## Accessibility

Native controls with labels; status regions announce search results, counts and hints; stage
changes move focus to the step heading; decision buttons use `aria-pressed` and keep focus; the
**Why this place?** toggle uses `aria-expanded`; layouts reflow without horizontal scroll from 320 px.
