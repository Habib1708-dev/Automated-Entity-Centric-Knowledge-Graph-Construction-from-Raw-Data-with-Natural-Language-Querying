"""Construction plans matching the CSV fixtures in conftest.py, shared by several test modules, and a small
table of detail rows with no unique column (`FRAME_FILES`, `FRAME_PLAN`, R155a) with its plan, and a
plan whose built key squashes to another rule's key (`UNIT_PLAN`, R155a)."""

from pathlib import Path

from kgbuilder.structured.plan import ConstructionPlan, NodeRule, RelationshipRule


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


# Frames listed per hive (R155a): no column of frames.csv is unique, the pair (hive_id, frame_no) is. Hive 1
# has frames 1-3, hive 2 frames 1 and 3, so frame number 3 and the note "honey" each occur under both hives.
FRAME_FILES = {
    "hives.csv": "hive_id,hive_name\n1,Linden Hive\n2,Clover Hive\n",
    "frames.csv": (
        "hive_id,frame_no,note\n1,1,capped brood\n1,2,honey\n1,3,empty comb\n2,1,honey\n2,3,capped brood\n"
    ),
}

# Frame is keyed by the key that code builds from its two columns; FRAME_OF relates each frame to its hive.
FRAME_RULE = NodeRule(
    source_file="frames.csv",
    label="Frame",
    unique_column="frame_key",
    key_columns=["hive_id", "frame_no"],
    properties=["frame_no", "note"],
    description="",
)
FRAME_PLAN = ConstructionPlan(
    nodes=[node("hives.csv", "Hive", "hive_id", ["hive_name"]), FRAME_RULE],
    relationships=[rel("frames.csv", "FRAME_OF", "Frame", "frame_key", "Hive", "hive_id")],
)

# A built key that squashes to a plain key's token: "P-10|00" (Unit, no name column) and "P-1000" (Model)
# are both "p1000" once `squash` drops separators, so only the rule tells the linker which a text may name.
UNIT_PLAN = ConstructionPlan(
    nodes=[
        node("models.csv", "Model", "model_id", ["model_name"]),
        NodeRule(
            source_file="units.csv",
            label="Unit",
            unique_column="unit_key",
            key_columns=["series", "serial"],
            properties=["note"],
            description="",
        ),
    ],
    relationships=[],
)


def write_files(directory: Path, files: dict[str, str]) -> Path:
    """Write `files` (name -> CSV text) into `directory`, created when missing. Returns `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (directory / name).write_text(content, encoding="utf-8")
    return directory
