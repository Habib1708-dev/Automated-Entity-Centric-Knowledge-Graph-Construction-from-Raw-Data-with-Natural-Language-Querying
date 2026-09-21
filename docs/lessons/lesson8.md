# Lesson 7 - Schema Proposal for Unstructured Data

In this lesson, you will design agents that propose how to extract information from unstructured data.

You'll learn:
- how "named entity recognition" can be used to identify the people, places and things
- how facts can be extracted as a "triple" of subject, predicate and object

<div style="background-color:#fff6ff; padding:13px; border-width:3px; border-color:#efe6ef; border-style:solid; border-radius:6px">
<p> 💻 &nbsp; <b>To access the helper.py, neo4j_for_adk.py and tools.py files :</b> 1) click on the <em>"File"</em> option on the top menu of the notebook and then 2) click on <em>"Open"</em>.

</div>

## 7.1. Agents

<img src="images/entire_solution.png" width="500">

Two agents to propose data extraction from unstructured data: a "named entity recognition" agent and a "fact extraction" agent:

- Input: `approved_user_goal`, `approved_files`, `approved_construction_plan`
- Output: 
    - `approved_entity_types` describing the type of entities that could be extracted from the unstructured data
    - `approved_fact_types` describing how those entities can be related in a fact triple
- Tools: `get_approved_user_goal`, `get_approved_files`, `get_well_known_types`, `sample_file`, `set_proposed_entities`, `get_proposed_entities`, `approve_proposed_entities`, `add_proposed_fact`, `get_proposed_facts`, `approve_proposed_facts`

**Workflow**

<img src="images/workflow.png" width="500">

Named entity recognition:

1. The context is initialized with an `approved_user_goal`, `approved_files` and an `approved_construction_plan`
2. The agent analyzes unstructured data files, looking for relevant entity types 
3. The agent proposes a list of entity types, seeking user approval

Fact extraction:

1. The context now includes `approved_entity_types`
2. The agent analyzes unstructured data files, looking for how those entities can be saved as fact triples
3. The agent proposes fact types, seeking user approval

## 7.2. Setup

The usual import of needed libraries, loading of environment variables, and connection to Neo4j.

# Import necessary libraries

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm # For OpenAI support
from google.adk.tools import ToolContext

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

## 7.3. Named Entity Recognition (NER) Sub-agent

The NER agent is responsible for proposing entities that could be extracted from the unstructured data files.

An entity is a person, place or thing that is relevant to the user's goal.

There are two general kinds of entities:

1. Well-known entities: these closely correlate with nodes in the existing structured data
   - in our example, this would be things like Products, Parts and Suppliers
2. Discovered entities: these are entities that are not pre-defined, but are mentioned in the markdown text
    - in the product reviews, this may may Reviewers, product complaints, or product features

The general goal of the NER agent is to analyze the markdown files and propose entities that are 
relevant to the user goal of root-cause analysis.


### 7.3.1. Agent Instructions (NER)


ner_agent_role_and_goal = """
  You are a top-tier algorithm designed for analyzing text files and proposing
  the kind of named entities that could be extracted which would be relevant 
  for a user's goal.
  """

ner_agent_hints = """
  Entities are people, places, things and qualities, but not quantities. 
  Your goal is to propose a list of the type of entities, not the actual instances
  of entities.

  There are two general approaches to identifying types of entities:
  - well-known entities: these closely correlate with approved node labels in an existing graph schema
  - discovered entities: these may not exist in the graph schema, but appear consistently in the source text

  Design rules for well-known entities:
  - always use existing well-known entity types. For example, if there is a well-known type "Person", and people appear in the text, then propose "Person" as the type of entity.
  - prefer reusing existing entity types rather than creating new ones
  
  Design rules for discovered entities:
  - discovered entities are consistently mentioned in the text and are highly relevant to the user's goal
  - always look for entities that would provide more depth or breadth to the existing graph
  - for example, if the user goal is to represent social communities and the graph has "Person" nodes, look through the text to discover entities that are relevant like "Hobby" or "Event"
  - avoid quantitative types that may be better represented as a property on an existing entity or relationship.
  - for example, do not propose "Age" as a type of entity. That is better represented as an additional property "age" on a "Person".
"""

ner_agent_chain_of_thought_directions = """
  Prepare for the task:
  - use the 'get_user_goal' tool to get the user goal
  - use the 'get_approved_files' tool to get the list of approved files
  - use the 'get_well_known_types' tool to get the approved node labels

  Think step by step:
  1. Sample some of the files using the 'sample_file' tool to understand the content
  2. Consider what well-known entities are mentioned in the text
  3. Discover entities that are frequently mentioned in the text that support the user's goal
  4. Use the 'set_proposed_entities' tool to save the list of well-known and discovered entity types
  5. Use the 'get_proposed_entities' tool to retrieve the proposed entities and present them to the user for their approval
  6. If the user approves, use the 'approve_proposed_entities' tool to finalize the entity types
  7. If the user does not approve, consider their feedback and iterate on the proposal
"""

ner_agent_instruction = f"""
{ner_agent_role_and_goal}
{ner_agent_hints}
{ner_agent_chain_of_thought_directions}
"""

#print(ner_agent_instruction)

### 7.3.2. Tool Definitions (NER)

As in previous lessons, you'll define some tools that explictly follow
a propose then approve pattern. 

# tools to propose and approve entity types
PROPOSED_ENTITIES = "proposed_entity_types"
APPROVED_ENTITIES = "approved_entity_types"

def set_proposed_entities(proposed_entity_types: list[str], tool_context:ToolContext) -> dict:
    """Sets the list proposed entity types to extract from unstructured text."""
    tool_context.state[PROPOSED_ENTITIES] = proposed_entity_types
    return tool_success(PROPOSED_ENTITIES, proposed_entity_types)

def get_proposed_entities(tool_context:ToolContext) -> dict:
    """Gets the list of proposed entity types to extract from unstructured text."""
    return tool_context.state.get(PROPOSED_ENTITIES, [])

def approve_proposed_entities(tool_context:ToolContext) -> dict:
    """Upon approval from user, records the proposed entity types as an approved list of entity types 

    Only call this tool if the user has explicitly approved the suggested files.
    """
    if PROPOSED_ENTITIES not in tool_context.state:
        return tool_error("No proposed entity types to approve. Please set proposed entities first, ask for user approval, then call this tool.")
    tool_context.state[APPROVED_ENTITIES] = tool_context.state.get(PROPOSED_ENTITIES)
    return tool_success(APPROVED_ENTITIES, tool_context.state[APPROVED_ENTITIES])

def get_approved_entities(tool_context:ToolContext) -> dict:
    """Get the approved list of entity types to extract from unstructured text."""
    return tool_context.state.get(APPROVED_ENTITIES, [])


The "well-known entities" are based on existing node labels used during graph construction.

This helper tool will get the existing node labels from the approved construction plan.

def get_well_known_types(tool_context:ToolContext) -> dict:
    """Gets the approved labels that represent well-known entity types in the graph schema."""
    construction_plan = tool_context.state.get("approved_construction_plan", {})
    # approved labels are the keys for each construction plan entry where `construction_type` is "node"
    approved_labels = {entry["label"] for entry in construction_plan.values() if entry["construction_type"] == "node"}
    return tool_success("approved_labels", approved_labels)



The full toolset includes some existing tools that you'll import
plus the extra tools you just defined.

from tools import get_approved_user_goal, get_approved_files, sample_file
ner_agent_tools = [
    get_approved_user_goal, get_approved_files, sample_file,
    get_well_known_types,
    set_proposed_entities,
    approve_proposed_entities
]

To see what the NER agent is working with, use the sample_file tool to look
at one of the markdown files.

file_result = sample_file("product_reviews/gothenburg_table_reviews.md")

print(file_result["content"])

The markdown has product reviews from multiple users that include a rating, the review and their username.

For root-cause analysis, you'll be interested in reviews that are negative and report product issues
like quality, challenges in assembly, or reliability.

We won't provide explicit instructions about product reviews to the agent, instead relying
on a combination of the stated user goal along with instruction to find entity types
that would support that user goal.

### 7.3.3. Construct the Sub-agent (NER)

NER_AGENT_NAME = "ner_schema_agent_v1"
ner_schema_agent = Agent(
    name=NER_AGENT_NAME,
    description="Proposes the kind of named entities that could be extracted from text files.",
    model=llm,
    instruction=ner_agent_instruction,
    tools=ner_agent_tools, 
)


The initial state is important in this phase, as the agent is designed to act
within a particular phase of an overall workflow.

The NER agent will need:

- the user goal, extended to mention product reviews and what to look for there
- a list of markdown files that have been pre-approved
- the approved construction plan from the structured data design phase

ner_agent_initial_state = {
    "approved_user_goal": {
        "kind_of_graph": "supply chain analysis",
        "description": """A multi-level bill of materials for manufactured products, useful for root cause analysis. 
        Add product reviews to start analysis from reported issues like quality, difficulty, or durability."""
    },
    "approved_files": [
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
    ],
    "approved_construction_plan": {
        "Product": {
            "construction_type": "node",
            "label": "Product",
        },
        "Assembly": {
            "construction_type": "node",
            "label": "Assembly",
        },
        "Part": {
            "construction_type": "node",
            "label": "Part",
        },
        "Supplier": {
            "construction_type": "node",
            "label": "Supplier",
        }
        # Relationship construction omitted, since it won't get used in this notebook
    }
}

OK, you're ready to run the agent. 

- use the make_agent_caller to create an execution environment
- prompt the agent with a single message that should kick-off the analysis
- expect the result to be a proposed list of entity types
- but *not* a list of approved entity types

**The entity types here may vary quite a bit. If you're not happy with the proposal,
you can run the cell again to get a new list.**

<p style="background-color:#f7fff8; padding:15px; border-width:3px; border-color:#e0f0e0; border-style:solid; border-radius:6px"> 🚨
&nbsp; <b>Different Run Results:</b> The output generated by LLMs can vary with each execution due to their stochastic nature. Your results might differ from those shown in the video.</p>

from helper import make_agent_caller

ner_agent_caller = await make_agent_caller(ner_schema_agent, ner_agent_initial_state)

await ner_agent_caller.call("Add product reviews to the knowledge graph to trace product complaints back through the manufacturing process.")

# Alternatively, uncomment this line to get verbose output
# await ner_agent_caller.call("Add product reviews.", True)

session_end = await ner_agent_caller.get_session()

print("\n---\n")

print("\nSession state: ", session_end.state)

if PROPOSED_ENTITIES in session_end.state:
    print("\nProposed entities: ", session_end.state[PROPOSED_ENTITIES])

if APPROVED_ENTITIES in session_end.state:
    print("\nInappropriately approved entities: ", session_end.state[APPROVED_ENTITIES])
else:
    print("\nAwaiting approval.")

**Note**

- often, the agent will confuse the process of "Assembly" with the resulting thing that is an "Assembly"
- why is that?
- the agent will see the term "Assembly" in the list of well-known entity types
- and, it will notice complaints about assembling furniture
- but, it has no way to know those are two different uses of the word
- to fix this, the schema proposal from the previous lesson could save descriptions for each node label

Once you're happy with the proposal, you can tell the agent that you approve.

await ner_agent_caller.call("Approve the proposed entities.")

session_end = await ner_agent_caller.get_session()

ner_end_state = session_end.state if session_end else {}

print("Session state: ", ner_end_state)

if APPROVED_ENTITIES in ner_end_state:
    print("\nApproved entities: ", ner_end_state[APPROVED_ENTITIES])
else:
    print("\nStill awaiting approval? That is weird. Please check the agent's state and the proposed entities.")

## 7.4. Fact Type Extraction Sub-agent


### 7.4.1. Agent Instructions (fact type extraction)

fact_agent_role_and_goal = """
  You are a top-tier algorithm designed for analyzing text files and proposing
  the type of facts that could be extracted from text that would be relevant 
  for a user's goal. 
"""

fact_agent_hints = """
  Do not propose specific individual facts, but instead propose the general type 
  of facts that would be relevant for the user's goal. 
  For example, do not propose "ABK likes coffee" but the general type of fact "Person likes Beverage".
  
  Facts are triplets of (subject, predicate, object) where the subject and object are
  approved entity types, and the proposed predicate provides information about
  how they are related. For example, a fact type could be (Person, likes, Beverage).

  Design rules for facts:
  - only use approved entity types as subjects or objects. Do not propose new types of entities
  - the proposed predicate should describe the relationship between the approved subject and object
  - the predicate should optimize for information that is relevant to the user's goal
  - the predicate must appear in the source text. Do not guess.
  - use the 'add_proposed_fact' tool to record each proposed fact type
"""

fact_agent_chain_of_thought_directions = """
    Prepare for the task:
    - use the 'get_approved_user_goal' tool to get the user goal
    - use the 'get_approved_files' tool to get the list of approved files
    - use the 'get_approved_entities' tool to get the list of approved entity types

    Think step by step:
    1. Use the 'get_approved_user_goal' tool to get the user goal
    2. Sample some of the approved files using the 'sample_file' tool to understand the content
    3. Consider how subjects and objects are related in the text
    4. Call the 'add_proposed_fact' tool for each type of fact you propose
    5. Use the 'get_proposed_facts' tool to retrieve all the proposed facts
    6. Present the proposed types of facts to the user, along with an explanation
"""

fact_agent_instruction = f"""
{fact_agent_role_and_goal}
{fact_agent_hints}
{fact_agent_chain_of_thought_directions}
"""

### 7.4.2. Tool Definitions (fact type extraction)

PROPOSED_FACTS = "proposed_fact_types"
APPROVED_FACTS = "approved_fact_types"

def add_proposed_fact(approved_subject_label:str,
                      proposed_predicate_label:str,
                      approved_object_label:str,
                      tool_context:ToolContext) -> dict:
    """Add a proposed type of fact that could be extracted from the files.

    A proposed fact type is a tuple of (subject, predicate, object) where
    the subject and object are approved entity types and the predicate 
    is a proposed relationship label.

    Args:
      approved_subject_label: approved label of the subject entity type
      proposed_predicate_label: label of the predicate
      approved_object_label: approved label of the object entity type
    """
    # Guard against invalid labels
    approved_entities = tool_context.state.get(APPROVED_ENTITIES, [])
    
    if approved_subject_label not in approved_entities:
        return tool_error(f"Approved subject label {approved_subject_label} not found. Try again.")
    if approved_object_label not in approved_entities:
        return tool_error(f"Approved object label {approved_object_label} not found. Try again.")
    
    current_predicates = tool_context.state.get(PROPOSED_FACTS, {})
    current_predicates[proposed_predicate_label] = {
        "subject_label": approved_subject_label,
        "predicate_label": proposed_predicate_label,
        "object_label": approved_object_label
    }
    tool_context.state[PROPOSED_FACTS] = current_predicates
    return tool_success(PROPOSED_FACTS, current_predicates)
    
def get_proposed_facts(tool_context:ToolContext) -> dict:
    """Get the proposed types of facts that could be extracted from the files."""
    return tool_context.state.get(PROPOSED_FACTS, {})


def approve_proposed_facts(tool_context:ToolContext) -> dict:
    """Upon user approval, records the proposed fact types as approved fact types

    Only call this tool if the user has explicitly approved the proposed fact types.
    """
    if PROPOSED_FACTS not in tool_context.state:
        return tool_error("No proposed fact types to approve. Please set proposed facts first, ask for user approval, then call this tool.")
    tool_context.state[APPROVED_FACTS] = tool_context.state.get(PROPOSED_FACTS)
    return tool_success(APPROVED_FACTS, tool_context.state[APPROVED_FACTS])


fact_agent_tools = [
    get_approved_user_goal, get_approved_files, 
    get_approved_entities,
    sample_file,
    add_proposed_fact,
    get_proposed_facts,
    approve_proposed_facts
]

### 7.4.3. Construct the Sub-agent (fact type extraction)

FACT_AGENT_NAME = "fact_type_extraction_agent_v1"
relevant_fact_agent = Agent(
    name=FACT_AGENT_NAME,
    description="Proposes the kind of relevant facts that could be extracted from text files.",
    model=llm,
    instruction=fact_agent_instruction,
    tools=fact_agent_tools, 
)


<p style="background-color:#f7fff8; padding:15px; border-width:3px; border-color:#e0f0e0; border-style:solid; border-radius:6px"> 🚨
&nbsp; <b>Different Run Results:</b> The output generated by LLMs can vary with each execution due to their stochastic nature. Your results might differ from those shown in the video.</p>

# make a copy of the NER agent's end state to use as the initial state for the fact agent
fact_agent_initial_state = ner_end_state.copy()

fact_agent_caller = await make_agent_caller(relevant_fact_agent, fact_agent_initial_state)

await fact_agent_caller.call("Propose fact types that can be found in the text.")
# await fact_agent_caller.call("Propose fact types that can be found in the text.", True)

session_end = await fact_agent_caller.get_session()

print("\n---\n")

print("\nSession state: ", session_end.state)

print("\nApproved entities: ", session_end.state.get(APPROVED_ENTITIES, []))

# Check that the agent proposed facts
if PROPOSED_FACTS in session_end.state:
    print("\nCorrectly proposed facts: ", session_end.state[PROPOSED_FACTS])
else:
    print("\nProposed facts not found in session state. What went wrong?")

# Check that the agent did not inappropriately approve facts
if APPROVED_FACTS in session_end.state:
    print("\nInappriately approved facts: ", session_end.state[APPROVED_FACTS])
else:
    print("\nApproved facts not found in session state, which is good.")

If you're happy with the fact type proposal, approve it.

await fact_agent_caller.call("Approve the proposed fact types.")

session_end = await fact_agent_caller.get_session()

print("Session state: ", session_end.state)

if APPROVED_FACTS in session_end.state:
    print("\nApproved fact types: ", session_end.state[APPROVED_FACTS])
else:
    print("\nFailed to approve fact types.")