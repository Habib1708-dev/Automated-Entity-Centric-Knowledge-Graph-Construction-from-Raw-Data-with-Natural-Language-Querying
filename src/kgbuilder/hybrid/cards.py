"""Deterministic node cards (R118, representation A): a node's evidence rendered with a fixed template.

Role in the pipeline: the first node representation (representation.py). `kg units` writes its cards for
review; R119 embeds them, and R120's card retriever finds a question's start nodes by them.
Design: Strategy (a `NodeRepresentation`). The template's words are the only words code adds, and they say
nothing about any domain; every other word comes from the node's evidence (a test checks it), so a card is
grounded by construction. A card reads:
    Quill Press (Press)
    Also called: the press
    press_id: P1
    PART_OF <- Part (12): Gear, Pin, Spindle, Spring, Wheel (+7 more)
    Claims (1 of 4):
    - Spindle (Part) has condition wobbles (Condition) [stated 2, denied 1]
A relation with more other ends than the names cap shows how many it leaves out, so a hub reads as a hub.
A claim line's tags come from the observation's fields, never from its wording: `denied`, `hedged`,
`conditional: <condition>`, and both counts when the node's observations of one claim disagree. Cards over
the length cap drop claims first (the last, least supported, first), then relations, the smallest first:
a card must keep saying what the node is and what it is connected to.
Not here: the evidence and its caps (unit_sources.py), the embedding (R119).
"""

from ..llm.base import prompt_version
from .evidence import EvidenceClaim, Neighbours, NodeEvidence, RenderedCard, evidence_hash

# The fixed words of a card, one format per kind of line. Domain-neutral: no example, no word of a corpus.
TITLE = "{title} ({label})"
ALIASES = "Also called: {names}"
PROPERTY = "{name}: {value}"
RELATION_OUT = "{type} -> {label} ({count}): {names}"
RELATION_IN = "{type} <- {label} ({count}): {names}"
MORE = " (+{more} more)"
CLAIMS = "Claims ({shown} of {total}):"
CLAIM = "- {sentence}"
TAGS = " [{tags}]"
DENIED = "denied"
HEDGED = "hedged"
CONDITIONAL = "conditional"
CONDITIONAL_ON = "conditional: {condition}"
CONFLICT = "stated {stated}, denied {denied}"
# every format above, so a change to any of them is a new version of the representation
CARD_TEMPLATE = "\n".join(
    [TITLE, ALIASES, PROPERTY, RELATION_OUT, RELATION_IN, MORE, CLAIMS, CLAIM, TAGS, DENIED, HEDGED,
     CONDITIONAL, CONDITIONAL_ON, CONFLICT]
)  # fmt: skip


class TemplateCards:
    """Representation A: each node's evidence rendered with the fixed template (Strategy). Deterministic;
    `max_chars` caps a card's length."""

    name = "template"
    version = prompt_version(CARD_TEMPLATE)

    def __init__(self, max_chars: int):
        self._max_chars = max_chars

    def render(self, evidence: list[NodeEvidence]) -> list[RenderedCard]:
        """One card per node, in the order given."""
        return [self._card(e) for e in evidence]

    def _card(self, e: NodeEvidence) -> RenderedCard:
        head = [TITLE.format(title=e.title, label=e.label)]
        if e.aliases:
            head.append(ALIASES.format(names=", ".join(e.aliases)))
        head += [PROPERTY.format(name=k, value=v) for k, v in e.properties.items()]
        text, truncated = _fit(head, e.relations, e.claims, e.claims_total, self._max_chars)
        return RenderedCard(ref=e.ref, text=text, evidence_hash=evidence_hash(e), truncated=truncated)


def relation_line(n: Neighbours) -> str:
    """`PART_OF <- Part (12): Gear, Pin, Spindle, Spring, Wheel (+7 more)`."""
    form = RELATION_OUT if n.outgoing else RELATION_IN
    line = form.format(type=n.type, label=n.label, count=n.count, names=", ".join(n.names))
    return line + (MORE.format(more=n.count - len(n.names)) if n.count > len(n.names) else "")


def claim_line(c: EvidenceClaim) -> str:
    """`- Spindle (Part) has condition wobbles (Condition) [hedged, stated 2, denied 1]`."""
    tags = []
    if c.modality == "possible":
        tags.append(HEDGED)
    elif c.modality == "conditional":
        tags.append(CONDITIONAL_ON.format(condition=c.condition) if c.condition else CONDITIONAL)
    if c.denied and c.stated:
        tags.append(CONFLICT.format(stated=c.stated, denied=c.denied))
    elif c.denied:
        tags.append(DENIED)
    return CLAIM.format(sentence=c.sentence) + (TAGS.format(tags=", ".join(tags)) if tags else "")


def _fit(
    head: list[str], relations: list[Neighbours], claims: list[EvidenceClaim], total: int, max_chars: int
) -> tuple[str, bool]:
    """The card's text within `max_chars`, and whether anything was dropped to fit: claims first, the last
    (least supported) first, then relations, the smallest first; a head still too long is cut at a space."""
    kept_relations, kept_claims = list(relations), list(claims)
    truncated = False
    while len(text := _join(head, kept_relations, kept_claims, total)) > max_chars:
        truncated = True
        if kept_claims:
            kept_claims.pop()
        elif kept_relations:
            # the smallest relation, the last of equal ones: the hubs and the first relations stay
            smallest = min(range(len(kept_relations)), key=lambda i: (kept_relations[i].count, -i))
            kept_relations.pop(smallest)
        else:
            return text[:max_chars].rsplit(" ", 1)[0], True
    return text, truncated


def _join(head: list[str], relations: list[Neighbours], claims: list[EvidenceClaim], total: int) -> str:
    lines = head + [relation_line(n) for n in relations]
    if claims:
        lines.append(CLAIMS.format(shown=len(claims), total=total))
        lines += [claim_line(c) for c in claims]
    return "\n".join(lines)
