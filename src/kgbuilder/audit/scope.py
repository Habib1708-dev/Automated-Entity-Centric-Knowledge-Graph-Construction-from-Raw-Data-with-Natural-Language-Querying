"""Which records a document may speak about: the scope index of the graph audit (R87).

Role in the pipeline: read by `audit/checks.py` (cross-scope links and attachments) and `audit/reach.py`
(foreign chunks). It restates the identity stage's rule (`resolution/mentions.py` reads a mention's anchors,
`resolution/records.read_scopes` their 2-hop neighbourhood) over the snapshot: a document's anchors are the
things the link stage made it ABOUT (by file name or as a record's own document, never the text links,
which come later), its scope every record within two hops of an anchor.
Design: "outside the scope" is the audit's notion of a cross-product or cross-entity link, not "another
owner": a shared part has several owners, and the build already accepts every record of the scope.
A document without anchors has no scope (the generality corpus), so nothing in it is cross-scope.
Must not: decide whether a link is wrong; a cross-scope link is a flag for the judge.
"""

from collections import defaultdict

from ..resolution.attachment import TEXT_ABOUT
from .inputs import scopes
from .snapshot import GraphSnapshot


class ScopeIndex:
    """The anchors and the scope of every document of a snapshot."""

    def __init__(self, snapshot: GraphSnapshot) -> None:
        reach = scopes(snapshot.records, snapshot.relations)
        self.anchors: dict[str, set[str]] = defaultdict(set)
        for link in snapshot.documents_about:
            if link.how != TEXT_ABOUT:
                self.anchors[link.source].add(link.thing)
        self._scope = {
            doc: set().union(*(reach.get(a, {a}) for a in anchors)) for doc, anchors in self.anchors.items()
        }

    def has_scope(self, doc_id: str) -> bool:
        return doc_id in self._scope

    def outside(self, doc_id: str, record: str) -> bool:
        """True when the document has a scope and the record is not in it."""
        return doc_id in self._scope and record not in self._scope[doc_id]

    def anchor_names(self, doc_id: str) -> list[str]:
        return sorted(self.anchors.get(doc_id, set()))
