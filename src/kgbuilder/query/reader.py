"""The reader: the model answers a question from the chunks it is given, and cites them.

Role in the pipeline: the last step of every question-answering system (systems.py): the graph route and
the vector-only baseline give it their chosen chunks, so the two differ only in how they choose.
Design: one prompt, one structured reply. The LLM proposes an answer with citations; code checks every
citation afterwards (validation/qa.py: the quote must be in the cited chunk the reader was shown). With no
chunk to read, no model is asked: an answer from nothing would be the model's own knowledge. The rule
that puts the answer in the field its form calls for (`FORM_RULE`) and the answer fields' descriptions
(`ANSWER_FIELDS`) live here and are shared with the agent's prompts (R147), so the two cannot drift; the
reader has no loop to send an answer back in, so for it the rule is prompt only, its form checked by the
exact-match scoring.
Not here: choosing the chunks (systems.py), scoring the answer (validation/qa.py).
"""

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from .answers import ShownChunk

# The answer-form rule, shared by the reader's prompt ("the chunks") and the agent's prompts ("the tools"),
# `{source}` filled in at import. Sets and numbers are scored by code, so they must come as a list or a
# number. The rule before R147 keyed on "which things" and sent everything else to `text`: the model read
# it as plural sets only, and R145 traced six agent failures to it ("Which" a single thing, "Who", "Where"
# and "What is the ... of" answered in text) and GN07 ("Where", answered in text) on every system.
# - the number clause comes first and covers any one number: a measure asked with "what" or "how deep" is a
#   number, which a clause on "values" before it would turn into entities; "a bound or an estimate", because
#   a number stated with a qualifier was answered in text (R145, H36);
# - the entities clause takes one thing as well as several, and a value that is not a number, by its name or
#   value alone (an answer with a qualifier in brackets is not the name);
# - one rule for dates, the same in `ANSWER_FIELDS` and the agent's `asks`: a year alone is a number, a
#   full date is text, never an entity ("when" and "which year" would otherwise point to two fields);
# - `text` may add to the other forms, never replace them: the gold's form decides which field is scored
#   (validation/qa.py), so an answer in both forms is right either way.
FORM_RULE = """- Put the answer in the field its form calls for:
  - one number (a count, an amount, a measurement, a year; how many, how much, how far, which year): give
    it in `number`, also when it is stated as a bound or an estimate;
  - one or several named things, or a value of a thing that is not a number or a date (who, which, where,
    what is the name or the value of ...): list them in `entities`, each once, by its name or value
    alone, as {source} or the question give it;
  - anything else (what was said or done, why, whether, when, a full date): answer in a short `text`.
  `text` may add to `entities` or `number`; it never replaces them."""

# The answer fields' descriptions, shared with the agent's reply model (R147): the response schema is prompt
# text too, and before R147 its "the things that answer a question asking which things" kept the old form
# rule alive next to the new one.
ANSWER_FIELDS = {
    "entities": (
        "the named things, or the values that are neither numbers nor dates, the question asks for, each "
        "once by its name alone; [] if there are none"
    ),
    "number": "the one number the question asks for: a count, an amount, a measurement or a year",
    "text": (
        "a short answer in words: the whole answer to a question asking for anything else, or words added "
        "to `entities` or `number`"
    ),
}

# The reader's prompt. Domain-neutral on purpose (prompt-engineering skill): the answer's form follows the
# question's wording, not a list of the benchmark's question types. Rule by rule:
# - "only what the chunks state": the system is judged on grounded answers; knowledge from elsewhere would
#   hide a retrieval failure behind a lucky guess;
# - the form rule (`FORM_RULE` above), then what an empty list means;
# - the document line: a chunk from the middle of a document often never names its subject;
# - the citation rule keeps the verbatim wording the extraction prompt uses ("ONE contiguous quote copied
#   verbatim"), which code then checks.
# Built here, not in `build_prompt`: `prompt_version` hashes this module string. The rendered rule holds no
# braces, so `build_prompt`'s `.format` sees only the question and the chunks.
PROMPT = (
    """You answer a question using only the text chunks below.

Rules:
- Use only what the chunks state. When they do not answer the question, say so in `text` and leave
  `entities` and `number` empty.
"""
    + FORM_RULE.format(source="the chunks")
    + """
- An empty `entities` list means the chunks name none.
- The `document` of a chunk is the name of what its document is about; use it when the chunk itself does
  not repeat that name.
- Cite every chunk your answer relies on: its id and ONE contiguous quote copied verbatim from it.

<question>{question}</question>

<chunks>
{chunks}
</chunks>"""
)

# The reply when no chunk reached the reader; code writes it, no model is asked.
NOTHING_TO_READ = "No text was retrieved for this question."


def _sentence(description: str) -> str:
    """A shared field description as the reader's schema writes it: a capital first letter and a stop."""
    return f"{description[:1].upper()}{description[1:]}."


class ReaderCitation(BaseModel):
    chunk_id: str = Field(description="The id of a chunk the answer relies on, exactly as given in its tag.")
    quote: str = Field(description="One contiguous passage copied verbatim from that chunk.")


class ReaderAnswer(BaseModel):
    """The model's answer: exactly the forms the question calls for, and the chunks it rests on."""

    entities: list[str] | None = Field(default=None, description=_sentence(ANSWER_FIELDS["entities"]))
    number: float | None = Field(default=None, description=_sentence(ANSWER_FIELDS["number"]))
    text: str | None = Field(default=None, description=_sentence(ANSWER_FIELDS["text"]))
    citations: list[ReaderCitation] = Field(default=[], description="The chunks the answer relies on.")


def build_prompt(question: str, chunks: list[ShownChunk]) -> str:
    """The reader's prompt for `question` over `chunks`, in the order given (best first)."""
    shown = "\n".join(f'<chunk id="{c.chunk_id}" document="{c.context}">\n{c.text}\n</chunk>' for c in chunks)
    return PROMPT.format(question=question, chunks=shown)


class Reader:
    """Answers questions from chunks with one model call each."""

    def __init__(self, llm: LLMClient, model: str, temperature: float = 0.0):
        self._llm = llm
        self._model = model
        self._temperature = temperature

    def read(self, question: str, chunks: list[ShownChunk]) -> ReaderAnswer:
        """The model's answer, or `NOTHING_TO_READ` without a call when there is no chunk.

        Raises `LLMResponseError` when the model keeps failing (llm/retry.py).
        """
        if not chunks:
            return ReaderAnswer(text=NOTHING_TO_READ)
        return self._llm.generate(
            build_prompt(question, chunks), ReaderAnswer, model=self._model, temperature=self._temperature
        )
