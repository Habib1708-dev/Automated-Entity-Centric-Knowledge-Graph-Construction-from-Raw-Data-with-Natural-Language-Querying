"""Profiling (uniqueness, foreign keys) and plan validation on small CSV fixtures, including node rules keyed
by several columns (R155a) and every saved plan from before them. No Neo4j needed."""

import json
from pathlib import Path

import pytest

from kgbuilder.structured.plan import (
    ConstructionPlan,
    column_value,
    key_value,
    name_property,
    validate_plan,
)
from kgbuilder.structured.profiler import ColumnProfile, DataProfile, FileProfile, profile_directory

from .sample_plans import FRAME_FILES, FRAME_PLAN, FRAME_RULE, GOOD_PLAN, node, rel, write_files

REPO = Path(__file__).resolve().parent.parent


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


def test_tables_that_no_key_joins_may_stand_apart(tmp_path):
    # R72: two unrelated record sets (the generality corpus has staff and pumps) share no key; the old check
    # demanded one connected graph and refused every plan for them
    (tmp_path / "people.csv").write_text("person_id,name\nA-1,Ada\nA-2,Ben\n", encoding="utf-8")
    (tmp_path / "machines.csv").write_text("serial,model\nM-10,Kestrel\nM-11,Kestrel\n", encoding="utf-8")
    profile = profile_directory(tmp_path)
    assert profile.foreign_keys == []
    plan = ConstructionPlan(
        nodes=[
            node("people.csv", "Person", "person_id", ["name"]),
            node("machines.csv", "Machine", "serial"),
        ],
        relationships=[],
    )
    assert validate_plan(plan, profile) == []


def test_tables_a_key_joins_must_be_joined_in_the_plan_also_through_a_link_table(data_dir):
    # products and suppliers share no column, but assemblies and assembly_supplier join them in the data
    plan = ConstructionPlan(
        nodes=[
            node("products.csv", "Product", "product_id", ["product_name"]),
            node("suppliers.csv", "Supplier", "supplier_id", ["name"]),
        ],
        relationships=[],
    )
    [issue] = validate_plan(plan, profile_directory(data_dir))
    assert "not connected" in issue and "Product" in issue and "Supplier" in issue
    assert "joined in the data" in issue and "assembly_supplier.csv.supplier_id" in issue


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


# --- node rules keyed by several columns (R155a) ---


def frame(**update):
    """The Frame rule of FRAME_PLAN with some fields changed."""
    return FRAME_RULE.model_copy(update=update)


def frame_plan(rule=FRAME_RULE, relationships=None, extra=()):
    """FRAME_PLAN with `rule` as its Frame rule; by default FRAME_OF starts at that rule's built key."""
    if relationships is None:
        relationships = [rel("frames.csv", "FRAME_OF", "Frame", rule.unique_column, "Hive", "hive_id")]
    return ConstructionPlan(nodes=[FRAME_PLAN.nodes[0], rule, *extra], relationships=relationships)


def test_a_file_without_a_unique_column_is_keyed_by_two_columns(tmp_path):
    staged = write_files(tmp_path / "staging", FRAME_FILES)
    profile = profile_directory(staged)
    assert not any(c.is_unique for c in profile.file("frames.csv").columns)
    assert validate_plan(FRAME_PLAN, profile, staged) == []


FRAME_OF = FRAME_PLAN.relationships[0]
# a built key named in a file that does not build it, and a built key given to the wrong label
HIVE_HAS_FRAME = rel("hives.csv", "HAS", "Hive", "hive_id", "Frame", "frame_key")
FRAME_NEAR_HIVE = rel("frames.csv", "NEAR", "Frame", "frame_key", "Hive", "frame_key")


# Each case: the plan, a substring of the issue it must get, how many issues it gets in all (None: not
# checked, other rules fire too), then the frames.csv text, where the staged rows are, and a column the
# profile is told is unique by itself.
@pytest.mark.parametrize(
    ("plan", "expected", "count", "frames", "staged", "unique"),
    [
        pytest.param(
            frame_plan(frame(key_columns=["hive_id"])),
            "key_columns needs two or more different columns",
            1, None, "dir", None, id="K1-one-column",
        ),
        pytest.param(
            frame_plan(frame(key_columns=["hive_id", "hive_id"])),
            "key_columns needs two or more different columns",
            1, None, "dir", None, id="K1-a-repeated-column",
        ),
        pytest.param(
            frame_plan(frame(key_columns=["hive_id", "slot"])),
            "'frames.csv' has no column 'slot'",
            1, None, "dir", None, id="K2-a-missing-column",
        ),
        pytest.param(
            frame_plan(frame(unique_column="note", properties=["frame_no"])),
            "unique_column 'note' is a column of 'frames.csv'; with key_columns it must be a new name",
            1, None, "dir", None, id="K3-the-built-name-is-a-column",
        ),
        pytest.param(
            frame_plan(frame(properties=["frame_no", "frame_key"])),
            "unique_column 'frame_key' is built from key_columns, not a property",
            None, None, "dir", None, id="K3-the-built-name-is-a-property",
        ),
        pytest.param(
            frame_plan(extra=[frame(label="FrameCopy")]),
            "unique_column 'frame_key' is the built key of another node of 'frames.csv'",
            None, None, "dir", None, id="K3-two-rules-build-one-name",
        ),
        pytest.param(
            frame_plan(frame(name_column="frame_key")),
            "name_column must be one of properties: the built key is a code",
            1, None, "dir", None, id="K4-the-name-is-the-built-key",
        ),
        pytest.param(
            FRAME_PLAN,
            "'frame_no' is unique in 'frames.csv' by itself; use unique_column 'frame_no' without "
            "key_columns",
            1, None, "dir", "frame_no", id="K5-a-part-is-unique-alone",
        ),
        pytest.param(
            FRAME_PLAN,
            "key_columns of Frame cannot be checked without the staged rows",
            1, None, "none", None, id="K6-no-staged-rows",
        ),
        pytest.param(
            FRAME_PLAN,
            "'frames.csv' cannot be read as comma-separated rows with columns [hive_id, frame_no]",
            1, None, "empty", None, id="K6-a-missing-staged-file",
        ),
        pytest.param(
            FRAME_PLAN,
            "key_columns [hive_id, frame_no] do not identify every row of 'frames.csv' "
            "(2 distinct combinations, 0 rows with an empty column, 3 rows)",
            1, "hive_id,frame_no,note\n1,1,a\n1,1,b\n2,1,c\n", "dir", None, id="K7-a-duplicate-pair",
        ),
        pytest.param(
            FRAME_PLAN,
            "(2 distinct combinations, 1 rows with an empty column, 3 rows)",
            1, "hive_id,frame_no,note\n1,1,a\n1,,b\n2,1,c\n", "dir", None, id="K7-an-empty-part",
        ),
        pytest.param(
            FRAME_PLAN,
            "(3 distinct combinations, 0 rows with an empty column, 4 rows)",
            1, "hive_id,frame_no,note\na|b,c,w\na,b|c,x\na,c,y\na|b,b|c,z\n", "dir", None,
            id="K7-a-separator-collision",
        ),
        pytest.param(
            frame_plan(relationships=[FRAME_OF, HIVE_HAS_FRAME]),
            "'hives.csv' has no column 'frame_key'",
            None, None, "dir", None, id="R2-a-built-key-of-another-file",
        ),
        pytest.param(
            frame_plan(relationships=[FRAME_OF, FRAME_NEAR_HIVE]),
            "'frame_key' is the built key of Frame, not of Hive",
            1, None, "dir", None, id="R1-a-built-key-on-the-wrong-label",
        ),
        pytest.param(
            frame_plan(relationships=[rel("frames.csv", "FRAME_OF", "Frame", "hive_id", "Hive", "hive_id")]),
            "Frame is keyed by key_columns: use its unique_column 'frame_key' as from_column/to_column, "
            "in 'frames.csv'",
            1, None, "dir", None, id="R4-a-keyed-label-on-a-real-column",
        ),
    ],
)  # fmt: skip
def test_key_columns_are_refused_with_an_issue_the_model_can_act_on(
    tmp_path, plan, expected, count, frames, staged, unique
):
    data = write_files(tmp_path / "staging", {**FRAME_FILES, **({"frames.csv": frames} if frames else {})})
    profile = profile_directory(data)
    if unique:
        profile.file("frames.csv").column(unique).is_unique = True
    staged_dir = {"dir": data, "none": None, "empty": write_files(tmp_path / "empty", {})}[staged]

    issues = validate_plan(plan, profile, staged_dir)  # never raises, also without the file

    assert [i for i in issues if expected in i], issues
    assert count is None or len(issues) == count, issues


def test_key_value_reads_a_plain_key_as_is_and_joins_a_built_one():
    hive = FRAME_PLAN.nodes[0]
    # a plain rule's key is the cell as read: a typed value stays typed, a missing one stays None
    assert key_value(hive, {"hive_id": 7}) == 7
    assert key_value(hive, {"hive_id": None}) is None
    assert key_value(FRAME_RULE, {"hive_id": " 1 ", "frame_no": "3"}) == "1|3"
    # a blank part leaves the row without a key; a 0 is a value, not a blank
    assert key_value(FRAME_RULE, {"hive_id": "1", "frame_no": " "}) is None
    assert key_value(FRAME_RULE, {"hive_id": "1", "frame_no": None}) is None
    assert key_value(FRAME_RULE, {"hive_id": 0, "frame_no": "2"}) == "0|2"


def test_column_value_gives_the_built_key_only_in_its_own_file():
    row = {"hive_id": "1", "frame_no": "3"}
    assert column_value(FRAME_PLAN, "frames.csv", "frame_key", row) == "1|3"
    assert column_value(FRAME_PLAN, "frames.csv", "hive_id", row) == "1"
    assert column_value(FRAME_PLAN, "hives.csv", "frame_key", row) is None


# Every plan saved before R155a: the gold plans and, where the local checkout has them, the build plans.
# Compared as JSON, not bytes: some files were written by an older dump and differ in whitespace only.
SAVED_PLANS = sorted(REPO.glob("tests/gold/**/*plan*.json")) + sorted(REPO.glob("out/*/plan.json"))


@pytest.mark.parametrize("path", SAVED_PLANS, ids=lambda p: p.relative_to(REPO).as_posix())
def test_every_saved_plan_loads_dumps_and_validates_as_before(path, tmp_path):
    text = path.read_text(encoding="utf-8")
    plan = ConstructionPlan.model_validate_json(text)
    dump = plan.model_dump_json(indent=2)

    assert '"key_columns"' not in dump
    assert json.loads(dump) == json.loads(text)
    profile_file = path.with_name("profile.json")
    if profile_file.exists():
        profile = DataProfile.model_validate_json(profile_file.read_text(encoding="utf-8"))
    else:  # a gold plan has no profile next to it: every file is then missing, with or without rows
        profile = DataProfile(files=[], foreign_keys=[])
    # the staged rows are read only for key_columns, which no saved plan has
    assert validate_plan(plan, profile, tmp_path) == validate_plan(plan, profile)
