"""The sentence code writes for a claim (R118): its two ends with their types, and its predicate in words.

Role in the pipeline: unit_sources.py gives every `:Observation` this sentence, as a claim unit of its own
(R119 embeds it) and as a claim line on the cards of the things that hold it (cards.py).
Design: `"{subject} ({subject type}) {predicate in words} {object} ({object type})"`, from the claim's
canonical names (what its mentions refer to), so two wordings of one thing read alike. Truth is never left
to this text: an embedding hardly tells "is" from "is not", and the English analyzer of a full-text index
drops "not" as a stop word. The truth fields stay on the observation and travel beside the text: as tags on
a card line, as fields of a claim hit (R120).
Not here: reading the claims (unit_sources.py).
"""

from typing import Literal

from pydantic import BaseModel

from ..llm.base import prompt_version

# The sentence's fixed shape; its hash versions every claim unit, so a change re-embeds them (R119)
CLAIM_TEMPLATE = "{subject} ({subject_type}) {predicate} {object} ({object_type})"
CLAIM_VERSION = prompt_version(CLAIM_TEMPLATE)


class ClaimSentence(BaseModel):
    """One claim's sentence: a line of `index/units.jsonl`, and the text R119 embeds for it."""

    unit: Literal["claim"] = "claim"
    id: str  # the observation's id
    text: str
    chunk_id: str  # the chunk the claim was read from: what a claim hit leads to


def predicate_words(predicate: str) -> str:
    """A predicate as words: "HAS_DEFECT" -> "has defect"."""
    return " ".join(predicate.lower().split("_")).strip()


def claim_text(subject: str, subject_type: str, predicate: str, obj: str, object_type: str) -> str:
    """The claim's sentence: "Spindle (Part) has condition wobbles (Condition)"."""
    return CLAIM_TEMPLATE.format(
        subject=subject,
        subject_type=subject_type,
        predicate=predicate_words(predicate),
        object=obj,
        object_type=object_type,
    )
