# R157: the benchmark, the frozen agent on 250 questions written before any output

Charts of step R157 (plan R152-R157, put in thesis form by R158-R159): the agentic query engine as frozen at
tag `query-engine-r151` (answering with gemini-3.8-flash), run once (R156) on 50 new gold questions per
dataset, five datasets: two new ones fetched for the benchmark (NTSB aviation accidents, Trinity House
lighthouses) and the three thesis datasets (furniture, held-out NHTSA, generality). Every answer is scored
twice: exact match (computed by code, `validation/qa.py`, except for the 23 free-text answers, which have no
exact form and whose exact-match outcome is the judges' verdict, R157's `*_bench_agent_verdicts.json`), and
by meaning, by two blind judges (Claude Opus 5.5, R144's procedure; they agreed on all 250, so the lead judge
decided nothing).

## Fig. 1: The frozen agent answers 243 of 250 unseen questions right by meaning, as well on the new datasets as on the thesis ones

**Files:** [r157_fig1_accuracy_by_dataset.svg](r157_fig1_accuracy_by_dataset.svg), [.png](r157_fig1_accuracy_by_dataset.png)

**What it shows:** accuracy per dataset and pooled, by both scores, as percent with 95 % Wilson intervals;
n = 50 per dataset, 250 pooled. The exact-match score has two parts: code scored 227 answers (216/227 right,
R156's `exact_code_scored`), and the 23 free-text answers were judged (23/23 right); together 239/250.

**How to read it:** one row per dataset; in each row the blue square is exact match (by code, free text by
the judges' verdict) and the orange circle the score by meaning (judges), each with its interval as a line. The counts at the right are k/n,
exact first. The gray band holds the two new datasets; the pooled row is set apart below the line. The axis
starts at 75 % (every interval's low end lies above it).

**Takeaway:** pooled, 239/250 by exact match (95.6 %, 92.3-97.5) and 243/250 by meaning (97.2 %,
94.3-98.6). By meaning: NTSB 50/50, lighthouses 48/50, furniture 46/50, held-out 49/50, generality 50/50;
by exact match 50/50, 46/50, 46/50, 49/50, 48/50. The new datasets give 98/100 by meaning, the thesis
datasets 145/150: no sign that the engine was fitted to the data it was developed on. Furniture, the tuning
set, is the lowest by meaning (46/50, 81.2-96.8); by exact match it ties with lighthouses (46/50 each).

**Source:** `tests/gold/r157/outcomes.json` (the R158 record of R157's verdicts,
`tests/gold/r157/<dataset>_bench_agent_{meaning,verdicts}.json`); the answers from the R156 runs in
`tests/gold/r156/runs.json` (MLflow `fbf2339f` NTSB, `975671c2` lighthouses, `fb74f46a` furniture, `ab12ca9d`
held-out, `61432a24` generality; $3.870), on the graphs of R155b (`tests/gold/r155/runs.json`: $1.529 for
R155, the two builds, $1.329, plus the refused first plan, $0.200) and the reloaded thesis builds.

**Caveats:** the gold and both judges come from one model family (Claude), which also wrote the agent's
fixes; one run, no repeat; 50 questions per dataset give intervals of about 7-16 points, so no two datasets
are told apart. The lighthouse articles are Wikipedia, which the answering model may have read (memorisation);
its two misses by meaning are table counts, not text recall.

## Fig. 2: Aggregation and negation-sensitive are the hardest types (42/46, 33/35 by meaning); intervals overlap

**Files:** [r157_fig2_accuracy_by_type.svg](r157_fig2_accuracy_by_type.svg), [.png](r157_fig2_accuracy_by_type.png)

**What it shows:** accuracy per question type (the type the gold gives each question), pooled over the five
datasets, by both scores, with 95 % Wilson intervals; n per type in the labels (40, 45, 39, 45, 35, 46).

**How to read it:** as fig. 1: blue square exact match, orange circle by meaning, lines the intervals, k/n at
the right (exact first). Rows in the gold's type order, which here runs from the strongest type to the
weakest by meaning; the axis starts at 75 %.

**Takeaway:** lookup 40/40 and structured filter 45/45 by both scores; disambiguation 38/39 exact, 39/39 by
meaning; multi-hop 42/45 and 44/45; negation-sensitive 32/35 and 33/35; aggregation 42/46 by both
(79.7-96.6). By meaning, aggregation (42/46) and negation-sensitive (33/35) are the hardest types; by exact
match they tie (42/46 and 32/35, 91.3 % and 91.4 %), and their intervals overlap, so neither is told apart
as the weakest. Four of the seven answers wrong by meaning are aggregations (counting or comparing lists).

**Source:** as fig. 1.

**Caveats:** the types overlap in their intervals; by meaning, only the intervals of multi-hop,
negation-sensitive and aggregation stop short of 100 %. Types are the gold writer's labels, not checked by code.

## Fig. 3: All 7 answers wrong by meaning involve the agent's reasoning, 6 with a tool limit; 4 more miss exact match only by their form

**Files:** [r157_fig3_failures_by_cause.svg](r157_fig3_failures_by_cause.svg), [.png](r157_fig3_failures_by_cause.png)

**What it shows:** the 11 of 250 answers wrong by either score, by the cause classes R157 traced through each
answer's trace to the code; a question can have several causes, so the bars add up to 19 and are counts per
cause, not parts of the 11.

**How to read it:** one bar per cause, in two groups set apart by a rule: above it the causes of answers
wrong by both scores (orange), below it the cause of answers wrong by exact match only, right by meaning
(blue). The bottom axis stops at 11, the wrong answers; the space to its right holds the labels. The count and
the question ids stand at each bar (NB NTSB, LB lighthouses, FB
furniture, HB held-out, GB generality), so a question with several causes can be followed from bar to bar.

**Takeaway:** agent reasoning 7/7 of the answers wrong by meaning (LB09, LB11, FB10, FB14, FB18, FB38, HB41:
counted by eye, searched once, misread a column, spent the call budget); a tool limit in 6 of them (no
anti-join in `query_tables`, the literal substring search of `find_passages`, no paging, and a table schema
showing only the three lowest values of a date column that mixes precisions); a retrieval miss in 1 (FB18) and a graph-build detail in 1 (FB14, a handle stored without its
"@"). The 4 exact-only misses (LB06, LB39, GB05, GB33) are all answer form: a right answer in a form exact
match cannot read (a year list, a month, a time, an intermediate answer listed with the asked one). No NTSB
answer was wrong.

**Source:** the `causes` of `tests/gold/r157/outcomes.json`, from R157's tracing (REFACTOR_PLAN R157).

**Caveats:** cause classes are one reader's tracing of 11 traces, not a measured rate; with 7 failures by
meaning, the bars show where to look, not how often each cause would recur. R157's text says no failure is a
graph-build gap: FB14's build detail is a contributing cause recorded next to the agent's call budget, not
the main one.
