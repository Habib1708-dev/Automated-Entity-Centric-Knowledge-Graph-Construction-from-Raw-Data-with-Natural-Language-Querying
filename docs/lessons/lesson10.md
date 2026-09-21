# Lesson 8 - Knowledge Graph Construction - Part I

With all the plans in place, it's time to construct the knowledge graph. 

For the **domain graph** construction, no agent is required. The construction plan has all the information needed to drive a rule-based import.

<img src="images/domain.png" width="600">

**Note**: This notebook uses Cypher queries to build the domain graph from CSV files. Don't worry if you're unfamiliar with Cypher — focus on understanding the big picture of how the structured data is transformed into a graph structure based on the construction plan.

## 8.1. Tool

A single tool which will build a knowledge graph using the defined construction rules.
- Input: `approved_construction_plan`
- Output: a domain graph in Neo4j
- Tools: `construct_domain_graph` + helper functions

**Workflow**

1. The context is initialized with an `approved_construction_plan` and `approved_files`
2. Process all the node construction rules
3. Process all the relationship construction rules


## 8.2. Setup

The usual import of needed libraries, loading of environment variables, and connection to Neo4j.

# Import necessary libraries

from google.adk.models.lite_llm import LiteLlm # For OpenAI support

# Convenience libraries for working with Neo4j inside of Google ADK
from neo4j_for_adk import graphdb, tool_success, tool_error

from typing import Dict, Any

import warnings
# Ignore all warnings
warnings.filterwarnings("ignore")

import logging
logging.basicConfig(level=logging.CRITICAL)

print("Libraries imported.")

# --- Define Model Constants for easier use ---
MODEL_GPT_4O = "openai/gpt-4o"

llm = LiteLlm(model=MODEL_GPT_4O)

# Test LLM with a direct call
print(llm.llm_client.completion(model=llm.model, messages=[{"role": "user", "content": "Are you ready?"}], tools=[]))

print("\nOpenAI ready.")

# Check connection to Neo4j by sending a query

neo4j_is_ready = graphdb.send_query("RETURN 'Neo4j is Ready!' as message")

print(neo4j_is_ready)

## 8.3. Tool Definitions (Domain Graph Construction)

The `construct_domain_graph` tool is responsible for constructing the "domain graph" from CSV files,
according to the approved construction plan.

### Function: create_uniqueness_constraint



This function creates a uniqueness constraint in Neo4j to prevent duplicate nodes with the same label and property value from being created.

def create_uniqueness_constraint(
    label: str,
    unique_property_key: str,
) -> Dict[str, Any]:
    """Creates a uniqueness constraint for a node label and property key.
    A uniqueness constraint ensures that no two nodes with the same label and property key have the same value.
    This improves the performance and integrity of data import and later queries.

    Args:
        label: The label of the node to create a constraint for.
        unique_property_key: The property key that should have a unique value.

    Returns:
        A dictionary with a status key ('success' or 'error').
        On error, includes an 'error_message' key.
    """    
    # Use string formatting since Neo4j doesn't support parameterization of labels and property keys when creating a constraint
    constraint_name = f"{label}_{unique_property_key}_constraint"
    query = f"""CREATE CONSTRAINT `{constraint_name}` IF NOT EXISTS
    FOR (n:`{label}`)
    REQUIRE n.`{unique_property_key}` IS UNIQUE"""
    results = graphdb.send_query(query)
    return results


### Function: load_nodes_from_csv

This function performs batch loading of nodes from a CSV file into Neo4j. It uses the `LOAD CSV` command with the `MERGE` operation to create nodes while avoiding duplicates based on the unique column. The Cypher query processes data in batches of 1000 rows for better performance.

**Note**: The csv files are stored in the `/import` directory of `neo4j` database. When you use the query `LOAD CSV from "file:///" + $source_file`, neo4j checks the `/import` directory by default.

def load_nodes_from_csv(
    source_file: str,
    label: str,
    unique_column_name: str,
    properties: list[str],
) -> Dict[str, Any]:
    """Batch loading of nodes from a CSV file"""

    # load nodes from CSV file by merging on the unique_column_name value
    query = f"""LOAD CSV WITH HEADERS FROM "file:///" + $source_file AS row
    CALL (row) {{
        MERGE (n:$($label) {{ {unique_column_name} : row[$unique_column_name] }})
        FOREACH (k IN $properties | SET n[k] = row[k])
    }} IN TRANSACTIONS OF 1000 ROWS
    """

    results = graphdb.send_query(query, {
        "source_file": source_file,
        "label": label,
        "unique_column_name": unique_column_name,
        "properties": properties
    })
    return results


### Execute Domain Graph Construction

This cell executes the main construction function using the approved construction plan. It builds the complete knowledge graph by importing all nodes and relationships according to the defined rules.

### Function: import_nodes

This function orchestrates the node import process by first creating a uniqueness constraint and then loading nodes from the CSV file. It ensures data integrity by establishing constraints before importing data.

def import_nodes(node_construction: dict) -> dict:
    """Import nodes as defined by a node construction rule."""

    # create a uniqueness constraint for the unique_column
    uniqueness_result = create_uniqueness_constraint(
        node_construction["label"],
        node_construction["unique_column_name"]
    )

    if (uniqueness_result["status"] == "error"):
        return uniqueness_result

    # import nodes from csv
    load_nodes_result = load_nodes_from_csv(
        node_construction["source_file"],
        node_construction["label"],
        node_construction["unique_column_name"],
        node_construction["properties"]
    )

    return load_nodes_result

### Function: import_relationships

This function imports relationships between nodes from a CSV file. It uses a Cypher query that matches existing nodes and creates relationships between them. The query finds pairs of nodes and creates relationships with specified properties between them.

def import_relationships(relationship_construction: dict) -> Dict[str, Any]:
    """Import relationships as defined by a relationship construction rule."""

    # load nodes from CSV file by merging on the unique_column_name value 
    from_node_column = relationship_construction["from_node_column"]
    to_node_column = relationship_construction["to_node_column"]
    query = f"""LOAD CSV WITH HEADERS FROM "file:///" + $source_file AS row
    CALL (row) {{
        MATCH (from_node:$($from_node_label) {{ {from_node_column} : row[$from_node_column] }}),
              (to_node:$($to_node_label) {{ {to_node_column} : row[$to_node_column] }} )
        MERGE (from_node)-[r:$($relationship_type)]->(to_node)
        FOREACH (k IN $properties | SET r[k] = row[k])
    }} IN TRANSACTIONS OF 1000 ROWS
    """
    
    results = graphdb.send_query(query, {
        "source_file": relationship_construction["source_file"],
        "from_node_label": relationship_construction["from_node_label"],
        "from_node_column": relationship_construction["from_node_column"],
        "to_node_label": relationship_construction["to_node_label"],
        "to_node_column": relationship_construction["to_node_column"],
        "relationship_type": relationship_construction["relationship_type"],
        "properties": relationship_construction["properties"]
    })
    return results

### Function: construct_domain_graph

This is the main orchestration function that builds the entire domain graph. It processes the construction plan in two phases:
1. **Node Construction**: First imports all nodes to ensure they exist before creating relationships
2. **Relationship Construction**: Then creates relationships between the existing nodes

This two-phase approach prevents relationship creation failures due to missing nodes.

def construct_domain_graph(construction_plan: dict) -> Dict[str, Any]:
    """Construct a domain graph according to a construction plan."""
    # first, import nodes
    node_constructions = [value for value in construction_plan.values() if value['construction_type'] == 'node']
    for node_construction in node_constructions:
        import_nodes(node_construction)

    # second, import relationships
    relationship_constructions = [value for value in construction_plan.values() if value['construction_type'] == 'relationship']
    for relationship_construction in relationship_constructions:
        import_relationships(relationship_construction)

## 8.4. Run construct_domain_graph()

This cell defines the approved construction plan as a dictionary containing rules for creating nodes and relationships. The plan includes:

- **Node Rules**: Define how to create Assembly, Part, Product, and Supplier nodes from CSV files
- **Relationship Rules**: Define how to create Contains, Is_Part_Of, and Supplied_By relationships

Each rule specifies the source file, labels, unique identifiers, and properties to be imported.

# the approved construction plan should look something like this...
approved_construction_plan = {
    "Assembly": {
        "construction_type": "node", 
        "source_file": "assemblies.csv", 
        "label": "Assembly", 
        "unique_column_name": "assembly_id", 
        "properties": ["assembly_name", "quantity", "product_id"]
    }, 
    "Part": {
        "construction_type": "node", 
        "source_file": "parts.csv", 
        "label": "Part", 
        "unique_column_name": "part_id", 
        "properties": ["part_name", "quantity", "assembly_id"]
    }, 
    "Product": {
        "construction_type": "node", 
        "source_file": "products.csv", 
        "label": "Product", 
        "unique_column_name": "product_id", 
        "properties": ["product_name", "price", "description"]
    }, 
    "Supplier": {
        "construction_type": "node", 
        "source_file": "suppliers.csv", 
        "label": "Supplier", 
        "unique_column_name": "supplier_id", 
        "properties": ["name", "specialty", "city", "country", "website", "contact_email"]
    }, 
    "Contains": {
        "construction_type": "relationship", 
        "source_file": "assemblies.csv", 
        "relationship_type": "Contains", 
        "from_node_label": "Product", 
        "from_node_column": "product_id", 
        "to_node_label": "Assembly", 
        "to_node_column": "assembly_id", 
        "properties": ["quantity"]
    }, 
    "Is_Part_Of": {
        "construction_type": "relationship", 
        "source_file": "parts.csv", 
        "relationship_type": "Is_Part_Of", 
        "from_node_label": "Part", 
        "from_node_column": "part_id", 
        "to_node_label": "Assembly", 
        "to_node_column": "assembly_id", 
        "properties": ["quantity"]
    }, 
    "Supplied_By": {
        "construction_type": "relationship", 
        "source_file": "part_supplier_mapping.csv", 
        "relationship_type": "Supplied_By", 
        "from_node_label": "Part", 
        "from_node_column": "part_id", 
        "to_node_label": "Supplier", 
        "to_node_column": "supplier_id", 
        "properties": ["supplier_name", "lead_time_days", "unit_cost", "minimum_order_quantity", "preferred_supplier"]
    }
}


construct_domain_graph(approved_construction_plan)

## 8.5 Inspect the Domain Graph

This cell filters the construction plan to extract only the relationship construction rules. This list will be used in the next cell to verify that all relationships were successfully created in the graph.

# extract a list of the relationship construction rules
relationship_constructions = [
    value for value in approved_construction_plan.values()
    if value.get("construction_type") == "relationship"
]
relationship_constructions

This cell creates and executes a Cypher query to verify that all relationship types from the construction plan were successfully created in the graph. 

The query uses several advanced Cypher features:
- `UNWIND`: Iterates through each relationship construction rule
- `CALL (construction) { ... }`: Subquery that executes for each construction rule
- `MATCH (from)-[r:relationship_type]->(to)`: Finds one example of each relationship type
- `LIMIT 1`: Returns only one example per relationship type

This provides a summary view showing one instance of each relationship pattern in the constructed graph.

# a fancy cypher query which to show one instance of each construction rule

# turn the list of rules into multiple single rules
unwind_list = "UNWIND $relationship_constructions AS construction"

# match a single path for a given construction.relationship_type
# return only the labels and types from the 3 parts of the path
match_one_path = """
    MATCH (from)-[r:$(construction.relationship_type)]->(to)
    RETURN labels(from) AS fromNode, type(r) AS relationship, labels(to) AS toNode
    LIMIT 1
"""
match_in_subquery = f"""
CALL (construction) {{
{match_one_path}
}}
"""

cypher = f"""
{unwind_list}
{match_in_subquery}
RETURN fromNode, relationship, toNode
"""

print(cypher)

print("\n---")

graphdb.send_query(cypher, {
    "relationship_constructions": relationship_constructions
})

