# R160-R173: how the engine was fixed and confirmed, and the loop one answer runs through

Diagrams of plans R160-R166 and R167-R173 (drawn in R174): the measure-fix-measure protocol behind the three
measurements of the agent on unseen questions, and the agent loop itself.

## Diag. 1: Measure, fix, measure again: three question sets written blind, the engine fixed between them from its traced failures

**Files:** [r173_diag1_fix_and_confirm_protocol.svg](r173_diag1_fix_and_confirm_protocol.svg), [.png](r173_diag1_fix_and_confirm_protocol.png)

**What it shows:** the three measurements as three columns in time order: the question set written blind
(R152-R154: 250; R160: 100, written before R161-R164; R171: 100, written after R167-R170 but blind to them,
each file reviewed once), the engine (frozen at R151; with R161-R164; with R167-R170), and the run with its
two scores (R156 $3.870, R157 239/250 exact and 243/250 by meaning, 11 wrong; R165 $1.603, R166 94/100 and
94/100, 6 wrong; R172 $1.627 plus $0.501 wasted, R173 98/100 and 98/100, 2 wrong).

**How to read it:** within a column the questions and the engine feed the run (the vertical arrows); a
column's traced failures feed only the next column's fixes (the slanted arrows), never its questions, which
were written without any output. Orange boxes are the engine, blue the scores, as in the charts.

**Takeaway:** every fix was proven first by an offline replay of the recorded failing calls ($0) and then
measured on a set written blind, so no question set was shaped by the engine that answers it; the tool text
changes with every fix, so each score describes the engine of its own column, and the sets differ, so no
difference is attributed to the fixes.

**Source:** `tests/gold/r157/outcomes.json`, `tests/gold/r166/outcomes.json`, `tests/gold/r173/outcomes.json`;
`tests/gold/r156/runs.json`, `tests/gold/r165/runs.json`, `tests/gold/r172/runs.json`.

**Caveats:** the writers of R160 and R171 and the reviewers are Claude subagents (Opus 5.5, then Fable 5.1),
the judges Opus 5.5, the fixes written by Claude: one model family throughout, the agent answering with
Gemini.

## Diag. 2: The agent loop: the model proposes one typed tool call per turn, code checks and runs it, the result is observed, until the answer

**Files:** [r173_diag2_agent_loop.svg](r173_diag2_agent_loop.svg), [.png](r173_diag2_agent_loop.png)

**What it shows:** one question's path through the agentic query engine (R135-R170, `agent/loop.py`):
the question with the tools' list and schemas; the model's one reply per turn, held to a schema (a call with
a tool name and typed arguments, or the answer); code's checks of the call (an unknown tool, arguments that
do not parse, a repeat, a query naming a field the schema lacks: refused with the reason written back); the
tool running read-only over the graph, the text or the staged tables; the result written back as the
turn's observation; the loop back to the model until the answer or a cap (10 tool calls, turns, prompt
size, time), when the tools close and the model answers from what it has; the answer's checks (an id no
tool returned, an empty answer, a declared form left empty: each sent back once); the two scores.

**How to read it:** left to right as a turn flows; the loop runs along the top; orange is the one box that
proposes, blue the tools, neutral the code that decides and contains.

**Takeaway:** the loop is ReAct in shape (reason, act, observe, repeat) but not in letter: the model emits
no written thought (its reasoning is its own thinking budget), the action is a typed call that code
validates, refuses or caps, and an answer is checked and sent back once before it stands. The division "the
LLM proposes, code decides" is the thesis's design rule, visible as the one orange box.

**Source:** `src/kgbuilder/agent/loop.py`, `prompt.py`, `citations.py`, the tools in `graph_tools.py`,
`text_tools.py` and `structured_tools.py`; the caps are the settings `tests/test_agent_loop.py` pins.

**Caveats:** a design diagram, not a measurement; the tool names shown are the twelve of R170's engine.
