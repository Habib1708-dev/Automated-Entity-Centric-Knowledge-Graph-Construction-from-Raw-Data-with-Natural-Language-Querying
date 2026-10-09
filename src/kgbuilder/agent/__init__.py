"""The agentic query engine (plan R130-R136): tools the agent calls to find, walk and read the graph.

R133: the seeding tool `find_nodes` (R132's fused node cards), the graph-walk tools `get_node`, `neighbours`
and `node_evidence` (graph_tools.py), and the text tools `search_text` and `search_claims` (text_tools.py),
each behind the `Tool` protocol (tool.py) and returning the models of results.py; node_reader.py reads what
no existing store reads. R134: the structured tools `schema`, `query_records` and `query_tables`
(structured_tools.py), whose typed queries (structured_query.py) code checks and compiles to read-only
Cypher (record_query.py) or SQL over the staged source tables (table_query.py, tables.py). R135 adds the
loop, its containment and its tracing.
Dependencies: pipeline -> agent -> hybrid, query, structured's plan model and identifier quoting, graph,
llm.base, core; nothing imports agent but the pipeline.
"""

from .graph_tools import FindNodes, GetNode, Neighbours, NodeEvidence
from .node_reader import Neo4jNodeReader, NodeReader, RelationPage
from .results import (
    ChunksResult,
    ChunkText,
    ClaimFact,
    ClaimsResult,
    NodeFacts,
    NodeHit,
    NodeResult,
    NodesResult,
    Property,
    Relation,
    RelationsResult,
    ToolResult,
)
from .structured_query import Condition, RecordQuery, StructuredQuery, TableQuery
from .structured_tools import QueryRecords, QueryResult, QueryTables, Schema, SchemaResult
from .tables import DuckDBTables, TableInfo, TableStore
from .text_tools import SearchClaims, SearchText
from .tool import Tool

__all__ = [
    "ChunksResult",
    "ChunkText",
    "ClaimFact",
    "ClaimsResult",
    "Condition",
    "DuckDBTables",
    "FindNodes",
    "GetNode",
    "Neighbours",
    "Neo4jNodeReader",
    "NodeEvidence",
    "NodeFacts",
    "NodeHit",
    "NodeReader",
    "NodeResult",
    "NodesResult",
    "Property",
    "QueryRecords",
    "QueryResult",
    "QueryTables",
    "RecordQuery",
    "Relation",
    "RelationPage",
    "RelationsResult",
    "Schema",
    "SchemaResult",
    "SearchClaims",
    "SearchText",
    "StructuredQuery",
    "TableInfo",
    "TableQuery",
    "TableStore",
    "Tool",
    "ToolResult",
]
