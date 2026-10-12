# Plan R152-R157: the benchmark protocol

## Diag. 1: The benchmark protocol: 250 questions written before any output, answered by a frozen agent, scored twice

**Files:** [r157_diag1_benchmark_protocol.svg](r157_diag1_benchmark_protocol.svg), [.png](r157_diag1_benchmark_protocol.png)

**What it shows:** how the benchmark of R157 was made, left to right as the data flows (not in the order the
steps ran: the agent was frozen at R151, before the gold was written, as its box says). Five
datasets: two new ones (NTSB aviation accidents, Trinity House lighthouses), fetched by a rule fixed before
any of their text was read (R153, R154), and the three thesis datasets. From the sources alone, 50 gold
questions per dataset (250, six types) were written before any output existed (R152-R154), and the graphs
were built: the two new graphs with composite keys (R155a, R155b), the thesis graphs reloaded unchanged from
their pinned builds. The agent, frozen at tag `query-engine-r151` before the gold was written, was given
only the questions and answered all 250 (R156). Every answer was scored twice against its gold answer: by
exact match (code scored 227 answers, 216/227 right; the 23 free-text answers have no exact form, so their
exact-match outcome is the judges' verdict, 23/23) and by meaning by two blind judges. The outcomes were
committed as a record (R158) that the R157 charts read.

**How to read it:** gray boxes are steps; the blue and orange boxes are the two scores, in the colours the
R157 charts give them. The two dataset groups sit in one frame whose single line forks to both the gold and
the builds. The agent gets only the gold's questions and the graphs; the gold answers go round it, along the
top, into the frame around the two scores, since both scores compare each answer with its gold answer.

**Takeaway:** 239/250 by exact match and 243/250 by meaning (pooled; see
[the R157 charts](../../../results/evaluation/r157_benchmark/README.md)), at $1.529 for R155, the two builds
($1.329) plus the refused first plan ($0.200), and $3.870 for the agent's 250 answers.

**Source:** `tests/gold/r157/outcomes.json` (the counts), `tests/gold/r155/runs.json` and
`tests/gold/r156/runs.json` (the costs; MLflow agent runs `fbf2339f`, `975671c2`, `fb74f46a`, `ab12ca9d`,
`61432a24`).

**Caveats:** the gold and both judges come from one model family (Claude Opus 5.5), which also wrote the
agent's fixes; the agent answers with Gemini (gemini-3.8-flash). One run, no repeat. The thesis datasets'
questions were written after R151's freeze from their source files only, but the engine was developed on
those datasets; the new datasets are the stricter test.
