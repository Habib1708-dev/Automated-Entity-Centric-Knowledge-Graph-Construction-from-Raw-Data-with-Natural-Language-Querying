"""Keys a database declares (R115): staged with file names, marked in the profile, and an extra plan rule.

Without a database: staging writes the keys of the staged tables only, the profile marks them (a dangling
declared foreign key is kept with its measured inclusion), and a profile of files carries no mark, so its
plan prompt is byte for byte the prompt before R115. Against the tests' own PostgreSQL (marked
`postgres`): which constraints count, rows in primary key order, and the furniture tables with their
constraints, whose profile differs from the files' only by the marks.
"""

import csv
import json
from pathlib import Path

from psycopg import sql

from kgbuilder.config import Settings
from kgbuilder.llm.refine import Critique
from kgbuilder.pipeline import PipelineContext, PipelineState, run_stages
from kgbuilder.pipeline import stages as st
from kgbuilder.structured import proposer
from kgbuilder.structured.postgres import PostgresTables
from kgbuilder.structured.profiler import DataProfile, profile_directory
from kgbuilder.structured.staging import (
    DECLARED_KEYS_FILE,
    DeclaredForeignKey,
    DeclaredKeys,
    stage_structured,
)

from .fakes import FakeTables, RecordingTracker, ScriptedLLM
from .pg_load import PgSchema, load_csv_dir
from .sample_plans import GOOD_PLAN

ROOT = Path(__file__).parent.parent

# Customers and their orders; order O2 names a customer that does not exist (a dangling reference), and
# every note names an existing order, so the search finds notes -> orders without any declaration. C1 has
# two orders, so `orders.customer_id` is not unique and the search proposes no customers -> orders.
SHOP = {
    "customers": "customer_id,name\nC1,Ada\nC2,Bo\n",
    "orders": "order_id,customer_id\nO1,C1\nO2,C9\nO3,C2\nO4,C1\n",
    "notes": "note_id,order_id\nN1,O1\nN2,O3\n",
}
SHOP_KEYS = DeclaredKeys(
    primary_keys={"customers": "customer_id", "orders": "order_id"},
    foreign_keys=[
        DeclaredForeignKey(
            from_table="orders", from_column="customer_id", to_table="customers", to_column="customer_id"
        )
    ],
)


def test_staging_writes_the_keys_of_the_staged_tables_with_their_file_names(tmp_path):
    keys = DeclaredKeys(
        primary_keys={"orders": "order_id", "a/b": "id"},
        foreign_keys=[
            DeclaredForeignKey(from_table="orders", from_column="ab_id", to_table="a/b", to_column="id"),
            *SHOP_KEYS.foreign_keys,
        ],
        skipped=["lines.lines_pkey: 2 columns"],
    )
    source = FakeTables({**SHOP, "a/b": "id\n1\n"}, keys)

    staged = stage_structured(tmp_path / "empty", tmp_path / "staging", source).staged_dir
    written = DeclaredKeys.model_validate_json((staged / DECLARED_KEYS_FILE).read_text(encoding="utf-8"))

    assert written.primary_keys == {"orders.csv": "order_id"}
    assert [(fk.from_table, fk.to_table) for fk in written.foreign_keys] == [("orders.csv", "customers.csv")]
    # nothing is dropped silently: the keys of the table that could not be staged are reported
    assert written.skipped == [
        "lines.lines_pkey: 2 columns",
        "a/b.id: the table was not staged",
        "orders.ab_id: a table of the key was not staged",
    ]


def context(tmp_path: Path, tables: FakeTables | None = None) -> PipelineContext:
    """A context for the profile stage, recording what it logs."""
    out = tmp_path / "out"
    return PipelineContext(
        settings=Settings(_env_file=None), driver=None, out=out, tracker=RecordingTracker(), tables=tables
    )


def plan_prompt(profile: DataProfile) -> str:
    """The first proposer prompt for `profile`, from a scripted LLM; whether its plan fits does not matter."""
    prompts: list[str] = []

    def script(prompt, schema):
        if schema is Critique:
            return Critique(verdict="valid", issues=[])
        prompts.append(prompt)
        return GOOD_PLAN

    proposer.propose_plan("g", profile, ScriptedLLM(script), model="m", max_rounds=1)
    return prompts[0]


def test_declared_keys_are_marked_and_a_dangling_foreign_key_is_kept(tmp_path):
    ctx = context(tmp_path, FakeTables(SHOP, SHOP_KEYS))
    profile = run_stages(ctx, PipelineState(data_dir=tmp_path), [st.ProfileStage()]).profile

    marked = {(f.file, c.name) for f in profile.files for c in f.columns if c.primary_key}
    assert marked == {("customers.csv", "customer_id"), ("orders.csv", "order_id")}
    declared, found = profile.foreign_keys[0], profile.foreign_keys[1:]
    # 2 of the 3 customers the orders name exist: below the search's 0.95, kept because it is declared
    assert (declared.from_file, declared.from_column, declared.declared) == (
        "orders.csv",
        "customer_id",
        True,
    )
    assert declared.inclusion == 0.6667
    assert [(fk.from_file, fk.declared) for fk in found] == [("notes.csv", None)]
    logged = ctx.tracker.run("profile").logged_metrics
    assert (logged["declared_primary_keys"], logged["declared_foreign_keys"]) == (2, 1)


def test_files_get_no_marks_and_the_plan_prompt_from_before_declared_keys(data_dir, tmp_path):
    ctx = context(tmp_path)
    state = run_stages(ctx, PipelineState(data_dir=data_dir, goal="g"), [st.ProfileStage()])

    profile_json = state.profile.model_dump_json(indent=1)
    assert '"primary_key"' not in profile_json and '"declared"' not in profile_json
    # the template without the slot is the template before R115: the prompt files get is unchanged
    before = proposer.PROPOSER_PROMPT.replace("{declared_rule}", "")
    assert plan_prompt(state.profile) == before.format(goal="g", profile=profile_json, feedback="")
    assert st.PlanStage().params(ctx, state)["declared_keys"] is False


def test_a_profile_with_declared_keys_gets_the_declared_keys_rule(tmp_path):
    ctx = context(tmp_path, FakeTables(SHOP, SHOP_KEYS))
    state = run_stages(ctx, PipelineState(data_dir=tmp_path, goal="g"), [st.ProfileStage()])

    assert proposer.DECLARED_KEYS_RULE in plan_prompt(state.profile)
    assert st.PlanStage().params(ctx, state)["declared_keys"] is True


def create(pg_schema: PgSchema, statement: str) -> None:
    """Run one DDL statement in the test schema; `{s}` stands for the schema."""
    pg_schema.conn.execute(sql.SQL(statement).format(s=sql.Identifier(pg_schema.name)))


def test_postgres_declares_single_column_keys_in_the_schema_and_skips_the_rest(pg_schema: PgSchema):
    geo = sql.Identifier(f"{pg_schema.name}_geo")
    pg_schema.conn.execute(sql.SQL("CREATE SCHEMA {}").format(geo))
    try:
        pg_schema.conn.execute(sql.SQL("CREATE TABLE {}.regions (region_id text PRIMARY KEY)").format(geo))
        create(pg_schema, "CREATE TABLE {s}.customers (customer_id text PRIMARY KEY)")
        create(
            pg_schema,
            "CREATE TABLE {s}.orders (order_id integer PRIMARY KEY, "
            "customer_id text REFERENCES {s}.customers, region_id text)",
        )
        region_key = (
            "ALTER TABLE {}.orders ADD CONSTRAINT orders_region FOREIGN KEY (region_id) REFERENCES {}.regions"
        )
        pg_schema.conn.execute(sql.SQL(region_key).format(sql.Identifier(pg_schema.name), geo))
        create(
            pg_schema,
            "CREATE TABLE {s}.lines (order_id integer REFERENCES {s}.orders, line_no integer, "
            "PRIMARY KEY (order_id, line_no))",
        )

        keys = PostgresTables(pg_schema.url, pg_schema.name).declared_keys()
    finally:
        pg_schema.conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(geo))

    assert keys.primary_keys == {"customers": "customer_id", "orders": "order_id"}
    assert [(fk.from_table, fk.from_column, fk.to_table) for fk in keys.foreign_keys] == [
        ("lines", "order_id", "orders"),
        ("orders", "customer_id", "customers"),
    ]
    assert keys.skipped == [
        "lines.lines_pkey: 2 columns",
        f"orders.orders_region: refers to schema {pg_schema.name}_geo",
    ]


def test_rows_are_exported_in_primary_key_order(pg_schema: PgSchema, tmp_path):
    create(pg_schema, "CREATE TABLE {s}.orders (order_id integer PRIMARY KEY, note text)")
    for order_id in (3, 1, 2):  # inserted out of order
        pg_schema.conn.execute(
            sql.SQL("INSERT INTO {} VALUES (%s, 'x')").format(sql.Identifier(pg_schema.name, "orders")),
            (order_id,),
        )

    staged = stage_structured(tmp_path, tmp_path / "staging", PostgresTables(pg_schema.url, pg_schema.name))
    with (staged.staged_dir / "orders.csv").open(encoding="utf-8", newline="") as f:
        assert [row["order_id"] for row in csv.DictReader(f)] == ["1", "2", "3"]


# The furniture tables' keys as a database would declare them; part_supplier_mapping has none of its own.
FURNITURE_KEYS = [
    "ALTER TABLE {s}.products ADD PRIMARY KEY (product_id)",
    "ALTER TABLE {s}.assemblies ADD PRIMARY KEY (assembly_id)",
    "ALTER TABLE {s}.components ADD PRIMARY KEY (part_id)",
    "ALTER TABLE {s}.suppliers ADD PRIMARY KEY (supplier_id)",
    "ALTER TABLE {s}.assemblies ADD FOREIGN KEY (product_id) REFERENCES {s}.products",
    "ALTER TABLE {s}.components ADD FOREIGN KEY (assembly_id) REFERENCES {s}.assemblies",
    "ALTER TABLE {s}.part_supplier_mapping ADD FOREIGN KEY (part_id) REFERENCES {s}.components",
    "ALTER TABLE {s}.part_supplier_mapping ADD FOREIGN KEY (supplier_id) REFERENCES {s}.suppliers",
]


def test_the_furniture_constraints_only_add_marks_to_the_profile(pg_schema: PgSchema, tmp_path):
    load_csv_dir(pg_schema.conn, pg_schema.name, ROOT / "data")
    for statement in FURNITURE_KEYS:
        create(pg_schema, statement)
    source = PostgresTables(pg_schema.url, pg_schema.name)
    from_postgres = profile_directory(stage_structured(tmp_path, tmp_path / "pg", source).staged_dir)
    from_files = profile_directory(stage_structured(ROOT / "data", tmp_path / "files").staged_dir)

    assert sum(bool(c.primary_key) for f in from_postgres.files for c in f.columns) == 4
    assert [fk.declared for fk in from_postgres.foreign_keys] == [True] * 4  # the search found the same 4
    # without the marks and the samples (an open issue: they differ between two runs) the profiles agree
    marks = {"primary_key", "samples"}
    without = {
        "files": {"__all__": {"columns": {"__all__": marks}}},
        "foreign_keys": {"__all__": {"declared"}},
    }
    assert from_postgres.model_dump(exclude=without) == from_files.model_dump(exclude=without)
    assert json.loads(from_files.model_dump_json()) == from_files.model_dump()  # files: no marks at all
