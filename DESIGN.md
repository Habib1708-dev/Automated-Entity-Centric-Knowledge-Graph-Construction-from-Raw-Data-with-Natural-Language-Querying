---
name: KG Builder
description: Automated knowledge-graph builder with a verifiable natural-language query engine; graph, answer, steps and sources in one dashboard.
colors:
  bg: "#ffffff"
  bg-subtle: "#f8fafc"
  bg-muted: "#f1f5f9"
  border: "#e2e8f0"
  border-strong: "#cbd5e1"
  text: "#0f172a"
  text-2: "#475569"
  text-3: "#64748b"
  accent: "#2563eb"
  accent-text: "#ffffff"
  accent-soft: "#eff6ff"
  ok: "#15803d"
  warn: "#b45309"
  bad: "#b91c1c"
  kind-record: "#475569"
  kind-individual: "#0f766e"
  kind-concept: "#94a3b8"
  kind-mention: "#cbd5e1"
  edge: "#cbd5e1"
  edge-text: "#0f766e"
  dark-bg: "#0b0d12"
  dark-bg-subtle: "#11141b"
  dark-bg-muted: "#171b24"
  dark-border: "#262c38"
  dark-border-strong: "#353d4d"
  dark-text: "#e8ecf3"
  dark-text-2: "#a7b0c0"
  dark-text-3: "#8892a4"
  dark-accent: "#4f8cff"
  dark-accent-text: "#0b0d12"
  dark-accent-soft: "#15213a"
  dark-ok: "#6fae86"
  dark-warn: "#c9a45a"
  dark-bad: "#d98080"
  dark-kind-record: "#a7b0c0"
  dark-kind-individual: "#4f9d95"
  dark-kind-concept: "#64748b"
  dark-kind-mention: "#475569"
  dark-edge: "#353d4d"
  dark-edge-text: "#4f9d95"
typography:
  title:
    fontFamily: "Inter Variable, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.6
    letterSpacing: "-0.01em"
  lead:
    fontFamily: "Inter Variable, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Inter Variable, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.6
    fontFeature: "'cv11', 'tnum'"
  label:
    fontFamily: "Inter Variable, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.6
    fontFeature: "'cv11', 'tnum'"
  mono:
    fontFamily: "JetBrains Mono, ui-monospace, monospace"
    fontSize: "15px"
    fontWeight: 400
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
spacing:
  "1": "4px"
  "2": "8px"
  "3": "12px"
  "4": "16px"
  "5": "24px"
  "6": "32px"
components:
  header-tab:
    backgroundColor: "transparent"
    textColor: "{colors.text-2}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  header-tab-hover:
    backgroundColor: "{colors.bg-muted}"
    textColor: "{colors.text}"
  header-tab-active:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-text}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "40px"
  button-primary-disabled:
    backgroundColor: "{colors.bg-muted}"
    textColor: "{colors.text-3}"
  button-icon:
    backgroundColor: "transparent"
    textColor: "{colors.text-2}"
    rounded: "{rounded.md}"
    size: "36px"
  input-search:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.text}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "0 32px 0 40px"
    height: "40px"
  input-question:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
  citation-mark:
    backgroundColor: "transparent"
    textColor: "{colors.accent}"
    typography: "{typography.label}"
    height: "22px"
  citation-mark-active:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-text}"
  source-row:
    backgroundColor: "transparent"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "4px 8px"
  source-row-active:
    backgroundColor: "{colors.accent-soft}"
  legend:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.text-2}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  side-panel:
    backgroundColor: "{colors.bg}"
    width: "420px"
---

# Design System: KG Builder

## Overview

**Creative North Star: "The Verifiable Dashboard"**

The category standard, chosen on purpose: a clean modern dashboard at the level of Vercel, Linear, Neo4j Bloom and ChatGPT/Claude.ai. Dataset tabs sit on top, the graph on the left, the chat on the right. Every surface is graph, answer, step or source; there is no decoration, no hero, no novelty layout. The interface is shown on a projector, so sizes run larger than a typical dashboard: body 17px, nothing under 15px.

Depth is flat: white grounds, slate text, 1px hairline rules, one panel level. Colour is almost entirely neutral slate; a single blue carries every "this is selected / this is cited / this is where focus is" signal, which makes the citation link between answer, source and graph node readable at a glance. The light theme is canonical; the dark theme follows the system setting and mutes every saturated tone so nothing reads as neon on near-black.

**Key Characteristics:**
- Slate neutrals plus one blue accent; status colours appear only as text and outlines.
- Inter throughout, JetBrains Mono only for ids, tool arguments, SQL and relation types.
- 15px type floor, 17px body, tabular numerals everywhere.
- Hairline borders and rules separate regions; shadows only on surfaces floating over the canvas.
- Graph kinds told apart by shape first, muted tone second, always explained by the legend.

## Colors

A cool slate neutral ramp with one saturated blue; status and graph tones are deliberately desaturated.

### Primary
- **Signal Blue** (accent; dark: dark-accent): the active tab, the Ask button, inline links, citation numbers and their markers, the selected source row, the picked and cited node rings on the graph, the highlighted quote underline, and the focus ring. **Signal Blue Wash** (accent-soft) is its tinted ground: active tab, selected source, quote highlight, focus halo, selection, and the flash on a newly arrived step.

### Neutral
- **White Ground** (bg; dark: dark-bg): the header, toolbar, chat column, panels, drawer, legend.
- **Canvas Mist** (bg-subtle): the graph canvas ground, table headers, hover on rows, disabled input.
- **Slate Wash** (bg-muted): hover on tabs and icon buttons, code blocks, timing-bar track, disabled button.
- **Hairline** (border): every region rule, row divider and panel edge. **Hairline Strong** (border-strong): input strokes, quote rule, scrollbars, the model share of the timing bar.
- **Ink** (text), **Slate Ink** (text-2: secondary labels, meta, counts), **Quiet Slate** (text-3: the lightest text allowed, 4.8:1 on white; turn numbers, ids, search icon).

### Status
- **Ok Green**, **Warn Amber**, **Bad Red** (ok / warn / bad): used as text colour or a currentColor outline on outcome badges, check marks, the loaded indicator and errors; never as a fill.

### Graph kinds
- **Record Slate** (kind-record), **Text Teal** (kind-individual), **Concept Grey** (kind-concept), **Mention Pale** (kind-mention); table edges in **Hairline Strong** (edge), text edges in **Text Teal** (edge-text) at 0.55 opacity.

### Named Rules
**The One Accent Rule.** Blue means selected, cited, focused or the primary action. Nothing else on the page is blue, and no second accent hue exists.

**The Muted Dark Rule.** In the dark theme every saturated tone (status, teal) is pulled toward grey; a bright tone on near-black reads as neon.

## Typography

**Body Font:** Inter Variable (with system-ui, sans-serif)
**Label/Mono Font:** JetBrains Mono 400 (with ui-monospace, monospace)

**Character:** One neutral sans for everything a person reads; a mono only where the text is machine-shaped. Numerals are tabular (`tnum`) so times, costs and counts align in columns.

### Hierarchy
- **Title** (600, 22px, -0.01em): the empty-state heading in the chat.
- **Lead** (600, 19px, 1.3-1.4): the question in an answer card, the graph toolbar title, the node name, the drawer heading. The product name in the header sits at 18px/600, two lines.
- **Body** (400, 17px, 1.6): answer text, passage text, header tabs, the question box. Readable measure capped at 60ch where text runs free.
- **Label** (400-600, 15px): meta lines, sources, steps, legend, properties, buttons, inputs, the status line. The floor; nothing is smaller.
- **Mono** (400, 15px): ids, tool arguments, SQL, relation types.

### Named Rules
**The 15px Floor Rule.** No text, including the graph canvas labels, renders under 15px; it must read from the back of a room.

## Layout

A full-window frame with no page scroll: a 64px header over a grid of graph (minmax(0, 1fr)) and chat (540px), each region scrolling on its own. The graph region stacks a toolbar row (title, view note, search at 300px on the right) over a canvas filling the rest; the legend floats at the canvas's bottom left, and the node detail panel (420px) covers the canvas's right edge below the toolbar. The source drawer covers the chat column, so the graph stays in view beside it.

Spacing runs on a 4px base (4, 8, 12, 16, 24, 32). Region padding is 24px horizontally, 12px vertically in toolbars, 16px in panel heads. Lists are ruled rows, not boxed cards.

Responsive: under 1400px the header drops secondary status counts so the tabs keep one row. Under 1100px the grid becomes one column (graph 70vh, chat below at min 70vh), the header wraps with the tabs on their own wrapping row, the search goes full width, the legend becomes static below the canvas, and the detail panel takes the full width.

## Elevation & Depth

Flat by default, tonal layering for the canvas (Canvas Mist under white chrome). One shadow exists, and only on surfaces that float over other content: the search results list, the node detail panel and the source drawer. Inputs signal focus with a 3px accent-soft halo, not a shadow.

### Shadow Vocabulary
- **Float** (`box-shadow: 0 8px 24px rgb(15 23 42 / 0.12), 0 2px 6px rgb(15 23 42 / 0.06)`; dark `0 8px 24px rgb(0 0 0 / 0.5), 0 2px 6px rgb(0 0 0 / 0.3)`): overlays above the canvas or chat.

### Named Rules
**The One Panel Level Rule.** A region holds content directly; panels never nest inside cards. Separation is a 1px rule and space.

## Shapes

Gently rounded and consistent: 8px for tabs, buttons, inputs, legend and dropdowns; 6px for inner rows, code blocks, selects, badges and tables; 12px only for the question box. Borders are 1px hairlines. On the graph, shape carries the kind: a table record is a rounded square, an individual named in the text a circle, a concept a diamond, a mention no identity covers a dashed circle (2px dashed Quiet Slate border).

## Components

### Header tabs
Quiet pill tabs. Body size in Slate Ink on transparent, 8px 12px padding, 8px radius; hover adds Slate Wash and Ink; the active tab is Signal Blue on Signal Blue Wash at 600. A loaded dataset carries a 7px Ok Green dot. The status at the right reads in Label size: loaded state in Ok Green 600, counts in Slate Ink. Transitions 120ms ease-out.

### Buttons
- **Primary (Ask):** Signal Blue fill, accent-text, 40px tall, 0 16px, 8px radius, Label 600, an arrow icon; hover drops opacity to 0.9; disabled is Slate Wash with Quiet Slate text.
- **Icon (close):** 36px square, transparent, Slate Ink, hover Slate Wash.
- **Text link:** Signal Blue, underlined at a 3px offset.
- **Disclosure:** Label 600 Ink with a chevron that rotates 90deg in 150ms; the count beside it in Slate Ink 400.

### Inputs / Fields
- **Search:** 40px, 1px Hairline Strong, 8px radius, search icon at left; focus swaps the stroke to Signal Blue with a 3px Signal Blue Wash halo. Results float below with the Float shadow; rows hover to Slate Wash.
- **Question box:** a 12px-radius frame (Hairline Strong) holding the textarea above an action row (variant select at 34px / 6px radius, Ask button); the frame takes the focus treatment via focus-within. When the tab's graph is not loaded, the frame is Canvas Mist and a Label-size note says so.

### Graph toolbar, legend and detail panel
The toolbar is a white row ruled off from the canvas. The legend is one opaque white row with a hairline border and 8px radius, Label size, each kind drawn as its real shape with its count in Quiet Slate; when the detail panel is open it shortens to the visible canvas and drops the line key. The detail panel is white, 420px, Float shadow, a ruled head (Lead name, Label kind), then ruled property rows (11.5em name column) and facts: mono relation type, the other end as a Signal Blue link, evidence quoted behind a 1px Hairline Strong rule.

### Graph canvas
Labels at 15px Inter under each node on an 85% opaque ground; hovered, selected, picked and cited nodes show full names. Picked nodes get a 5px Signal Blue ring, cited nodes a 4px ring; while a source is open everything else dims to 0.15 opacity (200ms). Edge width scales with the number of facts (1.5-5px).

### Answer card with inline source markers
An exchange is not boxed: question (Lead), answer (Body), a meta line, the numbered sources, and the folded steps, separated from the next by a hairline and 24px. Citation markers sit inline after the claim: a 22px outlined Signal Blue number with a 5px radius; hover or active fills it. The outcome badge is a currentColor outline at 6px radius in the status colour. Sources are rows in three columns (number in Signal Blue 700, kind, label truncated); hover Canvas Mist with a hairline, active Signal Blue Wash with a Signal Blue stroke.

### Steps table
A ruled list in fixed columns (turn 28px | tool and outcome | time 200px) so rows arrive live without the columns moving. Tool in Ink 600, outcome in Slate Ink truncated, a refused tool struck through in Bad Red. Each time cell has a 6px timing bar (model share Hairline Strong, tool share Quiet Slate) on a Slate Wash track. The newest row flashes from Signal Blue Wash (260ms); the waiting row pulses opacity. Totals sit in one Label line beneath.

### Source drawer
Covers the chat column, white with the Float shadow, sliding in from 24px right with a fade (220ms, cubic-bezier(0.16, 1, 0.3, 1)). The head pairs the Signal Blue number with the Lead title and an icon close. The body shows the id in Quiet Slate, checks as icon plus Label text (Ok Green or Warn Amber), the heading in Label 600, then the passage at Body size with the quoted span marked in Signal Blue Wash plus a 2px Signal Blue underline; table rows render as a hairline-bordered table with Canvas Mist headers.

### Motion
State transitions 120ms ease-out; chevron 150ms; graph dimming 200ms; entrances 220-260ms on cubic-bezier(0.16, 1, 0.3, 1). Everything stops under prefers-reduced-motion.

## Do's and Don'ts

### Do:
- **Do** take every colour, size, space and radius from the tokens; components never write a raw colour.
- **Do** keep Signal Blue for selected, cited, focused and the primary action only (The One Accent Rule).
- **Do** keep every text at 15px or above, body at 17px, with tabular numerals.
- **Do** tell graph kinds apart by shape and explain them in the legend; colour is never the only cue.
- **Do** separate content with 1px hairlines and space; use the Float shadow only on overlays.
- **Do** use SVG line icons (lucide) at text colour; JetBrains Mono only for machine text.

### Don't:
- **Don't** add a second accent hue or fill a surface with a status colour.
- **Don't** nest cards or box individual exchanges, steps or sources.
- **Don't** use bright saturated tones in the dark theme.
- **Don't** add decoration, hero areas or anything that is not graph, answer, step or source.
