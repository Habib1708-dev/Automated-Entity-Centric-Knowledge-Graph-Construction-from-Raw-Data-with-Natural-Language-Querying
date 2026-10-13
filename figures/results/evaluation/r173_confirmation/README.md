# R173: the confirmation experiments, three measurements of the agent on unseen questions

Charts of plan R174 (the thesis form of plans R160-R166 and R167-R173): the agentic query engine measured
three times on questions it had not seen, each set written from the sources before any output of the engine
it measures. R157: the engine frozen at tag `query-engine-r151`, 50 questions per dataset (250). R166: the
engine with R161-R164's fixes (the counting rule, word forms, the form rule, varied schema examples), 20 new
questions per dataset (100). R173: the engine with R167-R170's fixes (the empty-cell test, distinct counts
and year/decade periods, hyphens in phrases, heading lines kept in excerpts), 20 more new questions per
dataset (100). Five datasets each: NTSB aviation accidents and Trinity House lighthouses (fetched for the
benchmark), furniture, held-out NHTSA and generality (the thesis datasets). Every answer is scored twice:
exact match (computed by code, `validation/qa.py`; a free-text answer has no exact form, so its exact-match
outcome is the judges' verdict), and by meaning, by two blind judges (Claude Opus 5.5, R144's procedure);
the judges agreed on every question of every set, so the lead judge decided nothing. The question sets
differ and each was run once, so no difference between measurements is attributed to the fixes; the traces
of R173 show the new operations used and right where a question called for them (REFACTOR_PLAN R173).

## Fig. 1: On unseen questions the agent answers 97.2 %, 94.0 %, 98.0 % right by meaning (243/250, 94/100, 98/100); every interval overlaps the others

**Files:** [r173_fig1_accuracy_by_dataset_three_measurements.svg](r173_fig1_accuracy_by_dataset_three_measurements.svg), [.png](r173_fig1_accuracy_by_dataset_three_measurements.png)

**What it shows:** accuracy per dataset and pooled, by both scores, for the three measurements side by side
(R157, R166, R173, in time order), as percent with 95 % Wilson intervals; n = 50 per dataset in R157 and 20
in R166 and R173.

**How to read it:** one panel per measurement, the same rows in each, so a dataset is read across the three
on one line. In each row the blue square is exact match and the orange circle the score by meaning, each with
its interval as a line; the counts at the right are k/n, exact first. The gray band holds the two new
datasets; the pooled row is set apart below the line. The axis starts at 75 %.

**Takeaway:** pooled by meaning 243/250 (97.2 %, 94.3-98.6), 94/100 (94.0 %, 87.5-97.2), 98/100 (98.0 %,
93.0-99.4); by exact match 239/250, 94/100, 98/100. In R166 and R173 exact match equals the score by
meaning: no answer was right in content but wrong in form (R163's form rule). Per dataset in R173: NTSB
19/20, lighthouses 20/20, furniture 20/20, held-out 19/20, generality 20/20. A two-proportion test gives
z = 1.44 (p = 0.15) between R173 and R166 and z = 0.43 between R173 and R157: no measurement is told
apart from another.

**Source:** `tests/gold/r157/outcomes.json`, `tests/gold/r166/outcomes.json`, `tests/gold/r173/outcomes.json`
(the per-question records; the verdicts in `tests/gold/r157/`, `r166/`, `r173/`); the runs in
`tests/gold/r156/runs.json` (MLflow `fbf2339f` NTSB, `975671c2` lighthouses, `fb74f46a` furniture,
`ab12ca9d` held-out, `61432a24` generality), `tests/gold/r165/runs.json` (`00372eb4`, `5200996f`,
`2f56dbd5`, `94ba4784`, `80eb7c27`) and `tests/gold/r172/runs.json` (`ec1a6518`, `78bff118`, `35afadc8`,
`e4899ff3`, `2e7fa121`).

**Caveats:** three different question sets, one run each; the gold (Claude Opus 5.5 for R152-R160, Claude
Fable 5.1 for R171) and the judges (Opus 5.5) come from one model family, which also wrote the fixes; the
agent answers with Gemini. Twenty questions per dataset give intervals of about 15-25 points, so per-dataset
values in R166 and R173 are not compared. The engine's tool text changed between the measurements, so each
score describes its column's engine on its own questions.

## Fig. 2: Aggregation, the weakest type, goes 42/46 to 14/19 to 17/18 by meaning across the three measurements; the other types stay near the top

**Files:** [r173_fig2_accuracy_by_type_three_measurements.svg](r173_fig2_accuracy_by_type_three_measurements.svg), [.png](r173_fig2_accuracy_by_type_three_measurements.png)

**What it shows:** accuracy per question type (the type the gold gives each question), both scores, for the
three measurements; n per type 35-46 in R157 and 15-21 in R166 and R173.

**How to read it:** as Fig. 1, one panel per measurement with the same six rows. The axis starts at 50 %,
because R166's aggregation (14/19, 73.7 %) and its interval's low end (51 %) lie below 75 %.

**Takeaway:** by meaning, aggregation 42/46 (79.7-96.6), 14/19 (51.2-88.2), 17/18 (74.2-99.0): the type
whose failures R166 traced to table operations the tools could not express (a distinct count, a group by a
date's decade, an empty cell), which R167 and R168 added. Every other type is at or near the top in R173:
lookup 15/15, structured filter 15/16, disambiguation 15/15, multi-hop 21/21, negation-sensitive 15/15. The
intervals at n = 15-21 overlap across the three measurements for every type.

**Source:** as Fig. 1 (`by_type` of `figures/scripts/r157_data.py` on each record).

**Caveats:** as Fig. 1; the type counts per set differ (the writers were asked for a mix, not equal counts),
so a type's three rates are on different questions and small n.

## Fig. 3: The wrong answers fall from 11 to 6 to 2; what remains is a tool limit the agent's reasoning does not get round

**Files:** [r173_fig3_failures_by_cause_three_measurements.svg](r173_fig3_failures_by_cause_three_measurements.svg), [.png](r173_fig3_failures_by_cause_three_measurements.png)

**What it shows:** each measurement's answers wrong by either score, counted per cause class as the step
traced them (R157: agent reasoning 7, tool limit 6, retrieval miss 1, graph build 1, answer form 4; R166:
agent reasoning 5, tool limit 5; R173: agent reasoning 1, tool limit 2), with the question ids at each bar.

**How to read it:** one panel per measurement on one x scale; orange is an answer wrong by both scores,
blue wrong by exact match only (right by meaning). A question with several causes is counted under each, so
a panel's bars add up to more than its wrong answers (11, 6, 2).

**Takeaway:** the answer-form misses (4 in R157) are gone after R163's form rule; the agent-reasoning
count falls from 7 to 5 to 1 as the tool limits it was working round (counting by eye, a literal search, a
hidden rating line) were removed; the two R173 failures are HD05 (`contains` is a substring match, so
"installed" counted for the word "stall") and ND11 (a search for "rolled" does not find "rollover", and the
agent took one search as complete).

**Source:** the three outcome records' `causes` (R157's from `tests/gold/r157/outcomes.json`, R166's and
R173's from the roadmap's traced tables, committed in `tests/gold/r166/outcomes.json` and
`tests/gold/r173/outcomes.json`).

**Caveats:** cause classes are the tracer's reading of each trace (Claude), not a measured quantity; small
counts.

## Fig. 4: An answer costs 1.5-1.6 cents; the one wasted command cost $0.501

**Files:** [r173_fig4_cost_per_run.svg](r173_fig4_cost_per_run.svg), [.png](r173_fig4_cost_per_run.png)

**What it shows:** what each agent run cost (USD, MLflow `cost_usd` from `prices.yaml`): R156 $3.870 for
250 questions, R165 $1.603 for 100, R172 $1.627 for 100 plus $0.501 for a first NTSB command that was not
scored; the cents a question at each bar.

**How to read it:** one bar per run on one dollar axis; the gray segment is the wasted command, drawn beside
its scored run.

**Takeaway:** the agent costs 1.5-1.6 cents an answer across the three runs (the graphs were reloaded from
cache at no cost). The wasted command ran the agent over an output folder with no plan and no staged
tables, so it answered that the dataset had no tables; a guard now refuses such a folder before any model
call (commit 815ef16).

**Source:** `tests/gold/r156/runs.json`, `tests/gold/r165/runs.json`, `tests/gold/r172/runs.json`
(`cost_usd`, `wasted_run`).

**Caveats:** costs are the model's prices at run time (`prices.yaml`), not a measured budget; the reloads'
embedding calls report no tokens and are unpriced (cents).
