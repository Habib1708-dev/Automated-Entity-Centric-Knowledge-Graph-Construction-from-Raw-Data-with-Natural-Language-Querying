# Lesson 5 - File Suggestions

In this lesson, you will design an agent that can read the user goal, then use tools to evaluate
available files to suggest relevant data sources.

You'll learn:
- how memory helps coordinate agent tasks
- how tools can be be used to access the environment
- how to trust, but verify inside of tools

<div style="background-color:#fff6ff; padding:13px; border-width:3px; border-color:#efe6ef; border-style:solid; border-radius:6px">
<p> 💻 &nbsp; <b>To access the helper.py, neo4j_for_adk.py and tools.py files :</b> 1) click on the <em>"File"</em> option on the top menu of the notebook and then 2) click on <em>"Open"</em>.

</div>

## 5.1. Agent


<img src="images/entire_solution.png" width="500">

The File Suggestion agent is a tool-use agent that suggests files to use for import, based on the approved user goal from the previous lesson.


- Input: `approved_user_goal`, a dictionary pairing a kind of graph with a description of the purpose of the graph.
- Output: `approved_files`, a list of files that have been approved for import.
- Tools: `get_approved_user_goal`, `list_import_files`, `sample_file`, `set_suggested_files`, `approve_suggested_files`

## 5.2. Setup

The usual import of needed libraries, loading of environment variables, and connection to Neo4j.

# Import necessary libraries
import os
from pathlib import Path

from itertools import islice

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm # For OpenAI support
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.adk.tools import ToolContext
from google.genai import types # For creating message Content/Parts

# For type hints
from typing import Dict, Any, List

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

print("\nOpenAI is ready.")

## 5.3. Define the File Suggestion Agent

### 5.3.1. Agent Instructions

file_suggestion_agent_instruction = """
You are a constructive critic AI reviewing a list of files. Your goal is to suggest relevant files
for constructing a knowledge graph.

**Task:**
Review the file list for relevance to the kind of graph and description specified in the approved user goal. 

For any file that you're not sure about, use the 'sample_file' tool to get 
a better understanding of the file contents. 

Only consider structured data files like CSV or JSON.

Prepare for the task:
- use the 'get_approved_user_goal' tool to get the approved user goal

Think carefully, repeating these steps until finished:
1. list available files using the 'list_available_files' tool
2. evaluate the relevance of each file, then record the list of suggested files using the 'set_suggested_files' tool
3. use the 'get_suggested_files' tool to get the list of suggested files
4. ask the user to approve the set of suggested files
5. If the user has feedback, go back to step 1 with that feedback in mind
6. If approved, use the 'approve_suggested_files' tool to record the approval
"""

### 5.3.2 Tool Definitions

# import tools defined in previous lesson
from tools import get_approved_user_goal
from helper import get_neo4j_import_dir

**Note:** `neo4j` database has a directory called `import` where you can place `csv` files that you intend to import into your Neo4j database. `get_neo4j_import_dir` represents the path to the directory that is in-sync with the `import` directory of the Neo4j database (running in a sidecar container). This directory has the csv files that the agent will sample and the select the suggested files. 

<div style="background-color:#fff6ff; padding:13px; border-width:3px; border-color:#efe6ef; border-style:solid; border-radius:6px">
<p> 💻 &nbsp; <b>To access the <code>csv</code> files :</b> 1) click on the <em>"File"</em> option on the top menu of the notebook and then 2) click on <em>"Open"</em>. Click on the home directory (small folder icon), you will find the csv files inside the <code>data</code> folder.

</div>

# Tool: List Import Files

# this constant will be used as the key for storing the file list in the tool context state
ALL_AVAILABLE_FILES = "all_available_files"

def list_available_files(tool_context:ToolContext) -> dict:
    f"""Lists files available for knowledge graph construction.
    All files are relative to the import directory.

    Returns:
        dict: A dictionary containing metadata about the content.
                Includes a 'status' key ('success' or 'error').
                If 'success', includes a {ALL_AVAILABLE_FILES} key with list of file names.
                If 'error', includes an 'error_message' key.
                The 'error_message' may have instructions about how to handle the error.
    """
    # get the import dir using the helper function
    import_dir = Path(get_neo4j_import_dir())

    # get a list of relative file names, so files must be rooted at the import dir
    file_names = [str(x.relative_to(import_dir)) 
                 for x in import_dir.rglob("*") 
                 if x.is_file()]

    # save the list to state so we can inspect it later
    tool_context.state[ALL_AVAILABLE_FILES] = file_names

    return tool_success(ALL_AVAILABLE_FILES, file_names)

# Tool: Sample File
# This is a simple file reading tool that only works on files from the import directory
def sample_file(file_path: str, tool_context: ToolContext) -> dict:
    """Samples a file by reading its content as text.
    
    Treats any file as text and reads up to a maximum of 100 lines.
    
    Args:
      file_path: file to sample, relative to the import directory
      
    Returns:
        dict: A dictionary containing metadata about the content,
            along with a sampling of the file.
            Includes a 'status' key ('success' or 'error').
            If 'success', includes a 'content' key with textual file content.
            If 'error', includes an 'error_message' key.
            The 'error_message' may have instructions about how to handle the error.
    """
    # Trust, but verify. The agent may invent absolute file paths. 
    if Path(file_path).is_absolute():
        return tool_error("File path must be relative to the import directory. Make sure the file is from the list of available files.")
    
    import_dir = Path(get_neo4j_import_dir())

    # create the full path by extending from the import_dir
    full_path_to_file = import_dir / file_path
    
    # of course, _that_ may not exist
    if not full_path_to_file.exists():
        return tool_error(f"File does not exist in import directory. Make sure {file_path} is from the list of available files.")
    
    try:
        # Treat all files as text
        with open(full_path_to_file, 'r', encoding='utf-8') as file:
            # Read up to 100 lines
            lines = list(islice(file, 100))
            content = ''.join(lines)
            return tool_success("content", content)
    
    except Exception as e:
        return tool_error(f"Error reading or processing file {file_path}: {e}")

# Tool: Set/Get suggested files
SUGGESTED_FILES = "suggested_files"

def set_suggested_files(suggest_files:List[str], tool_context:ToolContext) -> Dict[str, Any]:
    """Set the suggested files to be used for data import.

    Args:
        suggest_files (List[str]): List of file paths to suggest

    Returns:
        Dict[str, Any]: A dictionary containing metadata about the content.
                Includes a 'status' key ('success' or 'error').
                If 'success', includes a {SUGGESTED_FILES} key with list of file names.
                If 'error', includes an 'error_message' key.
                The 'error_message' may have instructions about how to handle the error.
    """
    tool_context.state[SUGGESTED_FILES] = suggest_files
    return tool_success(SUGGESTED_FILES, suggest_files)

# Helps encourage the LLM to first set the suggested files.
# This is an important strategy for maintaining consistency through defined values.
def get_suggested_files(tool_context:ToolContext) -> Dict[str, Any]:
    """Get the files to be used for data import.

    Returns:
        Dict[str, Any]: A dictionary containing metadata about the content.
                Includes a 'status' key ('success' or 'error').
                If 'success', includes a {SUGGESTED_FILES} key with list of file names.
                If 'error', includes an 'error_message' key.
    """
    return tool_success(SUGGESTED_FILES, tool_context.state[SUGGESTED_FILES])

# Tool: Approve Suggested Files
# Just like the previous lesson, you'll define a tool which
# accepts no arguments and can sanity check before approving.
APPROVED_FILES = "approved_files"

def approve_suggested_files(tool_context:ToolContext) -> Dict[str, Any]:
    """Approves the {SUGGESTED_FILES} in state for further processing as {APPROVED_FILES}.
    
    If {SUGGESTED_FILES} is not in state, return an error.
    """
    if SUGGESTED_FILES not in tool_context.state:
        return tool_error("Current files have not been set. Take no action other than to inform user.")

    tool_context.state[APPROVED_FILES] = tool_context.state[SUGGESTED_FILES]
    return tool_success(APPROVED_FILES, tool_context.state[APPROVED_FILES])

# List of tools for the file suggestion agent
file_suggestion_agent_tools = [get_approved_user_goal, list_available_files, sample_file, 
    set_suggested_files, get_suggested_files,
    approve_suggested_files
]

### 5.3.3. Agent Definition

# Finally, construct the agent

file_suggestion_agent = Agent(
    name="file_suggestion_agent_v1",
    model=llm, # defined earlier in a variable
    description="Helps the user select files to import.",
    instruction=file_suggestion_agent_instruction,
    tools=file_suggestion_agent_tools,
)

print(f"Agent '{file_suggestion_agent.name}' created.")

## 5.4. Interact with the Agent

<p style="background-color:#f7fff8; padding:15px; border-width:3px; border-color:#e0f0e0; border-style:solid; border-radius:6px"> 🚨
&nbsp; <b>Different Run Results:</b> The output generated by LLMs can vary with each execution due to their stochastic nature. Your results might differ from those shown in the video.</p>

from helper import make_agent_caller

file_suggestion_caller = await make_agent_caller(file_suggestion_agent, {
    "approved_user_goal": {
        "kind_of_graph": "supply chain analysis",
        "description": "A multi-level bill of materials for manufactured products, useful for root cause analysis.."
    }   
})


# Run the Initial Conversation

# nudge the agent to look for files. in the full system, this will be the natural next step
await file_suggestion_caller.call("What files can we use for import?")

session_end = await file_suggestion_caller.get_session()

print("\n---\n")

# expect that the agent has listed available files
print("Available files: ", session_end.state[ALL_AVAILABLE_FILES])

# the suggested files should be reasonable looking CSV files
print("Suggested files: ", session_end.state[SUGGESTED_FILES])

Look through the transcript, noticing the tool use and final message.

# Agree with the file suggestions
await file_suggestion_caller.call("Yes, let's do it")

session_end = await file_suggestion_caller.get_session()

print("\n---\n")

print("Approved files: ", session_end.state[APPROVED_FILES])

Congratulations\! You've now setup the files suggestion agent.

## 5.5. Optional - Sequence diagram illustrating the workflow of  "File Suggestion Agent"  

<img src="images/file_suggestions_sequence_diagram.png" width="700">

