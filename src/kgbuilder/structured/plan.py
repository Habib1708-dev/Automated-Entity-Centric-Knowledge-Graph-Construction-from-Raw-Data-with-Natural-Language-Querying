"""The construction plan: declarative rules turning CSV files into nodes and relationships.

Role in the pipeline: the contract between the LLM proposer (which writes a plan), the human (who
reviews `out/plan.json`) and the importer (which executes it). `validate_plan` is the code gate every
plan must pass, whether it came from the LLM or from a hand edit.
Design: the field descriptions are sent to the LLM inside the response schema, so they are prompt text.
Not here: LLM calls (proposer.py) and graph writes (importer.py).
"""

import re

from pydantic import BaseModel, Field

from .profiler import DataProfile

# A value made of letters/digits joined by _ - or . without spaces is a code, not a name a person writes:
# "jönköping_coffee_table_assembly", "P-1000", "drawer.unit" match; "Legs" and "Drawer Rails" do not.
# [^\W_] is any Unicode letter or digit (\w without the underscore), so Swedish names count as letters.
_CODE_LIKE = re.compile(r"^[^\W_]+(?:[_.-][^\W_]+)+$")


class NodeRule(BaseModel):
    source_file: str
    label: str = Field(description="PascalCase node label, e.g. Product")
    unique_column: str = Field(description="Column that uniquely identifies each node")
    properties: list[str] = Field(description="Other columns to import as node properties")
    description: str = Field(description="One sentence on what this node represents")
    # Optional so hand-written plans from before this field keep working; linking then guesses the column.
    name_column: str | None = Field(
        default=None,
        description=(
            "The column people would use to refer to one node in text, e.g. part_name. It must be "
            "unique_column or one of properties. Null when no column holds a readable name."
        ),
    )


class RelationshipRule(BaseModel):
    source_file: str
    relationship_type: str = Field(description="UPPER_SNAKE_CASE type, e.g. SUPPLIED_BY")
    from_label: str
    from_column: str = Field(description="Column in source_file holding the from-node's key")
    to_label: str
    to_column: str = Field(description="Column in source_file holding the to-node's key")
    properties: list[str] = Field(description="Columns to import as relationship properties")


class ConstructionPlan(BaseModel):
    nodes: list[NodeRule]
    relationships: list[RelationshipRule]

    def node(self, label: str) -> NodeRule | None:
        return next((n for n in self.nodes if n.label == label), None)


def validate_plan(plan: ConstructionPlan, profile: DataProfile) -> list[str]:
    """Check a plan against the profiled data. Returns a list of problems; empty means valid."""
    issues = []

    def check_columns(rule_name: str, file: str, columns: list[str]) -> None:
        file_profile = profile.file(file)
        if file_profile is None:
            issues.append(f"{rule_name}: source file '{file}' does not exist")
            return
        for column in columns:
            if file_profile.column(column) is None:
                issues.append(f"{rule_name}: '{file}' has no column '{column}'")

    labels = [n.label for n in plan.nodes]
    for label in {name for name in labels if labels.count(name) > 1}:
        issues.append(f"node label '{label}' is defined more than once")

    for node in plan.nodes:
        name = f"node {node.label}"
        check_columns(name, node.source_file, [node.unique_column, *node.properties])
        file_profile = profile.file(node.source_file)
        column = file_profile.column(node.unique_column) if file_profile else None
        if column is not None and not column.is_unique:
            issues.append(
                f"{name}: '{node.unique_column}' is not unique in '{node.source_file}' "
                f"({column.distinct_count} distinct values, {column.null_count} nulls, "
                f"{file_profile.row_count} rows)"
            )
        issues.extend(_name_column_issues(name, node, profile))

    seen = set()
    for rel in plan.relationships:
        name = f"relationship {rel.relationship_type} ({rel.from_label}->{rel.to_label})"
        check_columns(name, rel.source_file, [rel.from_column, rel.to_column, *rel.properties])
        for label in (rel.from_label, rel.to_label):
            if plan.node(label) is None:
                issues.append(f"{name}: node label '{label}' is not defined in the plan")
        key = (rel.relationship_type, rel.from_label, rel.to_label)
        if key in seen:
            issues.append(f"{name}: defined more than once")
        seen.add(key)

    issues.extend(_connectivity_issues(plan))
    return issues


def _name_column_issues(name: str, node: NodeRule, profile: DataProfile) -> list[str]:
    """The name column must be imported, and must hold names people write rather than codes."""
    if node.name_column is None:
        return []
    # linking reads the name from the node, so it must be a column that is actually imported
    if node.name_column not in (node.unique_column, *node.properties):
        return [f"{name}: name_column '{node.name_column}' must be unique_column or one of properties"]
    file_profile = profile.file(node.source_file)
    column = file_profile.column(node.name_column) if file_profile else None
    # Text says "legs", never "uppsala_sofa_assembly": a code column links nothing. The LLM picked such a
    # column in a real run despite a prompt rule, so this is checked in code from the profiled samples.
    if column is not None and column.samples and all(_CODE_LIKE.match(s) for s in column.samples):
        return [
            f"{name}: name_column '{node.name_column}' holds codes such as '{column.samples[0]}', not names "
            "people write; choose a column with readable names, or null"
        ]
    return []


def _connectivity_issues(plan: ConstructionPlan) -> list[str]:
    """Union-find over labels: more than one component means islands no traversal can reach."""
    parent = {n.label: n.label for n in plan.nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    for rel in plan.relationships:
        if rel.from_label in parent and rel.to_label in parent:
            parent[find(rel.from_label)] = find(rel.to_label)

    components: dict[str, list[str]] = {}
    for label in parent:
        components.setdefault(find(label), []).append(label)
    if len(components) > 1:
        groups = "; ".join(", ".join(sorted(c)) for c in components.values())
        return [f"schema is not connected, isolated groups: {groups}"]
    return []
