# R231: question answering on the four selected datasets

Charts of step R231. They show the frozen query engine's question-answering accuracy on the four datasets of the final presentation: NTSB, furniture, held-out NHTSA and generality.

**The measurements**, each on its own questions written before any output:
- **R157:** the frozen engine (tag `query-engine-r151`), 50 benchmark questions per dataset;
- **R166:** before the R167-R170 fixes, 20 confirmation questions per dataset;
- **R173:** after them, 20 confirmation questions per dataset.

**The scores.** Every answer is scored twice:
- **exact match:** computed by code; a free-text answer's exact-match outcome is the judges' verdict;
- **by meaning:** two blind judges (Claude Opus 5.5).

**Scope.** These are the same outcomes as the five-dataset figures of [R157](../r157_benchmark/README.md) and [R173](../r173_confirmation/README.md), restricted to the four selected datasets. Those figures stay as the five-dataset record. Here the pooled numbers are over the four datasets only.

## Fig. 1: On unseen questions the agent answers 195/200, 77/80, 78/80 right by meaning on the four datasets

**Files:** [r231_fig1_accuracy_by_dataset.svg](r231_fig1_accuracy_by_dataset.svg), [.png](r231_fig1_accuracy_by_dataset.png)

**What it shows:** accuracy per dataset and pooled over the four, by both scores, with 95 % Wilson intervals. One panel per measurement.

**How to read it:**
- In each row, the blue square is exact match and the orange circle the score by meaning, each with its interval as a line.
- k/n at the right, exact first. The pooled row is set apart below the line.
- The axis starts at 75 %.

**Takeaway:**

| | Pooled, by meaning | Pooled, exact | NTSB | Furniture | Held-out | Generality |
|---|---|---|---|---|---|---|
| R157 (by meaning) | 195/200 | 193/200 | 50/50 | 46/50 | 49/50 | 50/50 |
| R166 (by meaning) | 77/80 | 77/80 | 19/20 | 19/20 | 19/20 | 20/20 |
| R173 (by meaning) | 78/80 | 78/80 | 19/20 | 20/20 | 19/20 | 20/20 |

R157's per-dataset exact scores are the same, except generality at 48/50.

**Source:**
- `tests/gold/r157/outcomes.json`, `tests/gold/r166/outcomes.json`, `tests/gold/r173/outcomes.json`.
- The runs are named in the R157 and R173 captions.

**Caveats:**
- The three question sets differ, so a difference is not attributed to the fixes.
- 20 questions per dataset give intervals of about 20 points.
- The gold and both judges come from one model family (Claude).

## Fig. 2: Every question type stays at 87% or more by meaning; the weakest is aggregation (34/37), aggregation (13/15), structured filter (12/13)

**Files:** [r231_fig2_accuracy_by_type.svg](r231_fig2_accuracy_by_type.svg), [.png](r231_fig2_accuracy_by_type.png)

**What it shows:** each measurement's questions on the four datasets by the type the gold gives them, by both scores, with 95 % Wilson intervals.

**How to read it:** as fig. 1, one row per type. The axis starts at 50 %, because a type has only 12-17 questions in R166 and R173.

**Takeaway:**
- The weakest type by meaning is aggregation in R157 (34/37) and R166 (13/15), and structured filter in R173 (12/13).
- Every other type is at or near 100 %.

**Source:** as fig. 1.

**Caveats:** the intervals of the small types overlap widely; no type is told apart as the weakest.

## Fig. 3: 7 of 200 benchmark answers are wrong by either score; every one wrong by meaning involves the agent's reasoning

**Files:** [r231_fig3_failures_by_cause.svg](r231_fig3_failures_by_cause.svg), [.png](r231_fig3_failures_by_cause.png)

**What it shows:** the R157 benchmark answers on the four datasets that are wrong by either score, each traced to its causes (R157's cause classes).

**How to read it:**
- Bars count the wrong answers per cause, with the question ids at each bar.
- A question can have several causes, so the bars add up to more than 7.
- Orange: wrong by both scores; blue: wrong by exact match only (right by meaning).

**Takeaway:**
- **5 answers are wrong by meaning:** FB10, FB14, FB18, FB38, HB41. Each involves the agent's reasoning, and 4 also a tool limit.
- **2 are right by meaning but in a form code cannot read:** GB05, GB33.
- No NTSB answer was wrong.

**Source:** `tests/gold/r157/outcomes.json`.

**Caveats:** one run, no repeat.
