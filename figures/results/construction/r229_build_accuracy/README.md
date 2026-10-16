# R229: build accuracy of the five benchmarked graphs

Charts of step R229, which draws the build-accuracy records of every benchmarked dataset side by side.

**The builds:**
- **Thesis datasets** (furniture, held-out NHTSA, generality): the r108 builds.
  - claims judged in R110 (precision) and R111 (recall);
  - mentions measured in R103, whose mention pass r108 reused from the cache;
  - the C3/C4/C6 audit judged in R108.
- **New datasets** (lighthouses, NTSB): the R155b builds, judged with the same rules in R225-R228.

**The judging.** Every judged number is a verdict of Claude Opus 5.5:
- one blind subagent per batch, then the lead's review of every non-valid verdict and a seeded 10 % of the rest;
- the gold (reader claims, mentions, identity pairs, targets) also comes from Claude.

Exact-match numbers (mention recall by name) are computed by code. Every rate carries its 95 % Wilson interval.

## Fig. 1: What the builds store is right: content precision 95% or more on all five datasets

**Files:** [r229_fig1_claim_precision_by_dataset.svg](r229_fig1_claim_precision_by_dataset.svg), [.png](r229_fig1_claim_precision_by_dataset.png)

**What it shows:** the precision of every extracted claim each build stores, judged against its chunk. Two measures:
- **strict:** every stored field right (truth, modality, condition, tone, time, end types);
- **content:** the subject-relation-object triple right.

n is the build's extracted claims: 293, 160, 530, 530 and 216.

**How to read it:**
- One row per dataset. Blue square: strict; orange circle: content. Lines: the intervals.
- k/n at the right, strict first.
- The gray band holds the two new datasets. The axis starts at 80 %.

**Takeaway:**
- **Content precision:** NTSB 289/293, lighthouses 152/160, furniture 518/530, held-out 520/530, generality 208/216. Every point is 95 % or more; lighthouses is the lowest at exactly 95 %, with an interval of 90-97 %.
- **Strict precision:** 285/293, 145/160, 498/530, 480/530 and 202/216.
- The gap between the two measures is the fields:
  - held-out's 40 strict-only errors are mostly modality and condition (R110);
  - lighthouses' 7 are 6 stored times of another event and 1 modality (R226).

**Source:**
- `tests/gold/r110/<ds>/claim_report.json`: MLflow `claim_eval` runs `6a95cec4` (furniture), `1ec7cfcd` (held-out), `959090dd` (generality), as corrected in R110.
- `tests/gold/r226/<ds>/claim_report.json`: `3f2b2207` (lighthouses), `30619df0` (NTSB).

**Caveats:**
- Only extracted claims are counted. Furniture and held-out also store derived claims (R110: 140/154 and 38/135 content), which the new builds do not have.
- The committed R110 reports are the corrected ones. Furniture strict is 498/530 there, not the first-pass 470/530 of R110's table.
- Gold and verdicts come from one model family, with no human agreement number.

## Fig. 2: The builds store few of a reader's claims: from 15% to 58% (furniture)

**Files:** [r229_fig2_claim_recall_by_dataset.svg](r229_fig2_claim_recall_by_dataset.svg), [.png](r229_fig2_claim_recall_by_dataset.png)

**What it shows:** every claim a careful reader lists for 40 random sentences per dataset. The reader's claims were written blind (R77, R225). A blind judge matched each one to the build's stored claims, or gave the cause of the miss (R111's causes). The bars split the claims three ways:
- **stored;**
- **missed though a fact type could hold them:** the model's miss (`extraction`, plus the rarer causes `assertion`, `role`, `attribution`, `attachment`, `event_structure`);
- **missed with no fact type for them:** the schema's miss (`no_schema_type`).

**How to read it:**
- One bar per dataset, as a share of its reader claims. The numbers in the bars are claims.
- At the right: stored k/n and the rate.

**Takeaway:**

| | stored | missed, fact type existed | missed, no fact type | where the gap is |
|---|---|---|---|---|
| NTSB | 13/74 | 55 | 6 | the model's: its schema could hold accident narrative it never extracted |
| Lighthouses | 11/73 | 32 | 30 | mixed: dated milestones the Event type could hold, and descriptions no type holds |
| Furniture | 42/72 | 19 | 11 | the best recall |
| Held-out | 19/67 | 16 | 32 | the schema's |
| Generality | 12/66 | 15 | 39 | the schema's |

**Source:**
- `tests/gold/r111/<ds>/recall_report.json`: MLflow `claim_recall` `92a987a3`, `35345dce`, `6925abd1`.
- `tests/gold/r226/<ds>/recall_report.json`: `760a7690`, `9915ded4`.

**Caveats:**
- 40 sentences per dataset give wide intervals (about ±10 points).
- Only the random sentences are counted. Each sample also has 20 sentences with a negation, modal or condition word, which stay in the records' "all strata" numbers.
- Whether a fact type "could hold" a claim is the judge's reading of the schema.

## Fig. 3: The mention index finds 83%-94% of what a reader names, and most of what it adds is a real mention

**Files:** [r229_fig3_mention_recall_and_precision.svg](r229_fig3_mention_recall_and_precision.svg), [.png](r229_fig3_mention_recall_and_precision.png)

**What it shows:** the mention index, the things a question can start from (R101's definition).

- **Left, recall** of the gold mentions of 40 random sentences per dataset (R101, R225; written blind) in two ways:
  - by exact name, computed by code;
  - with the near names a judge mapped to the gold mention.
- **Right, precision:** a seeded sample of the mention pass's own mentions, judged inside the definition.

**How to read it:**
- Left: blue square exact, orange circle judged, with k/n at the right (exact first).
- Right: one judged rate per dataset.
- Both axes start at 55 %.

**Takeaway:**
- **Judged recall:** NTSB 153/184, lighthouses 138/167, furniture 68/75, held-out 116/125, generality 103/109.
- **Exact recall:** 133/184, 119/167, 53/75, 100/125, 88/109.
- **Pass precision:** 51/57, 55/57, 54/59, 55/59, 49/60.
- The new datasets' judged recall (83 %) sits a little below the thesis datasets' (91-94 %), with overlapping intervals.
- Two lighthouses misses come from the sentence splitter cutting at "St." and "P." (R226).

**Source:**
- `tests/gold/r103/<ds>/mentions_r103_report.json`, with the sheets and verdicts beside it.
- `tests/gold/r226/<ds>/mention_report.json`: MLflow `mention_eval` `13a3a7a4` (lighthouses), `bba1e18e` (NTSB).

**Caveats:**
- The precision sample is about 60 mentions per dataset, not the whole pass.
- The gold mentions and the judge come from one model family.

## Fig. 4: Every build is precise; recall is what separates them

**Files:** [r229_fig4_precision_against_recall.svg](r229_fig4_precision_against_recall.svg), [.png](r229_fig4_precision_against_recall.png)

**What it shows:** each dataset's content precision of claims (fig. 1) against its claim recall on the random sentences (fig. 2), with 95 % Wilson intervals on both axes.

**How to read it:**
- One dot per dataset: filled for the thesis datasets, hollow for the new ones. Every dot is ink, so no colour is spent on dataset identity.
- Each label gives recall k/n · precision k/n.
- The y axis starts at 85 %.

**Takeaway:**
- Precision lies in a narrow band: 95 % (lighthouses) to 99 % (NTSB).
- Recall ranges from 15 % (lighthouses, 11/73) to 58 % (furniture, 42/72).
- So the builds differ in how much of the text they keep, not in how often what they keep is wrong.
- Only furniture's recall interval (47-69 %) clears the others'.

**Source:** as figs. 1 and 2.

**Caveats:**
- Precision is over every stored claim; recall is over a 40-sentence sample. The two measures have different n.
- The points are different builds and corpora, not one system's trade-off curve.

## Fig. 5: 4 of 5 builds pass every audit criterion; Lighthouses fails on 2 wrong record links

**Files:** [r229_fig5_audit_by_dataset.svg](r229_fig5_audit_by_dataset.svg), [.png](r229_fig5_audit_by_dataset.png)

**What it shows:** the judged audit criteria of R93:
- **C3:** merges of individual and concept nodes judged one thing.
- **C4:** mention-to-record links right.
- **C6:** for the anchor arm, the (record or individual start, chunk) pairs whose chunk is about its start.

Each has a hard rule:
- C4 and C6 need at least the 95 % bound;
- C3 fails on any wrong individual merge or wrong record link.

**How to read it:**
- One panel per criterion; one dot with its interval per dataset.
- k/n and "passes" or "fails" at the right; a failing row is bold.
- The vertical line is the 95 % bound of C4 and C6. Axes start at 75 %.

**Takeaway:**
- **Lighthouses** fails C4 (34/36). Both wrong links attach the generic word "lighthouse" to the article's own lighthouse while the sentence means another tower: St Agnes in the St Anthony's article, and the 1664 Tynemouth light in the St Mary's article.
- **It therefore also fails C3** at a merge rate of 87/87, because R93 counts a wrong record link as a wrong record merge.
- **Every other criterion passes on every dataset:**
  - C4: NTSB 36/36, furniture 63/63, held-out 77/77, generality 48/48.
  - C6: NTSB 58/60, lighthouses 77/79, furniture 109/109, held-out 86/86, generality 100/100.
  - C3 merges: NTSB 80/81, furniture 150/151, held-out 93/95, generality 85/86.

**Source:**
- `tests/gold/r108/<ds>/anchor_judged.json` and the verdicts beside it.
- `tests/gold/r228/<ds>/anchor_judged.json`: MLflow `anchor_judged` `e6b19f9e` (lighthouses), `0e90f347` (NTSB).

**Caveats:**
- C6 also has concept starts (all pure but one AMBIGUOUS), not shown.
- NTSB's two C6 failures come from one "flight instructor" mention covering three accidents of one report. Mentions are per document (REFACTOR_PLAN, Found along the way), so C3 cannot see it.
- The targets of C6 are not blind to earlier answers (R89, R227).
