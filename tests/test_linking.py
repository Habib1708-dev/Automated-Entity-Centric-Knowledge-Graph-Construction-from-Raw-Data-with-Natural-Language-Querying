"""Linking: the pure matching functions (no database) that decide ABOUT and REFERS_TO links."""

from kgbuilder.resolution.linking import DomainNode, match_document, match_entity, name_property

from .sample_plans import node

DOMAIN = [
    DomainNode(label="Product", key="P1", name="Table"),
    DomainNode(label="Product", key="P2", name="Coffee Table"),
    DomainNode(label="Product", key="P3", name="Stockholm Chair"),
    DomainNode(label="Part", key=7, name="Leg"),
]


def test_document_links_to_the_longest_name_contained_in_its_title():
    assert match_document("jonkoping_coffee_table_reviews", DOMAIN).key == "P2"
    assert match_document("Stockholm-Chair reviews (2024)", DOMAIN).key == "P3"


def test_document_ignores_very_short_names_and_unrelated_titles():
    assert match_document("legal_notes", DOMAIN) is None  # "Leg" is inside "legal": too short to trust
    assert match_document("warranty_terms", DOMAIN) is None


def test_entity_links_on_near_exact_names_including_aliases_and_word_order():
    assert match_entity(["Chair Stockholm"], DOMAIN, threshold=90).node.key == "P3"
    assert match_entity(["the big one", "stockholm chair"], DOMAIN, threshold=90).node.key == "P3"
    exact = match_entity(["Coffee Table"], DOMAIN, threshold=90)
    assert exact.node.key == "P2" and exact.score == 100


def test_entity_below_threshold_or_without_a_name_is_not_linked():
    assert match_entity(["Dining Table Deluxe"], DOMAIN, threshold=90) is None
    assert match_entity(["", "  "], DOMAIN, threshold=90) is None


def test_name_property_prefers_name_like_columns_and_falls_back_to_the_key():
    assert name_property(node("p.csv", "Product", "product_id", ["price", "product_name"])) == "product_name"
    assert name_property(node("p.csv", "Doc", "doc_id", ["Title"])) == "Title"
    assert name_property(node("p.csv", "Thing", "thing_id", ["price"])) == "thing_id"
