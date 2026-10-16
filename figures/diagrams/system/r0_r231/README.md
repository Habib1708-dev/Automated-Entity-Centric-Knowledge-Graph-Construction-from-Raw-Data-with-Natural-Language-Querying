# The system as a whole (R0-R231, drawn for the thesis in R232)

The architecture diagram of the final code: Figure 3.1 of the thesis. Drawn by
`figures/scripts/r0_r231_diagrams.py` from the code's structure; it holds no measured number.

## Diag. 1: The system: a pipeline builds an anchor graph from tables and text; an agent answers over it with read-only tools

**Files:** [r231_diag1_system_architecture.svg](r231_diag1_system_architecture.svg), [.png](r231_diag1_system_architecture.png), [.pdf](r231_diag1_system_architecture.pdf)

**What it shows:** the sources (tables, documents and a one-sentence goal), the builder's ten stages named by
their `kg` commands, the three stores, the query engine (screen, agent, tools, checks, outcome) and the user's
entry through the web interface or the command line; MLflow logs every stage and question set.

**How to read it:**
- Left to right is the flow of data.
- Orange rounded boxes: a language model proposes (plan, text-schema, extract, mention-pass, the agent).
- Square boxes: code decides; blue is the agent's read-only tools.
- Cylinders: stores.
- Dashed arrows: secondary flows (logging; the source tables staged into DuckDB for the agent's table queries).
- "link, resolve" calls a model only for near-miss record choices and identity adjudication; code verifies both.

**Takeaway:** the language model appears only where something must be proposed; every proposal passes through
code before it reaches a store, and the query engine can only read.

**Source:** the code at R231 (`cli.py`, `pipeline/`, `agent/system.py`, `query/screen.py`, `web/`).

**Caveats:** one box per stage hides the inner steps of `resolve` (see the construction diagrams); the order of
`link` before `mention-pass` in the code is shown here as one identity step.
