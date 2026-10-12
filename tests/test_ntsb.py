"""The NTSB aviation benchmark dataset (heldout/ntsb/): the data is exactly what its build script writes from
the raw records, the raw records follow the selection rule (accidents of 2021-2023 with one aircraft of six
models, the five lowest NTSB numbers per model whose narrative fits the word window), nothing personal is
kept, the narratives are verbatim and only in the documents, the pipeline's LLM-free first stages accept the
data (seven tables staged, the keys to the events and the models found), and linking gives the known
result: every document to its model by the default name column, every section to its event by its heading,
and at the preset's chunk size every chunk but four (pinned) to its event. The known gaps of the rule and the
data the README records (split model spellings, "Total" injury rows) are pinned too.
No Neo4j, no network, no LLM.
"""

import csv
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from kgbuilder.config import Settings, load_preset
from kgbuilder.resolution.linking import DomainNode, RecordKey, match_chunk_records, match_document
from kgbuilder.structured.plan import NodeRule, name_property
from kgbuilder.structured.profiler import profile_directory
from kgbuilder.structured.staging import stage_structured
from kgbuilder.text.chunking import chunk_document
from kgbuilder.text.documents import load_documents

REPO = Path(__file__).resolve().parent.parent
NTSB = REPO / "heldout" / "ntsb"
RAW, DATA = NTSB / "raw", NTSB / "data"

_spec = importlib.util.spec_from_file_location("ntsb_build", NTSB / "build.py")
ntsb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ntsb)


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def tables() -> dict[str, list[dict]]:
    return json.loads((RAW / "tables.json").read_text("utf-8"))["tables"]


def candidates() -> list[dict]:
    return json.loads((RAW / "selection.json").read_text("utf-8"))["candidates"]


def csv_rows(name: str) -> list[dict[str, str]]:
    with (DATA / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_the_committed_dataset_is_exactly_what_build_writes_from_raw(tmp_path):
    # proves that nothing in data/ was edited by hand: the benchmark must not be shaped by its author
    ntsb.build(RAW, tmp_path)
    assert files(tmp_path) == files(DATA)


def test_a_model_matches_by_its_make_prefix_and_exact_model_whatever_the_spaces_and_hyphens():
    assert ntsb.model_of("CIRRUS DESIGN CORP", "SR-22") == ("Cirrus", "SR22")
    assert ntsb.model_of("Cirrus Aircraft", "SR22") == ("Cirrus", "SR22")
    assert ntsb.model_of("PIPER", "PA28181") == ("Piper", "PA-28-181")
    assert ntsb.model_of("ROBINSON HELICOPTER COMPANY", "R44 II") == ("Robinson", "R44 II")
    assert ntsb.model_of("CIRRUS", "SR22T") is None  # a different model, not a spelling
    assert ntsb.model_of("ROBINSON", "R44") is None  # the R44 is not the R44 II
    assert ntsb.model_of("BEECHCRAFT", "36") is None


def test_a_model_split_over_model_and_series_or_named_by_a_successor_make_is_not_matched():
    # a known gap of the frozen rule (README step 3): the series column is not read, and a successor make
    # is no prefix of the selected one; these records of the six models are not candidates
    assert ntsb.model_of("CESSNA", "172") is None  # stored with series "S"
    assert ntsb.model_of("ROBINSON", "R44") is None  # stored with series "II"
    assert ntsb.model_of("TEXTRON AVIATION INC", "172S") is None
    assert ntsb.model_of("RAYTHEON AIRCRAFT COMPANY", "A36") is None


def test_fetch_refuses_an_access_parser_release_its_text_fix_was_not_written_for():
    ntsb.check_reader_version(ntsb.ACCESS_PARSER_VERSION)
    with pytest.raises(ImportError, match="0.0.7 is installed"):
        ntsb.check_reader_version("0.0.7")


def candidate(model: str, ntsb_no: str, words: int = 150, cause: bool = True) -> dict:
    return {"model": model, "ntsb_no": ntsb_no, "narr_accf_words": words, "has_probable_cause": cause}


def test_selection_takes_the_five_lowest_numbers_in_the_word_window_with_a_cause_per_model():
    names = [ntsb.model_name(m) for m in ntsb.MODELS]
    pool = [candidate(name, f"CEN21LA{i:03d}") for name in names[1:] for i in range(5)]
    first = names[0]
    pool += [
        candidate(first, "ERA21LA001"),
        candidate(first, "ANC21LA001", words=99),  # too short
        candidate(first, "ANC21LA002", words=251),  # too long
        candidate(first, "ANC21LA003", cause=False),  # no probable cause yet
        *[candidate(first, f"CEN22LA{i:03d}") for i in (5, 1, 4, 2, 3)],
    ]
    kept = [c["ntsb_no"] for c in ntsb.select_events(pool) if c["model"] == first]
    # text order of the NTSB number: CEN before ERA, so ERA21LA001 is the sixth
    assert kept == ["CEN22LA001", "CEN22LA002", "CEN22LA003", "CEN22LA004", "CEN22LA005"]


def test_selection_refuses_to_shrink_when_a_model_has_fewer_than_five_eligible_events():
    pool = [candidate(ntsb.model_name(m), f"CEN21LA{i:03d}") for m in ntsb.MODELS for i in range(4)]
    with pytest.raises(ValueError, match="4 eligible events"):
        ntsb.select_events(pool)


def test_compressed_text_switches_between_one_and_two_bytes_per_character():
    # "O’Hare" as Access stores it: one byte per character, 0x00, the quote in UTF-16LE, 0x00, one byte again
    assert ntsb.decompress_text(b"\xff\xfeO\x00\x19\x20\x00Hare") == "O’Hare"
    assert ntsb.decompress_text(b"\xff\xfeplain") == "plain"


def test_the_kept_events_are_the_ones_the_rule_picks_from_the_candidates():
    kept = ntsb.select_events(candidates())
    assert sorted(c["ev_id"] for c in kept) == sorted(e["ev_id"] for e in tables()["events"])
    per_model = {ntsb.model_name(m): 0 for m in ntsb.MODELS}
    for c in kept:
        per_model[c["model"]] += 1
    assert set(per_model.values()) == {ntsb.EVENTS_PER_MODEL}


def test_every_kept_event_meets_the_rule_in_its_own_rows():
    raw = tables()
    aircraft = {a["ev_id"]: a for a in raw["aircraft"]}
    narratives = {n["ev_id"]: n for n in raw["narratives"]}
    counts = {c["ev_id"]: c for c in candidates()}
    assert len(aircraft) == len(raw["aircraft"]) == len(raw["events"]) == 30  # one aircraft per event
    for event in raw["events"]:
        ev_id = event["ev_id"]
        assert event["ev_type"] == "ACC"
        assert ntsb.FIRST_DAY <= event["ev_date"][:10] <= ntsb.LAST_DAY
        assert ntsb.model_of(aircraft[ev_id]["acft_make"], aircraft[ev_id]["acft_model"]) is not None
        # the counts the rule read are the counts of the saved text
        accf_words = ntsb.words(narratives[ev_id]["narr_accf"])
        assert ntsb.MIN_WORDS <= accf_words <= ntsb.MAX_WORDS
        assert accf_words == counts[ev_id]["narr_accf_words"]
        assert narratives[ev_id]["narr_cause"].strip()


def test_nothing_personal_is_kept():
    raw = tables()
    columns = {c for rows in raw.values() for c in rows[0]}
    personal = {"owner_acft", "owner_street", "owner_city", "oper_street", "oper_city", "oper_zip"}
    assert not columns & (personal | {"crew_city", "crew_res_state", "lchg_userid"})
    # an operator who is a private individual is never named, nor traceable through the FAA registry by
    # the aircraft's registration
    individual = [a for a in raw["aircraft"] if a["oper_individual_name"] == "Y"]
    assert len(individual) == 10
    assert all(not a["oper_name"] and not a["regis_no"] for a in individual)
    blanked = {a["ntsb_no"] for a in individual}
    aircraft = json.loads((DATA / "aircraft.json").read_text("utf-8"))
    assert {a["ntsb_no"] for a in aircraft if a["registration"] is None} == blanked
    assert "city" not in csv_rows("crew.csv")[0]


def test_the_findings_cause_flags_are_empty_on_every_kept_row_so_data_drops_them():
    findings = tables()["Findings"]
    assert {(f["Cause_Factor"], f["cm_inPc"]) for f in findings} == {(None, "")}
    assert set(json.loads(next(iter((DATA / "findings.ndjson").open(encoding="utf-8"))))) == {
        "ntsb_no",
        "finding_no",
        "finding_code",
        "finding",
    }


def test_only_the_new_injury_person_categories_have_no_meaning_in_the_source():
    # the data dictionary and the code table define every other code; these four the build keeps as they are
    codes = json.loads((RAW / "codes.json").read_text("utf-8"))["codes"]
    raw = tables()
    undefined = {
        (table, column, row[column])
        for table, columns in ntsb.DECODED.items()
        for column in columns
        for row in raw[table]
        if row[column]
        and row[column].strip()
        and row[column].strip().upper() not in codes[f"{table}.{column}"]
    }
    assert undefined == {("injury", "inj_person_category", c) for c in ("Flig", "Pass", "Cabi", "LapC")}


def test_the_total_injury_rows_are_not_totals_and_the_level_rows_match_the_events():
    # README "Known properties": gold questions count the level rows, never the "Total" rows
    rows = csv_rows("injuries.csv")
    column = {
        "Fatal": "fatal_injuries",
        "Serious": "serious_injuries",
        "Minor": "minor_injuries",
        "None": "uninjured",
    }
    levels: Counter[tuple[str, str]] = Counter()  # (ntsb_no, person category) -> people
    per_event: Counter[tuple[str, str]] = Counter()  # (ntsb_no, events.csv column) -> people
    for r in rows:
        if r["injury_level"] != "Total":
            levels[(r["ntsb_no"], r["person_category"])] += int(r["count"])
            per_event[(r["ntsb_no"], column[r["injury_level"]])] += int(r["count"])
    totals = [r for r in rows if r["injury_level"] == "Total"]
    counted = [r for r in totals if r["count"]]
    disagree = {
        (r["ntsb_no"], r["person_category"])
        for r in counted
        if int(r["count"]) != levels[(r["ntsb_no"], r["person_category"])]
    }
    assert (len(rows), len(totals), len(counted)) == (143, 54, 10)
    assert disagree == {
        ("CEN21LA280", "Flig"),
        ("CEN22LA235", "Flig"),
        ("CEN22LA235", "Pass"),
        ("CEN21LA382", "Flig"),
        ("CEN21LA284", "Flig"),
    }
    for event in csv_rows("events.csv"):
        for name in column.values():
            assert int(event[name] or 0) == per_event[(event["ntsb_no"], name)]


def documents() -> dict[str, str]:
    return {p.stem: p.read_text("utf-8") for p in sorted((DATA / "documents").glob("*.md"))}


def with_lf(text: str) -> str:
    """The source text with "\\n" line endings and no outer whitespace: the only change the build makes."""
    return text.replace("\r\n", "\n").strip()


def test_every_narrative_and_cause_is_verbatim_in_its_models_document_and_nowhere_else():
    raw = tables()
    model = {a["ev_id"]: ntsb.model_of(a["acft_make"], a["acft_model"]) for a in raw["aircraft"]}
    docs = documents()
    structured = "".join(p.read_text("utf-8") for p in DATA.glob("*.*"))
    for n in raw["narratives"]:
        doc = docs[f"{ntsb.slug(ntsb.model_name(model[n['ev_id']]))}_accidents"]
        accf = with_lf(n["narr_accf"])
        assert accf in doc
        assert f"Probable cause: {with_lf(n['narr_cause'])}" in doc
        # the narratives travel on the text path only, as NHTSA's complaints do
        assert accf[:80] not in structured
    # a line break other than "\n" would split lines where the source has none
    assert not re.search(r"[\x00-\x08\x0b-\x1f]", "".join(docs.values()))


def test_staging_and_profiling_find_the_keys_to_the_events_and_the_models(tmp_path):
    report = stage_structured(DATA, tmp_path / "staging")
    assert sorted(report.tables) == [
        "aircraft.json",
        "crew.csv",
        "events.csv",
        "events_sequence.ndjson",
        "findings.ndjson",
        "injuries.csv",
        "models.csv",
    ]
    assert report.skipped == []
    keys = {
        (fk.from_file, fk.from_column, fk.to_file, fk.to_column)
        for fk in profile_directory(report.staged_dir).foreign_keys
    }
    children = ("crew.csv", "events_sequence.csv", "findings.csv", "injuries.csv")
    # ntsb_no is unique in both events and aircraft (one aircraft per event), so every child table, and
    # each of the two, is found pointing at both
    expected = {
        (child, "ntsb_no", parent, "ntsb_no")
        for child in children
        for parent in ("events.csv", "aircraft.csv")
    }
    expected |= {
        ("aircraft.csv", "ntsb_no", "events.csv", "ntsb_no"),
        ("events.csv", "ntsb_no", "aircraft.csv", "ntsb_no"),
    }
    # the source's own model spelling equals the selected one often enough to be found as a key too
    expected |= {
        ("aircraft.csv", "model", "models.csv", "model"),
        ("aircraft.csv", "reported_model", "models.csv", "model"),
    }
    assert keys == expected


def model_nodes(name_column: str | None) -> list[DomainNode]:
    """The model nodes as `read_domain_nodes` names them under a plan rule with this name column."""
    rows = csv_rows("models.csv")
    rule = NodeRule(
        source_file="models.csv",
        label="AircraftModel",
        unique_column="model",
        properties=[c for c in rows[0] if c != "model"],
        description="an aircraft model",
        name_column=name_column,
    )
    prop = name_property(rule)
    return [
        DomainNode(element_id=r["model"], label="AircraftModel", name=r[prop], key=r["model"]) for r in rows
    ]


def linked_documents(nodes: list[DomainNode]) -> dict[str, str | None]:
    """Per document stem, the model `match_document` links it to (None when unlinked)."""
    return {
        stem: (match.element_id if (match := match_document(stem, nodes)) else None) for stem in documents()
    }


def test_every_document_links_to_its_model_by_the_default_name_and_the_beech_not_by_the_model_column():
    expected = {f"{ntsb.slug(ntsb.model_name(m))}_accidents": m[1] for m in ntsb.MODELS}
    # with no name column the guess is `name` ("Beech A36"), which every file name contains
    assert linked_documents(model_nodes(None)) == expected
    # a plan that names `model` as the name column loses the Beech: "A36" is under the 4 characters
    # linking trusts in a file name
    assert linked_documents(model_nodes("model")) == {**expected, "beech_a36_accidents": None}


def test_every_section_heading_names_its_event_and_no_other():
    events = [
        RecordKey(element_id=e["ntsb_no"], key=e["ntsb_no"], name=e["ntsb_no"])
        for e in csv_rows("events.csv")
    ]
    found = []
    for text in documents().values():
        for section in text.split("\n---\n"):
            heading = next(line for line in section.splitlines() if line.startswith("## "))
            matched = [r.key for r in match_chunk_records(section, events)]
            assert matched == [heading.split()[1]]
            found += matched
    assert sorted(found) == sorted(r.key for r in events)


def test_at_the_presets_chunk_size_four_chunks_lose_their_heading_and_link_to_no_event():
    # the pipeline links chunks, not sections: a section over chunk_max_chars is cut, and only its first
    # piece keeps the `## <ntsb_no>` heading (README "Known properties")
    preset = load_preset(REPO / "presets.yaml", "ntsb", set(Settings.model_fields))
    size = [
        preset.get(name, Settings.model_fields[name].default)
        for name in ("chunk_max_chars", "chunk_min_chars", "chunk_overlap_chars")
    ]
    events = [
        RecordKey(element_id=e["ntsb_no"], key=e["ntsb_no"], name=e["ntsb_no"])
        for e in csv_rows("events.csv")
    ]
    chunks = [c for d in load_documents(DATA) for c in chunk_document(d, *size)]
    unlinked = []
    previous = None  # the event of the last linked chunk: the section the unlinked piece was cut from
    for c in chunks:
        matched = [r.key for r in match_chunk_records(c.text, events)]
        assert len(matched) <= 1
        if matched:
            previous = matched[0]
        else:
            unlinked.append((c.doc_id, previous, c.text.split()[0]))
    assert len(chunks) == 34
    # three are a probable cause alone; the Piper one is the end of a narrative cut mid-sentence
    assert unlinked == [
        ("documents/air_tractor_at_502b_accidents.md", "CEN21LA362", "Probable"),
        ("documents/beech_a36_accidents.md", "CEN23LA344", "Probable"),
        ("documents/cirrus_sr22_accidents.md", "CEN23LA017", "Probable"),
        ("documents/piper_pa_28_181_accidents.md", "CEN21LA353", "of"),
    ]
