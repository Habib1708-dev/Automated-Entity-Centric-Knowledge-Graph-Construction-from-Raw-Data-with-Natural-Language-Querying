"""The offline readers of a key built from several columns (R155a): the audit's records and relations, the
record documents and the target placer all name a detail row by the key the importer writes ("1|3"), so a
record ref computed offline is the one the graph holds; the audit snapshot's links and particulars leave a
built key out of text matching as the live linker does. Pure: no Neo4j, no network.
"""

from kgbuilder.anchor import read_staged, record_nodes
from kgbuilder.audit.inputs import Record, read_records, read_relations
from kgbuilder.audit.snapshot import SnapshotMention, _link, _particulars
from kgbuilder.resolution.identity_graph import Assignment
from kgbuilder.resolution.linking import key_matched, name_matched
from kgbuilder.structured.profiler import profile_directory
from kgbuilder.text.chunking import Chunk
from kgbuilder.text.documents import Document
from kgbuilder.text.record_documents import record_documents
from kgbuilder.validation.qa_gold import RecordEvidence

from .sample_plans import FRAME_FILES, FRAME_PLAN, FRAME_RULE, UNIT_PLAN, write_files


def test_records_and_relations_of_a_built_key_carry_the_joined_key(tmp_path):
    staged = write_files(tmp_path, FRAME_FILES)
    records = read_records(staged, FRAME_PLAN)
    frames = [r for r in records if r.label == "Frame"]

    # one record per row: frame 3 exists under both hives, and the pair tells them apart
    assert [r.id for r in frames] == ["Frame:1|1", "Frame:1|2", "Frame:1|3", "Frame:2|1", "Frame:2|3"]
    # no name column: the record is named by its key, as the graph's coalesce(name, key) reads it
    assert frames[2].key == "1|3" and frames[2].name == "1|3"
    assert frames[2].properties == {"frame_no": "3", "note": "empty comb"}
    relations = read_relations(staged, FRAME_PLAN, records)
    assert ("Frame:1|3", "FRAME_OF", "Hive:1") in {(r.source, r.type, r.target) for r in relations}
    assert len(relations) == 5


def test_a_record_document_of_a_built_key_is_named_by_it(tmp_path):
    staged = write_files(tmp_path, FRAME_FILES)
    profile = profile_directory(staged)
    # the notes are short; marked prose here so the frames become documents
    profile.file("frames.csv").column("note").is_prose = True

    docs = record_documents(staged, FRAME_PLAN, profile)

    assert [d.doc_id for d in docs][:3] == ["record/Frame/1|1", "record/Frame/1|2", "record/Frame/1|3"]
    assert (docs[2].record.key_property, docs[2].record.key) == ("frame_key", "1|3")


def test_a_gold_row_of_a_built_key_is_placed_on_its_record(tmp_path):
    staged = write_files(tmp_path, FRAME_FILES)
    known = {r.id for r in read_records(staged, FRAME_PLAN)}
    rows = read_staged(staged, {"frames.csv"})

    evidence = RecordEvidence(file="frames.csv", row={"hive_id": "1", "frame_no": "3"})
    assert record_nodes(evidence, FRAME_PLAN, rows, known) == ["Frame:1|3"]
    # "honey" is noted under both hives: the row picks two frames, never the hive
    by_note = RecordEvidence(file="frames.csv", row={"note": "honey"})
    assert record_nodes(by_note, FRAME_PLAN, rows, known) == ["Frame:1|2", "Frame:2|1"]


def test_a_built_key_is_matched_by_text_only_through_a_real_name_column():
    assert not key_matched(FRAME_RULE) and not name_matched(FRAME_RULE)  # named by its key: a code
    named = FRAME_RULE.model_copy(update={"name_column": "note"})
    assert not key_matched(named) and name_matched(named)
    assert key_matched(FRAME_PLAN.nodes[0]) and name_matched(FRAME_PLAN.nodes[0])  # a plain key


# "P-10|00" and "P-1000" squash to one token: only the Model's plain key may be named by the heading
UNIT_RECORDS = [
    Record(id="Model:P-1000", label="Model", key="P-1000", name="Linden", properties={}),
    Record(id="Unit:P-10|00", label="Unit", key="P-10|00", name="P-10|00", properties={}),
]
UNIT_TEXT = "# Notes P-1000\nThe unit hums."


def test_the_snapshot_links_no_title_or_heading_to_a_built_key():
    doc = Document(doc_id="p1000_reviews.md", title="p1000_reviews", text=UNIT_TEXT)
    chunk = Chunk(chunk_id="p1000_reviews.md#0", doc_id=doc.doc_id, index=0, text=UNIT_TEXT)

    documents, sections = _link([doc], [chunk], UNIT_RECORDS, UNIT_PLAN)

    # the same graph test_linking's live test builds: no document link, one section link to the Model
    assert documents == []
    assert [(s.source, s.thing) for s in sections] == [("p1000_reviews.md#0", "Model:P-1000")]


def test_a_particular_of_a_built_key_is_not_named_by_that_key():
    mention = SnapshotMention(
        id="m1", name="the unit", type="Thing", doc_id="p1000_reviews.md", chunks=[], derived=False
    )
    to_unit = Assignment(
        mention="m1", said="the unit", kind="record", canonical="Unit:P-10|00", name="hum unit", type="Thing",
        reason="name",
    )  # fmt: skip

    [particular] = _particulars([to_unit], [mention], UNIT_RECORDS, UNIT_PLAN)

    # `read_particulars` takes record keys from `read_record_keys`, which leaves the built key out
    assert particular.names == ["the unit", "hum unit"]
