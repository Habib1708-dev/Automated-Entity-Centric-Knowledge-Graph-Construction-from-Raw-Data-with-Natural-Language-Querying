"""Profiling (uniqueness, foreign keys) and plan validation on small CSV fixtures. No Neo4j needed."""

from kgbuilder.plan import ConstructionPlan, validate_plan
from kgbuilder.profiler import profile_directory

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
