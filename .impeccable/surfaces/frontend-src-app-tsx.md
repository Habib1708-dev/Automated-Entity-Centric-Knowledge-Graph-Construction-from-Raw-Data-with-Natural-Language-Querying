---
version: 1
slug: "frontend-src-app-tsx"
primary_target: "frontend/src/App.tsx"
related_targets: []
---

# Surface: the KG Builder interface (frontend/)

Mode: operate. The examiners and the author use it to browse each benchmarked dataset's graph and ask verifiable questions. It is shown live on a projector at the defence and explored alone afterwards.

Quality bar (user, 2026-10-16): Vercel dashboard, Neo4j Bloom/Browser, ChatGPT/Claude.ai, Linear. The user chose the category standard on purpose.

## Direction contract

THESIS: The verifiable answer inside the category's standard dashboard: dataset tabs on top, the graph on the left, the chat on the right. Every answer opens to its steps and sources in one click. It refuses decoration, novelty layouts and anything that is not graph, answer, step or source.

OWN-WORLD:
- Grounds: white #FFFFFF (dark mode #0B0D12); slate text #0F172A; borders #E2E8F0.
- One blue accent #2563EB, only for the active tab, selection, citations and focus.
- The graph's kinds are told apart by shape and a muted tone, never by colour alone, and explained in a legend.
- Type: Inter for UI (17px body, labels at least 15px, titles 20-22px); JetBrains Mono only for tool arguments, SQL and ids.
- Corners: 8px radius. Hairline 1px rules; one panel level, never nested cards.

STORY: The viewer picks a tab and sees whether its graph is loaded. They ask a question and watch the steps arrive live, each with its time. They read the answer with numbered citations, open Steps or Sources, and click a source to see the passage or table row; the cited things light up in the graph.

FIRST VIEWPORT:
- A 64px header: the product name on the left, five pill tabs, and the status (loaded, counts) on the right.
- Below, a grid: the graph (1fr) | the chat (520px).
- The graph area: a toolbar (Overview, search) above the canvas; the legend at bottom left; a node detail panel opening at its right edge.
- The chat: the messages, with the input pinned at the bottom (variant select and Ask button). When the tab's graph is not loaded, the input says so.

FORM: canon, the category standard (standing exit taken by the user); seed:b46146d2c3a28a5d. Signature interaction: a citation number links the answer, its source and its node on the graph; the rest of the graph dims while a source is open.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
