# How the query engine works (designs of R130-R210, drawn for the thesis in R234)

Method diagrams of Chapter 6 of the thesis. Drawn by `figures/scripts/r130_r210_diagrams.py`; they hold no
measured number. Settings shown are the code's.

## Diag. 1: The query engine: a model chooses one read-only tool call per turn; code checks every call, every citation and the outcome

**Files:** [r210_diag1_query_engine.svg](r210_diag1_query_engine.svg), [.png](r210_diag1_query_engine.png), [.pdf](r210_diag1_query_engine.pdf)

**What it shows:** the screen before the model; the loop (the model proposes one typed step, code checks a call,
the tool runs read-only, its result is written back with every cut announced) with its four caps; the three tool
families and the stores they read; the checks on the answer, each sent back once; the outcomes.

**How to read it:** orange rounded: the model proposes; square: code decides; blue: the tools; cylinders: stores.

**Takeaway:** the model chooses what to do next; it never writes a query, never sees a store directly, and every
answer it gives is checked by code before it is returned.

**Source:** `agent/loop.py`, `agent/prompt.py`, `agent/outcome.py`, `agent/citations.py`, `agent/recheck.py`,
`query/screen.py`, `config.py` (caps) at R210.

**Caveats:** the answer checks run in the order empty, outcome, form, recheck, citations; the diagram lists them
in one box.

## Diag. 2: Two ranked paths inside the tools: fused card lists find start nodes; fused and reranked chunk lists find passages

**Files:** [r210_diag2_retrieval_paths.svg](r210_diag2_retrieval_paths.svg), [.png](r210_diag2_retrieval_paths.png), [.pdf](r210_diag2_retrieval_paths.pdf)

**What it shows:** `find_nodes` (25 dense and 25 lexical template cards, RRF k = 60, 15 shown) and `search_text`
(10 dense and 10 lexical chunks, RRF k = 10, a small model's reranking, 10 whole passages shown; the RRF order if
the reranker fails).

**How to read it:** as diag. 1.

**Takeaway:** start nodes and passages are found by different lists, each fused; only the passages are reranked by
a model.

**Source:** `agent/graph_tools.py`, `agent/text_tools.py`, `hybrid/retrievers.py`, `hybrid/seed_fusion.py`,
`hybrid/passage_rerank.py`, `config.py` at R210; the settings were chosen in R131 and R198-R199.

**Caveats:** the reranker reads the agent's search words, not the user's question.

## Diag. 3: Structured questions: the model fills a typed query; code validates and compiles it, and only reads

**Files:** [r210_diag3_structured_query.svg](r210_diag3_structured_query.svg), [.png](r210_diag3_structured_query.png), [.pdf](r210_diag3_structured_query.pdf)

**What it shows:** the typed query of `query_records` and `query_tables`, its validation against the schema, its
compilation to parameterised Cypher or a DuckDB SELECT, read-only execution, and rows with record keys the answer
can cite; the free-SQL variant (measured, not adopted) in the dashed frame.

**How to read it:** as diag. 1; dashed: an experimental variant.

**Takeaway:** counts, filters and joins are computed by a database, never by the model reading rows.

**Source:** `agent/structured_query.py`, `agent/record_query.py`, `agent/table_query.py`,
`agent/structured_tools.py`, `agent/sql_tool.py`, `agent/sql_check.py` at R210.

**Caveats:** the operator list is abridged (the `in` list holds up to 50 values; text fields can be measured by the
number inside them).
