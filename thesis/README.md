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
latexmk -xelatex main.tex
```

The body font is Calibri, so the thesis compiles with XeLaTeX (or LuaLaTeX), not pdfLaTeX. Where Calibri is
not installed, the preamble falls back to Carlito, which has the same metrics.

**Without a TeX installation** (how the drafts are checked; nothing is installed system-wide): two portable
programs in any folder, neither added to the system PATH.
- Tectonic 0.15.0 (`tectonic-0.15.0-x86_64-pc-windows-msvc.zip` from the project's GitHub releases), a
  XeTeX-based engine that downloads LaTeX packages on first use and reruns TeX and biber as needed.
- biber **2.17** (`biber-MSWIN64.zip` from SourceForge, `biblatex-biber/2.17`). The version must match
  Tectonic's biblatex 3.17; biber 2.19 refuses it.

```
PATH="/path/to/biber217:$PATH" /path/to/tectonic.exe --keep-logs --outdir build main.tex
```

In Git Bash, write the biber folder as `/c/...`: a `C:` in PATH is read as a separator.

## Rules

- Every number in the text has a row in `evidence_registry.csv`, names its score (exact match or by
  meaning) and its n.
- The Lighthouses dataset is not reported; Chapter 3 states that a fifth dataset was built, failed the hard
  audit gate C4, and was dropped.
- Chapters are written one stage at a time and approved by the author before the next stage starts.
