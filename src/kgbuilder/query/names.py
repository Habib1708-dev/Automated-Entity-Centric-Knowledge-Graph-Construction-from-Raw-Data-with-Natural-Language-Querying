"""Link the names in a question to graph nodes, by spelling and by meaning.

Role in the pipeline: the first step of the graph's retrieval route (systems.py); its links are where the
traversal starts.
Design: code decides the links, no model is asked. A run of words in the question links to a node whose
name or alias it spells alike, scored like entity resolution (`core.similarity.name_similarity`), and the
nodes whose names lie nearest the question in meaning are linked too, by rank rather than by a similarity
threshold, so nothing has to be tuned per embedding model. The node names are embedded once per linker.
`find` serves the query plans' `find_entity` and claim words (R74) from the same names and vectors.
Not here: reading the names from the graph (graph_store.py) and what the links lead to (traversal.py).
"""

import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

from ..core.similarity import dot, name_similarity, unit_vector
from ..core.text import norm
from .answers import LinkedNode

# A word: letters and digits, keeping inner hyphens, apostrophes, dots and slashes ("hp40-1183", "can't",
# "fuel/propulsion"), so an identifier stays one word; punctuation around it ("hp40-1183?") is dropped.
_WORD = re.compile(r"[a-z0-9]+(?:[-'./][a-z0-9]+)*")

# English function words. A run made only of them names nothing ("of the", "which"), and a node named like
# one would be linked to every question. They belong to the language, not to any domain.
_FUNCTION_WORDS = frozenset(
    re.findall(
        r"\w+",
        """
    a about all an and any are as at be by can did do does for from had has have how if in is it its
    more most of on or other over than that the their them there these they this those to under was
    were what when where which who whom whose why will with
""",
    )
)

# A name shorter than this (after norm) matches too much by chance to be linked by spelling.
_MIN_NAME_CHARS = 3


class NodeName(BaseModel):
    """A node that a question can name: its display name and the other names it is known by."""

    kind: Literal["thing", "kind"]
    node_id: str
    name: str
    aliases: list[str] = []
    label: str | None = (
        None  # a thing's record label or a kind's entity type (find_entity narrows by it, R74)
    )


def words(text: str) -> list[str]:
    """The words of `text` after `norm` (case, accents and markdown ignored)."""
    return _WORD.findall(norm(text))


def spans(question_words: list[str], length: int) -> list[str]:
    """Every run of `length` consecutive words, except runs made only of function words."""
    return [
        " ".join(run)
        for run in (question_words[i : i + length] for i in range(len(question_words) - length + 1))
        if not all(w in _FUNCTION_WORDS for w in run)
    ]


class NameLinker:
    """Links a question to the nodes it names. Build it once per graph: it embeds the node names."""

    def __init__(
        self,
        nodes: list[NodeName],
        name_vectors: list[list[float]] | None,
        fuzzy: float,
        neighbours: int,
    ):
        """`name_vectors` are the embeddings of the nodes' display names, in the order of `nodes`, or None
        without an embedder (then only spelling links). `fuzzy` is the spelling score a span must reach,
        `neighbours` how many nodes nearest in meaning are linked."""
        self._nodes = nodes
        self._vectors = [unit_vector(v) for v in name_vectors] if name_vectors else []
        self._fuzzy = fuzzy
        self._neighbours = neighbours

    def link(self, question: str, question_vector: list[float] | None) -> list[LinkedNode]:
        """The nodes the question names, spelling links first; each node once."""
        question_words = words(question)
        by_length: dict[int, list[str]] = {}
        linked: dict[tuple[str, str], LinkedNode] = {}
        for node in self._nodes:
            if any(self._spelled(name, question_words, by_length) for name in (node.name, *node.aliases)):
                linked[(node.kind, node.node_id)] = _linked(node, "spelling")
        for node in self._nearest(question_vector):
            linked.setdefault((node.kind, node.node_id), _linked(node, "meaning"))
        return list(linked.values())

    @property
    def nodes(self) -> list[NodeName]:
        return self._nodes

    def find(
        self,
        name: str,
        embed: Callable[[str], list[float]],
        kinds: set[str],
        label: str | None,
        union: bool,
    ) -> list[NodeName]:
        """The nodes of `kinds` (and `label`, when given) that a name names (R74, `find_entity`): every node
        spelled alike; with `union` also, and otherwise only when none is, the `neighbours` nearest in
        meaning. `embed` is called only when meaning is needed. Two same-named records both come back."""
        candidates = [n for n in self._nodes if n.kind in kinds and (label is None or n.label == label)]
        wanted = " ".join(words(name))
        spelled = self.spelled(name, kinds, label)
        if (spelled and not union) or not self._vectors or self._neighbours == 0 or not wanted:
            return spelled
        q = unit_vector(embed(name))
        index = {id(n): i for i, n in enumerate(self._nodes)}
        nearest = sorted(candidates, key=lambda n: dot(q, self._vectors[index[id(n)]]), reverse=True)
        found = {(n.kind, n.node_id): n for n in spelled}
        for n in nearest[: self._neighbours]:
            found.setdefault((n.kind, n.node_id), n)
        return list(found.values())

    def spelled(self, name: str, kinds: set[str], label: str | None) -> list[NodeName]:
        """The nodes of `kinds` (and `label`, when given) whose name or an alias the name spells alike,
        whole name against whole name; never a node by meaning alone."""
        wanted = " ".join(words(name))
        return [
            n
            for n in self._nodes
            if n.kind in kinds
            and (label is None or n.label == label)
            and any(
                name_similarity(wanted, " ".join(words(x)), self._fuzzy) >= self._fuzzy
                for x in (n.name, *n.aliases)
            )
        ]

    def _spelled(self, name: str, question_words: list[str], by_length: dict[int, list[str]]) -> bool:
        """Whether a run of the question's words spells `name` alike (same number of words)."""
        name_words = words(name)
        if len(" ".join(name_words)) < _MIN_NAME_CHARS or all(w in _FUNCTION_WORDS for w in name_words):
            return False
        length = len(name_words)
        candidates = by_length.setdefault(length, spans(question_words, length))
        wanted = " ".join(name_words)
        return any(name_similarity(wanted, span, self._fuzzy) >= self._fuzzy for span in candidates)

    def _nearest(self, question_vector: list[float] | None) -> list[NodeName]:
        """The `neighbours` nodes whose names lie nearest the question in meaning."""
        if not self._vectors or question_vector is None or self._neighbours == 0:
            return []
        q = unit_vector(question_vector)
        ranked = sorted(range(len(self._nodes)), key=lambda i: dot(q, self._vectors[i]), reverse=True)
        return [self._nodes[i] for i in ranked[: self._neighbours]]


def _linked(node: NodeName, by: Literal["spelling", "meaning"]) -> LinkedNode:
    return LinkedNode(kind=node.kind, node_id=node.node_id, name=node.name, by=by)
