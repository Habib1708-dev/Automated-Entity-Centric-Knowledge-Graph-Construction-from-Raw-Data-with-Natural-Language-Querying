# figures/: rules for Claude

This folder holds the thesis figures: charts of measured results and diagrams of methods. Every figure is
drawn by code from a committed record, captioned, and checked by a test. These rules add to the root
`CLAUDE.md`; where they are stricter, they win inside this folder.

## 1. Layout: one concern per folder

```
figures/
  CLAUDE.md                       these rules
  README.md                       the index: every figure, one line each, with its link
  scripts/                        code only, never a figure
    style.py                      the one style: palette, technique encoding, matplotlib defaults, save()
    <step>_data.py                reads a committed record into what the charts draw (pure, no drawing)
    <step>_charts.py              draws one step's result charts from <step>_data
    <steps>_diagrams.py           draws method diagrams (boxes and arrows)
    build.py                      the one entry point; a figure not built here does not exist
  results/<topic>/<step>_<subject>/   charts of measured numbers, plus README.md (the captions)
  diagrams/<topic>/<steps>/           diagrams of methods and designs, plus README.md (the captions)
```

- **`results/` vs `diagrams/`:** a chart shows numbers a run produced; a diagram shows how something works.
  Never mix them in one folder.
- **`<topic>`** is the thesis area the figure belongs to: `retrieval`, `construction`, `resolution`,
  `evaluation`, and so on. Add a topic folder only when its first figure exists. No empty folders.
- **`<step>_<subject>`** is one folder per measured result, named after the roadmap step that produced the
  numbers (`r128_techniques`). A later step with new numbers gets its own folder. It never overwrites an
  earlier step's figures, which are the record of that step.
- **Data stays out of `figures/`:** charts read the committed records in `tests/gold/<step>/`. If a chart
  needs a number that is not committed, commit the record first (as `kg retrieve-table` JSON was in R129).

## 2. Naming

- Charts: `<step>_fig<N>_<what_it_shows>.svg` and `.png`, for example
  `r128_fig1_evidence_vs_start_nodes`.
- Diagrams: `<step>_diag<N>_<what_it_shows>`, for example `r126_diag1_seed_derivation`.
- Use lowercase snake_case, and number within the folder from 1.
- The name says what the figure shows, never the chart type (`evidence_vs_start_nodes`, not `scatter`).
- The figure's own title is a sentence that states the finding ("Text retrieval finds evidence; node cards
  find start nodes"). The subtitle states the data, the n and how to read it.

## 3. Generated, never drawn by hand

- Every figure comes from `uv run python -m figures.scripts.build`. Never edit an SVG or PNG by hand, and
  never commit a figure the build does not make.
- **Data source:** only committed records (`tests/gold/...`). Never read `out/`, MLflow or a database from
  `figures/scripts/`. Never chart a `smoke` or `dev` run (root `CLAUDE.md`, evaluation rules).
- **Formats:** SVG (vector, for the thesis) and PNG at 200 dpi (preview). `style.save()` writes both,
  without dates and with LF line ends, so a rebuild of unchanged data is byte-identical.
- **Changes:** a change to a figure is a change to its script, followed by a rebuild. A changed number means
  a changed record, which is a roadmap step's job, not a figure's.

## 4. Captions: every figure is explained

Each figure folder has a `README.md` with one section per figure:

- **Title:** the figure's title.
- **Files:** the SVG and PNG, linked.
- **What it shows:** the measure, the techniques, the datasets, K.
- **How to read it:** axes, colour, shape, fill, bars and intervals.
- **Takeaway:** the finding, with counts written as k/n.
- **Source:** the committed record file and the MLflow run ids it came from.
- **Caveats:** what the figure cannot show; an exploratory analysis is labelled as exploratory.

Rules for the numbers in captions:
- A caption's numbers must be the record's numbers. The test (`tests/test_figures.py`) checks that every
  figure is captioned, that every captioned file exists, and that the data functions return the committed
  counts.
- Name the accuracy measure and its n, as the evaluation rules require. Retrieval scores are exact matches
  computed by code; a judge's score says so.

## 5. Choosing the form

The data's job picks the chart, before any colour (the `dataviz` skill, `choosing-a-form`):

| The data's job | Form | Example |
|---|---|---|
| Two measures per item (a trade-off) | scatter, 95 % Wilson error bars on both axes | fig1: evidence against start nodes |
| A rate against a budget K | lines on a log-K axis; small multiples with emphasis past 4 series | fig2: recall by K |
| A grid of items x K x dataset | heatmap, one sequential hue, the count in each cell | fig3: per-dataset heatmaps |
| Paired comparison of two systems (McNemar) | diverging bars of the discordant counts, with p | fig4: card against text |
| A share of a whole (lists too short, refusals) | lines (emphasis) or a stacked bar | fig5: short lists |
| One headline number | no chart: the caption or a table | |
| How a method or experiment works | boxes and arrows | diag1, diag2 |

## 6. Style

- Draw only with `style.py`. A technique looks the same in every figure:
  - colour = family: text retrieval blue, node cards orange, name linker aqua;
  - shape = what is searched: circle chunk, triangle claim, square template card, diamond summary,
    pentagon name linker;
  - fill = mode: filled dense, hollow lexical.
- **At most three hues** (the validated all-pairs cap). Never generate a fourth hue: use shape, fill, small
  multiples, or gray for context (the emphasis form). A new palette is run through the `dataviz` skill's
  validator first, and its result is recorded in `style.py`.
- **One y-scale per panel**, never a dual axis. Solid hairline grid, no dashed grid.
- Rates are shown as percent, with n in the axis label or panel title.
- Text in matplotlib reads a pair of `$` as maths: escape every dollar sign (`\$0.02`).
- **Look at every PNG before committing:** labels colliding, text clipped, a legend over data, a colour bar
  over a panel. The validator checks colour, not layout.

## 7. Adding a figure (the workflow)

1. The numbers exist in a committed record. If not, that is a step of its own.
2. Add a data function to `<step>_data.py`, and a test that it returns the record's counts.
3. Add the chart function to `<step>_charts.py` (or a diagram to `<steps>_diagrams.py`), with a purpose
   header and a comment saying why this form was chosen.
4. Register it in `build.py`, run the build, and look at the PNG.
5. Write its caption in the folder's `README.md`, and add one line to `figures/README.md`.
6. Run `uv run pytest` and `uv run ruff check`. The figures belong to the roadmap step that asked for them
   (the `implement-step` skill), committed with it.

## 8. Never

- Never put figures in `docs/`, which is local only and never committed.
- Never chart a run that the user has not approved for reported numbers.
- Never import `figures` from `src/kgbuilder`. The pipeline must not depend on its pictures.
