# Lesson 8 - Knowledge Graph Construction - Part II

In this lesson, you'll continue with knowledge graph construction. The previous lesson created the domain
graph from CSV files according to the construction plan. Now, you will process the markdown files,
chunking them up into the lexical graph and the subject graph which will connect to the domain graph
for a complete knowledge graph. 

You will learn:
- how to use Neo4j's graphrag library to perform the chunking and entity extraction
- techniques for entity resolution
  

<img src="images/last.png" width="600">

**Note**: This notebook uses Cypher queries to build the domain graph from CSV files. Don't worry if you're unfamiliar with Cypher — focus on understanding the big picture of how the unstructured data is transformed into a graph structure based on the extraction plan.

<div style="background-color:#fff6ff; padding:13px; border-width:3px; border-color:#efe6ef; border-style:solid; border-radius:6px">
<p> 💻 &nbsp; <b>To access the helper.py, neo4j_for_adk.py and tools.py files :</b> 1) click on the <em>"File"</em> option on the top menu of the notebook and then 2) click on <em>"Open"</em>.

</div>

## 8.1 Tools

Two tools, with helper functions:
  1. `make_kg_builder` - to chunk markdown and produce the lexical and subject graphs
  2. `correlate_subject_and_domain_nodes` - to connect the subject graph to the domain graph
- Input: `approved_files`, `approved_construction_plan`, `approved_entities`, `approved_fact_types`
- Output: a completed knowledge graph with domain, lexical and subject graphs
  
**Workflow**

1. The context is initialized with an `approved_construction_plan` and `approved_files`
2. For each markdown file, `make_kg_builder` is called to create a construction pipeline
3. For each resulting entity label, `correlate_subject_and_domain_nodes` will connect the subject and domain graphs


## 8.2 Setup

The usual import of needed libraries, loading of environment variables, and connection to Neo4j.

### 8.2.1 Common Setup

# Import necessary libraries
import os
import re
from pathlib import Path

from google.adk.models.lite_llm import LiteLlm # For OpenAI support

# Convenience libraries for working with Neo4j inside of Google ADK
from neo4j_for_adk import graphdb, tool_success, tool_error

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

### 8.2.2 Load Part of the Domain Graph with a Helper Function

You're only loading the product nodes from the domain graph, because they're the only nodes that you'll use to connect the domain graph to the lexical graph.

from tools import load_product_nodes

load_product_nodes()

# expect to find non-entity nodes with a "Product" label
graphdb.send_query("MATCH (n) WHERE NOT n:`__Entity__` return DISTINCT labels(n) as nonEntityLabels")

### 8.2.3 Initialize State from Previous Workflow

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



approved_files = [
    "product_reviews/gothenburg_table_reviews.md",
    "product_reviews/helsingborg_dresser_reviews.md",
    "product_reviews/jonkoping_coffee_table_reviews.md",
    "product_reviews/linkoping_bed_reviews.md",
    "product_reviews/malmo_desk_reviews.md",
    "product_reviews/norrkoping_nightstand_reviews.md",
    "product_reviews/orebro_lamp_reviews.md",
    "product_reviews/stockholm_chair_reviews.md",
    "product_reviews/uppsala_sofa_reviews.md",
    "product_reviews/vasteras_bookshelf_reviews.md"
]


# approved entities from the `ner_agent` of Lesson 7
approved_entities = ['Product', 'Issue', 'Feature', 'Location']


# approved fact types from the `relevant_fact_agent` of Lesson 7
approved_fact_types = {'has_issue': {'subject_label': 'Product', 'predicate_label': 'has_issue', 'object_label': 'Issue'}, 'includes_feature': {'subject_label': 'Product', 'predicate_label': 'includes_feature', 'object_label': 'Feature'}, 'used_in_location': {'subject_label': 'Product', 'predicate_label': 'used_in_location', 'object_label': 'Location'}}

## 8.3 Tool Definitions for loading, chunking and entity extraction

The Neo4j GraphRAG library has a convenient `SimpleKGPipeline` which you can use to process chunks and extract entities with relationships.

For the markdown files you will be processing, you'll need to create some helper functions.

### 8.3.1 SimpleKGPipeline Interface

<img src="images/KG_pipeline.png" width="600">

from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline
 
# for example, creating a KG pipeline requires these arguments
if False:
    example = SimpleKGPipeline(
        llm=None, # the LLM to use for Entity and Relation extraction
        driver=None,  # a neo4j driver to write results to graph
        embedder=None,  # an Embedder for chunks
        from_pdf=True,   # sortof True because you will use a custom loader
        pdf_loader=None, # the custom loader for Markdown
        text_splitter=None, # the splitter you defined above
        schema=None, # that you just defined above
        prompt_template=None, # the template used for entity extraction on each chunk
    )

### 8.3.2 Text-Splitter for Chunking up the Markdown

Define a custom text splitter that uses regex patterns to chunk markdown text. This splitter breaks documents at specified delimiters (like "---") to create meaningful text segments for processing.

from neo4j_graphrag.experimental.components.text_splitters.base import TextSplitter
from neo4j_graphrag.experimental.components.types import TextChunk, TextChunks

# Define a custom text splitter. Chunking strategy could be yet-another-agent
class RegexTextSplitter(TextSplitter):
    """Split text using regex matched delimiters."""
    def __init__(self, re: str):
        self.re = re
    
    async def run(self, text: str) -> TextChunks:
        """Splits a piece of text into chunks.

        Args:
            text (str): The text to be split.

        Returns:
            TextChunks: A list of chunks.
        """
        texts = re.split(self.re, text)
        i = 0
        chunks = [TextChunk(text=str(text), index=i) for (i, text) in enumerate(texts)]
        return TextChunks(chunks=chunks)



### 8.3.3 Custom Markdown Data Loader

This custom loader adapts the Neo4j GraphRAG PDF loader to work with markdown files. It reads markdown content, extracts the document title from the first H1 header, and wraps it in the expected document format for the pipeline.

# custom file data loader

from neo4j_graphrag.experimental.components.pdf_loader import DataLoader
from neo4j_graphrag.experimental.components.types import PdfDocument, DocumentInfo

class MarkdownDataLoader(DataLoader):
    def extract_title(self,markdown_text):
        # Define a regex pattern to match the first h1 header
        pattern = r'^# (.+)$'

        # Search for the first match in the markdown text
        match = re.search(pattern, markdown_text, re.MULTILINE)

        # Return the matched group if found
        return match.group(1) if match else "Untitled"

    async def run(self, filepath: Path, metadata = {}) -> PdfDocument:
        with open(filepath, "r") as f:
            markdown_text = f.read()
        doc_headline = self.extract_title(markdown_text)
        markdown_info = DocumentInfo(
            path=str(filepath),
            metadata={
                "title": doc_headline,
            }
        )
        return PdfDocument(text=markdown_text, document_info=markdown_info)

### 8.3.4 Set up LLM, Embedder and Neo4j Driver

Initialize the core components needed for the Neo4j GraphRAG pipeline: an OpenAI LLM for entity extraction, an embeddings model for vectorizing text chunks, and the Neo4j database driver for graph storage.

from neo4j_graphrag.llm import OpenAILLM
from neo4j_graphrag.embeddings import OpenAIEmbeddings

# create an OpenAI client for use by Neo4j GraphRAG
llm_for_neo4j = OpenAILLM(model_name="gpt-4o", model_params={"temperature": 0})

# use OpenAI for creating embeddings
embedder = OpenAIEmbeddings(model="text-embedding-3-large")

# use the same driver set up by neo4j_for_adk.py
neo4j_driver = graphdb.get_driver()

### 8.3.5 Entity Schema

Use the approved entity types from the previous workflow as the allowed node types for entity extraction. This constrains the LLM to only extract entities of these specific types.

# approved entities list can be used directly 
schema_node_types = approved_entities

print("schema_node_types: ", schema_node_types)

Transform the approved fact types into relationship types by extracting the predicate labels and converting them to uppercase format for the schema.

# the keys from approved fact types dictionary can be used for relationship types
schema_relationship_types = [key.upper() for key in approved_fact_types.keys()]

print("schema_relationship_types: ", schema_relationship_types)

Create relationship patterns by converting fact types into tuples that specify allowed relationships between specific node types (subject-predicate-object patterns).

# rewrite the fact types into a list of tuples
schema_patterns = [
    [ fact['subject_label'], fact['predicate_label'].upper(), fact['object_label'] ]
    for fact in approved_fact_types.values()
]

print("schema_patterns:", schema_patterns)

Assemble the complete entity schema dictionary that will guide the LLM's entity extraction, combining node types, relationship types, and patterns into a single configuration.

# the complete entity schema
entity_schema = {
    "node_types": schema_node_types,
    "relationship_types": schema_relationship_types,
    "patterns": schema_patterns,
    "additional_node_types": False, # True would be less strict, allowing unknown node types
}

### 8.3.6 Contexualized Entity Extraction Prompt

This helper function extracts the first few lines from a file to provide context for entity extraction. This context helps the LLM better understand the document structure and content when processing individual chunks.

def file_context(file_path:str, num_lines=5) -> str:
    """Helper function to extract the first few lines of a file

    Args:
        file_path (str): Path to the file
        num_lines (int, optional): Number of lines to extract. Defaults to 5.

    Returns:
        str: First few lines of the file
    """
    with open(file_path, 'r') as f:
        lines = []
        for _ in range(num_lines):
            line = f.readline()
            if not line:
                break
            lines.append(line)
    return "\n".join(lines)

This function creates a contextualized prompt template for entity and relationship extraction. It combines general extraction instructions with file-specific context to improve the accuracy of the LLM's entity recognition on each text chunk.

# per-chunk entity extraction prompt, with context
def contextualize_er_extraction_prompt(context:str) -> str:
    """Creates a prompt with pre-amble file content for context during entity+relationship extraction.
    The context is concatenated into the string, which later will be used as a template
    for values like {schema} and {text}.
    """
    general_instructions = """
    You are a top-tier algorithm designed for extracting
    information in structured formats to build a knowledge graph.

    Extract the entities (nodes) and specify their type from the following text.
    Also extract the relationships between these nodes.

    Return result as JSON using the following format:
    {{"nodes": [ {{"id": "0", "label": "Person", "properties": {{"name": "John"}} }}],
    "relationships": [{{"type": "KNOWS", "start_node_id": "0", "end_node_id": "1", "properties": {{"since": "2024-08-01"}} }}] }}

    Use only the following node and relationship types (if provided):
    {schema}

    Assign a unique ID (string) to each node, and reuse it to define relationships.
    Do respect the source and target node types for relationship and
    the relationship direction.

    Make sure you adhere to the following rules to produce valid JSON objects:
    - Do not return any additional information other than the JSON in it.
    - Omit any backticks around the JSON - simply output the JSON on its own.
    - The JSON object must not wrapped into a list - it is its own JSON object.
    - Property names must be enclosed in double quotes
    """

    context_goes_here = f"""
    Consider the following context to help identify entities and relationships:
    <context>
    {context}  
    </context>"""
    
    input_goes_here = """
    Input text:

    {text}
    """

    return general_instructions + "\n" + context_goes_here + "\n" + input_goes_here

## 8.4 Make and Use the Knowledge Graph (KG) builder

### 8.4.1 Make the Neo4j KG Builder Pipeline

This function creates a customized KG builder pipeline for a specific file by extracting file context and creating a contextualized extraction prompt. It combines all the previously defined components (loader, splitter, schema, LLM) into a complete pipeline.

Process each approved markdown file by creating a KG builder pipeline and running it asynchronously. This extracts entities and relationships from the text chunks and stores them in the Neo4j database as the subject graph.

def make_kg_builder(file_path:str) -> SimpleKGPipeline:
    """Builds a KG builder for a given file, which is used to contextualize the chunking and entity extraction."""
    context = file_context(file_path)
    contextualized_prompt = contextualize_er_extraction_prompt(context)

    return SimpleKGPipeline(
        llm=llm_for_neo4j, # the LLM to use for Entity and Relation extraction
        driver=neo4j_driver,  # a neo4j driver to write results to graph
        embedder=embedder,  # an Embedder for chunks
        from_pdf=True,   # sortof True because you will use a custom loader
        pdf_loader=MarkdownDataLoader(), # the custom loader for Markdown
        text_splitter=RegexTextSplitter("---"), # the splitter you defined above
        schema=entity_schema, # that you just defined above
        prompt_template=contextualized_prompt,
    )

<p style="background-color:#f7fff8; padding:15px; border-width:3px; border-color:#e0f0e0; border-style:solid; border-radius:6px"> 🚨
&nbsp; <b>Different Run Results:</b> The output generated by LLMs can vary with each execution due to their stochastic nature. Your results might differ from those shown in the video.</p>

from helper import get_neo4j_import_dir

neo4j_import_dir = get_neo4j_import_dir() or "."

for file_name in approved_files:
    file_path = os.path.join(neo4j_import_dir, file_name)
    print(f"Processing file: {file_name}")
    kg_builder = make_kg_builder(file_path)
    results = await kg_builder.run_async(file_path=str(file_path))
    print("\tResults:", results.result)
print("All files processed.")

## 8.5 Tool Definition for Entity Resolution


Connect entities in the subject graph to entities in the domain graph.

For each type of entity in the subject graph, you will devise a strategy for correlating
with the right node in the domain graph. 

For example, you should expect that Products with product names exist in the subject graph,
and that these should correlate with products in the domain graph.

To do this, you will:
1. find the unique entity labels in the subject graph
2. find the unique node labels in the domain graph
3. attempt to correlate property keys
4. perform entity resolution by analyzing the similarity of property values

### 8.5.1 Unique Entity Labels in the Subject Graph

The unique triples of (subject, predicate, object) will give you an idea about what the subject graph looks like.

Query the Neo4j database to find all nodes that have the `__Entity__` label (entities created by the knowledge graph builder) and return their distinct label combinations.

# first, take a look at the entity labels
results = graphdb.send_query("""MATCH (n)
    WHERE n:`__Entity__`
    RETURN DISTINCT labels(n) AS entity_labels
    """)

results['query_result']

Flatten the label arrays into individual label strings using UNWIND, which transforms the array of labels into separate rows for each label.

# unwind those lists of labels
results = graphdb.send_query("""MATCH (n)
    WHERE n:`__Entity__`
    WITH DISTINCT labels(n) AS entity_labels
    UNWIND entity_labels AS entity_label
    RETURN DISTINCT entity_label
    """)

results['query_result']

Filter out internal Neo4j labels that start with double underscores ("__") to focus only on the meaningful entity type labels extracted from the text.

# filter out labels that start with "__"
results = graphdb.send_query("""MATCH (n)
    WHERE n:`__Entity__`
    WITH DISTINCT labels(n) AS entity_labels
    UNWIND entity_labels AS entity_label
    WITH entity_label
    WHERE NOT entity_label STARTS WITH "__"
    RETURN entity_label
    """)

results['query_result']

Combine the previous query steps into a reusable function that returns all unique entity labels from the subject graph, excluding internal Neo4j system labels.

# wrap the query into a callable function
def find_unique_entity_labels():
    result = graphdb.send_query("""MATCH (n)
        WHERE n:`__Entity__`
        WITH DISTINCT labels(n) AS entity_labels
        UNWIND entity_labels AS entity_label
        WITH entity_label
        WHERE NOT entity_label STARTS WITH "__"
        RETURN collect(entity_label) as unique_entity_labels
        """)
    if result['status'] == 'error':
        raise Exception(result['message'])
    return result['query_result'][0]['unique_entity_labels']

Test the function to see what entity labels were actually extracted from the processed markdown files into the subject graph.

# try out the function
unique_entity_labels = find_unique_entity_labels()

print("Unique entity labels: ", unique_entity_labels)

### 8.5.2 Unique Entity Keys for a Given Label

Create a function to find all unique property keys for entities of a specific label in the subject graph. This helps identify what properties are available for matching with domain graph nodes.

def find_unique_entity_keys(entityLabel:str):
    result = graphdb.send_query("""MATCH (n:$($entityLabel))
    WHERE n:`__Entity__`
    WITH DISTINCT keys(n) as entityKeys
    UNWIND entityKeys as entityKey
    RETURN collect(distinct(entityKey)) as unique_entity_keys
    """, {
        "entityLabel": entityLabel
    })
    if result['status'] == 'error':
        raise Exception(result['message'])
    return result['query_result'][0]['unique_entity_keys']
        
# try out the function to get the unique keys for 
# subject nodes labeled as Product
find_unique_entity_keys("Product")

### 8.5.3 Unique Domain keys for a Given Label

Create a function to find unique property keys for nodes of a specific label in the domain graph (nodes without the `__Entity__` label). This enables comparison with subject graph properties for entity resolution.

def find_unique_domain_keys(domainLabel:str):
    result = graphdb.send_query("""MATCH (n:$($domainLabel))
    WHERE NOT n:`__Entity__` // exclude entities created by the KG builder, these should be domain nodes
    WITH DISTINCT keys(n) as domainKeys
    UNWIND domainKeys as domainKey
    RETURN collect(distinct(domainKey)) as unique_domain_keys
    """, {
        "domainLabel": domainLabel
    })
    if result['status'] == 'error':
        raise Exception(result['message'])
    return result['query_result'][0]['unique_domain_keys']
        

find_unique_domain_keys("Product")

### 8.5.4 Normalize keys

This is a simple version of "stemming" as done in NLP.

Define a function to normalize property key names by removing label prefixes, converting to lowercase, and standardizing spacing. This helps match similar property keys that may have different naming conventions between subject and domain graphs.

def normalize_key(label:str, key:str) -> str:
    """Normalizes a a property key for a given label.

    Keys are normalized by:
    - lowercase the key
    - remove any leading/trailing whitespace
    - remove label prefix from key
    - replace internal whitespace with "_"

    for example: 
        - "Product_name" -> "name"
        - "product name" -> "name"
        - "price" -> "price

    Args:
        label (str): The label to normalize keys for
        keys (List[str]): The list of keys to normalize

    Returns:
        List[str]: The normalized list of keys
    """
    lowercase_key = key.lower()
    unprefixed_key = re.sub(f"^{label.lower()}[_ ]*", "", lowercase_key)
    normalized_key = re.sub(" ", "_", unprefixed_key)
    return normalized_key

print(normalize_key("Product", "Product_name"))
print(normalize_key("Product", "Product Name"))
print(normalize_key("Product", "product name"))
print(normalize_key("Product", "price"))

### 8.5.5 Correlate Keys for a Given Label

Use fuzzy string matching to find correlations between entity graph property keys and domain graph property keys. This function compares normalized key names and returns matches above a similarity threshold, helping identify which properties can be used for entity resolution.

# use the rapidfuzz library for fuzzy text similarity scoring
from rapidfuzz import fuzz

# for a given label, get pairs of entity and domain keys that correlate
def correlate_entity_and_domain_keys(label: str, entity_keys: list[str], domain_keys: list[str], similarity: float = 0.9) -> list[tuple[str, str]]:
    correlated_keys = []
    for entity_key in entity_keys:
        for domain_key in domain_keys:
            # only consider exact matches. this could use fuzzy matching
            normalized_entity_key = normalize_key(label, entity_key)
            normalized_domain_key = normalize_key(label, domain_key)
            # rapidfuzz similarity is 0.0 -> 100.0, so divide by 100 for 0.0 -> 1.0
            fuzzy_similarity = (fuzz.ratio(normalized_entity_key, normalized_domain_key) / 100)
            if (fuzzy_similarity > similarity): 
                correlated_keys.append((entity_key, domain_key, fuzzy_similarity))
    correlated_keys.sort(key=lambda x: x[2], reverse=True)
    return correlated_keys

label = "Product"
entity_keys = find_unique_entity_keys(label)
domain_keys = find_unique_domain_keys(label)

# try correlating with a low-ish threshold
correlated_keys = correlate_entity_and_domain_keys(label, entity_keys, domain_keys, similarity=0.5)

print(f"{label} correlated keys (entity key, domain key, similarity score)...")

# show the keys
correlated_keys

### 8.5.6 Value Similarity using Jaro–Winkler Distance

The Jaro–Winkler distance is a string comparison method, emphasizing common prefixes to favor strings that match from the start. 

- measures "edit distance" between two strings
- produces values from 0.0 (exact match) to 1.0 (no similarity)
- use `similarity = 1.0 - distance` to get a similarity score

See [Jaro-WinklerDistance](https://en.wikipedia.org/wiki/Jaro–Winkler_distance) for details.

Ideally, you would sample a few values that you expect to correlate well, trying different similarity metrics
to find one that works well for that particular value pair. 

Neo4j provides many [text similarity functions](https://neo4j-contrib.github.io/neo4j-apoc-procedures/3.4/utilities/text-functions/). 
Other options include:
- [`apoc.text.hammingDistance`]()
- [`apoc.text.levenshteinSimilarity`]()
- [`apoc.text.sorensenDiceSimilarity`]()
- [`apoc.text.fuzzyMatch`]()

And for vector similarity:
- `vector.similarity.cosine` to directly calculate cosine similarity
- `db.index.vector.queryNodes` to perform vector similarity search (after first creating a vector index on the domain nodes)

Wrap the entity resolution logic into a reusable function that correlates subject and domain nodes based on property value similarity using Jaro-Winkler distance. This creates the bridge between extracted entities and the existing domain graph.

# use the Jaro-Winkler function to calculate distance between product names
results = graphdb.send_query("""
// MATCH all pairs of subject and domain nodes -- this is an expensive cartesian product
MATCH (entity:$($entityLabel):`__Entity__`), (domain:$($entityLabel))
WITH entity, domain, apoc.text.jaroWinklerDistance(entity[$entityKey], domain[$domainKey]) as score
// experiment with different thresholds to see how the results change
WHERE score < 0.4
RETURN entity[$entityKey] AS entityValue, domain[$domainKey] AS domainValue, score
// experiment with different limits to see more or fewer pairs
LIMIT 3
""", {
    "entityLabel": "Product",
    "entityKey": "name",
    "domainKey": "product_name"
})

results['query_result']

Create `CORRESPONDS_TO` relationships between subject graph entities and domain graph nodes with similar property values. Uses MERGE to avoid duplicate relationships and adds timestamps to track when correlations were established.

# connect all corresponding nodes with a relationship
results = graphdb.send_query("""
MATCH (entity:$($entityLabel):`__Entity__`),(domain:$($entityLabel))
// use the score as a predicate to filter the pairs. this is better
WHERE apoc.text.jaroWinklerDistance(entity[$entityKey], domain[$domainKey]) < 0.1
MERGE (entity)-[r:CORRESPONDS_TO]->(domain)
ON CREATE SET r.created_at = datetime()
ON MATCH SET r.updated_at = datetime()
RETURN elementId(entity) as entity_id, r, elementId(domain) as domain_id
""", {
    "entityLabel": "Product",
    "entityKey": "name",
    "domainKey": "product_name"
})

results['query_result']

# run this repeatedly to illustrate that MERGE only happens once

Test the Jaro-Winkler distance function by finding all pairs of subject and domain Product nodes where the name properties have similarity scores below a threshold, showing potential matches for entity resolution.

# wrap as a function
def correlate_subject_and_domain_nodes(label: str, entity_key: str, domain_key: str, similarity: float = 0.9) -> dict:
    """Correlate entity and domain nodes based on label, entity key, and domain key,
    where the corresponding values of the entity and domain properties are similar
    
    For example, if you have a label "Person" and an entity key "name", and a domain key "person_name",
    this function will create a relationship like:
    (:Person:`__Entity__` {name: "John"})-[:CORRELATES_TO]->(:Person {person_name: "John"}) 
    

    Args:
        label (str): The label of the entity and domain nodes.
        entity_key (str): The key of the entity node.
        domain_key (str): The key of the domain node.
        similarity (float, optional): The similarity threshold for correlation. Defaults to 0.9.
    
    Returns:
        dict: A dictionary containing the correlation between the entity and domain nodes.
    """
    results = graphdb.send_query("""
    MATCH (entity:$($entityLabel):`__Entity__`),(domain:$($entityLabel))
    WHERE apoc.text.jaroWinklerDistance(entity[$entityKey], domain[$domainKey]) < $distance
    MERGE (entity)-[r:CORRESPONDS_TO]->(domain)
    ON CREATE SET r.created_at = datetime() // MERGE sub-clause when the relationship is newly created
    ON MATCH SET r.updated_at = datetime()  // MERGE sub-clause when the relationship already exists
    RETURN $entityLabel as entityLabel, count(r) as relationshipCount
    """, {
        "entityLabel": label,
        "entityKey": entity_key,
        "domainKey": domain_key,
        "distance": (1.0 - similarity)
    })

    if results['status'] == 'error':
        raise Exception(results['message'])

    return results['query_result']


correlate_subject_and_domain_nodes("Product", "name", "product_name")

### 8.5.7 Correlate and connect the subject nodes to the domain nodes

Execute the complete entity resolution workflow by iterating through all extracted entity labels, finding the best property key correlations, and automatically creating connections between the subject graph and domain graph to complete the knowledge graph integration.

# do it all:
# - loop over all entity labels
# - correlate the keys
# - correlate (and connect) the nodes
for entity_label in find_unique_entity_labels():
    print(f"Correlating entities labeled {entity_label}...")
    
    entity_keys = find_unique_entity_keys(entity_label)
    domain_keys = find_unique_domain_keys(entity_label)

    correlated_keys = correlate_entity_and_domain_keys(entity_label, entity_keys, domain_keys, similarity=0.8)

    if (len(correlated_keys) > 0):
        top_correlated_keypair = correlated_keys[0]
        print("\tbased on:", top_correlated_keypair)
        correlate_subject_and_domain_nodes(entity_label, top_correlated_keypair[0], top_correlated_keypair[1])
    else:
        print("\tNo correlation found")