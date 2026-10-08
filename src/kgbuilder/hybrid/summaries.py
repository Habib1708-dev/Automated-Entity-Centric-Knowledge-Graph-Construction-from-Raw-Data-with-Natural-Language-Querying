"""LLM node summaries (R123, representation B): a model writes each node's text from the same evidence as the
template cards, and code checks every text before it is used.

Role in the pipeline: the second node representation (representation.py). `kg index --cards summary` renders
every node's evidence with it and embeds the texts as `:SummaryCard` units beside the template cards; the card
retrievers find a question's start nodes by them. A summary only ranks: no summary text reaches the reader.
Design: Strategy (a `NodeRepresentation`) over the `LLMClient` port; the composition root injects the model
(wrapped in `CachedLLM`, so a re-index costs nothing). The evidence is shown as numbered facts in the
template's own line formats (cards.py), with the tags code computes from the observations; the model replies
with a `NodeSummary` (the text and the fact ids it used). The LLM proposes, code decides: `grounding_issues`
checks every reply (the node is named, the cited facts exist, every number and every name-like word is found
in the evidence, the length cap); a refused reply is retried once with its issues listed (llm/refine.py), and
a node whose retry is refused too gets its template card, marked `fallback`. What code cannot check (a denied
fact written as plain fact) is judged by Claude on a sample (R124). `version` hashes everything that can
change a text: the prompt, the reply schema, the model, the temperature, the thinking level, the length cap
and the version of the fallback cards.
Not here: the evidence (unit_sources.py), the template (cards.py), writing the units (unit_graph.py).
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, ConfigDict, Field

from ..core.errors import LLMUnavailableError
from ..core.text import contains_words, norm
from ..llm.base import LLMClient, ThinkingLevel, prompt_version
from ..llm.refine import refine
from .cards import ALIASES, PROPERTY, TemplateCards, qualified_claim, relation_line
from .evidence import NodeEvidence, RejectedSummary, RenderedCard, evidence_hash

# The summary prompt (R123). Intent: a short text whose vector lies near the questions that concern the node,
# written from the node's facts only. What each rule guards against, and the code check behind it
# (`grounding_issues`; "judged" = only Claude's sample judgement in R124 can see it):
# - the name first: the node's identity, which tells two things of one name apart by their connections
#   (R121, F01: the cards win where the part's card names its product); checked: the name or an alias is in
#   the text;
# - the facts only, names and numbers copied exactly: no invented neighbour, value or cause; checked: every
#   number and every capitalised word is in the evidence, every cited fact id exists;
# - every qualifier kept: an embedding hardly tells "is" from "is not" (R118), so a denied, hedged or
#   conditional fact must not read as plain fact; judged;
# - "write little": a node of one fact (an opinion phrase, a file's title) must not be padded with general
#   knowledge; the name and number checks catch part of it, the rest is judged;
# - the length cap: a hub's text must not dilute its vector; checked.
# The example is from an invented domain (string instruments) that no evaluated corpus uses, and a test checks
# that no four consecutive words of the prompt occur in a corpus. The fact lines use the template's formats,
# so A and B read the evidence in one notation.
PROMPT = """You write the search text of one node of a knowledge graph. The text is embedded as a vector, \
and a question finds the node when its meaning is close to the text. Nobody reads it as an answer, so it \
must be accurate, specific and short.

The node: {title} ({label})

What the graph holds about it, one numbered fact per line:
{facts}

How to read the facts:
- "TYPE -> Label (n): a, b" means the node has the relation TYPE to n nodes of that label, among them a and \
b. "<-" means those nodes have the relation to this node. "(+k more)" counts the ones not named.
- A claim reads "subject (its type) relation object (its type)". A tag in square brackets qualifies it: \
[denied] means its source says it is not so; [hedged] means it only may be so; [conditional: ...] means it \
holds only under that condition; [stated s, denied d] means s sources state it and d sources deny it.

Rules:
1. Use only the facts above. Add no name, number, cause, purpose or opinion that they do not state.
2. Begin with the node's name exactly as written: "{title}". Then say what kind of thing it is.
3. Then say what it is connected to, and what the claims say.
4. Keep every qualifier. Write a denied claim as denied, a hedged claim as possible, a conditional claim \
with its condition, and a disputed claim with both counts. Never write any of them as plain fact.
5. Copy names and numbers exactly as the facts write them. Write relation types as plain words ("BUILT_BY" \
becomes "built by").
6. If the facts say little, write little.
7. Write at most {max_chars} characters. If not everything fits, keep the name, the kind and the \
connections, then the facts listed first.
8. In facts_used, give the id of every fact the text uses, such as "F2".

An example from an invented domain:
The node: Arden Viola (Instrument)
F1. Also called: the viola
F2. year: 1931
F3. BUILT_BY -> Luthier (1): Tamsin Hale
F4. FITTED_TO <- Fitting (5): Bridge, Chinrest, Tailpiece (+2 more)
F5. Arden Viola (Instrument) has trait warm tone (Trait) [stated 3, denied 1]
F6. Chinrest (Fitting) has trait loose fit (Trait) [conditional: in humid weather]
F7. Tailpiece (Fitting) has trait rattle (Trait) [hedged]
F8. Arden Viola (Instrument) has trait buzzing (Trait) [denied]
A good text: "Arden Viola, also called the viola, is an instrument from 1931, built by Tamsin Hale. It has 5 \
fittings, among them Bridge, Chinrest and Tailpiece. 3 sources state its warm tone and 1 denies it. Its \
Chinrest has a loose fit, but only in humid weather. Its Tailpiece may rattle. A buzzing is denied."
facts_used: F1, F2, F3, F4, F5, F6, F7, F8"""

FACT = "F{n}. {fact}"  # one numbered fact line; the ids are what `facts_used` cites

ROUNDS = 2  # a refused reply is retried once with its issues (the plan's rule), then the template card
_WORKERS = 8  # nodes summarised in parallel: one small request each, and CachedLLM writes atomically

# A word as written, keeping its case: letters and digits with inner hyphens, apostrophes, dots and slashes
# ("HP40-1183", "Smith's" are one word each); `_PARTS` then splits it the way the evidence is split.
_WRITTEN_WORD = re.compile(r"[^\W_]+(?:[-'’./][^\W_]+)*")
_PARTS = re.compile(r"[a-z0-9]+")
# a number standing alone ("1931", "2.5", "1,299.00", the 1 of "T-1"); digits inside a word ("F5", "HP40")
# make the word name-like, which the name check covers
_NUMBER = re.compile(r"(?<![^\W\d_])\d+(?:[.,]\d+)*(?![^\W\d_])")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")  # "QualityAspect" -> "Quality Aspect": a label in words
_POSSESSIVE = re.compile(r"['’]s$")


class SummaryOptions(BaseModel):
    """What decides a summary's text besides the evidence (the settings' values)."""

    model_config = ConfigDict(frozen=True)

    model: str
    temperature: float
    thinking: ThinkingLevel
    max_chars: int


class NodeSummary(BaseModel):
    """The model's reply for one node: its search text, and the facts the text rests on."""

    text: str = Field(description="The node's search text, written from its facts only.")
    facts_used: list[str] = Field(description='The id of every fact the text uses, such as "F2".')


class SummaryCards:
    """Representation B: each node's evidence summarised by a model and checked by code (Strategy)."""

    name = "summary"

    def __init__(self, options: SummaryOptions, llm: LLMClient | None, fallback: TemplateCards):
        self._options = options
        self._llm = llm  # None: the version and params are known, but nothing can be rendered
        self._fallback = fallback
        self.version = summary_version(options, fallback.version)

    def render(self, evidence: list[NodeEvidence]) -> list[RenderedCard]:
        """One card per node, in the order given: its checked summary, or its template card marked
        `fallback`. Calls the model once or twice per node (cached). Raises `LLMUnavailableError` without a
        model, and `LLMResponseError` when the provider keeps failing for a node."""
        llm = self._llm
        if llm is None:
            raise LLMUnavailableError(
                "summary cards are written by a model: kg index --cards summary writes them (kg units never "
                "calls a model)"
            )
        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            return list(pool.map(lambda e: self._card(llm, e), evidence))

    def params(self) -> dict[str, object]:
        """The model and its settings, the length cap and the prompt's own version."""
        o = self._options
        return {
            "summary_model": o.model,
            "summary_temperature": o.temperature,
            "summary_thinking": o.thinking,
            "summary_max_chars": o.max_chars,
            "summary_prompt_version": prompt_version(PROMPT),
        }

    def prompts(self) -> dict[str, str]:
        return {"summary": PROMPT}

    def _card(self, llm: LLMClient, e: NodeEvidence) -> RenderedCard:
        o = self._options
        lines = facts(e)
        prompt = summary_prompt(e, lines, o.max_chars)
        replies: list[NodeSummary] = []

        def propose(feedback: str) -> NodeSummary:
            # the retry is the same prompt with the refused reply and its issues appended (refine.py)
            reply = llm.generate(
                f"{prompt}\n\n{feedback}" if feedback else prompt,
                NodeSummary,
                model=o.model,
                temperature=o.temperature,
                thinking=o.thinking,
            )
            replies.append(reply)
            return reply

        result = refine(propose, lambda s: grounding_issues(s, e, lines, o.max_chars), max_rounds=ROUNDS)
        rejected = [
            RejectedSummary(text=reply.text, facts_used=reply.facts_used, issues=log.issues)
            for reply, log in zip(replies, result.history, strict=True)
            if log.issues
        ]
        if result.accepted:
            return RenderedCard(
                ref=e.ref,
                text=result.value.text.strip(),
                evidence_hash=evidence_hash(e),
                truncated=False,
                facts_used=result.value.facts_used,
                fallback=False,
                rejected=rejected,
            )
        card = self._fallback.render([e])[0]
        return card.model_copy(update={"facts_used": [], "fallback": True, "rejected": rejected})


def facts(e: NodeEvidence) -> list[str]:
    """The node's evidence as fact lines in the template's formats: its other names, its properties, its
    relations, its claims with their tags. The title is not a fact: the prompt names the node."""
    lines = [ALIASES.format(names=", ".join(e.aliases))] if e.aliases else []
    lines += [PROPERTY.format(name=k, value=v) for k, v in e.properties.items()]
    lines += [relation_line(n) for n in e.relations]
    return lines + [qualified_claim(c) for c in e.claims]


def summary_prompt(e: NodeEvidence, lines: list[str], max_chars: int) -> str:
    """The prompt for one node: its name and label, and its facts numbered F1..Fn."""
    numbered = "\n".join(FACT.format(n=i, fact=line) for i, line in enumerate(lines, start=1))
    return PROMPT.format(title=e.title, label=e.label, facts=numbered or "(none)", max_chars=max_chars)


def summary_version(options: SummaryOptions, fallback_version: str) -> str:
    """12 hex characters over everything that can change a summary's text from the same evidence."""
    material = [
        PROMPT,
        FACT,
        NodeSummary.model_json_schema(),
        options.model_dump(mode="json"),
        fallback_version,
    ]
    return prompt_version(json.dumps(material, sort_keys=True))


def grounding_issues(summary: NodeSummary, e: NodeEvidence, lines: list[str], max_chars: int) -> list[str]:
    """What the code check finds wrong with a summary of the node `e` whose facts are `lines`; empty when it
    passes. Each issue is written to be shown to the model in the retry."""
    text = summary.text.strip()
    if not text:
        return ["the text is empty"]
    issues = []
    if len(text) > max_chars:
        issues.append(f"the text has {len(text)} characters; write at most {max_chars}")
    if not any(contains_words(text, name) for name in [e.title, *e.aliases]):
        issues.append(f'the text does not name the node: write "{e.title}"')
    ids = {f"F{n}" for n in range(1, len(lines) + 1)}
    if unknown := sorted(set(summary.facts_used) - ids):
        issues.append(f"facts_used cites {', '.join(unknown)}, which is not a fact id")
    source = "\n".join([e.title, e.label, *lines])
    if numbers := sorted(set(_NUMBER.findall(text)) - set(_NUMBER.findall(source))):
        issues.append(f"the numbers {', '.join(numbers)} are in no fact; copy numbers exactly from the facts")
    if names := unknown_names(text, source):
        issues.append(f"the words {', '.join(names)} are in no fact; copy names exactly from the facts")
    return issues


def unknown_names(text: str, source: str) -> list[str]:
    """The name-like words of `text` (a capital letter where a sentence does not start, or a capital after
    the first letter, or a digit) with a part that `source` does not hold, compared without case and
    accents. A label written in words ("Quality Aspect" for "QualityAspect") is found; a capitalised first
    word of a sentence is an ordinary word. Lower-case words are not checked: an invented one is judged."""
    vocabulary = set(_PARTS.findall(norm(source))) | set(_PARTS.findall(norm(_CAMEL.sub(" ", source))))
    unknown = set()
    for match in _WRITTEN_WORD.finditer(text):
        word = _POSSESSIVE.sub("", match.group())
        named = _name_like(word, _starts_sentence(text, match.start()))
        if named and not set(_PARTS.findall(norm(word))) <= vocabulary:
            unknown.add(word)
    return sorted(unknown)


def _name_like(word: str, sentence_start: bool) -> bool:
    if any(ch.isdigit() for ch in word):
        return not word.replace(",", "").replace(".", "").isdigit()  # a bare number: the number check's
    if not any(ch.isupper() for ch in word):
        return False
    return not sentence_start or any(ch.isupper() for ch in word[1:])


def _starts_sentence(text: str, start: int) -> bool:
    """Whether the word at `start` begins the text or a sentence (after ".", "!", "?" or a line break),
    opening quotes and brackets between them ignored."""
    before = text[:start].rstrip(" \t\"'“‘(")
    return not before or before[-1] in ".!?\n"
