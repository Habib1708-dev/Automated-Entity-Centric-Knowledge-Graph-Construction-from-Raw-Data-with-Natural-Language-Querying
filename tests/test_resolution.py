"""Entity resolution: candidate finding, decisions and grouping as pure functions, the candidate preview,
the merge -> undo round trip against Neo4j, and the rule that merging keeps every separately stated fact."""

import json
import math

import pytest

from kgbuilder.config import Settings
from kgbuilder.pipeline import stages as st
from kgbuilder.pipeline.runner import run_stages
from kgbuilder.pipeline.stage import PipelineContext, PipelineState
from kgbuilder.resolution.blocking import MutualNearest, ScoreThreshold
from kgbuilder.resolution.matchers import EmbeddingMatcher, EntityRecord
from kgbuilder.resolution.resolver import (
    MentionRow,
    SamePair,
    decide,
    decide_in_passes,
    find_candidates,
    group_merges,
    mention_lines,
    nominate,
    preview,
    resolve_entities,
    undo_merges,
)
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document
from kgbuilder.text.extraction import Triple
from kgbuilder.text.lexical import write_lexical_graph
from kgbuilder.text.subject_graph import write_subject_graph
from kgbuilder.validation.checks import CheckContext
from kgbuilder.validation.judge import build_sheet

from .fakes import RecordingTracker, ScriptedLLM


def entity(id: str, name: str, type: str = "Product", mentions: int = 1) -> EntityRecord:
    return EntityRecord(id=id, name=name, type=type, aliases=[name], mentions=mentions)


ENTITIES = [
    entity("t1", "Table", mentions=3),
    entity("t2", "Tables"),
    entity("t3", "Table Lamp"),
    entity("s1", "Sofa"),
    entity("x1", "Table", type="Problem"),  # same name, other type: never a candidate
]
BY_ID = {e.id: e for e in ENTITIES}


def pairs(candidates):
    return {(c.a, c.b) for c in candidates}


def test_candidates_are_same_type_pairs_above_the_borderline():
    assert pairs(find_candidates(ENTITIES, borderline=80)) == {("t1", "t2")}
    assert ("t1", "t3") in pairs(find_candidates(ENTITIES, borderline=50))
    assert not any("x1" in p for p in pairs(find_candidates(ENTITIES, borderline=0.1)))


class SynonymEmbedder:
    """Puts "Sofa" and "Couch" on one vector and every other name on another: cosine 100 or 0."""

    def embed(self, texts):
        return [[1.0, 0.0] if t in ("Sofa", "Couch") else [0.0, 1.0] for t in texts]


def test_embeddings_nominate_synonyms_but_never_auto_merge():
    records = [entity("s1", "Sofa"), entity("s2", "Couch"), entity("t1", "Table")]
    matcher = EmbeddingMatcher(SynonymEmbedder(), records)
    candidates = find_candidates(records, borderline=80, embedding=matcher, blocking=ScoreThreshold(95))
    assert [(c.a, c.b, c.signal, c.score) for c in candidates] == [("s1", "s2", "embedding", 100.0)]
    by_id = {e.id: e for e in records}
    assert decide(candidates, by_id, auto_merge=92, adjudicate=None)[0].action == "skipped_borderline"
    assert decide(candidates, by_id, auto_merge=92, adjudicate=lambda a, b: True)[0].action == "llm_merge"


class AngleEmbedder:
    """Each name sits on the unit circle at a fixed angle, so cosine similarity follows the angle."""

    ANGLES = {"da": 0, "dbb": 2.9, "dccc": 16.7, "wccc": 31, "wbb": 42, "wa": 43.5, "k-apart": 90}

    def embed(self, texts):
        return [
            [math.cos(math.radians(self.ANGLES[t])), math.sin(math.radians(self.ANGLES[t]))] for t in texts
        ]


def test_a_second_pass_joins_groups_that_filled_each_others_nearest_slots():
    """Six wordings of one kind in two trios (R45, the misaligned holes of R41): each trio fills its
    members' 2 nearest slots, so the bridge "dccc" / "wccc" is not mutual and one pass leaves two groups.
    Merged, each trio is one entity and the two meet. "k-apart" is answered "not the same" every time."""
    records = [entity(n, n, "Defect") for n in AngleEmbedder.ANGLES]
    asked = []

    def adjudicator_for(candidates, members):
        def same(a, b):
            # a merged entity keeps its canonical id, so "the same question" means the same members
            asked.append((tuple(a.aliases), tuple(b.aliases)))
            return "k-apart" not in (a.id, b.id)

        return same

    def run(max_passes):
        asked.clear()
        matcher = EmbeddingMatcher(AngleEmbedder(), records)
        return decide_in_passes(records, 101, matcher, MutualNearest(2), 92, adjudicator_for, max_passes)

    _, groups, passes = run(max_passes=1)
    assert sorted(sorted([g.canonical, *g.absorbed]) for g in groups) == [
        ["da", "dbb", "dccc"],
        ["wa", "wbb", "wccc"],
    ]
    _, (group,), passes = run(max_passes=3)
    assert sorted([group.canonical, *group.absorbed]) == ["da", "dbb", "dccc", "wa", "wbb", "wccc"]
    assert passes == 3  # the third pass asks the merged six about "k-apart" once more and merges nothing
    assert len(asked) == len(set(asked)) == 9  # 5 + 3 + 1: never the same members asked twice


def test_meaning_based_candidates_need_an_embedder_and_a_threshold():
    records = [entity("s1", "Sofa"), entity("s2", "Couch")]
    assert nominate(records, 80, SynonymEmbedder(), blocking=None) == []  # no blocking rule = switched off
    assert nominate(records, 80, None, blocking=ScoreThreshold(95)) == []  # no embedder, nothing to compare
    assert [c.signal for c in nominate(records, 80, SynonymEmbedder(), blocking=ScoreThreshold(95))] == [
        "embedding"
    ]


def test_preview_names_each_pair_its_route_and_orders_by_score():
    records = [*ENTITIES, entity("s2", "Couch")]
    candidates = nominate(records, borderline=50, embedder=SynonymEmbedder(), blocking=ScoreThreshold(95))
    rows = [(p.signal, p.a, p.b, p.route) for p in preview(records, candidates, auto_merge=90).pairs]
    assert rows[0] == ("embedding", "Sofa", "Couch", "llm")  # a meaning score always goes to the LLM
    fuzzy = [r for r in rows if r[0] == "fuzzy"]
    assert fuzzy[0] == ("fuzzy", "Table", "Tables", "auto")  # 90.9 >= 90: merged on spelling alone
    assert all(route == "llm" for *_, route in fuzzy[1:])


def test_decisions_auto_llm_and_skipped():
    candidates = find_candidates(ENTITIES, borderline=50)
    actions = {(d.a_id, d.b_id): d.action for d in decide(candidates, BY_ID, 90, lambda a, b: False)}
    assert actions[("t1", "t2")] == "auto" and actions[("t1", "t3")] == "llm_keep"


def test_grouping_is_transitive_and_the_most_mentioned_entity_stays():
    records = [entity("a", "Desk"), entity("b", "Desks", mentions=5), entity("c", "Desk's")]
    by_id = {e.id: e for e in records}
    decisions = decide(find_candidates(records, borderline=80), by_id, auto_merge=80, adjudicate=None)
    (group,) = group_merges(by_id, decisions)
    assert group.canonical == "b" and sorted(group.absorbed) == ["a", "c"]


def test_a_merged_group_is_named_by_its_most_general_name():
    """For a kind merged across products, the shortest name is the one true of every member (R44): the
    longest carried one review's detail to all of them."""
    records = [entity("h", "crack developing along the bottom", "Defect"), entity("l", "crack", "Defect")]
    by_id = {e.id: e for e in records}
    decisions = decide(find_candidates(records, borderline=0), by_id, 90, lambda a, b: True)
    (group,) = group_merges(by_id, decisions)
    assert group.canonical == "l" and group.absorbed == ["h"]


def dump(driver) -> dict:
    """Everything entity resolution may touch, in a comparable form."""

    def rows(query):
        return sorted(str(r.data()) for r in driver.execute_query(query)[0])

    return {
        "entities": rows("MATCH (e:Entity) RETURN properties(e) AS p"),
        "mentions": rows("MATCH (c:Chunk)-[:MENTIONS]->(e:Entity) RETURN c.chunk_id AS c, e.id AS e"),
        "observations": rows("MATCH (o:Observation) RETURN properties(o) AS p"),
        # SUBJECT and OBJECT point at entities (by id), FROM at a chunk (by chunk id)
        "edges": rows(
            "MATCH (o:Observation)-[r]->(n) RETURN o.id AS o, type(r) AS t, coalesce(n.id, n.chunk_id) AS n"
        ),
    }


def fact(subject, predicate, obj, obj_type, chunk, evidence=None):
    return Triple(
        subject=subject, subject_type="Product", predicate=predicate, object=obj, object_type=obj_type,
        evidence=evidence or f"{subject} {obj}", chunk_id=chunk,
    )  # fmt: skip


@pytest.mark.neo4j
def test_merging_keeps_every_separately_stated_fact_and_drops_exact_repeats(driver):
    """Two chunks saying "the table wobbles" are two pieces of evidence (subject_graph.py writes one
    observation per chunk and wording). Merging "Table" into "Tables" must not fold them into one; only an
    observation identical in predicate, ends, chunk and quote after the merge is a repeat (R64's rule)."""
    chunks = [Chunk(chunk_id=f"d.md#{i}", doc_id="d.md", index=i, text=f"text {i}") for i in range(2)]
    write_lexical_graph(driver, [Document(doc_id="d.md", title="d", text="x")], chunks)
    write_subject_graph(
        driver,
        [
            fact("Table", "HAS_PROBLEM", "wobble", "Problem", "d.md#0"),
            fact("Tables", "HAS_PROBLEM", "wobble", "Problem", "d.md#1"),
            # the same statement once more under the other spelling: identical after the merge
            fact("Table", "HAS_PROBLEM", "wobble", "Problem", "d.md#1", evidence="Tables wobble"),
        ],
        extractor="test",
    )
    report = resolve_entities(driver, None, model="m", auto_merge=90)

    records, _, _ = driver.execute_query(
        "MATCH (:Entity)<-[:SUBJECT]-(f:Observation {predicate: 'HAS_PROBLEM'})"
        "-[:OBJECT]->(:Entity {name: 'wobble'}) "
        "RETURN f.chunk_id AS chunk, f.evidence AS evidence, f.subject_name AS said ORDER BY chunk"
    )
    # of the two repeats in d.md#1, the one with the first wording survives, on every rebuild (R44)
    assert [(r["chunk"], r["evidence"], r["said"]) for r in records] == [
        ("d.md#0", "Table wobble", "Table"),
        ("d.md#1", "Tables wobble", "Table"),
    ]
    mentions, _, _ = driver.execute_query(
        "MATCH (c:Chunk)-[m:MENTIONS]->(e:Entity) WHERE e.name <> 'wobble' "
        "RETURN c.chunk_id AS c, count(m) AS n"
    )
    assert {r["c"]: r["n"] for r in mentions} == {"d.md#0": 1, "d.md#1": 1}  # one mention per chunk, not two
    assert report.merges == 1 and report.duplicate_facts_removed == 1


@pytest.mark.neo4j
def test_each_fact_keeps_its_own_wording_after_a_merge(driver):
    """The node takes one name for the kind; the fact still says what its review said (R44)."""
    chunks = [Chunk(chunk_id=f"{d}.md#0", doc_id=f"{d}.md", index=0, text="text") for d in ("h", "l")]
    documents = [Document(doc_id=f"{d}.md", title=d, text="x") for d in ("h", "l")]
    write_lexical_graph(driver, documents, chunks)
    detailed = "crack developing along the bottom"
    write_subject_graph(
        driver,
        [
            fact("Helsingborg Dresser", "HAS_DEFECT", detailed, "Defect", "h.md#0"),
            fact("Linköping Bed", "HAS_DEFECT", "crack", "Defect", "l.md#0"),
        ],
        extractor="test",
    )
    # only the two defects are the same kind; the products stay apart
    llm = ScriptedLLM(lambda prompt, schema: SamePair(same="crack developing" in prompt))
    report = resolve_entities(driver, llm, model="m", borderline=0)
    assert report.merges == 1

    sheet = build_sheet(CheckContext(driver).facts, [])
    assert sorted((f.subject, f.object) for f in sheet.facts) == [
        ("Helsingborg Dresser", detailed),
        ("Linköping Bed", "crack"),
    ]
    (name,) = driver.execute_query("MATCH (e:Entity {type: 'Defect'}) RETURN e.name AS n")[0]
    assert name["n"] == "crack"


@pytest.mark.neo4j
def test_three_reviews_of_one_claim_stay_three_observations_and_a_rewrite_adds_nothing(driver):
    """Counting reports is a question the observation graph must answer ("how many reviews say the drawers
    stick?"): a merge of the claim's wordings keeps one observation per review, and writing the same
    extraction again (a rerun of `kg extract`) adds no node or edge."""
    documents = [Document(doc_id=f"{d}.md", title=d, text="x") for d in ("a", "b", "c")]
    chunks = [Chunk(chunk_id=f"{d}.md#0", doc_id=f"{d}.md", index=0, text="text") for d in ("a", "b", "c")]
    write_lexical_graph(driver, documents, chunks)
    triples = [
        fact("Table", "HAS_PROBLEM", "wobble", "Problem", "a.md#0"),
        fact("Table", "HAS_PROBLEM", "wobble", "Problem", "b.md#0"),
        fact("Tables", "HAS_PROBLEM", "wobble", "Problem", "c.md#0"),
    ]
    first = write_subject_graph(driver, triples, extractor="test")
    before = dump(driver)
    assert write_subject_graph(driver, triples, extractor="test") == first and dump(driver) == before

    report = resolve_entities(driver, None, model="m", auto_merge=90)  # "Table"/"Tables" 90.9: merged
    assert report.merges == 1 and report.duplicate_facts_removed == 0
    records, _, _ = driver.execute_query(
        "MATCH (:Entity {type: 'Product'})<-[:SUBJECT]-(o:Observation)-[:FROM]->(c:Chunk) "
        "RETURN c.chunk_id AS chunk ORDER BY chunk"
    )
    assert [r["chunk"] for r in records] == ["a.md#0", "b.md#0", "c.md#0"]


@pytest.mark.neo4j
def test_merge_then_undo_restores_the_graph(driver):
    chunks = [Chunk(chunk_id=f"d.md#{i}", doc_id="d.md", index=i, text=f"text {i}") for i in range(2)]
    write_lexical_graph(driver, [Document(doc_id="d.md", title="d", text="x")], chunks)
    write_subject_graph(
        driver,
        [
            fact("Table", "HAS_PROBLEM", "wobble", "Problem", "d.md#0"),
            fact("Tables", "HAS_PROBLEM", "scratch", "Problem", "d.md#1"),
            fact("Table", "SIMILAR_TO", "Tables", "Product", "d.md#1"),  # becomes a self-loop when merged
        ],
        extractor="test",
    )
    before = dump(driver)

    llm = ScriptedLLM(lambda prompt, schema: SamePair(same=False))
    report = resolve_entities(driver, llm, model="m", auto_merge=90)  # "Table"/"Tables" scores 90.9
    assert report.merges == 1 and report.self_loops_removed == 1
    # the self-reference is gone and the other two claims now hang on the merged node
    assert len(report.observations) == 3
    after = driver.execute_query(
        "MATCH (o:Observation)-[:SUBJECT]->(e:Entity) RETURN o.predicate AS p, e.name AS e ORDER BY p"
    )[0]
    assert {r["e"] for r in after} == {"Table"} and [r["p"] for r in after] == ["HAS_PROBLEM", "HAS_PROBLEM"]
    assert report.entities_after == report.entities_before - 1
    assert dump(driver) != before

    assert undo_merges(driver, report) == 1
    assert dump(driver) == before
    assert undo_merges(driver, report) == 1 and dump(driver) == before  # idempotent


@pytest.mark.neo4j
def test_preview_stage_lists_candidates_and_changes_nothing(driver, tmp_path):
    chunks = [Chunk(chunk_id=f"d.md#{i}", doc_id="d.md", index=i, text=f"text {i}") for i in range(2)]
    write_lexical_graph(driver, [Document(doc_id="d.md", title="d", text="x")], chunks)
    write_subject_graph(
        driver,
        [
            fact("Table", "HAS_PROBLEM", "wobble", "Problem", "d.md#0"),
            fact("Tables", "HAS_PROBLEM", "scratch", "Problem", "d.md#1"),
        ],
        extractor="test",
    )
    before = dump(driver)
    tracker = RecordingTracker()
    ctx = PipelineContext(settings=Settings(), driver=driver, out=tmp_path, tracker=tracker)

    result = run_stages(ctx, PipelineState(), [st.PreviewResolveStage()]).resolve_preview
    assert [{p.a, p.b} for p in result.pairs] == [{"Table", "Tables"}]  # order follows entity ids
    assert dump(driver) == before
    run = tracker.run("resolve_preview")
    assert run.logged_metrics["candidates"] == 1 and run.logged_metrics["candidates_embedding"] == 0
    assert run.logged_params["embed_model"] is None  # no embedder: meaning-based candidates impossible
    written = json.loads((tmp_path / st.PreviewResolveStage.PREVIEW_FILE).read_text(encoding="utf-8"))
    assert written["pairs"][0]["route"] in ("auto", "llm")


def row(entity: str, names: list[str], document: str, text: str) -> MentionRow:
    return MentionRow(entity=entity, names=names, document=document, text=text)


def test_mention_lines_show_the_sentences_that_name_the_entity_with_their_document():
    long_praise = "Lovely colour and a great price overall, we are very happy with it. " * 5
    rows = [
        row("r", ["drawer rails"], "Dresser Reviews", long_praise + "The drawer rails stick badly."),
        row("r", ["drawer rails"], "Dresser Reviews", "Nothing about them here."),  # reached via context
        row("r", ["drawer rails", "metal rails"], "Desk Reviews", "The metal rails are rough."),  # alias
        row("r", ["drawer rails"], "Desk Reviews", "The drawer rails stick badly."),  # repeated sentence
        row("r", ["drawer rails"], "Bed Reviews", "Drawer rails wobble."),
        row("r", ["drawer rails"], "Sofa Reviews", "Drawer rails squeak."),  # beyond the limit of 3
    ]
    assert mention_lines(rows, limit=3) == {
        "r": [
            # the name is past character 300 of its chunk: the old 300-character window never showed it
            "[Dresser Reviews] The drawer rails stick badly.",
            "[Desk Reviews] The metal rails are rough.",
            "[Desk Reviews] The drawer rails stick badly.",
        ]
    }
    assert mention_lines([row("x", ["shade"], "Lamp Reviews", "No mention.")]) == {"x": []}


def test_a_merged_entity_shows_the_sentences_of_all_its_members():
    rows = [
        row("a", ["crack"], "Bed Reviews", "The slats started to crack."),
        row("b", ["crack along the bottom"], "Dresser Reviews", "A crack along the bottom of a drawer."),
        row("x", ["dent"], "Lamp Reviews", "A small dent."),
    ]
    assert mention_lines(rows, owner={"a": "a", "b": "a", "x": "x"}) == {
        "a": [
            "[Bed Reviews] The slats started to crack.",
            "[Dresser Reviews] A crack along the bottom of a drawer.",
        ],
        "x": ["[Lamp Reviews] A small dent."],
    }


@pytest.mark.neo4j
def test_the_adjudication_prompt_carries_each_names_sentences_and_document(driver):
    chunks = [
        Chunk(
            chunk_id="d.md#0",
            doc_id="d.md",
            index=0,
            text="Great desk. The Table wobbles.",
            context="Desk Reviews",
        ),
        Chunk(
            chunk_id="d.md#1",
            doc_id="d.md",
            index=1,
            text="The Tables scratch easily.",
            context="Desk Reviews",
        ),
    ]
    write_lexical_graph(driver, [Document(doc_id="d.md", title="d", text="x")], chunks)
    write_subject_graph(
        driver,
        [
            fact("Table", "HAS_PROBLEM", "wobble", "Problem", "d.md#0"),
            fact("Tables", "HAS_PROBLEM", "scratch", "Problem", "d.md#1"),
        ],
        extractor="test",
    )
    prompts: list[str] = []

    def answer(prompt, schema):
        prompts.append(prompt)
        return SamePair(same=False)

    resolve_entities(driver, ScriptedLLM(answer), model="m", auto_merge=95)  # "Table"/"Tables" 90.9: ask
    [prompt] = prompts
    assert "- [Desk Reviews] The Table wobbles." in prompt  # the sentence, not the praise before it
    assert "- [Desk Reviews] The Tables scratch easily." in prompt
    assert "Great desk" not in prompt
    # R41: the type decides between "same item" and "same kind" (entities are kinds across documents)
    assert "If Product names individual items" in prompt and "the same kind" in prompt
