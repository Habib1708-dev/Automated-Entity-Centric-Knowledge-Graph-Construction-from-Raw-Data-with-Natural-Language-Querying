# Adversarial review: furniture_qa_sql.json (FQ01, FQ02)

Reviewer: Claude Fable 5.1, 2026-10-14. Read only `data/*.csv`, the question file,
`r185/furniture/earlier_questions.json` and `tests/gold/qa/furniture_qa*.json`. Queries were run
through `kgbuilder.validation.qa_records.connect(Path('data'))`, as the checker does.

## Data shape checked first (applies to both questions)

- `part_supplier_mapping.csv`: 176 rows, 88 distinct part ids, 41 distinct part names, 16 distinct
  supplier ids. Every part id has exactly 2 rows, exactly 1 of them preferred (`preferred_supplier`
  loads as BOOLEAN: 88 true, 88 false). No duplicate rows. Every mapping supplier id exists in
  `suppliers.csv`; every mapping part id exists in `components.csv` and vice versa.
- `unit_cost` is text: always `$` + number, but 15 of the 176 values have one decimal
  (`$9.4`, `$14.0`, `$37.5` ...). This matters for FQ01 (below).
- `supplier_name` in the mapping is `nordic Wood Industries` (lower-case n) for SUP-001; the other 15
  names match `suppliers.csv` exactly. Nordic is not in either answer, so no alias is needed.

## FQ01 (count of parts whose preferred offer is dearer)

Checked:
- Re-derived independently with a per-part pivot (`max(CASE WHEN preferred ...)` vs
  `max(CASE WHEN NOT preferred ...)`, numeric cast via `substr(unit_cost, 2)`), no self-join:
  dearer 42, cheaper 46, equal 0, over 88 parts. Matches `expected.number = 42`.
- Stored `sql` returns `[(42,)]`.
- Every example in the note checked against the rows: S-1074 ($42.73 vs $34.32, counts),
  S-1114 ($23.78 vs $3.16, counts), S-1079 ($2.22 vs $39.15, does not), S-1082 ($0.57 vs $10.57,
  does not). Linköping Bed slats: 15 Wooden Slat part ids (S-1124 in slats_subassembly, S-1134 ..
  S-1160 in bed_slat_1 .. bed_slat_14), 7 of them count; 15 Fabric Sleeve ids (all in the
  Linköping bed assembly), 10 count. All correct.
- Alternative readings: (a) count by distinct part name instead of part id gives 24 of 41 names,
  a real risk because "parts" is ambiguous in a table where the same name recurs 15 times;
  (b) "higher" vs "at least as high": no equal pairs, so no difference; (c) "the other supplier"
  presumes exactly two offers per part, which holds for all 88 ids; (d) comparing `unit_cost` as
  raw text instead of numbers.
- Novelty: FB25 asks the same comparison for the Malmö Desk drawer unit only (a list of parts);
  F48 compares lead times for one component. A whole-table count is new in meaning.

Changed:
1. Question tightened to `Counting each part id separately, for how many parts ...` so reading (a)
   no longer fits. The gold answer and sql are unchanged.
2. Note corrected. The original note said "comparing the strings would still work here" — this is
   FALSE: lexicographic comparison of `$6.35` vs `$36.78` misorders 23 of the 88 pairs and the
   string self-join returns 39, not 42. The note now says so, names two misordered pairs
   (S-1083, S-1095), mentions the one-decimal values, and gives the by-name figure (24 of 41).

Doubts left standing: none on the answer. The question is a plain-English rendering of a strict
greater-than; "unit cost" is unambiguous (one column).

## FQ02 (suppliers that offer parts but are never preferred)

Checked:
- Re-derived independently with `EXCEPT` (supplier ids in the mapping minus ids with a preferred
  row), joined to `suppliers.name`: exactly the 8 names in `expected`, in a different order (sets,
  so fine). Stored `sql` returns the same 8.
- Trap check: the plain anti-join over all 20 suppliers returns 12 (the 8 plus SUP-007, SUP-010,
  SUP-017, SUP-020, which have no mapping rows). The question's "offer at least one part" excludes
  them unambiguously.
- Per-supplier offer counts in the note verified: Canadian Timber 32, Italian Textile 22, Korean
  Metal 15, Taiwan Precision 6, Mexican Hardware 6, UK Foam 4, Singapore Adhesive 2, Spanish Glass 1
  (S-1132 Diffuser, non-preferred, $43.01 vs Sydney Glass Works $6.63). The 8 preferred-somewhere
  suppliers listed in the note are exactly the other 8 mapping suppliers.
- `records`: the 8 `suppliers.csv` rows, `supplier_id` and `name` cells match the file byte for byte.
- Alternative readings: (a) "suppliers" as the 20 in `suppliers.csv` vs the 16 in the mapping: same
  answer, because "offer at least one part" restricts to the mapping; (b) "preferred supplier for
  any part" read per part name instead of per part id: same answer, since preferred status is
  decided per row and these 8 have no `yes` row at all; (c) names: the mapping's `supplier_name`
  spells all 8 exactly as `suppliers.csv` does, so no alias is needed.
- Novelty: F58 asks how many suppliers supply no parts (the 4 excluded here); F55 asks who is
  preferred most often. Neither asks for the never-preferred-but-offering set. New in meaning.

Changed: nothing. Question, expected, records, sql and note all hold.

Doubts left standing: none.
