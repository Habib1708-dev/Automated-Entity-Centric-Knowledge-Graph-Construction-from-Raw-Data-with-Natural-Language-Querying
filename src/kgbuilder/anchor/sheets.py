"""The blind judging sheets of the anchor-graph criteria C3, C4 and C6 (R93): what the judge sees, and the
code side it must not see.

Role in the pipeline: written by `kg anchor-sheets` (built in anchor/sheet_builder.py from a build's offline
snapshot and the target nodes R90 placed); the judge (Claude in the session) answers each item into a verdict
file (`validation/anchor_verdicts.py`), and `anchor/judged.py` scores it.
  C3 identity: a node with more than one mention ("are these one thing?"), and an R87 split group ("are
     these nodes one thing?");
  C4 record linking: a mention and a record ("does this mention refer to this record?");
  C6 purity: a (start, chunk) pair ("does this chunk concern this start?").
Design: an item shows only source text and source data: the mention's sentence and chunk, the record's
staged cells and plan relations. What the graph or the code says about the item (the flags of R87, the edge's
rule and score, which arm reaches a pair, whether a link exists) is a `CodeItem` of the code side, which
scoring reads and the judge never does. Chunk texts are listed once per sheet and items name them by id.
Not here: building the sheets (sheet_builder.py), verdicts and scores.
"""

from pydantic import BaseModel


class SheetChunk(BaseModel):
    document: str  # the document id (its path in the dataset)
    heading: str  # the document's first heading or title, as the chunker carries it
    text: str


class MentionView(BaseModel):
    id: str
    name: str  # as the text writes it
    document: str
    chunks: list[str]
    sentence: str  # the first sentence of its chunks holding the name; "" when none does


class RecordView(BaseModel):
    ref: str  # "<label>:<key>"
    label: str
    key: str
    name: str | None
    cells: dict[str, str]  # the staged row's values, as the importer kept them
    relations: list[str]  # the plan's relations one hop away, e.g. "PART_OF -> Product:P-1 (Desk Lamp)"


class NodeView(BaseModel):
    id: str
    kind: str  # record, individual or concept
    type: str
    name: str
    record: RecordView | None = None
    mentions: list[MentionView] = []


class MergeItem(BaseModel):
    id: str
    node: NodeView


class SplitItem(BaseModel):
    id: str
    type: str
    name: str
    nodes: list[NodeView]


class LinkItem(BaseModel):
    id: str
    mention: MentionView
    record: RecordView
    same_name: list[RecordView]  # other records of the same name, so a verdict can name the right one


class PurityItem(BaseModel):
    id: str
    start: NodeView
    chunk: str


class SheetHeader(BaseModel):
    dataset: str
    build: str
    snapshot_hash: str
    question: str


class C3Sheet(SheetHeader):
    criterion: str = "C3"
    split_question: str
    merges: list[MergeItem]
    splits: list[SplitItem]
    chunks: dict[str, SheetChunk]


class C4Sheet(SheetHeader):
    criterion: str = "C4"
    links: list[LinkItem]
    chunks: dict[str, SheetChunk]


class C6Sheet(SheetHeader):
    criterion: str = "C6"
    pairs: list[PurityItem]
    chunks: dict[str, SheetChunk]


class CodeItem(BaseModel):
    """What code knows about an item, hidden from the judge."""

    id: str
    node: str  # merge: the node; split: the group's nodes, comma-joined; link: the record; purity: the start
    kind: str  # merge: individual | concept; split: "split"; link: "link" | "unlinked"; purity: start kind
    flags: list[str] = []
    arms: list[str] = []  # purity: the arms whose W2 reaches the pair
    edge: str = ""  # link: the REFERS_TO rule and score as the graph holds them


class CodeSide(BaseModel):
    criterion: str
    items: list[CodeItem]


class JudgingSheets(BaseModel):
    c3: C3Sheet
    c4: C4Sheet
    c6: C6Sheet
    code: dict[str, CodeSide]  # "C3", "C4", "C6"
