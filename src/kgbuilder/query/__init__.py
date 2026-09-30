"""Question answering over the graph (layered-model Step 2, R71): the graph as an index into the text.

answers.py holds what a system returns; names.py links the names of a question to graph nodes;
traversal.py and graph_store.py read the graph (fixed traversal patterns, chunks, the vector index);
reader.py asks the model to answer from the chosen chunks; systems.py puts them together as the graph's
retrieval route and the vector-only baseline.
"""
