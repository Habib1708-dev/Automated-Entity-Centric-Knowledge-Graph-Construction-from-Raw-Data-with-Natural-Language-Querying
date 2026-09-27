"""Profiling (uniqueness, foreign keys) and plan validation on small CSV fixtures. No Neo4j needed."""

from kgbuilder.structured.plan import ConstructionPlan, name_property, validate_plan
from kgbuilder.structured.profiler import ColumnProfile, DataProfile, FileProfile, profile_directory

from .sample_plans import GOOD_PLAN, node, rel


def test_uniqueness(data_dir):
    profile = profile_directory(data_dir)
    assert profile.file("products.csv").column("product_id").is_unique
    assert not profile.file("assemblies.csv").column("product_id").is_unique
    assert not profile.file("dirty.csv").column("item_id").is_unique
    assert not any(
        c.is_unique for c in profile.file("assembly_supplier.csv").columns if c.name.endswith("_id")
    )


def test_foreign_keys(data_dir):
    found = {(fk.from_file, fk.from_column, fk.to_file) for fk in profile_directory(data_dir).foreign_keys}
    assert ("assemblies.csv", "product_id", "products.csv") in found
    assert ("assembly_supplier.csv", "supplier_id", "suppliers.csv") in found
    # P9 does not exist, so inclusion is 2/3 and falls below the threshold
    assert ("dirty.csv", "product_id", "products.csv") not in found


# Running text, well above the length bound, three sentences: what a report summary looks like.
PROSE = (
    "The unit failed after two weeks of normal use. The maker was informed and offered a replacement "
    "part free of charge. A second failure followed within days and the case was then escalated."
)


def test_prose_columns_are_detected_by_length_and_sentences(tmp_path):
    (tmp_path / "records.csv").write_text(
        "record_id,record_name,summary,tagline,notes\n"
        f'R1,Alpha,"{PROSE}",Short and sweet.,\n'
        f'R2,Beta,"{PROSE}",Sturdy oak frame.,\n',
        encoding="utf-8",
    )
    [file] = profile_directory(tmp_path).files
    summary = file.column("summary")
    assert summary.is_prose and summary.avg_chars > 120 and summary.multi_sentence_ratio == 1.0
    # a tagline is one short sentence: text, but never prose - this is what keeps the furniture
    # description column from becoming record documents
    assert not file.column("tagline").is_prose
    assert not file.column("record_name").is_prose and not file.column("record_id").is_prose
    assert not file.column("notes").is_prose  # all null: the aggregates must not crash or trigger


def test_name_property_prefers_the_plans_name_column_then_guesses():
    part = node("c.csv", "Part", "part_id", ["sub_assembly_name", "part_name"])
    # without name_column the guess picks the sub-assembly code: the bug R11 fixes
    assert name_property(part) == "sub_assembly_name"
    assert name_property(part.model_copy(update={"name_column": "part_name"})) == "part_name"
    assert name_property(node("p.csv", "Doc", "doc_id", ["Title"])) == "Title"
    assert name_property(node("p.csv", "Thing", "thing_id", ["price"])) == "thing_id"


def test_good_plan_is_valid(data_dir):
    assert validate_plan(GOOD_PLAN, profile_directory(data_dir)) == []


def test_plan_problems_are_reported(data_dir):
    bad = ConstructionPlan(
        nodes=[
            node("products.csv", "Product", "product_id", ["colour"]),
            node("dirty.csv", "Item", "item_id"),
            node("suppliers.csv", "Supplier", "supplier_id"),
        ],
        relationships=[rel("dirty.csv", "OF", "Item", "item_id", "Product", "product_id")],
    )
    issues = "\n".join(validate_plan(bad, profile_directory(data_dir)))
    assert "no column 'colour'" in issues
    assert "'item_id' is not unique" in issues
    assert "not connected" in issues and "Supplier" in issues


def test_name_column_must_be_an_imported_column(data_dir):
    profile = profile_directory(data_dir)
    product = GOOD_PLAN.nodes[0]
    named = GOOD_PLAN.model_copy(
        update={"nodes": [product.model_copy(update={"name_column": "product_name"}), *GOOD_PLAN.nodes[1:]]}
    )
    assert validate_plan(named, profile) == []
    # a column that is not among the imported ones: the node would have no name to link by
    unnamed = GOOD_PLAN.model_copy(
        update={"nodes": [product.model_copy(update={"name_column": "colour"}), *GOOD_PLAN.nodes[1:]]}
    )
    issues = "\n".join(validate_plan(unnamed, profile))
    assert "name_column 'colour' must be unique_column or one of properties" in issues


def test_a_name_column_of_codes_is_rejected_with_a_hint():
    def column(name, samples, unique=False):
        return ColumnProfile(
            name=name, dtype="VARCHAR", null_count=0, distinct_count=2, is_unique=unique, samples=samples
        )

    profile = DataProfile(
        files=[
            FileProfile(
                file="assemblies.csv",
                row_count=2,
                columns=[
                    column("assembly_id", ["A-1", "A-2"], unique=True),
                    column("assembly_name", ["jönköping_coffee_table_assembly", "uppsala_sofa_assembly"]),
                    column("component_name", ["Legs", "Table Top"]),
                ],
            )
        ],
        foreign_keys=[],
    )

    def plan(name_column):
        rule = node("assemblies.csv", "Assembly", "assembly_id", ["assembly_name", "component_name"])
        return ConstructionPlan(
            nodes=[rule.model_copy(update={"name_column": name_column})], relationships=[]
        )

    [issue] = validate_plan(plan("assembly_name"), profile)
    assert "holds codes such as 'jönköping_coffee_table_assembly'" in issue
    assert validate_plan(plan("component_name"), profile) == []
