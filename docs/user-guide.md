# Lifescape user guide

Lifescape helps you find U.S. towns you might want to live in, keep a shortlist, and then test the
finalists against evidence you trust. It runs on your computer. Your searches never leave it.

## Before you start

```bash
uv tool install dist/lifescape-0.1.0-py3-none-any.whl
lifescape app
```

A browser tab opens at `http://127.0.0.1:8765`. Use `lifescape app --no-open` to skip the tab and
`--port` to change the port.

## Five minutes to a shortlist

1. **Pick a town you like.** Type a town in *Towns you like*, for example `Traverse City, MI`, and
   choose **Use as example**. Add a second if you have one. Towns under 2,500 people cannot be
   examples, but you can add them to your shortlist later.
2. **Choose what matters.** Each quality starts as "Like my example". Change it to a value you
   choose, and use *How much it matters* to weigh it. The page tells you when you have enough to
   search.
3. **Optional: set boundaries.** Choose **Set boundaries first** to cap home value, limit population,
   or restrict regions. A limit removes a town only when we know its value fails. Unknown values
   stay and are marked **Needs verification**.
4. **Find places.** You get ten towns. Each card shows why it appeared, its biggest trade-off, and
   any missing data. Open **Why this place?** for the full comparison with your examples.
5. **Decide.** Mark towns **Keep**, **Not for me**, or **Unsure**. Change your search and run it
   again: towns you rejected are left out, and cards say whether a town moved and why.
6. **Review your shortlist.** Keep at least three. Add any town by hand. **Export search as JSON**
   saves a copy; **Start over** clears this browser.

## What the match percentage means

It measures how closely a town's public Census numbers resemble your examples and targets. It is
not a quality score and not a prediction that you will like the town. It ignores anything we do
not have data for, and missing data lowers a town's score instead of helping it.

## Verifying finalists

Open **Verify** with at least two kept towns. For each town you see which evidence metrics have been
provided and which critical ones are missing. Discovery data never fills them in.

- The app ships with a **synthetic** demo evidence set so you can see how verification works.
  Towns that share a name with a demo town, such as Williamsburg, VA, match it. Results from
  synthetic data are test output and are labelled that way.
- For real work, choose **Advanced evidence import** and upload your reviewed evidence CSV. The
  column layout is `data/benchmarks/evidence.csv`. Every row needs its source, date, and
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
