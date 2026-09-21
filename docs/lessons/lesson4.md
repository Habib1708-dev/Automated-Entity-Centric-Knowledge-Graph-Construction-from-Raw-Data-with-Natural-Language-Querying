# Lesson 3 - Introduction to Google's ADK - Part II

In this lesson, you will familiarize yourself with Google's Agent Development Kit (ADK) that you will use in the next lessons to build your multi-agent system.

You'll learn:
- how to create and run an agent using ADK (Part I)
- how to create a team of Agents consisting of a root agent and 2 sub-agents (Part II)
- how the team of agents can access a sharable context (Part II)

For each agent, you'll define a tool that allows the agent to interact with the Neo4j database we setup for this course. 


<div style="background-color:#fff6ff; padding:13px; border-width:3px; border-color:#efe6ef; border-style:solid; border-radius:6px">
<p> 💻 &nbsp; <b>To access the helper.py and neo4j_for_adk.py files:</b> 1) click on the <em>"File"</em> option on the top menu of the notebook and then 2) click on <em>"Open"</em>.
</div>

## 3.1. Setup

# Import necessary libraries
import os
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm # For OpenAI support
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types # For creating message Content/Parts
from typing import Optional, Dict, Any

# Convenience libraries for working with Neo4j inside of Google ADK
from neo4j_for_adk import graphdb

import warnings
# Ignore all warnings
warnings.filterwarnings("ignore")

import logging
logging.basicConfig(level=logging.CRITICAL)

print("Libraries imported.")

# --- Define Model Constants for easier use ---
MODEL_GPT = "openai/gpt-4o"

llm = LiteLlm(model=MODEL_GPT)

# Test LLM with a direct call
print(llm.llm_client.completion(model=llm.model, 
                                messages=[{"role": "user", "content": "Are you ready?"}], 
                                tools=[]))

print("\nOpenAI is ready for use.")

## 3.2. Set up AgentCaller

Remember that we defined `AgentCaller` in part I as a simple wrapper for interacting with an ADK agent. Additionally we defined a factory function `make_agent_caller` to make instances of `AgentCaller`. Let's import the factory function from the `helper` script. 

from helper import make_agent_caller

## 3.3. A Simple Multi-Agent Team \- Delegation for Greetings & Farewells 

Using multiple agents is a common pattern in real-world applications. It allows for better modularity, specialization, and scalability. 

**You will:**

1. Define another simple tool that will handle farewells (`say_goodbye`).  
2. Create two new specialized sub-agents: `greeting_agent` and `farewell_agent`.  
3. Create a new top-level agent (`cypher_agent_team`) to act as the **root agent**.  
4. Configure the root agent with its sub-agents, enabling **automatic delegation**.  
5. Test the delegation flow by sending different types of requests to the root agent.

### 3.3.1 Define Tools for Sub-Agents

# Define the hello tool 
def say_hello(person_name: str) -> dict:
    """Formats a welcome message to a named person. 

    Args:
        person_name (str): the name of the person saying hello

    Returns:
        dict: A dictionary containing the results of the query.
              Includes a 'status' key ('success' or 'error').
              If 'success', includes a 'query_result' key with an array of result rows.
              If 'error', includes an 'error_message' key.
    """
    return graphdb.send_query("RETURN 'Hello to you, ' + $person_name AS reply",
    {
        "person_name": person_name
    })

# Define the new goodbye tool
def say_goodbye() -> dict:
    """Provides a simple farewell message to conclude the conversation."""
    return graphdb.send_query("RETURN 'Goodbye from Cypher!' as farewell")


### 3.3.2. Define the Sub-Agents (Greeting & Farewell)

Now, create the `Agent` instances for our specialists. Notice their highly focused `instruction` and, critically, their clear `description`. The `description` is the primary information the *root agent* uses to decide *when* to delegate to these sub-agents.

**Best Practice:** 
- Sub-agent `description` fields should accurately and concisely summarize their specific capability. This is crucial for effective automatic delegation.
- Sub-agent `instruction` fields should be tailored to their limited scope, telling them exactly what to do and *what not* to do (e.g., "Your *only* task is...").

# --- Greeting Agent ---
greeting_subagent = Agent(
    model=llm,
    name="greeting_subagent_v1",
    instruction="You are the Greeting Agent. Your ONLY task is to provide a friendly greeting to the user. "
                "Use the 'say_hello' tool to generate the greeting. "
                "If the user provides their name, make sure to pass it to the tool. "
                "Do not engage in any other conversation or tasks.",
    description="Handles simple greetings and hellos using the 'say_hello' tool.", # Crucial for delegation
    tools=[say_hello],
)
print(f"✅ Agent '{greeting_subagent.name}' created.")


# --- Farewell Agent ---
farewell_subagent = Agent(
    # Can use the same or a different model
    model=llm, # Sticking with GPT for this example
    name="farewell_subagent_v1",
    instruction="You are the Farewell Agent. Your ONLY task is to provide a polite goodbye message. "
                "Use the 'say_goodbye' tool when the user indicates they are leaving or ending the conversation "
                "(e.g., using words like 'bye', 'goodbye', 'thanks bye', 'see you'). "
                "Do not perform any other actions.",
    description="Handles simple farewells and goodbyes using the 'say_goodbye' tool.", # Crucial for delegation
    tools=[say_goodbye],
)
print(f"✅ Agent '{farewell_subagent.name}' created.")

### 3.3.3. Define the Root Agent with Sub-Agents

The `root_agent` can now manage sub-agents by including them in the `sub_agents` parameter and updating its instructions to explain when to delegate tasks. With this setup, ADK enables automatic delegation: if a user query is better suited to a sub-agent based on its description, the root agent will automatically hand off control to that sub-agent. For effective delegation, clearly specify in the root agent’s instructions which sub-agents exist and when to delegate to them.

root_agent = Agent(
    name="friendly_agent_team_v1", # Give it a new version name
    model=llm,
    description="The main coordinator agent. Delegates greetings/farewells to specialists.",
    instruction="""You are the main Agent coordinating a team. Your primary responsibility is to be friendly.
 
                You have specialized sub-agents: 
                1. 'greeting_agent': Handles simple greetings like 'Hi', 'Hello'. Delegate to it for these. 
                2. 'farewell_agent': Handles simple farewells like 'Bye', 'See you'. Delegate to it for these. 

                Analyze the user's query. If it's a greeting, delegate to 'greeting_agent'. 
                If it's a farewell, delegate to 'farewell_agent'. 
                
                For anything else, respond appropriately or state you cannot handle it.
                """,
    tools=[], # No tools for the root agent
    # Key change: Link the sub-agents here!
    sub_agents=[greeting_subagent, farewell_subagent]
)


print(f"✅ Root Agent '{root_agent.name}' created with sub-agents: {[sa.name for sa in root_agent.sub_agents]}")


### 3.3.4. Interact with the Agent Team

Now that you've defined our root agent with its specialized sub-agents, let's test the delegation mechanism.

from helper import make_agent_caller

root_agent_caller = await make_agent_caller(root_agent)

async def run_team_conversation():
    await root_agent_caller.call("Hello I'm ABK", True)

    await root_agent_caller.call("Thanks, bye!", True)

# Execute the conversation using await
await run_team_conversation()


You've now structured your application with multiple collaborating agents. This modular design is fundamental for building more complex and capable agent systems. In the next step, you'll give our agents the ability to remember information across turns using session state.

## 3.4. Adding Memory and Personalization with Session State

**Optional Reading**

Currently, agents can delegate tasks but cannot remember past interactions. To enable memory for context-aware behavior, ADK uses **Session State**—a Python dictionary linked to each user session.

> `session.state` is tied to a specific user session identified by `APP_NAME`, `USER_ID`, `SESSION_ID`. It persists information *across multiple conversational turns* within that session. 

Session State lets agents and tools store and retrieve information across multiple turns. Tools access this state through the ToolContext object, while agents can automatically save their final responses using the output_key setting.

1. **`ToolContext`** Tools can accept a `ToolContext` object giving access to the session state via `tool_context.state` allowing tools to save information *during* execution.  
2. **`output_key`**  Agents can have `output_key="your_key"` allowing ADK to save the agent's final textual response into `session.state["your_key"]`.

These constructs allow agents to remember past details, adapt their actions, and personalize responses during a session.

### 3.4.1. Create State-Aware hello/goodbye Tools

You will create a new version of the hello/goodbye tools. The key feature is accepting `tool_context: ToolContext` 
which allows them to access `tool_context.state`. 

They will write to or read from the `user_name` state variable.


* **Key Concept: `ToolContext`** This object is the bridge allowing your tool logic to interact with the session's context, including reading and writing state variables. ADK injects it automatically if defined as the last parameter of your tool function.


* **Best Practice:** When reading from state, use `dictionary.get('key', default_value)` to handle cases where the key might not exist yet, ensuring your tool doesn't crash.

from google.adk.tools.tool_context import ToolContext

def say_hello_stateful(user_name:str, tool_context:ToolContext):
    """Says hello to the user, recording their name into state.
    
    Args:
        user_name (str): The name of the user.
    """
    tool_context.state["user_name"] = user_name
    print("\ntool_context.state['user_name']:", tool_context.state["user_name"])
    return graphdb.send_query(
        f"RETURN 'Hello to you, ' + $user_name + '.' AS reply",
    {
        "user_name": user_name
    })

def say_goodbye_stateful(tool_context: ToolContext) -> dict:
    """Says goodbye to the user, reading their name from state."""
    user_name = tool_context.state.get("user_name", "stranger")
    print("\ntool_context.state['user_name']:", user_name)
    return graphdb.send_query("RETURN 'Goodbye, ' + $user_name + ', nice to chat with you!' AS reply",
    {
        "user_name": user_name
    })


print("✅ State-aware 'say_hello_stateful' and 'say_goodbye_stateful' tools defined.")


### 3.4.2. Redefine Sub-Agents and Update Root Agent

To ensure this step is self-contained and builds correctly, you first redefine the `greeting_agent` and `farewell_agent` exactly as they were in Step 3.3\. Then, you define the new root agent (`root_agent_stateful`):

* It uses the new `say_hello_stateful` and `say_goodbye_stateful` tools.  
* It includes the greeting and farewell sub-agents for delegation.  


# define a stateful greeting agent. the only difference is that this agent will use the stateful say_hello_stateful tool
greeting_agent_stateful = Agent(
    model=llm,
    name="greeting_agent_stateful_v1",
    instruction="You are the Greeting Agent. Your ONLY task is to provide a friendly greeting using the 'say_hello' tool. Do nothing else.",
    description="Handles simple greetings and hellos using the 'say_hello_stateful' tool.",
    tools=[say_hello_stateful],
)
print(f"✅ Agent '{greeting_agent_stateful.name}' redefined.")


farewell_agent_stateful = Agent(
    model=llm,
    name="farewell_agent_stateful_v1",
    instruction="You are the Farewell Agent. Your ONLY task is to provide a polite goodbye message using the 'say_goodbye_stateful' tool. Do not perform any other actions.",
    description="Handles simple farewells and goodbyes using the 'say_goodbye_stateful' tool.",
    tools=[say_goodbye_stateful],
)
print(f"✅ Agent '{farewell_agent_stateful.name}' redefined.")

root_agent_stateful = Agent(
    name="friendly_team_stateful", # New version name
    model=llm,
    description="The main coordinator agent. Delegates greetings/farewells to specialists.",
    instruction="""You are the main Agent coordinating a team. Your primary responsibility is to be friendly.

                You have specialized sub-agents: 
                1. 'greeting_agent_stateful': Handles simple greetings like 'Hi', 'Hello'. Delegate to it for these. 
                2. 'farewell_agent_stateful': Handles simple farewells like 'Bye', 'See you'. Delegate to it for these. 

                Analyze the user's query. If it's a greeting, delegate to 'greeting_agent_stateful'. If it's a farewell, delegate to 'farewell_agent_stateful'. 
                
                For anything else, respond appropriately or state you cannot handle it.
                """,
        tools=[], # Still no tools for root
        sub_agents=[greeting_agent_stateful, farewell_agent_stateful], # Include sub-agents
    )

print(f"✅ Root Agent '{root_agent_stateful.name}' created using agents with stateful tools.")


### 3.4.3. Interact and Test State Flow

Now, you can initialize a new `AgentCaller`. This time, you'll provide an initial state.

Then you can execute a conversation designed to test the state interactions using the `root_agent_stateful` root agent.


root_stateful_caller = await make_agent_caller(root_agent_stateful)

session = await root_stateful_caller.get_session()

print(f"Initial State: {session.state}")

Now, you can define a conversation, run it, then examine the final session state.

async def run_stateful_conversation():
    await root_stateful_caller.call("Hello, I'm ABK!")

    await root_stateful_caller.call("Thanks, bye!")

# Execute the conversation using await in an async context (like Colab/Jupyter)
await run_stateful_conversation()

session = await root_stateful_caller.get_session()

print(f"\nFinal State: {session.state}")

## 3.5. Finally, An Interactive Conversation

Now, let's make this interactive so you can ask your own questions! Run the cell below. It will prompt you to enter your queries directly.

**Note:** In the video, we re-run the first cell in 3.4.3 to start with a fresh state (no stored name). You might get different results from the video.

async def run_interactive_conversation():
    while True:
        user_query = input("Ask me something (or type 'exit' to quit): ")
        if user_query.lower() == 'exit':
            break
        response = await root_stateful_caller.call(user_query)
        print(f"Response: {response}")

# Execute the interactive conversation
await run_interactive_conversation()