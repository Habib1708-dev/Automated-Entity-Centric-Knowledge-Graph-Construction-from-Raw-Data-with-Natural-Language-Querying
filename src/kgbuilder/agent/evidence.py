"""The answer-time evidence ledger (R193): before a set answer stands, every passage the agent read is judged
on its own against the question, and a passage stating a member the answer left out is sent back whole.

Role in the pipeline: the loop (loop.py) asks the ledger about the answer it is about to finish with, in the
experimental variant `agent_evidence` only (plan R193-R195, pipeline/qa_systems.py); the agent itself has no
ledger. R192 found experiment A's failures were mostly passages the agent had read and left out ("performed
a forced landing" read beside "made a forced landing", one kept), not passages it never found; and HS01's
closing answer dropped what the semantic filter had kept. So the ledger checks the answer against what was
read, once, after the model has chosen.
Design: the LLM proposes, code decides. Code takes every chunk the loop's ledger read and fetches each whole
from the store: a tool may have shown only an excerpt (find_passages shows sentences), and the user asked
that the ledger give back entire results, never compacted or cut, so no candidate cap applies. The model
reads them in batches under short ids (semantic_filter.render) and says for each whether it states something
the question asks for, about which thing, with a quote; code keeps a yes only when its quote is verbatim in
the passage (as semantic_filter.verify), keeps its thing only when the passage or its document's name
writes it, and calls a kept row covered when the answer cites its passage or names its thing. The rows left
uncovered go back once, each passage in full, with the tools closed; the model's reply is the answer, so it
may add the members or keep its answer, and nothing is added by code. Strategy pattern: the loop holds one
ledger, or none.
Not here: the loop's send-back accounting (loop.py), the semantic filter tool (semantic_filter.py).
"""

from pydantic import BaseModel, Field

from ..core.text import norm
from ..llm.base import LLMClient
from ..query.graph_store import GraphStore, StoredChunk
from .prompt import AgentStep
from .semantic_filter import FilterOptions, render

# The judge's prompt. Rule by rule: "something the question asks for" turns the question itself into the
# criterion, as no statement is written at answer time; "in any wording: other words for the same idea count"
# is R192's failure (literal reading), said once; the negation rule is the semantic filter's (read_check's);
# `thing` names the member the passage is about, which code checks against the passage and its document's
# name, so a thing the model invents never covers or uncovers a row; the quote rule is the verbatim-evidence
# wording code verifies; one verdict per passage lets code count those given none.
PROMPT = """Decide, for each passage below, whether it states something the question asks for: that some \
thing belongs in the answer.

Rules:
- `states` is true only when the passage itself states it, in any wording: other words for the same idea
  count. A passage saying the opposite, saying it did not happen, or saying only that it might happen, does
  not state it.
- `thing`: the thing it states it about, by its name as the passage or its document's name writes it.
- When it states it, give ONE contiguous quote copied verbatim from the passage that states it.
- Give one verdict for every passage, by the id in its tag.

<question>{question}</question>

<passages>
{passages}
</passages>"""

# Sent back, once, with the tools closed, as the extra text of the closing prompt (loop.py). Each passage is
# given whole with the thing the judge named, so the model decides from the full text, not from a quote; the
# way out is the same answer again, so a passage judged wrongly costs one reply and adds nothing by itself.
SEND_BACK = """Each passage below is among the results you read. Read again on its own, each was judged \
to state something the question asks for, about the thing named in its tag, and your answer neither names \
that thing nor cites the passage. For each, add the thing to `entities` and cite the passage with ONE \
contiguous quote copied verbatim from its text, unless the passage does not answer the question; then send \
your answer.

<passages>
{passages}
</passages>"""


class EvidenceVerdict(BaseModel):
    id: str = Field(description="The passage's id, exactly as in its tag.")
    states: bool = Field(description="Whether the passage itself states something the question asks for.")
    thing: str = Field(default="", description="When it does: the thing it states it about, by its name.")
    quote: str = Field(
        default="", description="When it does: ONE contiguous quote copied verbatim from the passage."
    )


class EvidenceReply(BaseModel):
    verdicts: list[EvidenceVerdict] = Field(description="One verdict for every passage shown.")


class EvidenceRow(BaseModel):
    """A passage judged to state something the question asks for: whole, with its verified quote and the
    thing it is about ("" when the passage and its document's name do not write the name given)."""

    chunk_id: str
    context: str
    text: str
    thing: str
    quote: str


class EvidenceReview(BaseModel):
    """What the ledger found for one answer: the passages read, the verified rows, the rows the answer
    leaves out, and the verdicts set aside."""

    read: int = 0
    rows: list[EvidenceRow] = []
    left_out: list[EvidenceRow] = []
    unverified: int = 0  # a yes whose quote is not in its passage
    unanswered: int = 0  # passages no verdict named


class EvidenceLedger:
    """Judges the passages an answer was made from and finds the members it leaves out; `store` gives each
    passage whole, `options` the judging model and batch."""

    def __init__(self, llm: LLMClient, options: FilterOptions, store: GraphStore):
        self._llm = llm
        self._options = options
        self._store = store

    def applies(self, step: AgentStep) -> bool:
        """Whether `step` is an answer the ledger checks: one declaring a set of things."""
        return step.action == "answer" and step.asks == "things"

    def review(self, question: str, step: AgentStep, read: list[str], names: list[str]) -> EvidenceReview:
        """The rows of the `read` chunk ids for `question`, and those neither cited by `step` nor named by
        `names` (the answer's entities as names). Raises `LLMResponseError` when the model keeps failing."""
        chunks = self._store.chunks(read)
        review = EvidenceReview(read=len(chunks))
        size = self._options.batch
        for start in range(0, len(chunks), size):
            batch = chunks[start : start + size]
            part = verify(self._ask(question, batch), batch)
            review.rows += part.rows
            review.unverified += part.unverified
            review.unanswered += part.unanswered
        cited = {c.id for c in step.citations}
        review.left_out = [r for r in review.rows if not covered(r, cited, names)]
        return review

    def message(self, review: EvidenceReview) -> str:
        """`SEND_BACK` with every left-out passage whole."""
        return SEND_BACK.format(passages=whole(review.left_out))

    def _ask(self, question: str, batch: list[StoredChunk]) -> EvidenceReply:
        o = self._options
        prompt = PROMPT.format(question=question, passages=render(batch))
        return self._llm.generate(
            prompt, EvidenceReply, model=o.model, temperature=o.temperature, thinking=o.thinking
        )


def verify(reply: EvidenceReply, batch: list[StoredChunk]) -> EvidenceReview:
    """The batch's rows: a yes stands only when its quote, under `norm`, is in the passage its id names, and
    its thing only when the passage or its document's name holds it. An unknown id is ignored, a repeated
    id's first verdict counts, a passage with no verdict is counted as unanswered."""
    ids = {f"P{n}": c for n, c in enumerate(batch, 1)}
    first: dict[str, EvidenceVerdict] = {}
    for verdict in reply.verdicts:
        if verdict.id.strip() in ids:
            first.setdefault(verdict.id.strip(), verdict)
    review = EvidenceReview(unanswered=len(ids) - len(first))
    for key, chunk in ids.items():
        verdict = first.get(key)
        if verdict is None or not verdict.states:
            continue
        if not (norm(verdict.quote) and norm(verdict.quote) in norm(chunk.text)):
            review.unverified += 1
            continue
        written = norm(f"{chunk.context} {chunk.text}")
        thing = verdict.thing.strip() if norm(verdict.thing) and norm(verdict.thing) in written else ""
        review.rows.append(EvidenceRow(chunk_id=chunk.chunk_id, context=chunk.context, text=chunk.text,
                                       thing=thing, quote=verdict.quote))  # fmt: skip
    return review


def covered(row: EvidenceRow, cited: set[str], names: list[str]) -> bool:
    """Whether the answer holds `row`: it cites the passage, or names its thing (one name within the other
    as whole words under `norm`, so "Godrevy" covers "Godrevy Lighthouse" and "it" covers no "kit"). A row
    with no checked thing is covered only by its citation."""
    if row.chunk_id in cited:
        return True
    thing = f" {norm(row.thing)} "
    spaced = [f" {norm(n)} " for n in names if norm(n)]
    return bool(thing.strip()) and any(n in thing or thing in n for n in spaced)


def whole(rows: list[EvidenceRow]) -> str:
    """The rows as the send-back shows them: each passage's full text, its document and its thing."""
    return "\n".join(
        f'<passage id="{r.chunk_id}" document="{r.context}" thing="{r.thing}">\n{r.text}\n</passage>'
        for r in rows
    )
