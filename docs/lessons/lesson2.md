# Lesson 3 - Introduction to Google's ADK - Part II

In this lesson, you will familiarize yourself with Google's Agent Development Kit (ADK) that you will use in the next lessons to build your multi-agent system.

You'll learn:
- how to create and run an agent using ADK (Part I)
- how to create a team of Agents consisting of a root agent and 2 sub-agents (Part II)
- how the team of agents can access a sharable context (Part II)

For each agent, you'll define a tool that allows the agent to interact with the Neo4j database we setup for this course. # Lesson 3 - Introduction to Google's ADK - Part I

In this lesson, you will familiarize yourself with Google's Agent Development Kit (ADK) that you will use in the next lessons to build your multi-agent system.

You'll learn:
- how to create and run an agent using ADK (Part I)
- how to create a team of Agents consisting of a root agent and 2 sub-agents (Part II)
- how the team of agents can access a sharable context (Part II)

For each agent, you'll define a tool that allows the agent to interact with the Neo4j database we setup for this course. 


## 3.1. Setup

# Import necessary libraries
import os
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm # For OpenAI support
from google.adk.sessions import InMemorySessionService
from google.adk.runners import Runner
from google.genai import types # For creating message Content/Parts
from typing import Optional, Dict, Any

import warnings
warnings.filterwarnings("ignore")

import logging
logging.basicConfig(level=logging.CRITICAL)

print("Libraries imported.")

# Define Model Constants for easier use 
MODEL_GPT = "openai/gpt-4o"

llm = LiteLlm(model=MODEL_GPT)

# Test LLM with a direct call
print(llm.llm_client.completion(model=llm.model, 
                                messages=[{"role": "user", 
                                           "content": "Are you ready?"}], 
                                tools=[]))

print("\nOpenAI is ready for use.")

## 3.2. Explore `neo4j_for_adk`

In your lab environment, you are provided with a graph database that your agent will interact with. For that, you are provided with a helper called `neo4j_for_adk` from which you'll import the instance `graphdb`, which wraps the Neo4j Python driver to make it ADK friendly.

# Convenience libraries for working with Neo4j inside of Google ADK
from neo4j_for_adk import graphdb

<div style="background-color:#fff6ff; padding:13px; border-width:3px; border-color:#efe6ef; border-style:solid; border-radius:6px">
<p> 💻 &nbsp; <b>To access neo4j_for_adk.py </b> 1) click on the <em>"File"</em> option on the top menu of the notebook and then 2) click on <em>"Open"</em>.

</div>

`graphdb` has a method `send_query` which expects a cypher query, runs the query and then formats the results as follows:

<img src="images/send_query_expln.png" alt="diagram of the send_query method from the graphdb module showing success or failure response to a query" width=400>  

# Sending a simple query to the database
neo4j_is_ready = graphdb.send_query("RETURN 'Neo4j is Ready!' as message")

print(neo4j_is_ready)

**Optional Note**: Neo4j Database Setup

We set up the database as a sidecar container. You can find the Docker installation instructions (and others) [here](https://neo4j.com/docs/operations-manual/current/docker/introduction/). We configured the username and password as part of the database setup. We also installed a plugin called [APOC](https://neo4j.com/labs/apoc/) (which will be needed in the last notebook). We defined these environment variables, which are used by `neo4j_for_adk.py`:
- `NEO4J_URI="bolt://localhost:7687"`
- `NEO4J_USERNAME="your_database_username"`
- `NEO4J_PASSWORD="your_database_password"`

## 3.3. Define your Agent's Tool

# Define a basic tool -- send a parameterized cypher query
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


# Example tool usage (optional test)
print(say_hello("ABK"))

# Example tool usage (optional test)
print(say_hello("RETURN 'injection attack avoided'"))

## 3.4. Define the Agent `friendly_cypher_agent`

**Optional Reading**

- An `Agent` in Google ADK orchestrates the interaction between the user, the LLM, and the available tools

- you configure it with several key parameters:
    * `name`: A unique identifier for this agent (e.g., "friendly_cypher_agent\_v1").  
    * `model`: Specifies which LLM to use. you'll use the `llm` variable we defined above.  
    * `description`: A summary of the agent's overall purpose. This is like public documentation that helps other agents decide when to delegate tasks to *this* agent.  
    * `instruction`: Detailed guidance given to the LLM on how this agent should behave, its persona, goals, and specifically *how and when* to utilize its assigned `tools`.  
    * `tools`: A list containing the actual Python tool functions the agent is allowed to use (e.g., `[say_hello]`).

**Best Practice:** 
- Provide clear and specific `instruction` prompts. The more detailed the instructions, the better the LLM can understand its role and how to use its tools effectively. Be explicit about error handling if needed.
- Choose descriptive `name` and `description` values. These are used internally by ADK and are vital for features like automatic delegation (covered later).

# Define the Cypher Agent
hello_agent = Agent(
    name="hello_agent_v1",
    model=llm, # defined earlier in a variable
    description="Has friendly chats with a user.",
    instruction="""You are a helpful assistant, chatting with a user. 
                Be polite and friendly, introducing yourself and asking who the user is. 

                If the user provides their name, use the 'say_hello' tool to get a custom greeting.
                If the tool returns an error, inform the user politely. 
                If the tool is successful, present the reply.
                """,
    tools=[say_hello], # Pass the function directly
)

print(f"Agent '{hello_agent.name}' created.")

## 3.5. Run the Agent

To run an agent, you'll need some additional components namely an execution environment and memory.


### 3.5.1. Event Loop   

<img src="images/event_loop_3.png"  alt="diagram of Agent Development Kit runtime showing the Runner component handling an Event Loop with Services for the User" width=400> 

The [ADK Runtime](https://google.github.io/adk-docs/runtime/) orchestrates agents throughout execution. The main component is an event-driven loop intermediated by a Runner. When the runner receives a user query, it asks the agent to start processing. The agent processes the query and emits an event. The Runner receives the event, records state changes, updates memory and forwards the event to the user interface. After that, the agent's logic resumes and the cycle repeats until no further events are produced by the agent and the user has a response.

### 3.5.2. Create the Runner and SessionService

Let's assume we have a single user talking to the agent in a single session. Let's create this user, the session and the runner:
* `SessionService`: Responsible for managing conversation history and state for different users and sessions. The `InMemorySessionService` is a simple implementation that stores everything in memory, suitable for testing and simple applications. It keeps track of the messages exchanged.  
* `Runner`: The engine that orchestrates the interaction flow. It takes user input, routes it to the appropriate agent, manages calls to the LLM and tools based on the agent's logic, handles session updates via the `SessionService`, and yields events representing the progress of the interaction.

app_name = hello_agent.name + "_app"
user_id = hello_agent.name + "_user"
session_id = hello_agent.name + "_session_01"
    
# Initialize a session service and a session
session_service = InMemorySessionService()
await session_service.create_session(
    app_name=app_name,
    user_id=user_id,
    session_id=session_id
)
    
runner = Runner(
    agent=hello_agent,
    app_name=app_name,
    session_service=session_service
)

### 3.5.3. Run the Agent

Here's what's happening:
 
1. Package the user query into the ADK `Content` format.
2. Call`runner.run_async` (providing it with user/session context and the new message)
4. Iterate through the **Events** yielded by the runner. Events represent steps in the agent's execution (e.g., tool call requested, tool result received, intermediate LLM thought, final response).  
5. Identify and print the **final response** event using `event.is_final_response()`.

**Why `async`?** Interactions with LLMs and potentially tools (like external APIs) are I/O-bound operations. Using `asyncio` allows the program to handle these operations efficiently without blocking execution.

user_message = "Hello, I'm ABK"
print(f"\n>>> User Message: {user_message}")

# Prepare the user's message in ADK format
content = types.Content(role='user', parts=[types.Part(text=user_message)])

final_response_text = "Agent did not produce a final response." # Default will be replaced if the agent produces a final response.


# We iterate through events to find the final answer.
verbose = False
async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
    if verbose:
        print(f"  [Event] Author: {event.author}, Type: {type(event).__name__}, Final: {event.is_final_response()}, Content: {event.content}")
    
    # Key Concept: is_final_response() marks the concluding message for the turn.
    if event.is_final_response():
        if event.content and event.content.parts:
            final_response_text = event.content.parts[0].text # Assuming text response in the first part
        elif event.actions and event.actions.escalate: # Handle potential errors/escalations
            final_response_text = f"Agent escalated: {event.error_message or 'No specific message.'}"
        break # Stop processing events once the final response is found

print(f"<<< Agent Response: {final_response_text}")

## 3.6. Create Helper Class: `AgentCaller`

### 3.6.1 Set up AgentCaller

Let's wrap the runner in the helper class: `AgentCaller`. This helper will make it easier to make repeated calls to the agent by assuming we have a single user talking to the agent in a single session.

class AgentCaller:
    """A simple wrapper class for interacting with an ADK agent."""
    
    def __init__(self, agent: Agent, runner: Runner, 
                 user_id: str, session_id: str):
        """Initialize the AgentCaller with required components."""
        self.agent = agent
        self.runner = runner
        self.user_id = user_id
        self.session_id = session_id


    def get_session(self):
        return self.runner.session_service.get_session(app_name=self.runner.app_name, user_id=self.user_id, session_id=self.session_id)

    
    async def call(self, user_message: str, verbose: bool = False):
        """Call the agent with a query and return the response."""
        print(f"\n>>> User Message: {user_message}")

        # Prepare the user's message in ADK format
        content = types.Content(role='user', parts=[types.Part(text=user_message)])

        final_response_text = "Agent did not produce a final response." 
        
        # Key Concept: run_async executes the agent logic and yields Events.
        # We iterate through events to find the final answer.
        async for event in self.runner.run_async(user_id=self.user_id, session_id=self.session_id, new_message=content):
            # You can uncomment the line below to see *all* events during execution
            if verbose:
                print(f"  [Event] Author: {event.author}, Type: {type(event).__name__}, Final: {event.is_final_response()}, Content: {event.content}")

            # Key Concept: is_final_response() marks the concluding message for the turn.
            if event.is_final_response():
                if event.content and event.content.parts:
                    # Assuming text response in the first part
                    final_response_text = event.content.parts[0].text
                elif event.actions and event.actions.escalate: # Handle potential errors/escalations
                    final_response_text = f"Agent escalated: {event.error_message or 'No specific message.'}"
                break # Stop processing events once the final response is found

        print(f"<<< Agent Response: {final_response_text}")
        return final_response_text


### 3.6.2 Make an instance of the AgentCaller

Rather than a class constructor, you'll use a factory method which needs to make some async calls to initialize the components (user_id, session_id and runner) before passing to them to the AgentCaller.

The factory method takes some parameters:

* `Agent`: the agent that we defined earlier
* `initial_state`: optional initialization of the agent's "memory"

Inside, the method will create memory for the agent and a runner.


async def make_agent_caller(agent: Agent, initial_state: Optional[Dict[str, Any]] = {}) -> AgentCaller:
    """Create and return an AgentCaller instance for the given agent."""
    app_name = agent.name + "_app"
    user_id = agent.name + "_user"
    session_id = agent.name + "_session_01"
    
    # Initialize a session service and a session
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state=initial_state
    )
    
    runner = Runner(
        agent=agent,
        app_name=app_name,
        session_service=session_service
    )
    
    return AgentCaller(agent, runner, user_id, session_id)


### 3.6.3. Run the Conversation

Now you can define an async function to run the conversation.

hello_agent_caller = await make_agent_caller(hello_agent)

# We need an async function to await our interaction helper
async def run_conversation():
    await hello_agent_caller.call("Hello I'm ABK")

    await hello_agent_caller.call("I am excited")

# Execute the conversation using await
await run_conversation()


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