"""A question as a Lucene query for the full-text indexes of the retrieval layer (R120).

Role in the pipeline: the lexical retrievers (retrievers.py) search the chunk, card and claim full-text
indexes (graph/index_layer.py) with it.
Design: the question's words (`core.text.words`), each quoted, joined by spaces, which Lucene reads as OR, so
a passage scores by how many of the words it holds and how rare they are (BM25). Quoting makes every
Lucene operator in a word plain text ("hp40-1183", "fuel/propulsion" would otherwise be a NOT and a
regular expression), and the index's English analyzer still stems a quoted word ("drawers" finds "drawer")
and drops its stop words. A word is taken once.
Not here: the search itself (unit_store.py).
"""

from ..core.text import words


def lucene_query(text: str) -> str | None:
    """`"drawer" "rails" "stick"` for "Do the drawer rails stick?"; None when the text has no word, so the
    caller can skip the search instead of sending Lucene an empty query it rejects."""
    terms = list(dict.fromkeys(words(text)))
    return " ".join(f'"{term}"' for term in terms) if terms else None
