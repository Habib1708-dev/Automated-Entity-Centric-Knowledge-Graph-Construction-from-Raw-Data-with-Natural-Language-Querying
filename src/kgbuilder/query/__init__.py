"""Question answering over the graph (layered-model Step 2, R71): the graph as an index into the text.

answers.py holds what a system returns; names.py links the names of a question to graph nodes;
traversal.py and graph_store.py read the graph (fixed traversal patterns, chunks, the vector index);
reader.py asks the model to answer from the chosen chunks; ranking.py orders chunks by similarity to a
question. Query plans (R74): planner.py asks the model for a plan, plan.py is the closed set of primitives
and the code check of a plan against the schema of graph_schema.py, plan_cypher.py compiles each primitive
to one parameterised Cypher fragment, plan_run.py runs a plan step by step, and read_check.py checks one
candidate's text against a statement with a quote code verifies. exact.py (text2cypher, checked by
cypher_check.py and the database's EXPLAIN) remains as the plans' logged fallback. systems.py puts them
together: the graph system and records plus vector RAG (both plan systems) and the vector-only baseline.
"""
