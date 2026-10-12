"""The agent's tools (R133, agent/graph_tools.py and text_tools.py) over fake stores and a fake node reader,
without Neo4j: `find_nodes` as R132's seeding (both card lists `candidates` deep, fused by RRF, nodes in
fused order, cards without a node left out, the first k kept, no word search without a word); `get_node`
cutting long values and extra names with a note; `neighbours` passing the type and the limit and saying when
the total is larger; `node_evidence` starting a record as a thing and a kind as a kind, ranking the reached
chunks by `about`, keeping the cap and the total, and giving each chunk its own claims; `search_text` and
`search_claims` fusing their two lists, a claim followed by its opposite-truth siblings; an unknown ref
answered with a note; the ids each result offers for citation; and every text the model reads free of the
evaluated datasets' words. R150: `rank_by_documents` passing its filters trimmed and lowercase with its
caps, offering the counted refs, saying when more exist, when a label is unknown (with the labels there
are, never an empty one) and when nothing matches, and refusing a blank text to look for."""

import re

import pytest
from pydantic import ValidationError

from kgbuilder.agent import (
    ClaimFact,
    FindNodes,
    GetNode,
    MentionCount,
    MentionPage,
    Neighbours,
    NodeEvidence,
    NodeFacts,
    NodeHit,
    Property,
    RankByDocuments,
    Relation,
    RelationPage,
    RelationsResult,
    SearchClaims,
    SearchText,
)
from kgbuilder.agent.graph_tools import (
    EvidenceArguments,
    FindNodesArguments,
    NeighboursArguments,
    NodeArguments,
    RankArguments,
)
from kgbuilder.agent.text_tools import FindPassages, ReadDocument, SearchArguments
from kgbuilder.hybrid.seed_fusion import SeedSetting
from kgbuilder.hybrid.unit_store import CardStart
from kgbuilder.query.answers import ClaimHit

from .test_query import FakeStore, FixedEmbedder, chunk

SETTING = SeedSetting(name="rrf60_c2", representation="template", method="rrf", candidates=2, rrf_k=60)


class SplitUnitStore:
    """`UnitStore` whose vector and word searches give their own lists; records each call."""

    def __init__(self, dense=(), lexical=(), claims_dense=(), claims_lexical=(), chunks=()):
        self._dense, self._lexical = list(dense), list(lexical)
        self._claims = {"dense": list(claims_dense), "lexical": list(claims_lexical)}
        self._chunks = list(chunks)
        self.calls: list[tuple] = []

    def nearest_cards(self, representation, vector, k):
        self.calls.append(("nearest_cards", representation, k))
        return self._dense[:k]

    def search_cards(self, representation, query, k):
        self.calls.append(("search_cards", representation, k))
        return self._lexical[:k]

    def nearest_claims(self, vector, k, claim_filter):
        return self._claims["dense"][:k]

    def search_claims(self, query, k, claim_filter):
        return self._claims["lexical"][:k]

    def search_chunks(self, query, k):
        return self._chunks[:k]


def hit(ref: str, kind: str = "record") -> NodeHit:
    return NodeHit(ref=ref, kind=kind, label="Thing", title=ref.title())


def fact(claim_id: str, chunk_id: str = "d.md#0") -> ClaimFact:
    return ClaimFact(id=claim_id, sentence=f"sentence {claim_id}", chunk_id=chunk_id, truth="affirmed",
                     triple_truth="affirmed", modality="actual", hedge="", condition="")  # fmt: skip


class FakeReader:
    """`NodeReader` with fixed nodes, relations and claims; records what it is asked."""

    def __init__(self, nodes=(), facts=None, starts=None, page=None, claims=(), mentioned=None):
        self._nodes = {n.ref: n for n in nodes}
        self._facts = facts or {}
        self._starts = starts or {}
        self._page = page or RelationPage(relations=[], total=0)
        self._claims = {c.id: c for c in claims}
        self._mentioned = mentioned or MentionPage(things=[], total=0, labels=[])
        self.asked: list[tuple] = []

    def nodes(self, refs):
        return [self._nodes[r] for r in refs if r in self._nodes]

    def node(self, ref):
        return self._facts.get(ref)

    def start(self, ref):
        return self._starts.get(ref)

    def relations(self, ref, relation, limit):
        self.asked.append(("relations", ref, relation, limit))
        return self._page

    def node_claims(self, ref, chunk_ids):
        self.asked.append(("node_claims", ref, list(chunk_ids)))
        return [c for c in self._claims.values() if c.chunk_id in chunk_ids]

    def claims(self, claim_ids):
        self.asked.append(("claims", list(claim_ids)))
        return [self._claims[i] for i in claim_ids if i in self._claims]

    def mentioned(self, label, contains, limit, names):
        self.asked.append(("mentioned", label, contains, limit, names))
        return self._mentioned


# --- find_nodes --------------------------------------------------------------------------------------


def find_nodes(units, reader, k=3):
    return FindNodes(units, reader, FixedEmbedder(), "template", SETTING, k)


def test_find_nodes_fuses_both_card_lists_and_keeps_the_first_k_that_name_a_node():
    units = SplitUnitStore(dense=["a", "gone", "x"], lexical=["b", "a"])
    reader = FakeReader(nodes=[hit("a"), hit("b"), hit("x")])
    found = find_nodes(units, reader, k=2).run(FindNodesArguments(query="the Meridian Kettle"))
    # each list read two deep ("x" is third in the dense list): a is in both, b and gone once each; the card
    # whose node is gone is left out
    assert [n.ref for n in found.nodes] == ["a", "b"] and found.cites() == ["a", "b"]
    assert units.calls == [("nearest_cards", "template", 2), ("search_cards", "template", 2)]


def test_find_nodes_without_a_word_searches_by_vector_only_and_says_when_nothing_matches():
    units = SplitUnitStore(dense=["gone"], lexical=["b"])
    found = find_nodes(units, FakeReader()).run(FindNodesArguments(query="?!"))
    assert found.nodes == [] and found.note == "No node matches the query."
    assert [c[0] for c in units.calls] == ["nearest_cards"]


# --- get_node and neighbours -------------------------------------------------------------------------


def test_get_node_cuts_long_values_and_extra_names_and_says_so():
    node = NodeFacts(
        **hit("Kettle:K1").model_dump(), aliases=["k1", "kettle one", "the kettle"],
        properties=[Property(name="notes", value="x" * 12, source="kettles.csv row id=K1"),
                    Property(name="id", value="K1", source="kettles.csv row id=K1")],
    )  # fmt: skip
    result = GetNode(FakeReader(facts={"Kettle:K1": node}), value_chars=5, names=2).run(
        NodeArguments(ref="Kettle:K1")
    )
    assert [p.value for p in result.node.properties] == ["xxxxx...", "K1"]
    assert result.node.aliases == ["k1", "kettle one"]
    assert result.note == "notes cut at 5 characters; 2 of 3 other names shown"
    assert result.cites() == ["Kettle:K1"]


def test_an_unknown_ref_is_answered_with_a_note_by_every_node_tool():
    reader = FakeReader()
    store = FakeStore()
    assert (
        GetNode(reader, 10, 10).run(NodeArguments(ref="nope")).note.startswith("No node has the ref 'nope'")
    )
    listed = Neighbours(reader, 5).run(NeighboursArguments(ref="nope"))
    assert listed.relations == [] and listed.note.startswith("No node has the ref")
    read = NodeEvidence(reader, store, FixedEmbedder(), 3).run(EvidenceArguments(ref="nope", about="x"))
    assert read.chunks == [] and read.note.startswith("No node has the ref") and store.reach_calls == []
    assert reader.asked == []  # nothing read for a ref that names no node


def relation(ref: str, claims=()) -> Relation:
    return Relation(via="claim" if claims else "record", type="HAS", outgoing=True, ref=ref, name=ref,
                    label="Thing", count=max(len(claims), 1), claims=list(claims))  # fmt: skip


def test_neighbours_passes_the_type_and_limit_and_says_when_more_exist():
    page = RelationPage(relations=[relation("b"), relation("c", ["o1", "o2"])], total=7)
    reader = FakeReader(nodes=[hit("a")], page=page)
    result = Neighbours(reader, 2).run(NeighboursArguments(ref="a", relation="HAS"))
    assert reader.asked == [("relations", "a", "HAS", 2)]
    assert result.total == 7 and result.note.startswith("2 of 7 relations shown")
    assert result.cites() == ["a", "b", "c", "o1", "o2"]


def test_neighbours_of_a_node_without_relations_says_none_was_found():
    result = Neighbours(FakeReader(nodes=[hit("a")]), 2).run(NeighboursArguments(ref="a"))
    assert (result.relations, result.note) == ([], "No relation found.")


def test_relation_ids_are_offered_once_each():
    result = RelationsResult(ref="a", relations=[relation("b", ["o1"]), relation("b", ["o1", "o2"])], total=2)
    assert result.cites() == ["a", "b", "o1", "o2"]


# --- node_evidence -----------------------------------------------------------------------------------


class ReachingStore(FakeStore):
    """A graph store whose traversal reaches fixed chunks per pattern; records the starts."""

    def __init__(self, reached, chunks):
        super().__init__(chunks=chunks)
        self._reached_by = reached

    def reach(self, things, kinds):
        self.reach_calls.append((things, kinds))
        return self._reached_by


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (CardStart(kind="thing", node_id="4:x:9"), (["4:x:9"], [])),
        (CardStart(kind="kind", node_id="k1"), ([], ["k1"])),
    ],
)
def test_node_evidence_starts_a_record_as_a_thing_and_a_kind_as_a_kind(start, expected):
    store = ReachingStore({"p": set()}, [])
    reader = FakeReader(starts={"n": start})
    NodeEvidence(reader, store, FixedEmbedder(), 2).run(EvidenceArguments(ref="n", about="x"))
    assert store.reach_calls == [expected]


def test_node_evidence_ranks_the_reached_chunks_by_about_and_gives_each_its_claims():
    chunks = [chunk("d.md#0", [0.0, 1.0]), chunk("d.md#1", [1.0, 0.0]), chunk("d.md#2", [0.7, 0.7])]
    store = ReachingStore({"one": {"d.md#0", "d.md#1"}, "two": {"d.md#1", "d.md#2"}}, chunks)
    claims = [fact("o1", "d.md#1"), fact("o2", "d.md#2"), fact("o3", "d.md#0")]
    reader = FakeReader(starts={"n": CardStart(kind="kind", node_id="n")}, claims=claims)
    embedder = FixedEmbedder({"how it is made": [1.0, 0.0]})
    result = NodeEvidence(reader, store, embedder, 2).run(EvidenceArguments(ref="n", about="how it is made"))
    assert [c.chunk_id for c in result.chunks] == ["d.md#1", "d.md#2"]  # nearest `about` first, capped
    assert [[f.id for f in c.claims] for c in result.chunks] == [["o1"], ["o2"]]
    assert (result.total, result.note) == (3, "2 of 3 passages shown")
    assert ("node_claims", "n", ["d.md#1", "d.md#2"]) in reader.asked  # only the shown chunks' claims
    assert embedder.batches == [["how it is made"]]
    assert result.cites() == ["d.md#1", "o1", "d.md#2", "o2"]


# --- search_text and search_claims -------------------------------------------------------------------


def test_search_text_fuses_the_vector_and_word_lists_and_keeps_the_cap():
    store = FakeStore(chunks=[chunk(f"d.md#{i}", [1.0, 0.0], f"text {i}") for i in range(4)],
                      nearest=["d.md#0", "d.md#1", "d.md#3"])  # fmt: skip
    units = SplitUnitStore(chunks=["d.md#2", "d.md#1"])
    result = SearchText(store, units, FixedEmbedder(), rrf_k=60, limit=2).run(
        SearchArguments(query="kettle lid")
    )
    # d.md#1 is in both lists; each list is read two deep, so d.md#3 is never asked for
    assert [c.chunk_id for c in result.chunks] == ["d.md#1", "d.md#0"]
    assert result.chunks[0].text == "text 1" and result.total == 3
    assert store.nearest_calls == [2]


def claim_hit(claim_id: str, siblings=()) -> ClaimHit:
    return ClaimHit(id=claim_id, chunk_id="d.md#0", truth="affirmed", siblings=list(siblings))


def test_search_claims_follows_each_claim_with_its_opposite_truth_siblings_within_the_cap():
    units = SplitUnitStore(claims_dense=[claim_hit("o1", ["o9"]), claim_hit("o2")],
                           claims_lexical=[claim_hit("o1", ["o9"]), claim_hit("o3")])  # fmt: skip
    reader = FakeReader(claims=[fact(i) for i in ("o1", "o2", "o3", "o9")])
    result = SearchClaims(units, reader, FixedEmbedder(), rrf_k=60, limit=3).run(SearchArguments(query="lid"))
    # o1 is first in both lists, then its sibling o9, then o2 (the dense list comes first on a tie)
    assert [c.id for c in result.claims] == ["o1", "o9", "o2"]
    assert reader.asked == [("claims", ["o1", "o9", "o2"])]
    assert result.cites() == ["o1", "d.md#0", "o9", "o2"]


def test_search_claims_says_when_nothing_was_found():
    result = SearchClaims(SplitUnitStore(), FakeReader(), FixedEmbedder(), 60, 3).run(
        SearchArguments(query="lid")
    )
    assert (result.claims, result.note) == ([], "No claim found.")


# --- rank_by_documents (R150) ------------------------------------------------------------------------


def counted(ref: str, documents: int) -> MentionCount:
    return MentionCount(ref=ref, kind="individual", label="Person", name=ref.title(), documents=documents,
                        spellings=[ref])  # fmt: skip


def test_rank_by_documents_passes_the_filters_trimmed_and_lowercase_with_its_caps_and_offers_the_refs():
    page = MentionPage(things=[counted("i-ab", 3), counted("i-abe", 1)], total=2, labels=["Person"])
    reader = FakeReader(mentioned=page)
    result = RankByDocuments(reader, 2, 3).run(RankArguments(contains=" @Ab ", label="Person"))
    assert reader.asked == [("mentioned", "Person", "@ab", 2, 3)]
    assert [t.documents for t in result.things] == [3, 1] and result.total == 2
    assert result.cites() == ["i-ab", "i-abe"] and result.note == ""  # all shown: nothing to say
    RankByDocuments(reader, 2, 3).run(RankArguments())
    assert reader.asked[-1] == ("mentioned", None, None, 2, 3)  # no filter asks for everything


def test_rank_by_documents_says_when_more_exist_when_a_label_is_unknown_and_when_nothing_matches():
    cut = MentionPage(things=[counted("i-a", 4), counted("i-b", 2)], total=5, labels=["Person"])
    assert (
        RankByDocuments(FakeReader(mentioned=cut), 2, 3).run(RankArguments()).note
        == "2 of 5 shown; give a label or some characters of the name to narrow them."
    )
    empty = MentionPage(things=[], total=0, labels=["", "Condition", "Person"])  # "": a record, no plan
    unknown = RankByDocuments(FakeReader(mentioned=empty), 2, 3).run(RankArguments(label="Nope"))
    assert unknown.note == (
        "No label 'Nope' among the things the text names; the labels are Condition, Person."
    )
    known = RankByDocuments(FakeReader(mentioned=empty), 2, 3).run(RankArguments(label="Person"))
    assert known.note == "No named thing matches."  # a known label with no match is no unknown label
    assert RankByDocuments(FakeReader(mentioned=empty), 2, 3).run(RankArguments()).note == (
        "No named thing matches."
    )


def test_rank_by_documents_lists_no_labels_when_the_text_names_nothing():
    """A graph with no REFERS_TO edges (text never resolved) has no labels: the note is no broken list."""
    nothing = MentionPage(things=[], total=0, labels=[])
    result = RankByDocuments(FakeReader(mentioned=nothing), 2, 3).run(RankArguments(label="Person"))
    assert result.note == "No named thing matches."


def test_rank_by_documents_refuses_a_blank_text_to_look_for():
    with pytest.raises(ValidationError):
        RankArguments(contains="  ")
    assert RankArguments().model_dump() == {"contains": None, "label": None}


# --- what the model reads ----------------------------------------------------------------------------

# words of the evaluated datasets (furniture reviews, vehicle complaints and recalls, the generality set's
# equipment logs): none may reach the model through a tool's description or its arguments' descriptions
DATASET_WORDS = re.compile(
    r"\b(product|dresser|drawer|furniture|part|parts|defect|vehicle|car|brake|recall|complaint|pump|"
    r"review|engine)s?\b",
    re.IGNORECASE,
)
TOOLS = [
    FindNodes, GetNode, Neighbours, NodeEvidence, SearchText, SearchClaims, ReadDocument, FindPassages,
    RankByDocuments,
]  # fmt: skip


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_what_the_model_reads_of_a_tool_names_no_evaluated_dataset(tool):
    texts = [tool.description] + [f.description or "" for f in tool.arguments.model_fields.values()]
    assert all(not DATASET_WORDS.search(text) for text in texts), texts
    assert all(f.description for f in tool.arguments.model_fields.values())  # every argument explained
