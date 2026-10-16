# Thesis sources

LaTeX sources of the master's thesis *Automated Knowledge Graph Constructor With Natural Language Querying
Engine* (Habib Khalaf, Lebanese University, Faculty of Science, Branch I).

This folder holds only the thesis text, its tables and its evidence registry. It must not hold code that the
pipeline imports, and it must not hold figure-drawing code: figures are drawn by `figures/scripts/` from
committed `tests/gold/` records and are only included here.

## Layout

| Path | What it holds |
|---|---|
| `main.tex` | document root: preamble, front matter, chapters, back matter |
| `preamble.tex` | packages, page layout, macros |
| `frontmatter/` | title page, declaration, acknowledgments, abstract, abbreviations |
| `chapters/ch01_*.tex` … `ch09_*.tex` | the nine chapters of the main body (the only pages counted) |
| `appendices/` | appendices A–G |
| `refs.bib` | references; every entry is verified before it is cited (see `refs_check.md`) |
| `refs_check.md` | one line per reference: how and where it was verified |
| `evidence_registry.csv` | every number the thesis states, traced to its step, record file, MLflow run and commit |

## Build

```
cd thesis
latexmk -pdf main.tex
```

## Rules

- Every number in the text has a row in `evidence_registry.csv`, names its score (exact match or by
  meaning) and its n.
- The Lighthouses dataset is not reported; Chapter 3 states that a fifth dataset was built, failed the hard
  audit gate C4, and was dropped.
- Chapters are written one stage at a time and approved by the author before the next stage starts.
