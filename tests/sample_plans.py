"""Construction plans matching the CSV fixtures in conftest.py, shared by several test modules."""

from kgbuilder.plan import ConstructionPlan, NodeRule, RelationshipRule


def node(file, label, key, props=()):
    """Shorthand for a NodeRule without a description."""
    return NodeRule(source_file=file, label=label, unique_column=key, properties=list(props), description="")


def rel(file, rel_type, from_label, from_col, to_label, to_col, props=()):
    """Shorthand for a RelationshipRule."""
    return RelationshipRule(
        source_file=file,
        relationship_type=rel_type,
        from_label=from_label,
        from_column=from_col,
        to_label=to_label,
        to_column=to_col,
        properties=list(props),
    )


# Product -CONTAINS-> Assembly -SUPPLIED_BY-> Supplier: valid and connected for the conftest files.
GOOD_PLAN = ConstructionPlan(
    nodes=[
        node("products.csv", "Product", "product_id", ["product_name", "price"]),
        node("assemblies.csv", "Assembly", "assembly_id", ["assembly_name"]),
        node("suppliers.csv", "Supplier", "supplier_id", ["name"]),
    ],
    relationships=[
        rel("assemblies.csv", "CONTAINS", "Product", "product_id", "Assembly", "assembly_id", ["quantity"]),
        rel("assembly_supplier.csv", "SUPPLIED_BY", "Assembly", "assembly_id", "Supplier", "supplier_id"),
    ],
)
