# How the builder represents and identifies things (designs of R63-R230, drawn for the thesis in R232)

Method diagrams of Chapter 4 of the thesis. Drawn by `figures/scripts/r63_r230_diagrams.py`; they hold no
measured number. Thresholds shown are the code's settings.

## Diag. 1: A fact stored on a shared node leaks to everything linked to it; in the anchor graph a shared node only finds chunks, which are read

**Files:** [r230_diag1_leak_and_witness_rule.svg](r230_diag1_leak_and_witness_rule.svg), [.png](r230_diag1_leak_and_witness_rule.png), [.pdf](r230_diag1_leak_and_witness_rule.pdf)

**What it shows:** the leak found by the path-truth audit (R63) on the furniture data, and the same question in
the anchor graph (R88 on). On the left, the defect stated in the Helsingborg Dresser's review is an edge from
the shared "drawer rails" node, so the path from the Linköping Bed reads as a defect of the bed. On the right,
the shared concept only leads to the two chunks that mention it; each chunk leads to the product it is about,
and the reader rejects the bed's chunk ("quality drawer slides rated for 25kg loads").

**How to read it:** square: a record from a table; rounded: a node made from text; blue: a chunk. The dashed
orange arrow is the false reading of the path.

**Takeaway:** the witness rule of the anchor graph: a shared node helps to find chunks; it never proves a fact.

**Source:** the anchor-graph direction document (2026-10-06, section 3.1) and R63's audit.

**Caveats:** simplified: the furniture plan connects products to parts through assemblies and components.

## Diag. 2: The anchor graph: text is linked to the records and things it mentions; every link carries its reason, and the claims are kept aside

**Files:** [r230_diag2_anchor_graph_layers.svg](r230_diag2_anchor_graph_layers.svg), [.png](r230_diag2_anchor_graph_layers.png), [.pdf](r230_diag2_anchor_graph_layers.pdf)

**What it shows:** the node labels and edges of the final graph: the lexical layer (documents, chunks), the
mention layer, the identity targets (records, individuals, concepts) reached by `REFERS_TO`, the record layer
with the plan's typed relations, the `ABOUT` attachment, and the claim layer (`:Observation`) that is stored
but not used for navigation; the walks W1-W5 of the navigation contract.

**How to read it:** as diag. 1; the dashed frame holds what is stored but not navigated.

**Takeaway:** every connection between text and records is a typed edge with its reason and evidence, and
every multi-hop walk passes through a record relation or a chunk.

**Source:** `text/lexical.py`, `text/subject_graph.py`, `resolution/identity_graph.py`, `resolution/linking.py`,
`anchor/navigation.py` at R230.

**Caveats:** retrieval cards (`:RetrievalUnit`), built for the query engine by `kg index`, are left out.

## Diag. 3: Identity: rules decide what they can; a model is asked only about near misses, and its answer counts only if its quotes are found

**Files:** [r230_diag3_identity_resolution.svg](r230_diag3_identity_resolution.svg), [.png](r230_diag3_identity_resolution.png), [.pdf](r230_diag3_identity_resolution.pdf)

**What it shows:** the `resolve` stage per class of thing. Records: the key, the name in the document's scope,
a key or key attribute next to the name, then a model's choice among near misses that code verifies.
Individuals: nominated pairs adjudicated by a model whose quotes code must find. Concepts: candidates by
spelling or mutual nearest neighbours, four guards, merging by spelling or by the model. All outcomes are
written as `REFERS_TO` edges.

**How to read it:** orange rounded: the model proposes; square: code decides; arrows run left to right within a
class.

**Takeaway:** a model's identity decision never reaches the graph unchecked, and identity is an edge that can be
removed, not a merged node.

**Source:** `resolution/records.py`, `record_choice.py`, `individuals.py`, `concepts.py`, `guards.py`,
`blocking.py`, `identity_graph.py`; thresholds from `config.py` and `presets.yaml` at R230.

**Caveats:** the records lane condenses five rule tiers into three boxes (key; name; key, attribute or variant
attribute in a sentence); the concept adjudicator has no quote check.
