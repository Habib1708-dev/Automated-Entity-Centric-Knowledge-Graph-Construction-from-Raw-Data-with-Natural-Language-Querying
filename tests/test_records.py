"""Record matching for mentions of keyed types (R75, resolution/records.py), pure: a name links on its own
only when it is the record's name (the name test itself: tests/test_names.py), inside the scope of the
mention's document, or unique in the domain when the document has no scope (the linking rules of R11, moved
here; R94 ended the fallback beyond a scope), the record's key in the name or a sentence, and an attribute
that tells two records of one name apart. A record's name inside a longer name, or a spelling that differs
inside a word, is no link (R95a: R93 judged such links wrong). No Neo4j."""

from kgbuilder.resolution.records import RecordCandidate, match_record, name_matches


def record(
    element_id: str, label: str, name: str, key: str | None = None, **attributes: str
) -> RecordCandidate:
    return RecordCandidate(
        element_id=element_id, label=label, name=name, key=key or element_id, attributes=attributes
    )


DOMAIN = [
    record("p1", "Product", "Table"),
    record("p2", "Product", "Coffee Table"),
    record("p3", "Product", "Stockholm Chair"),
    record("a7", "Part", "Leg"),
]


def ids(matches) -> list[str]:
    return [m.record.element_id for m in matches]


def test_a_name_is_the_records_after_normalisation_whatever_its_word_order_spacing_or_script():
    assert ids(name_matches("Chair Stockholm", DOMAIN, threshold=90)) == ["p3"]
    [exact] = name_matches("Coffee Table", DOMAIN, threshold=90)
    assert exact.record.element_id == "p2" and exact.score == 100
    # words are letters of any script: names in another script match exactly, and only their own record
    cities = [record("c1", "Place", "Москва"), record("c2", "Place", "Киев")]
    assert ids(name_matches("москва", cities, threshold=90)) == ["c1"]


def test_a_name_below_the_threshold_or_empty_matches_nothing():
    assert name_matches("Dining Table Deluxe", DOMAIN, threshold=90) == []
    assert name_matches("  ", DOMAIN, threshold=90) == []


def test_every_record_tied_for_the_best_score_comes_back():
    legs = [record("a1", "Assembly", "Legs"), record("a2", "Assembly", "Legs")]
    assert ids(name_matches("legs", legs, threshold=90)) == ["a1", "a2"]


def test_an_exact_name_wins_over_an_inflected_one():
    drawers = [record("a1", "Assembly", "Drawer"), record("a2", "Assembly", "Drawers")]
    [match] = name_matches("drawers", drawers, threshold=90)
    assert match.record.element_id == "a2" and match.score == 100


# Two products that both have an assembly called "Legs": the case that needs scopes.
CHAIR, TABLE = record("p1", "Product", "Chair"), record("p2", "Product", "Table")
CHAIR_LEGS, TABLE_LEGS = record("a1", "Assembly", "Legs"), record("a2", "Assembly", "Legs")
FURNITURE = [CHAIR, TABLE, CHAIR_LEGS, TABLE_LEGS]


def match(name: str, scopes=(), sentences=(), records=FURNITURE):
    return match_record(name, list(sentences), records, [list(s) for s in scopes], threshold=90)


def test_a_generic_name_links_inside_the_scope_of_its_document():
    result = match("legs", scopes=[[CHAIR, CHAIR_LEGS]])
    assert result.link.record.element_id == "a1" and result.link.reason == "name" and result.link.scoped


def test_without_a_scope_a_generic_name_is_ambiguous_and_a_unique_one_links():
    ambiguous = match("legs")
    assert ambiguous.link is None and ids_of(ambiguous.tied) == ["a1", "a2"]
    unique = match("table")
    assert unique.link.record.element_id == "p2" and not unique.link.scoped


def ids_of(records: list[RecordCandidate]) -> list[str]:
    return [r.element_id for r in records]


def test_a_name_outside_the_scope_of_its_document_links_no_record():
    # R94: the scope is the only evidence that a document speaks of a record. A desk review's "drawer" whose
    # own record is called "Drawer Unit" must not reach a nightstand's "Drawer" through the whole domain
    # (R93: 4 such links, all wrong). The price, accepted: a chair review naming "the table" stays unlinked.
    desk, desk_drawer = record("p3", "Product", "Desk"), record("a3", "Assembly", "Drawer Unit")
    stand_drawer = record("a4", "Assembly", "Drawer")
    result = match("drawer", scopes=[[desk, desk_drawer]], records=[desk, desk_drawer, stand_drawer])
    assert result.link is None and result.tied == []
    assert match("table", scopes=[[CHAIR, CHAIR_LEGS]]).link is None


CIVIC, ACCORD = record("v1", "Vehicle", "CIVIC"), record("v2", "Vehicle", "ACCORD")


def test_a_records_name_inside_a_longer_name_is_no_link():
    """R95a. The longer name may name another thing that has the record's name as a complement or a
    modifier: R93 judged "pre-drilled holes for the drawer handle" -> Drawer Handle and "drawer slide
    mechanism" -> Drawer INCORRECT. Code cannot tell which reading holds, so it links neither."""
    handle, drawer = record("a1", "Assembly", "Drawer Handle"), record("a2", "Assembly", "Drawer")
    for name in ("pre-drilled holes for the drawer handle", "drawer slide mechanism"):
        result = match(name, scopes=[[handle, drawer]], records=[handle, drawer])
        assert result.link is None and result.tied == []
    assert match("2016 Honda Civic", scopes=[[CIVIC, ACCORD]], records=[CIVIC, ACCORD]).link is None
    # the record's own name still links inside the scope
    civic = match("civic", scopes=[[CIVIC, ACCORD]], records=[CIVIC, ACCORD]).link
    assert civic.record.element_id == "v1" and civic.reason == "name" and civic.scoped


def test_a_spelling_that_differs_inside_a_word_is_no_link_and_an_ending_is_one():
    # R93: "drawer slides" -> Drawer Sides (spelling 96) was INCORRECT
    sides, rails = record("s1", "Component", "Drawer Sides"), record("s2", "Component", "Drawer Rails")
    assert match("drawer slides", scopes=[[sides, rails]], records=[sides, rails]).link is None
    inflected = match("drawer rail", scopes=[[sides, rails]], records=[sides, rails]).link
    assert inflected.record.element_id == "s2" and inflected.reason == "name" and inflected.score < 100


PUMPS = [
    record("x1", "Pump", "HP40-1183", key="HP40-1183", model="Kettle K-9"),
    record("x2", "Pump", "HP40-2291", key="HP40-2291", model="Kettle K-9"),
]


def test_a_key_in_the_mentions_own_name_decides():
    result = match("pump HP40-1183", records=PUMPS)
    assert result.link.record.element_id == "x1" and result.link.reason == "key"


def test_a_key_in_a_sentence_decides_only_when_the_name_does_not():
    sentence = "The Kettle K-9 unit HP40-2291 was regreased."
    result = match("Kettle K-9 unit", sentences=[sentence], records=PUMPS)
    assert result.link.record.element_id == "x2" and result.link.reason == "key_in_sentence"
    assert result.link.evidence == sentence
    # two keys in the sentences: nothing tells them apart
    both = ["HP40-1183 and HP40-2291 were inspected."]
    assert match("Kettle K-9 unit", sentences=both, records=PUMPS).link is None


STAFF = [
    record("s104", "Staff", "Maria Lopez", key="S-104", team="Soil Ecology"),
    record("s219", "Staff", "Maria Lopez", key="S-219", team="Finance Office"),
]


def test_an_attribute_in_the_same_sentence_tells_same_named_records_apart():
    sentence = "Present: Jon Pike (chair), Maria Lopez (Finance Office), Aiko Tanaka."
    result = match("Maria Lopez", sentences=[sentence], records=STAFF)
    assert result.link.record.element_id == "s219" and result.link.reason == "attribute"
    assert result.link.evidence == sentence


def test_same_named_records_without_a_telling_attribute_stay_unlinked_and_ambiguous():
    result = match("Maria Lopez", sentences=["Maria Lopez joined the fieldwork."], records=STAFF)
    assert result.link is None and ids_of(result.tied) == ["s104", "s219"]
    # both attributes in one sentence tell nothing either
    mixed = ["Maria Lopez of Soil Ecology met the Finance Office."]
    assert match("Maria Lopez", sentences=mixed, records=STAFF).link is None


VEHICLES = [record("v1", "Vehicle", "RAV4", key="RAV4"), record("v2", "Vehicle", "CIVIC", key="CIVIC")]


def test_a_key_elsewhere_in_a_listing_sentence_is_no_evidence():
    """Found in R75's held-out run: a recall sentence lists many models, and "Camry" was linked to the RAV4
    record because RAV4 stood in the same sentence. Only a key next to the name tells which record it is."""
    listing = (
        "Toyota is recalling certain 2017-2019 Toyota Camry, Corolla, Rav4, Sienna, and Yaris iA vehicles."
    )
    assert match("Camry", sentences=[listing], records=VEHICLES).link is None
    assert match("Corolla", sentences=[listing], records=VEHICLES).link is None
    # the key right next to the name, with at most a bracket or a colon between, does tell
    next_to = match("the vehicle", sentences=["The seal of the vehicle (RAV4) failed."], records=VEHICLES)
    assert next_to.link.record.element_id == "v1" and next_to.link.reason == "key_in_sentence"
    before = match("pump", sentences=["The seal of pump HP40-1183 failed."], records=PUMPS)
    assert before.link.record.element_id == "x1"
