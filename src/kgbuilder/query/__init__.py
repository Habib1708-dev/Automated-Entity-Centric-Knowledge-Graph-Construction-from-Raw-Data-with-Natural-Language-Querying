"""Question answering over the graph (layered-model Step 2, R71): the graph as an index into the text.

answers.py holds what a system returns; names.py links the names of a question to graph nodes;
traversal.py and graph_store.py read the graph (fixed traversal patterns, chunks, the vector index);
reader.py asks the model to answer from the chosen chunks. router.py labels a question exact or retrieval;
exact.py lets the model write Cypher, which cypher_check.py and the database's EXPLAIN check before it runs
against the schema of graph_schema.py. systems.py puts them together: the graph's retrieval route, the
routed graph system and the vector-only baseline.
"""
