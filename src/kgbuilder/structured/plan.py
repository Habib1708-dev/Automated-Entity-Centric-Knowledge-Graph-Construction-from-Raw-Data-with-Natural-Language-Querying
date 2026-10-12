"""The construction plan: declarative rules turning CSV files into nodes and relationships.

Role in the pipeline: the contract between the LLM proposer (which writes a plan), the human (who
reviews `out/plan.json`) and the importer (which executes it). `validate_plan` is the code gate every
plan must pass, whether it came from the LLM or from a hand edit.
Design: the field descriptions are sent to the LLM inside the response schema, so they are prompt text;
their examples come from an invented domain (beekeeping), never from an evaluated dataset (R75).
A file with no unique column is keyed by several columns (R155a): the model names them in `key_columns`,
code checks on the staged rows that together they tell every row apart, and computes the key
(`key_value`) into the property `unique_column` names, so every reader downstream still reads one key.
Not here: LLM calls (proposer.py) and graph writes (importer.py).
"""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from .profiler import DataProfile
from .staging import read_text_rows

# A value made of letters/digits joined by _ - or . without spaces is a code, not a name a person writes:
# "jönköping_coffee_table_assembly", "P-1000", "drawer.unit" match; "Legs" and "Drawer Rails" do not.
# [^\W_] is any Unicode letter or digit (\w without the underscore), so Swedish names count as letters.
_CODE_LIKE = re.compile(r"^[^\W_]+(?:[_.-][^\W_]+)+$")

# Joins the parts of a built key ("7|3"). Not ":" (record_ref is "<label>:<key>"), not "/" (record doc ids
# are "record/<label>/<key>"), not "#" (chunk ids are "<doc_id>#<n>"), not whitespace (parts are stripped).
KEY_SEPARATOR = "|"


def _unset(value: object) -> bool:
    """An unset field is left out of the JSON, the idiom of profiler.py (R115)."""
    return value is None


class NodeRule(BaseModel):
    source_file: str
    label: str = Field(description="PascalCase node label, e.g. Apiary")
    unique_column: str = Field(
        description=(
            "Column that uniquely identifies each node; with key_columns, a new name for the key that code "
            "builds from them, e.g. frame_key"
        )
    )
    # Optional and left out of the JSON when unset: every plan from before R155a loads and dumps as it did.
    # No min_length: a one-column list must reach validate_plan, whose issue the model can act on in the
    # next round, instead of failing the parse.
    key_columns: list[str] | None = Field(
        default=None,
        exclude_if=_unset,
        description=(
            "Only for a file with no unique column: the fewest columns whose values together tell its rows "
            "apart and are never empty, e.g. [hive_id, frame_no]; code joins them into unique_column. Null "
            "when unique_column is a column of the file."
        ),
    )
    properties: list[str] = Field(description="Other columns to import as node properties")
    description: str = Field(description="One sentence on what this node represents")
    # Optional so hand-written plans from before this field keep working; linking then guesses the column.
    name_column: str | None = Field(
        default=None,
        description=(
            "The column people would use to refer to one node in text, e.g. hive_name. It must be "
            "unique_column or one of properties. Null when no column holds a readable name."
        ),
    )


def name_property(rule: NodeRule) -> str:
    """The property holding a node's human-readable name.

    The plan's `name_column` when set. Otherwise a guess: the first name-like column, else the key. The
    guess is what made R11 necessary: `sub_assembly_name` comes before `part_name`, so parts were matched
    by their sub-assembly code.
    Moved here from resolution/linking.py (R67): record documents need it too, and it reads only the plan.
    """
    if rule.name_column is not None:
        return rule.name_column
    for prop in [*rule.properties, rule.unique_column]:
        if "name" in prop.lower() or "title" in prop.lower():
            return prop
    return rule.unique_column


class RelationshipRule(BaseModel):
    source_file: str
    relationship_type: str = Field(description="UPPER_SNAKE_CASE type, e.g. KEPT_AT")
    from_label: str
    from_column: str = Field(
        description=(
            "Column in source_file holding the from-node's key, or the from-node's unique_column when it is "
            "built from key_columns of this same file"
        )
    )
    to_label: str
    to_column: str = Field(
        description=(
            "Column in source_file holding the to-node's key, or the to-node's unique_column when it is "
            "built from key_columns of this same file"
        )
    )
    properties: list[str] = Field(description="Columns to import as relationship properties")


class ConstructionPlan(BaseModel):
    nodes: list[NodeRule]
    relationships: list[RelationshipRule]

    def node(self, label: str) -> NodeRule | None:
        return next((n for n in self.nodes if n.label == label), None)


def joined_key(row: Mapping[str, object], columns: Sequence[str]) -> str | None:
    """The cells of `columns` as stripped text, joined by `KEY_SEPARATOR`; None when any is None or blank.

    For text rows (csv cells) only: a DuckDB-typed value prints differently (1.50 -> 1.5, 007 -> 7).
    """
    # `"" if v is None` and never `v or ""`: the second turns a typed 0 into a blank part
    parts = ["" if row.get(c) is None else str(row.get(c)).strip() for c in columns]
    return None if any(p == "" for p in parts) else KEY_SEPARATOR.join(parts)


def key_value(rule: NodeRule, row: Mapping[str, object]) -> object:
    """The key of the node `row` becomes: the cell of `unique_column` as read for a plain rule, else
    `joined_key` over the rule's `key_columns` (None when a part is empty: the importer skips the row)."""
    if rule.key_columns is None:
        return row.get(rule.unique_column)
    return joined_key(row, rule.key_columns)


def built_keys(plan: ConstructionPlan, source_file: str) -> dict[str, NodeRule]:
    """`unique_column` -> rule, for the rules with `key_columns` that read `source_file`: the key names a
    relationship rule of that file may use as an end although no such column exists."""
    return {
        n.unique_column: n for n in plan.nodes if n.key_columns is not None and n.source_file == source_file
    }


def built_ends(plan: ConstructionPlan, rel: RelationshipRule) -> dict[str, NodeRule]:
    """Column -> rule for the ends of `rel` that are built keys of its source file; the other ends are
    real columns. The one place validator and importer decide this, so the two never disagree."""
    built = built_keys(plan, rel.source_file)
    return {c: built[c] for c in (rel.from_column, rel.to_column) if c in built}


def column_value(plan: ConstructionPlan, source_file: str, column: str, row: Mapping[str, object]) -> object:
    """A relationship end's cell: the built key when `column` names one for this file, else the cell."""
    rule = built_keys(plan, source_file).get(column)
    return row.get(column) if rule is None else key_value(rule, row)


def validate_plan(plan: ConstructionPlan, profile: DataProfile, staged_dir: Path | None = None) -> list[str]:
    """Check a plan against the profiled data. Returns a list of problems; empty means valid.

    `staged_dir` holds the staged CSVs. Only a rule with `key_columns` needs it: the profile measures each
    column alone, so whether a combination of columns tells the rows apart is read from the rows (R155a).
    Without it such a rule is refused, never passed unchecked.
    """
    issues = []
    labels = [n.label for n in plan.nodes]
    for label in {name for name in labels if labels.count(name) > 1}:
        issues.append(f"node label '{label}' is defined more than once")
    for node in plan.nodes:
        if node.key_columns is None:
            issues.extend(_plain_key_issues(node, profile))
        else:
            issues.extend(_key_issues(f"node {node.label}", node, plan, profile, staged_dir))
    issues.extend(_relationship_issues(plan, profile))
    issues.extend(_connectivity_issues(plan, profile))
    return issues


def _column_issues(rule_name: str, file: str, columns: list[str], profile: DataProfile) -> list[str]:
    """The file must exist in the profile and hold every column of `columns`."""
    file_profile = profile.file(file)
    if file_profile is None:
        return [f"{rule_name}: source file '{file}' does not exist"]
    return [f"{rule_name}: '{file}' has no column '{c}'" for c in columns if file_profile.column(c) is None]


def _plain_key_issues(node: NodeRule, profile: DataProfile) -> list[str]:
    """A node keyed by one column of its file: the columns exist, the key is unique, the name is a name."""
    name = f"node {node.label}"
    issues = _column_issues(name, node.source_file, [node.unique_column, *node.properties], profile)
    file_profile = profile.file(node.source_file)
    column = file_profile.column(node.unique_column) if file_profile else None
    if column is not None and not column.is_unique:
        issues.append(
            f"{name}: '{node.unique_column}' is not unique in '{node.source_file}' "
            f"({column.distinct_count} distinct values, {column.null_count} nulls, "
            f"{file_profile.row_count} rows)"
        )
    issues.extend(_name_column_issues(name, node, profile))
    return issues


def _key_issues(
    name: str, node: NodeRule, plan: ConstructionPlan, profile: DataProfile, staged_dir: Path | None
) -> list[str]:
    """A node keyed by `key_columns`: the plan-level checks first, the staged rows only when they pass.

    The rows are read last so that a missing file or column becomes an issue, never a crash, and so that
    a plan the profile already refuses costs no pass over the file.
    """
    issues = _built_name_issues(name, node, plan, profile)
    file_profile = profile.file(node.source_file)
    # a part unique by itself is a plain key the model padded: told from the profile, without the rows.
    # Minimality beyond this case is asked for in words only: checking it costs a pass per column, and on a
    # sample a smaller combination can be unique by chance where the full data is not.
    for column in node.key_columns if file_profile else []:
        profiled = file_profile.column(column)
        if profiled is not None and profiled.is_unique:
            issues.append(
                f"{name}: '{column}' is unique in '{node.source_file}' by itself; use unique_column "
                f"'{column}' without key_columns"
            )
    if issues:
        return issues
    return _key_row_issues(name, node, staged_dir)


def _built_name_issues(name: str, node: NodeRule, plan: ConstructionPlan, profile: DataProfile) -> list[str]:
    """Two or more different columns that exist; a new name for the built key; a name column that is not
    the built key (a code joined by code is never a name a person writes)."""
    issues = []
    if len(set(node.key_columns)) < 2:
        issues.append(
            f"{name}: key_columns needs two or more different columns; for one column use unique_column"
        )
    issues += _column_issues(name, node.source_file, [*node.key_columns, *node.properties], profile)
    file_profile = profile.file(node.source_file)
    if file_profile is not None and file_profile.column(node.unique_column) is not None:
        issues.append(
            f"{name}: unique_column '{node.unique_column}' is a column of '{node.source_file}'; with "
            "key_columns it must be a new name for the key that code builds"
        )
    if node.unique_column in node.properties:
        issues.append(
            f"{name}: unique_column '{node.unique_column}' is built from key_columns, not a property"
        )
    # two built keys of one name in one file would make a relationship end ambiguous (`built_keys`)
    twins = [
        n
        for n in plan.nodes
        if n is not node and n.key_columns is not None and n.source_file == node.source_file
    ]
    if any(n.unique_column == node.unique_column for n in twins):
        issues.append(
            f"{name}: unique_column '{node.unique_column}' is the built key of another node of "
            f"'{node.source_file}'"
        )
    if node.name_column is not None and node.name_column not in node.properties:
        issues.append(f"{name}: name_column must be one of properties: the built key is a code")
    else:
        issues.extend(_name_column_issues(name, node, profile))
    return issues


def _key_row_issues(name: str, node: NodeRule, staged_dir: Path | None) -> list[str]:
    """Every staged row must give a complete key, and no two rows the same one: one node per row."""
    if staged_dir is None:
        return [f"key_columns of {node.label} cannot be checked without the staged rows"]
    columns = node.key_columns
    path = staged_dir / node.source_file
    rows = read_text_rows(path) if path.is_file() else None
    # a CSV that is not comma-separated reads as one column named by the whole header line
    if rows is None or (rows and not all(c in rows[0] for c in columns)):
        return [
            f"{name}: '{node.source_file}' cannot be read as comma-separated rows with columns "
            f"[{', '.join(columns)}]"
        ]
    keys = [joined_key(row, columns) for row in rows]
    distinct = {k for k in keys if k is not None}
    empty = keys.count(None)
    # counted on the joined text: cells that differ only by spaces, or a separator inside a cell
    # ("a|b","c" against "a","b|c"), are refused as duplicates, never merged into one node
    if empty or len(distinct) != len(rows):
        return [
            f"{name}: key_columns [{', '.join(columns)}] do not identify every row of '{node.source_file}' "
            f"({len(distinct)} distinct combinations, {empty} rows with an empty column, {len(rows)} rows)"
        ]
    return []


def _relationship_issues(plan: ConstructionPlan, profile: DataProfile) -> list[str]:
    """Each rule's columns exist, its labels are defined, its ends match their nodes' keys, and no rule is
    defined twice."""
    issues = []
    seen = set()
    for rel in plan.relationships:
        name = f"relationship {rel.relationship_type} ({rel.from_label}->{rel.to_label})"
        built = built_ends(plan, rel)
        # a built key is no column of the file; one built in another file is refused here as "no column"
        real = [c for c in (rel.from_column, rel.to_column) if c not in built]
        issues += _column_issues(name, rel.source_file, [*real, *rel.properties], profile)
        for label in (rel.from_label, rel.to_label):
            if plan.node(label) is None:
                issues.append(f"{name}: node label '{label}' is not defined in the plan")
        issues += _end_issues(name, rel, plan, built)
        key = (rel.relationship_type, rel.from_label, rel.to_label)
        if key in seen:
            issues.append(f"{name}: defined more than once")
        seen.add(key)
    return issues


def _end_issues(
    name: str, rel: RelationshipRule, plan: ConstructionPlan, built: dict[str, NodeRule]
) -> list[str]:
    """A built key may only be the end of its own node, and a node with a built key is reached only by it.
    `built` is `built_ends(plan, rel)`.

    The second refuses at plan time what would otherwise fail every row at import: the likely slip is the
    detail label with its foreign-key column, which holds the parent's key, never the detail's.
    """
    issues = []
    for label, column in ((rel.from_label, rel.from_column), (rel.to_label, rel.to_column)):
        if column in built and built[column].label != label:
            issues.append(f"{name}: '{column}' is the built key of {built[column].label}, not of {label}")
        rule = plan.node(label)
        keyed = rule is not None and rule.key_columns is not None
        if keyed and (column != rule.unique_column or rel.source_file != rule.source_file):
            issues.append(
                f"{name}: {label} is keyed by key_columns: use its unique_column '{rule.unique_column}' "
                f"as from_column/to_column, in '{rule.source_file}'"
            )
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


def _connectivity_issues(plan: ConstructionPlan, profile: DataProfile) -> list[str]:
    """Tables the data joins must be joined in the plan; tables nothing joins may stand apart (R72).

    Two tables are joined in the data when a chain of foreign-key candidates links their files, possibly
    through a link table that the plan imports only as relationships. Their labels must then lie in one
    component of the plan's graph: an island there is a relationship the plan forgot, and no traversal
    could cross it. Tables that no key links (two unrelated record sets) have nothing to relate by, so a
    plan may keep them apart instead of inventing a link.
    """
    labels = [n.label for n in plan.nodes]
    component = _groups(labels, [(r.from_label, r.to_label) for r in plan.relationships])
    files = sorted({f.file for f in profile.files} | {n.source_file for n in plan.nodes})
    joined = _groups(files, [(fk.from_file, fk.to_file) for fk in profile.foreign_keys])
    # the plan's components met by each group of files the data joins
    met: dict[str, dict[str, list[str]]] = {}
    for node in plan.nodes:
        met.setdefault(joined[node.source_file], {}).setdefault(component[node.label], []).append(node.label)
    issues = []
    for file_group, components in met.items():
        if len(components) > 1:
            groups = "; ".join(", ".join(sorted(c)) for c in components.values())
            keys = ", ".join(
                f"{fk.from_file}.{fk.from_column} -> {fk.to_file}.{fk.to_column}"
                for fk in profile.foreign_keys
                if joined[fk.from_file] == file_group
            )
            issues.append(
                f"schema is not connected, isolated groups: {groups} (joined in the data by {keys})"
            )
    return issues


def _groups(items: list[str], pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Union-find: each item's group representative after joining every pair of known items."""
    parent = {item: item for item in items}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    for a, b in pairs:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    return {item: find(item) for item in items}
