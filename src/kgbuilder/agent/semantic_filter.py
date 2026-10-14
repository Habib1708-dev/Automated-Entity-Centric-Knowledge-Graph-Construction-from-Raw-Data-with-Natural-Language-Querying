"""The agent's semantic filter (R184): every passage that states an idea, in any wording, found by reading
each candidate passage, not by its words.

Role in the pipeline: a tool of the experimental variant `agent_semfilter` (plan R183-R191,
pipeline/qa_systems.py), beside the agent's own tools; the agent itself does not have it. R180-R182 left the
failures whose passages word the idea otherwise than any searched word ("performed a forced landing" for
"forced landing", "dynamic rollover" for "rolled over"): `find_passages` matches words, `search_text` ranks
only the best few, so no tool listed every passage that states something.
Design: the LLM proposes, code decides. Code gathers the candidates (the passages nearest the statement in
meaning, those its words score highest, and every passage holding a word the agent gives, fused by reciprocal
rank fusion and cut at a cap), the model reads them in batches and says for each whether it states the
statement, with a quote, and code keeps a passage only when its quote is verbatim in it (as
`query/read_check.py` verifies one candidate), then counts the passages and their documents. The cap and the
judging are said in the result's note, so a count that may miss passages beyond the cap says so.
Not here: the word and meaning searches the agent already has (text_tools.py), the loop (loop.py).
"""

from pydantic import BaseModel, Field

from ..core.identity import document_of
from ..core.text import norm
from ..hybrid.fusion import rrf
from ..hybrid.retrievers import ChunkLexical
from ..hybrid.unit_store import UnitStore
from ..llm.base import Embedder, LLMClient, ThinkingLevel
from ..query.graph_store import GraphStore, StoredChunk
from .results import ChunkText, FilteredResult
from .text_reader import TextReader
from .text_tools import form_pattern

# The filter's prompt. Rule by rule: "the passage itself" keeps each verdict to the passage shown, not to what
# its document says elsewhere; the negation rule is read_check's (a passage saying the opposite, or only that
# it might happen, does not state it), so the agent words a statement that should also count a denial as
# such ("mentions X, present or not"); "in any wording" is why the tool exists; the quote rule is the
# verbatim-evidence wording code verifies (`judge`); one verdict per passage lets code count those left out.
PROMPT = """Decide, for each passage below, whether it states the statement.

Rules:
- `states` is true only when the passage itself states it, in any wording. A passage saying the opposite,
  saying it did not happen, or saying only that it might happen, does not state it.
- When it states it, give ONE contiguous quote copied verbatim from the passage that states it.
- Give one verdict for every passage, by the id in its tag.

<statement>{statement}</statement>

<passages>
{passages}
</passages>"""


class PassageVerdict(BaseModel):
    id: str = Field(description="The passage's id, exactly as in its tag.")
    states: bool = Field(description="Whether the passage itself states the statement.")
    quote: str = Field(
        default="", description="When it does: ONE contiguous quote copied verbatim from the passage."
    )


class FilterReply(BaseModel):
    verdicts: list[PassageVerdict] = Field(description="One verdict for every passage shown.")


class FilterOptions(BaseModel):
    """The model the passages are judged with, and how many it reads per call."""

    model: str
    temperature: float
    thinking: ThinkingLevel  # sent with every call, whatever level the agent's own model thinks at
    batch: int = Field(ge=1)


class Judged(BaseModel):
    """What the model's verdicts left after code's check: each kept passage with its verified quote, and
    the verdicts set aside."""

    kept: list[tuple[StoredChunk, str]] = []
    unverified: int = 0  # a yes whose quote is not in its passage
    unanswered: int = 0  # passages no verdict named; an unknown or repeated id is not counted twice


class PassageJudge:
    """Asks the model about passages in batches and keeps the verified yeses, in the candidates' order."""

    def __init__(self, llm: LLMClient, options: FilterOptions):
        self._llm = llm
        self._options = options

    def judge(self, statement: str, chunks: list[StoredChunk]) -> Judged:
        """The verified passages of `chunks` for `statement`, one model call per batch. Raises
        `LLMResponseError` when the model keeps failing (llm/retry.py)."""
        judged = Judged()
        size = self._options.batch
        for start in range(0, len(chunks), size):
            batch = chunks[start : start + size]
            part = verify(self._ask(statement, batch), batch)
            judged.kept += part.kept
            judged.unverified += part.unverified
            judged.unanswered += part.unanswered
        return judged

    def _ask(self, statement: str, batch: list[StoredChunk]) -> FilterReply:
        o = self._options
        prompt = PROMPT.format(statement=statement, passages=render(batch))
        return self._llm.generate(
            prompt, FilterReply, model=o.model, temperature=o.temperature, thinking=o.thinking
        )


def render(batch: list[StoredChunk]) -> str:
    """The passages as the model reads them: short ids P1, P2, ... (the order code maps them back by), each
    with its document's name, as read_check shows a chunk."""
    return "\n".join(
        f'<passage id="P{n}" document="{c.context}">\n{c.text}\n</passage>' for n, c in enumerate(batch, 1)
    )


def verify(reply: FilterReply, batch: list[StoredChunk]) -> Judged:
    """The batch's verified passages: a yes stands only when its quote, under `norm`, is in the passage its
    id names. An id no passage has is ignored, a repeated id's first verdict counts, and a passage with no
    verdict is counted as unanswered (and not kept)."""
    ids = {f"P{n}": c for n, c in enumerate(batch, 1)}
    first: dict[str, PassageVerdict] = {}
    for verdict in reply.verdicts:
        if verdict.id.strip() in ids:
            first.setdefault(verdict.id.strip(), verdict)
    judged = Judged(unanswered=len(ids) - len(first))
    for key, chunk in ids.items():
        verdict = first.get(key)
        if verdict is None or not verdict.states:
            continue
        if norm(verdict.quote) and norm(verdict.quote) in norm(chunk.text):
            judged.kept.append((chunk, verdict.quote))
        else:
            judged.unverified += 1
    return judged


class FilterArguments(BaseModel):
    statement: str = Field(description="What a passage must state, as one sentence.")
    words: list[str] = Field(
        default=[],
        max_length=5,
        description=(
            "Optional words or short phrases: every passage holding one of them, in any of its forms, is "
            "read too, besides the passages nearest the statement."
        ),
    )


class FilterPassages:
    """`filter_passages`: the candidate passages for a statement read one by one; those stating it kept,
    counted with their documents, at most `limit` shown."""

    name = "filter_passages"
    description = (
        "Find every passage that states something, in any wording: the passages nearest the statement in "
        "meaning or by its words, and every passage holding one of the given words, are each read and kept "
        "only when they state it, with a quote. Use it to count or list everything the text says about an "
        "idea worded in different ways. Slower than the searches."
    )
    arguments = FilterArguments

    def __init__(
        self, judge: PassageJudge, store: GraphStore, units: UnitStore, text: TextReader, embedder: Embedder,
        rrf_k: int, candidates: int, limit: int,
    ):  # fmt: skip
        self._judge = judge
        self._store = store
        self._lexical = ChunkLexical(units)
        self._text = text
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._candidates = candidates
        self._limit = limit

    def run(self, arguments: FilterArguments) -> FilteredResult:
        ids, found = self._gather(arguments)
        judged = self._judge.judge(arguments.statement, self._store.chunks(ids))
        documents = list(dict.fromkeys(document_of(c.chunk_id) for c, _ in judged.kept))
        shown = [ChunkText(chunk_id=c.chunk_id, context=c.context, text=q) for c, q in judged.kept]
        return FilteredResult(
            chunks=shown[: self._limit], total=len(judged.kept), documents=documents,
            document_count=len(documents), considered=len(ids), unverified=judged.unverified,
            note=self._note(arguments, len(shown), len(ids), found, judged),
        )  # fmt: skip

    def _gather(self, arguments: FilterArguments) -> tuple[list[str], int]:
        """The candidate ids, fused best first and cut at the cap, and how many there were before the cut."""
        vector = self._embedder.embed([arguments.statement])[0]
        lists = {
            "dense": self._store.nearest_chunks(vector, self._candidates),
            "lexical": self._lexical.retrieve(arguments.statement, vector, self._candidates).chunks,
        }
        for word in (w.strip().lower() for w in arguments.words if w.strip()):
            matched, _ = self._text.matching([form_pattern(word)], self._candidates)
            lists[f"word:{word}"] = [c.chunk_id for c in matched]
        # rank fusion orders them so that a cut keeps the best of every list, with search_text's constant
        fused = rrf(lists, self._rrf_k)
        return fused[: self._candidates], len(fused)

    def _note(self, arguments: FilterArguments, kept: int, read: int, found: int, judged: Judged) -> str:
        """Said by code: how many passages state it, what was read, and what may be missing."""
        how = "the passages nearest the statement in meaning or by its words"
        if arguments.words:
            how += " and every passage holding " + ", ".join(repr(w) for w in arguments.words)
        parts = [f"{kept} of {read} passages read state it; they were {how}."]
        if found > read:
            parts.append(f"{found - read} more candidates were past the cap and not read.")
        if kept > self._limit:
            parts.append(f"{self._limit} of the {kept} are shown.")
        if judged.unverified or judged.unanswered:
            left = (
                f"{judged.unverified} whose quote is not in the passage, {judged.unanswered} given no verdict"
            )
            parts.append(f"Left out: {left}.")
        return " ".join(parts)
