# Lifescape user guide

Lifescape helps you find U.S. towns you might want to live in, keep a shortlist, and then test the
finalists against evidence you trust. It runs on your computer. Your searches never leave it.

## Before you start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once, then run:

```bash
uvx --from git+https://github.com/buildproven/lifescape lifescape app
```

A browser tab opens at `http://127.0.0.1:8765`. Use `--no-open` to skip the tab, `--port` to change
the port, and `--output-dir` to choose where reports are saved (default: a `Lifescape` folder in
your home directory).

## Five minutes to a shortlist

1. **Start.** The fastest way: tap **Try it with Traverse City, MI**. Otherwise type a town you
   like, or tap a style (**Affordable and quiet**, **Lively and walkable**, **College town**,
   **Retiree-friendly**). You can use a town and a style together. Towns under 2,500 people cannot
   be examples, but you can add them to your shortlist later.
2. **Find places.** You get ten towns. Each card shows why it appeared, its biggest trade-off, and
   any missing data. Open **Why this place?** for the full comparison.
3. **Optional: fine-tune or set limits.** *Fine-tune what matters* lets you choose a level for any
   quality and how much it matters. **Set limits first** caps home value, limits population, or
   restricts regions. A limit removes a town only when we know its value fails. Unknown values stay
   and are marked **Needs verification**.
4. **Decide.** Tap **Keep**, **Not for me**, or **Unsure**. Search again and the towns you rejected
   are replaced; cards say whether a town moved and why.
5. **Review your shortlist.** Keep at least three. A **Side by side** table compares kept
   towns on every quality. Add any town by hand. **Print or save as PDF** makes a paper copy;
   **Export search as JSON** saves a backup; **Start over** clears this browser.

## What the match percentage means

It measures how closely a town's public Census numbers resemble your examples and targets. It is
not a quality score and not a prediction that you will like the town. It ignores anything we do
not have data for, and missing data lowers a town's score instead of helping it.

## Verifying finalists

Open **Verify** with at least two kept towns. For each town you see which evidence metrics have been
provided and which critical ones are missing. Discovery data never fills them in. If you have no
evidence yet, **Download research checklist** gives you a Markdown list of your finalists, the critical facts to confirm for each, and links to
official sources (Census, Medicare Care Compare, FCC, FEMA, NOAA). The links are places to look,
not verified evidence, and the FCC and FEMA maps need an address.

- The app ships with a **synthetic** demo evidence set so you can see how verification works.
  Towns that share a name with a demo town, such as Williamsburg, VA, match it. Results from
  synthetic data are test output and are labelled that way.
- For real work, choose **Advanced evidence import** and upload your reviewed evidence CSV. The
  column layout is `data/benchmarks/evidence.csv` in the repository. Every row needs its source, date, and
  confidence. See [source policy](source-policy.md).
- **Run comparison** turns on only when at least two towns have evidence. Towns missing a critical
  value are shown as blocked, never guessed.

## If something goes wrong

| You see | Meaning | Do this |
|---|---|---|
| "Saved search could not be opened" | Your saved search is damaged or from a newer version | Choose **Download backup JSON** to keep it, then **Start fresh** |
| "Older results" on Matches | Results came from an older place catalog | They stay readable; choose **Search again** to refresh |
| "Not saved" | Your browser blocks local storage | The search works until you reload; use **Export search as JSON** |
| "Discovery is unavailable" | The place catalog failed its integrity check | Reinstall Lifescape; Advanced evidence import still works |

## Limits you should know

Matches use six Census-derived numbers, not climate, healthcare, or walkability. See
[known limitations](limitations.md).
