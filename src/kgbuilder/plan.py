"""The construction plan: declarative rules turning CSV files into nodes and relationships."""

from pydantic import BaseModel, Field

from .profiler import DataProfile


class NodeRule(BaseModel):
    source_file: str
    label: str = Field(description="PascalCase node label, e.g. Product")
    unique_column: str = Field(description="Column that uniquely identifies each node")
    properties: list[str] = Field(description="Other columns to import as node properties")
    description: str = Field(description="One sentence on what this node represents")


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
    for label in {l for l in labels if labels.count(l) > 1}:
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


def _connectivity_issues(plan: ConstructionPlan) -> list[str]:
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
