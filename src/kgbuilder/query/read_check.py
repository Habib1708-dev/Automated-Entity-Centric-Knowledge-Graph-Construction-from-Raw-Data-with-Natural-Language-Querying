"""The `read_check` primitive's reader (R74): does this candidate's text support a statement? A yes counts
only with a quote that code finds in the chunk it names.

Role in the pipeline: plan_run.py calls it once per candidate of a `read_check` step, with the candidate's
own chunks and, for a claim, the claim itself with its evidence (R85); the verified candidates go on to
the next step (a count, a list). Counts over text are made this way ("verified per item", task file
section 1): the graph finds the candidates, the reader checks each one, code checks every quote and counts.
Design: one domain-neutral prompt, one small structured reply per candidate. The LLM proposes, code
decides: `verify` accepts a yes only when the quote, compared under `norm`, is in the cited chunk the
reader was shown, and for a claim holds the claim's evidence or lies inside it. No chunk, no call.
Not here: choosing candidates or chunks (plan_run.py), the final answer's reader (reader.py).
"""

from pydantic import BaseModel, Field

from ..core.text import norm
from ..llm.base import LLMClient
from .answers import ShownChunk

# The check's prompt. Rule by rule: "only what the text states" keeps the check grounded; the negation rule is
# the reason read_check exists (a sentence saying the opposite, or only that it might happen, is a no); the
# quote rule is what code verifies, in the wording the reader and extraction prompts use. `claim_rule` and
# `candidate` are empty unless the candidate is a claim, so a record's or a chunk's check reads exactly as
# before R85.
PROMPT = """Decide whether the text below supports a statement.

Rules:
- Answer `supported` true only when the text itself states it. A sentence saying the opposite, saying it
  did not happen, or saying only that it might happen, is not support.
- When supported, give the id of the chunk that states it and ONE contiguous quote copied verbatim from it.{claim_rule}

<statement>{statement}</statement>
{candidate}
<chunks>
{chunks}
</chunks>"""  # noqa: E501 (the rule line above ends in the claim rule's placeholder)

# R85: a claim candidate is shown with its own evidence, because the claims of one chunk share their text:
# without it every claim of a chunk that states the statement was verified, whatever the claim said (R83:
# G24, 7 claims of one sentence, among them "mechanical seal COMPONENT_OF HP40-2291" for "who replaced the
# seal"). Code checks the quote against the claim's evidence (`verify`); whether this claim is what states
# the statement is the model's reading.
CLAIM_RULE = """
- The candidate is one claim taken from the text. Answer `supported` true only when this claim, read in its
  text, states the statement; another claim of the same text stating it is not enough. Quote the
  candidate's evidence or a passage that contains it."""

CANDIDATE = """<candidate>
claim: {claim}
evidence: {evidence}
</candidate>
"""


class CheckReply(BaseModel):
    supported: bool = Field(description="Whether the text states the statement.")
    chunk_id: str | None = Field(
        default=None, description="The chunk that states it, exactly as given in its tag."
    )
    quote: str | None = Field(
        default=None, description="One contiguous passage copied verbatim from that chunk."
    )


class CheckCandidate(BaseModel):
    """A claim checked by read_check (R85): what it says, as its own mentions word it, and the evidence it was
    extracted from."""

    claim: str  # "subject PREDICATE object"
    evidence: str


class CheckResult(BaseModel):
    """One candidate's check: verified only when supported and its quote is in the named shown chunk."""

    verified: bool
    chunk_id: str | None = None
    quote: str | None = None
    called: bool = False  # False when the candidate had no text, so no model was asked


def build_prompt(statement: str, chunks: list[ShownChunk], candidate: CheckCandidate | None = None) -> str:
    shown = "\n".join(f'<chunk id="{c.chunk_id}" document="{c.context}">\n{c.text}\n</chunk>' for c in chunks)
    return PROMPT.format(
        statement=statement,
        chunks=shown,
        claim_rule=CLAIM_RULE if candidate else "",
        candidate=CANDIDATE.format(claim=candidate.claim, evidence=candidate.evidence) if candidate else "",
    )


def verify(reply: CheckReply, chunks: list[ShownChunk], candidate: CheckCandidate | None = None) -> bool:
    """A yes stands only when its quote, under `norm`, is in the chunk it names, which the reader was
    shown, and, for a claim, holds the claim's evidence or lies inside it (R85): the support must be where
    this claim was read, not another sentence of its chunk. A claim without evidence (a derived one) is
    checked by its chunk alone."""
    if not reply.supported or not reply.quote or not norm(reply.quote):
        return False
    text = next((c.text for c in chunks if c.chunk_id == reply.chunk_id), None)
    if text is None or norm(reply.quote) not in norm(text):
        return False
    if candidate is None:
        return True
    quote, evidence = norm(reply.quote), norm(candidate.evidence)
    return evidence in quote or quote in evidence


class ReadChecker:
    """Checks one candidate with one model call."""

    def __init__(self, llm: LLMClient, model: str, temperature: float = 0.0):
        self._llm = llm
        self._model = model
        self._temperature = temperature

    def check(
        self, statement: str, chunks: list[ShownChunk], candidate: CheckCandidate | None = None
    ) -> CheckResult:
        """The verified result, or not verified without a call when there is no text. `candidate` is the
        claim being checked, when the candidate is one (R85).

        Raises `LLMResponseError` when the model keeps failing (llm/retry.py)."""
        if not chunks:
            return CheckResult(verified=False)
        reply = self._llm.generate(
            build_prompt(statement, chunks, candidate),
            CheckReply,
            model=self._model,
            temperature=self._temperature,
        )
        ok = verify(reply, chunks, candidate)
        return CheckResult(
            verified=ok,
            chunk_id=reply.chunk_id if ok else None,
            quote=reply.quote if ok else None,
            called=True,
        )
