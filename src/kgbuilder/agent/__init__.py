"""The agentic query engine (plan R130-R136): tools the agent calls to find, walk and read the graph.

R133: the seeding tool `find_nodes` (R132's fused node cards), the graph-walk tools `get_node`, `neighbours`
and `node_evidence` (graph_tools.py), and the text tools `search_text` and `search_claims` (text_tools.py),
each behind the `Tool` protocol (tool.py) and returning the models of results.py; node_reader.py reads what
no existing store reads. R134 adds the structured tools, R135 the loop, its containment and its tracing.
Dependencies: pipeline -> agent -> hybrid, query, structured's plan model, graph, llm.base, core; nothing
imports agent but the pipeline.
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
from .text_tools import SearchClaims, SearchText
from .tool import Tool

__all__ = [
    "ChunkText",
    "ChunksResult",
    "ClaimFact",
    "ClaimsResult",
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
    "Relation",
    "RelationPage",
    "RelationsResult",
    "SearchClaims",
    "SearchText",
    "Tool",
    "ToolResult",
]
