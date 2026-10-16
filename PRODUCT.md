# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + TypeScript (Vite) with Cytoscape.js for the graph, served by the project's FastAPI app (`kg serve`, `src/kgbuilder/web/`). Chosen by the user on 2026-10-16.

## Users

The audience is the thesis's examiners, in two scenes that weigh equally:
- the thesis defence, where the interface is shown live on a projector to a committee;
- examiners exploring it afterwards on their own laptops, at their own pace.

The author also uses it to inspect graphs and agent runs.

## Product Purpose

The interface shows a master's thesis system: "Automated KG Builder With Natural Language Querying Engine".
- **What the system does:** it builds a knowledge graph from heterogeneous raw data (tables and text together) into Neo4j, then answers natural-language questions with an agent that calls tools over the graph, the text and the source tables.
- **What the interface presents:** five benchmarked datasets, each as a tab:
  - furniture;
  - NHTSA recalls;
  - generality;
  - lighthouses;
  - NTSB accidents.
- **Success:** a viewer asks a question and can verify the answer. They can see every step the agent took, how long each took, what it cost, and the source behind every claim.

## Positioning

The answer is not taken on trust.
- **The steps:** every tool call, its arguments, its time and its result are visible, as are the send-backs where code returned an answer to the model.
- **The sources:** every cited passage, table row and query resolves to the source text or row it came from, with code's citation checks (quote found, entity found, number found).
- **The graph:** it fuses table records with entities extracted from text, and each edge keeps its provenance (a table row, or the sentence of a text claim).

## Operating Context

- **Questions:**
  - Asked live during a defence, or by an examiner exploring.
  - Each costs about $0.03 at R219 prices and takes 10-40 s.
  - Only one question runs at a time.
- **The graph:** Neo4j holds one dataset's graph at a time. Questions go only to the loaded dataset; the other tabs can still be browsed, offline from their builds.
- **What a reply shows:**
  - an outcome: answer, clarify, out_of_scope, insufficient_evidence, or refuse (the security screen);
  - the agent variant used: `agent` (the benchmarked system), `agent_sql` or `agent_best`.

## Capabilities and Constraints

- **The API (`src/kgbuilder/web/app.py`):**
  - datasets and which one is loaded;
  - each build's schema by type;
  - an overview of the busiest things, a neighbourhood, and search;
  - a thing's detail, with properties and their source rows;
  - a passage by id;
  - a streamed question (server-sent events): model time per turn, tool calls, send-backs, LLM requests with tokens and cost, and then the answer with its trace.
- **Not built yet:**
  - loading a dataset's graph from the page (roadmap R222b);
  - README instructions (R224).
- **Accuracy numbers:** when shown, they must name which score they are (exact match or LLM judge) and their n. Numbers come from the roadmap, never invented.

## Brand Commitments

The user's binding visual constraints (2026-10-16):
- minimalistic and modern;
- no neon and no unnecessary elements;
- a clean layout with clear visual separation of concerns;
- fonts big enough: body about 17-18px, nothing under 14px.

Standing preference (2026-10-16): the user chose the category standard, a clean modern dashboard, over a distinctive visual world. The quality bar is set by the products it should sit alongside: Vercel's dashboard, Neo4j Bloom/Browser, ChatGPT/Claude.ai and Linear.

## Evidence on Hand

- **The data:** the real builds `out/r165_*_build` and `out/r156_generality` (listed in `datasets.yaml`), and the real agent answers produced by `kg ask` / `kg qa`.
- **Reported results:** in `REFACTOR_PLAN.md`; for example, R157: `agent` was right on 243/250 questions by meaning (LLM judge).
- **Not on hand:** there are no screenshots or brand assets.

## Product Principles

- Verification first: every claim in an answer leads to its source in one click.
- The interface shows what the system did, never more: no invented numbers, no decoration standing in for evidence.
- It must be readable from the back of a room and explorable alone.
- The graph is browsable on every tab; questions are honest about which graph is loaded.

## Accessibility & Inclusion

- Projector readability: large type and strong contrast in both light and dark themes.
- No information carried by colour alone (outcomes and checks carry words or symbols).
