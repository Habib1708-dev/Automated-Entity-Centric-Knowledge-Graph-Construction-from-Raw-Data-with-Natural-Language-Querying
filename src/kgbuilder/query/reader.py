"""The reader: the model answers a question from the chunks it is given, and cites them.

Role in the pipeline: the last step of every question-answering system (systems.py): the graph route and
the vector-only baseline give it their chosen chunks, so the two differ only in how they choose.
Design: one prompt, one structured reply. The LLM proposes an answer with citations; code checks every
citation afterwards (validation/qa.py: the quote must be in the cited chunk the reader was shown). With no
chunk to read, no model is asked: an answer from nothing would be the model's own knowledge.
Not here: choosing the chunks (systems.py), scoring the answer (validation/qa.py).
"""

from pydantic import BaseModel, Field

from ..llm.base import LLMClient
from .answers import ShownChunk

# The reader's prompt. Domain-neutral on purpose (prompt-engineering skill): the answer's form follows the
# question's wording, not a list of the benchmark's question types. Rule by rule:
# - "only what the chunks state": the system is judged on grounded answers; knowledge from elsewhere would
#   hide a retrieval failure behind a lucky guess;
# - the form rule: sets and numbers are scored by code, so they must come as a list or a number;
# - the document line: a chunk from the middle of a document often never names its subject;
# - the citation rule keeps the verbatim wording the extraction prompt uses ("ONE contiguous quote copied
#   verbatim"), which code then checks.
PROMPT = """You answer a question using only the text chunks below.

Rules:
- Use only what the chunks state. When they do not answer the question, say so in `text` and leave
  `entities` and `number` empty.
- When the question asks which things, list them in `entities`, each once, named as the chunks or the
  question name them; an empty list means the chunks name none. When it asks how many or how much, give
  `number`. Otherwise answer in a short `text`.
- The `document` of a chunk is the name of what its document is about; use it when the chunk itself does
  not repeat that name.
- Cite every chunk your answer relies on: its id and ONE contiguous quote copied verbatim from it.

<question>{question}</question>

<chunks>
{chunks}
</chunks>"""

# The reply when no chunk reached the reader; code writes it, no model is asked.
NOTHING_TO_READ = "No text was retrieved for this question."


class ReaderCitation(BaseModel):
    chunk_id: str = Field(description="The id of a chunk the answer relies on, exactly as given in its tag.")
    quote: str = Field(description="One contiguous passage copied verbatim from that chunk.")


class ReaderAnswer(BaseModel):
    """The model's answer: exactly the forms the question calls for, and the chunks it rests on."""

    entities: list[str] | None = Field(
        default=None, description="The things that answer a question asking which things; [] if none."
    )
    number: float | None = Field(
        default=None, description="The answer to a question asking how many or how much."
    )
    text: str | None = Field(default=None, description="A short answer in words, for any other question.")
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
