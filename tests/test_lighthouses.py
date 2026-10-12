"""The lighthouses benchmark dataset (heldout/lighthouses/): the data and the attribution file are exactly
what its build script writes from the raw records, the raw records follow the selection rule, the article
prose is kept verbatim, no cell repeats a value, the pipeline's LLM-free first stages accept the data
(three tables staged, the keys to the lighthouses and the engineers found), and document linking gives
the known result: nine of ten documents by the default name column, all ten by the article column.
No Neo4j, no network, no LLM.
"""

import csv
import importlib.util
import json
from pathlib import Path

from kgbuilder.resolution.linking import DomainNode, match_document
from kgbuilder.structured.plan import NodeRule, name_property
from kgbuilder.structured.profiler import profile_directory
from kgbuilder.structured.staging import stage_structured

REPO = Path(__file__).resolve().parent.parent
LIGHTHOUSES = REPO / "heldout" / "lighthouses"
RAW, DATA = LIGHTHOUSES / "raw", LIGHTHOUSES / "data"

_spec = importlib.util.spec_from_file_location("lighthouses_build", LIGHTHOUSES / "build.py")
lighthouses = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lighthouses)


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def raw(name: str) -> dict:
    return json.loads((RAW / name).read_text("utf-8"))


def test_the_committed_dataset_is_exactly_what_build_writes_from_raw(tmp_path):
    # proves that nothing in data/ was edited by hand: the benchmark must not be shaped by its author
    lighthouses.build(RAW, tmp_path)
    assert files(tmp_path) == files(DATA)
    committed = (LIGHTHOUSES / "ATTRIBUTION.md").read_text("utf-8")
    assert committed == lighthouses.attribution(RAW), "ATTRIBUTION.md is written by build.py"


def statement(value: str, rank: str = "normal") -> dict:
    return {"rank": rank, "mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}


def test_a_preferred_value_wins_else_every_normal_value_and_deprecated_never_counts():
    values = [statement("a"), statement("b", "preferred"), statement("c", "deprecated")]
    assert [s["mainsnak"]["datavalue"]["value"] for s in lighthouses.best_statements(values)] == ["b"]
    values = [statement("a"), statement("c", "deprecated"), statement("d")]
    assert [s["mainsnak"]["datavalue"]["value"] for s in lighthouses.best_statements(values)] == ["a", "d"]


def candidate(qid: int, title: str, length: int = 8_000) -> dict:
    return {"qid": f"Q{qid}", "title": title, "length": length}


def test_document_selection_keeps_own_lighthouse_pages_in_the_byte_window_lowest_qid_first():
    candidates = [
        candidate(900, "Far Lighthouse"),
        candidate(1, "Stub Lighthouse", 5_999),  # a stub
        candidate(2, "Famous Lighthouse", 10_001),  # a long, famous article
        candidate(3, "Some Island"),  # a place article, not the lighthouse's own
        candidate(4, "High and Low Lighthouses"),  # one page shared by two items
        candidate(5, "High and Low Lighthouses"),
        *[candidate(qid, f"Lighthouse {qid}") for qid in (80, 10, 70, 60, 50, 40, 30, 20, 11, 12)],
    ]
    kept = [c["qid"] for c in lighthouses.select_documents(candidates)]
    # numeric order, not text order: Q80 comes before Q900 although "Q900" < "Q80" as text
    assert kept == ["Q10", "Q11", "Q12", "Q20", "Q30", "Q40", "Q50", "Q60", "Q70", "Q80"]


def test_every_table_record_is_a_lighthouse_operated_by_trinity_house():
    tables = raw("wikidata.json")
    for record in tables["lighthouses"]:
        for prop, item in (
            (lighthouses.INSTANCE_OF, lighthouses.LIGHTHOUSE),
            (lighthouses.OPERATOR, lighthouses.TRINITY_HOUSE),
        ):
            best = lighthouses.best_statements(record["claims"][prop])
            assert item in [s["mainsnak"]["datavalue"]["value"]["id"] for s in best], record["qid"]
    qids = [r["qid"] for r in tables["lighthouses"]]
    assert qids == sorted(set(qids), key=lighthouses.qid_number)


def test_the_table_has_one_row_per_record_and_the_engineers_are_its_architects_and_structural_engineers():
    with (DATA / "lighthouses.csv").open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["qid"] for r in rows] == [r["qid"] for r in raw("wikidata.json")["lighthouses"]]
    named = {
        name
        for row in rows
        for column in lighthouses.ENGINEER_PROPERTIES
        for name in row[column].split(lighthouses.SEPARATOR)
        if name
    }
    engineers = json.loads((DATA / "engineers.json").read_text("utf-8"))
    assert {e["name"] for e in engineers} == named
    # a joined cell splits back into its values only if no value holds the separator itself
    tables = raw("wikidata.json")
    strings = [
        st["mainsnak"]["datavalue"]["value"]
        for r in tables["lighthouses"]
        for st in r["claims"].get(lighthouses.COLUMNS["light_characteristic"], [])
        if "datavalue" in st["mainsnak"]
    ]
    values = [*tables["labels"].values(), *(r["label"] for r in tables["lighthouses"]), *strings]
    assert not [value for value in values if value and lighthouses.SEPARATOR in value]


def test_the_kept_articles_are_the_ones_the_document_rule_picks_from_the_candidates():
    candidates = raw("documents.json")["candidates"]
    kept = lighthouses.select_documents(candidates)
    assert len(kept) == lighthouses.DOCUMENTS
    articles = [json.loads(p.read_text("utf-8")) for p in sorted((RAW / "articles").glob("*.json"))]
    assert sorted(a["qid"] for a in articles) == sorted(c["qid"] for c in kept)
    titles = {c["qid"]: c["title"] for c in kept}
    assert all(a["title"] == titles[a["qid"]] and a["revid"] and a["timestamp"] for a in articles)


def test_markdown_turns_section_markers_into_headings_and_drops_empty_sections():
    extract = "Lead text.\n\n\n== History ==\nOld text.\n\n=== Early ===\nFirst.\n\n== References ==\n\n"
    assert lighthouses.markdown("A Light", extract) == (
        "# A Light\n\nLead text.\n\n## History\nOld text.\n\n### Early\nFirst.\n"
    )


def test_every_line_of_prose_in_a_document_is_verbatim_from_its_article():
    for path in sorted((RAW / "articles").glob("*.json")):
        article = json.loads(path.read_text("utf-8"))
        document = (DATA / "articles" / f"{lighthouses.slug(article['title'])}.md").read_text("utf-8")
        assert document.startswith(f"# {article['title']}\n")
        prose = [line for line in document.splitlines() if line.strip() and not line.startswith("#")]
        source = set(article["extract"].splitlines())
        assert prose and all(line in source for line in prose), path.name


def test_staging_and_profiling_find_the_keys_to_the_lighthouses_and_the_engineers(tmp_path):
    report = stage_structured(DATA, tmp_path / "staging")
    assert sorted(report.tables) == ["engineers.json", "events.ndjson", "lighthouses.csv"]
    assert report.skipped == []
    keys = {
        (fk.from_file, fk.from_column, fk.to_file, fk.to_column)
        for fk in profile_directory(report.staged_dir).foreign_keys
    }
    # the nested lighthouse object of an event is flattened into dotted columns by staging
    assert ("events.csv", "lighthouse.qid", "lighthouses.csv", "qid") in keys
    assert ("events.csv", "lighthouse.name", "lighthouses.csv", "name") in keys
    # the architect column is not found: three of its cells name two people ("A; B"), so too few of its
    # distinct values are engineer names; the single-valued structural_engineer column is
    assert ("lighthouses.csv", "structural_engineer", "engineers.csv", "name") in keys
    # and that found key carries one person only, so through a key the table reaches 1 of the 4
    # engineers (README "Joins"); a rebuild that changes this must change the README too
    rows = table_rows()
    assert {r["structural_engineer"] for r in rows} - {""} == {"James Walker"}


def table_rows() -> list[dict[str, str]]:
    with (DATA / "lighthouses.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_a_value_repeated_by_two_statements_is_kept_once_in_its_cell():
    # two statements told apart only by a qualifier raw/ drops render to the same text: keep it once
    record = {"claims": {"P1": [statement("17"), statement("14"), statement("14")]}}
    for st in record["claims"]["P1"]:
        st["mainsnak"]["datavalue"]["type"] = "string"
    assert lighthouses._values(record, "P1", {}) == ["17", "14"]
    for row in table_rows():
        for column, cell in row.items():
            values = cell.split(lighthouses.SEPARATOR)
            assert len(values) == len(set(values)), (row["qid"], column, cell)


def lighthouse_nodes(name_column: str | None) -> list[DomainNode]:
    """The lighthouse nodes as `read_domain_nodes` names them under a plan rule with this name column.

    The rule's properties are the CSV's columns in file order, as a plan that imports them all lists them;
    with no name column, `name_property` guesses the first column containing "name" or "title".
    """
    columns = list(table_rows()[0])
    rule = NodeRule(
        source_file="lighthouses.csv",
        label="Lighthouse",
        unique_column="qid",
        properties=[c for c in columns if c != "qid"],
        description="a lighthouse",
        name_column=name_column,
    )
    prop = name_property(rule)
    return [
        DomainNode(element_id=r["qid"], label="Lighthouse", name=r[prop], key=r["qid"])
        for r in table_rows()
        if r[prop]
    ]


def linked_documents(nodes: list[DomainNode]) -> dict[str, str | None]:
    """Per document stem, the qid of the lighthouse `match_document` links it to (None when unlinked)."""
    documents = sorted((DATA / "articles").glob("*.md"))
    assert len(documents) == lighthouses.DOCUMENTS
    return {
        doc.stem: (match.element_id if (match := match_document(doc.stem, nodes)) else None)
        for doc in documents
    }


def test_nine_of_ten_documents_link_by_the_default_name_and_old_higher_only_by_the_article_title():
    # documents are linked to the node whose name the file name contains (linking.py), and the name is
    # the plan's name column, else the guessed `name` (the Wikidata label). "Old Higher Lighthouse" is
    # "Portland Bill High Light" in Wikidata, so by default its document stays unlinked (README "Joins")
    articles = [json.loads(p.read_text("utf-8")) for p in (RAW / "articles").glob("*.json")]
    qid_of = {lighthouses.slug(a["title"]): a["qid"] for a in articles}
    by_name = linked_documents(lighthouse_nodes(None))
    assert by_name == {**qid_of, "old_higher_lighthouse": None}
    # only a plan that chooses the article column as the name links all ten, each to its own item
    assert linked_documents(lighthouse_nodes("enwiki_article")) == qid_of
