"""What the adjudicator of two individuals is shown about each side, and what its quotes are checked against
(R100).

Role in the pipeline: `kg resolve` (particulars.py, via individuals.py) and its offline replay
(audit/reidentify.py), after records are matched and before nominated pairs are adjudicated.
Design: pure functions of the mentions' chunk texts and the record matches. Until R100 a side showed at most
three lines, each the first sentence naming it, often a heading ("# Harbour Station monthly report, May
2025"); the sentence that holds the evidence ("the corer jammed ... and was freed by J. Pike", a role, a
date) is often the one before or after and never names the thing, and a record unit showed none of its
data. So a side now shows every sentence of its chunks that names it with one sentence before and after
(capped), each marked whether it names the side; a record unit also shows its record's cells and relations;
and the pair is shown the records both sides' chunks name besides their own, which the prompt says is
context, not evidence on its own. The lines shown are exactly what a quote must come from
(individuals.verified), so the prompt and the check read one list.
Not here: the prompt and the decision (individuals.py), reading record data (record_choice.py,
audit/relink.py).
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ..core.identity import record_ref
from ..core.text import norm, split_sentences
from .mentions import MentionText
from .record_choice import CandidateView
from .records import RecordMatch

if TYPE_CHECKING:  # individuals.py reads this module's evidence; a unit is only an annotation here
    from .individuals import Unit

# Enough sentences naming a side to see its roles and events across its documents, few enough to keep a
# call cheap: a record unit can be named in many chunks (a person in every minutes). Each brings at most its
# two neighbours.
_MAX_NAMING = 4
# A "sentence" longer than this (a table row, a list without full stops) is cut, as in the resolver's lines
_SENTENCE_CHARS = 400
# Records shown as named by both sides: context only, so a few are enough
_MAX_SHARED = 5


class EvidenceLine(BaseModel):
    """One sentence shown for a side: its document, the sentence (cut), and whether it names the side."""

    document: str
    sentence: str
    names_it: bool


class SideEvidence(BaseModel):
    """What a side shows: its lines and, for a record's unit, the record with what the data holds about it."""

    lines: list[EvidenceLine]
    record: str | None = None  # the unit's record ref
    view: CandidateView | None = None


class IdentityEvidence(BaseModel):
    """Every unit's evidence, and the records each unit's chunks name (for the records a pair shares)."""

    sides: dict[str, SideEvidence]  # unit id -> its evidence
    named: dict[str, list[str]]  # unit id -> record refs its chunks name, sorted
    views: dict[str, CandidateView]  # record ref -> its data, for the records shown

    def shared(self, a: "Unit", b: "Unit") -> list[str]:
        """The records both units' chunks name, besides the units' own records: at most `_MAX_SHARED`."""
        own = {a.record, b.record}
        both = set(self.named.get(a.id, [])) & set(self.named.get(b.id, []))
        return sorted(both - own)[:_MAX_SHARED]


def side_lines(names: list[str], chunks: Iterable[tuple[str, str]]) -> list[EvidenceLine]:
    """The lines of one side: every sentence of `chunks` ((document, text) in reading order) that names one
    of `names` (after `norm`, as `sentences_naming` reads a name), with the sentence before and after it in
    the same chunk; each line once, at most `_MAX_NAMING` naming sentences."""
    wanted = [norm(n) for n in names if norm(n)]

    def names_it(sentence: str) -> bool:
        return any(w in norm(sentence) for w in wanted)

    lines: list[EvidenceLine] = []
    seen: set[tuple[str, str]] = set()
    naming = 0
    for document, text in chunks:
        sentences = split_sentences(text)
        for i, sentence in enumerate(sentences):
            if not names_it(sentence) or naming >= _MAX_NAMING:
                continue
            naming += 1
            for neighbour in sentences[max(0, i - 1) : i + 2]:
                cut = neighbour[:_SENTENCE_CHARS]
                if (document, cut) not in seen:
                    seen.add((document, cut))
                    lines.append(EvidenceLine(document=document, sentence=cut, names_it=names_it(neighbour)))
    return lines


def build_evidence(
    units: list["Unit"],
    texts: list[MentionText],
    matches: dict[str, RecordMatch],
    views: dict[str, CandidateView],
) -> IdentityEvidence:
    """The evidence of every unit: `texts` are the mentions' chunks (`read_mention_texts`' rows), `matches`
    the keyed mentions' record decisions (which records a chunk names), `views` record ref -> its data."""
    owner = {m: u.id for u in units for m in u.mentions}
    chunks: dict[str, list[tuple[str, str]]] = {}
    seen: dict[str, set[str]] = {}
    in_chunk: dict[str, set[str]] = {}
    for t in texts:
        unit = owner[t.mention]
        if t.chunk_id not in seen.setdefault(unit, set()):  # a record's unit meets one chunk once
            seen[unit].add(t.chunk_id)
            chunks.setdefault(unit, []).append((t.document, t.text))
        link = matches[t.mention].link if t.mention in matches else None
        if link is not None:
            in_chunk.setdefault(t.chunk_id, set()).add(record_ref(link.record.label, link.record.key))
    named = {
        unit: sorted(set().union(*(in_chunk.get(c, set()) for c in chunk_ids)))
        for unit, chunk_ids in seen.items()
    }
    sides = {
        u.id: SideEvidence(
            lines=side_lines(u.names, chunks.get(u.id, [])),
            record=u.record,
            view=views.get(u.record) if u.record else None,
        )
        for u in units
    }
    return IdentityEvidence(sides=sides, named=named, views=views)
