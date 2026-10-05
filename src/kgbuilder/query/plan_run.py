"""Run a checked query plan (R74): each step through its compiled Cypher fragment, its items handed to the
next step, the terminal turned into an answer.

Role in the pipeline: the plan system (systems.py) gets a plan from the planner, checks it (plan.py) and
runs it here. A run that cannot finish (read_check given more candidates than its bound, a read that
fails) raises `QueryPlanError` with a reason the planner's retry can act on.
Design: Interpreter over the closed set of primitives: one method per operation, every query from
plan_cypher.py, every model call through the reader or the read_check reader, so the model chooses the
steps and their values and code does the rest. Counts and lists are computed by code from the items; text
is read only by `read_check` (one candidate at a time, quotes verified) and by `answer_from_chunks`.
Not here: checking the plan (plan.py), the planner and the fallbacks (planner.py, systems.py).
"""

from collections import Counter
from typing import Protocol

from pydantic import BaseModel

from ..core.errors import QueryPlanError
from ..llm.base import Embedder
from ..validation.qa import Citation
from . import plan_cypher as cy
from .answers import RetrievalTrace, ShownChunk
from .graph_store import CypherStore, GraphStore, StoredChunk
from .names import NameLinker
from .plan import ItemKind, PlanSchema, PlanStep, QueryPlan, as_number
from .ranking import rank as rank_chunks
from .read_check import ReadChecker
from .reader import Reader

Items = dict[ItemKind, list[str]]  # the ids a step produced, per kind, in order


class ChunkSource(Protocol):
    """Where `retrieve_chunks` without an input finds text: the graph's retrieval route or vector search."""

    def ranked(self, question: str) -> tuple[list[StoredChunk], RetrievalTrace | None]:
        """Candidate chunks for `question`, best first, and how they were found (None for vector search)."""
        ...


class PlanStore(GraphStore, CypherStore, Protocol):
    """The reads a plan needs: the compiled fragments (`run_read`) and the chunks they lead to."""


class PlanSettings(BaseModel):
    """Bounds and sizes of a run (from config.Settings)."""

    top_k: int  # chunks given to the final reader, as in the other systems
    step_cap: int = 200  # items a step may produce: a bound on every step
    check_limit: int = 30  # candidates read_check may read one by one; more must be narrowed first
    check_chunks: int = 3  # chunks of one candidate shown to read_check, nearest the statement first
    neighbours: int = 3  # names nearest in meaning, for a name nothing spells alike and for claim words


class StepTrace(BaseModel):
    """What one step did: how many items of each kind it produced, and a note (direction, refusals)."""

    op: str
    items: dict[str, int]
    note: str = ""


class PlanRun(BaseModel):
    """The answer a plan gave, the chunks read along the way, the verified quotes, and the steps."""

    entities: list[str] | None = None
    number: float | None = None
    text: str | None = None
    shown: list[ShownChunk] = []
    citations: list[Citation] = []
    steps: list[StepTrace] = []
    checks: int = 0  # read_check model calls
    verified: int = 0  # candidates read_check verified


class PlanRunner:
    """Runs checked plans over one graph; build it once per system."""

    def __init__(
        self,
        store: PlanStore,
        embedder: Embedder,
        linker: NameLinker,
        schema: PlanSchema,
        name_properties: dict[str, str],
        reader: Reader,
        checker: ReadChecker,
        source: ChunkSource,
        settings: PlanSettings,
    ):
        self._store = store
        self._embedder = embedder
        self._linker = linker
        self._schema = schema
        self._name_properties = name_properties
        self._reader = reader
        self._checker = checker
        self._source = source
        self._settings = settings
        self._entity_names = {n.node_id: n.name for n in linker.nodes if n.kind == "kind"}

    def run(self, plan: QueryPlan, question: str) -> PlanRun:
        """Run `plan` (already checked) for `question`. Raises `QueryPlanError` when a step cannot finish,
        and `LLMResponseError` when a model keeps failing."""
        result = PlanRun()
        outputs: list[Items] = []
        for step in plan.steps:
            inputs = outputs[step.input] if step.input is not None else None
            items, note = self._step(step, inputs, question, result)
            outputs.append(items)
            result.steps.append(StepTrace(op=step.op, items={k: len(v) for k, v in items.items()}, note=note))
        return result

    # --- the steps -------------------------------------------------------------------------------------

    def _step(
        self, step: PlanStep, inputs: Items | None, question: str, result: PlanRun
    ) -> tuple[Items, str]:
        handler = getattr(self, f"_{step.op}")
        return handler(step, inputs or {}, question, result)

    def _find_entity(self, step, inputs, question, result):
        record_labels = self._schema.record_labels
        kinds = {"thing", "kind"} if self._schema.claims else {"thing"}
        if step.label in record_labels:
            kinds = {"thing"}
        elif step.label is not None:
            kinds = {"kind"}
        found = self._linker.find(step.name or "", self._embed, kinds, step.label, union=False)
        items: Items = {
            "record": [n.node_id for n in found if n.kind == "thing"],
            "entity": [n.node_id for n in found if n.kind == "kind"],
        }
        return items, ", ".join(n.name for n in found[:5])

    def _filter_records(self, step, inputs, question, result):
        prop = self._schema.label_properties(step.label).get(step.property) if step.property else None
        within = inputs.get("record") if "record" in inputs else None
        cypher, params = cy.filter_records(
            step.label, prop, step.operator, step.value, within, self._settings.step_cap
        )
        return {"record": self._ids(cypher, params)}, ""

    def _related(self, step, inputs, question, result):
        ids = inputs.get("record", [])
        if not ids:
            return {"record": []}, "no input records"
        direction = self._direction(step.relationship, ids, step.label)
        if direction is None:
            return {"record": []}, f"{step.relationship} does not join these records"
        prop = (
            self._schema.relationship_properties(step.relationship).get(step.property)
            if step.property
            else None
        )
        cypher, params = cy.related(
            step.relationship,
            direction,
            step.label,
            prop,
            step.operator,
            step.value,
            ids,
            self._settings.step_cap,
        )
        return {"record": self._ids(cypher, params)}, f"direction {direction}"

    def _find_claims(self, step, inputs, question, result):
        records = inputs.get("record") if inputs else None
        if records and step.include_parts:
            cypher, params = cy.parts_of(records, sorted(self._schema.record_labels), self._settings.step_cap)
            records = list(dict.fromkeys([*records, *self._ids(cypher, params)]))
        entities = inputs.get("entity") if inputs else None
        cypher, params = cy.find_claims(
            records if inputs else None,
            entities if inputs else None,
            step.predicate,
            self._claim_words(step.subject_like),
            self._claim_words(step.object_like),
            step.tone,
            step.time_words,
            self._settings.step_cap,
        )
        return {"claim": self._ids(cypher, params)}, ""

    def _read_check(self, step, inputs, question, result):
        total = sum(len(v) for v in inputs.values())
        if total > self._settings.check_limit:
            raise QueryPlanError(
                f"read_check got {total} candidates, more than {self._settings.check_limit}: "
                "narrow its input with a filter or a more specific find step first"
            )
        statement_vector = self._embed(step.statement or "")
        kept: Items = {}
        for kind, ids in inputs.items():
            chunks_of = self._item_chunks(kind, ids)
            kept[kind] = []
            for item in ids:
                shown = _shown(
                    rank_chunks(statement_vector, chunks_of.get(item, []))[: self._settings.check_chunks]
                )
                outcome = self._checker.check(step.statement or "", shown)
                result.checks += int(outcome.called)
                _add_shown(result, shown)
                if outcome.verified:
                    kept[kind].append(item)
                    result.verified += 1
                    result.citations.append(
                        Citation(chunk_id=outcome.chunk_id or "", quote=outcome.quote or "")
                    )
        return kept, f"{sum(len(v) for v in kept.values())} of {total} verified"

    def _retrieve_chunks(self, step, inputs, question, result):
        chunks = self._chunks_for(inputs, question)
        return {"chunk": [c.chunk_id for c in chunks]}, ""

    # --- the terminals ---------------------------------------------------------------------------------

    def _list(self, step, inputs, question, result):
        if step.property is not None:
            values = self._rows(*cy.record_values(inputs.get("record", []), step.property))
            result.entities = list(dict.fromkeys(str(r["value"]) for r in values if r["value"] is not None))
        elif inputs.get("claim"):
            result.entities = self._claim_names(inputs["claim"], step.what or "about")
        else:
            result.entities = self._names(inputs)
        return {}, ""

    def _count(self, step, inputs, question, result):
        unit = step.unit or "items"
        if unit == "items":
            result.number = float(sum(len(v) for v in inputs.values()))
        elif unit == "about":
            rows = self._rows(*cy.claims_about(inputs.get("claim", [])))
            result.number = float(len({r["about"] for r in rows}))
        else:
            documents: set[str] = set()
            for kind, ids in inputs.items():
                documents |= {r["document"] for r in self._rows(*cy.item_documents(kind, ids))}
            result.number = float(len(documents))
        return {}, ""

    def _sum(self, step, inputs, question, result):
        rows = self._rows(*cy.record_values(inputs.get("record", []), step.property))
        numbers = [n for n in (as_number(r["value"]) for r in rows) if n is not None]
        result.number = float(sum(numbers))
        return {}, f"{len(numbers)} of {len(rows)} values are numbers"

    def _rank(self, step, inputs, question, result):
        if step.property is not None:
            rows = self._rows(*cy.record_values(inputs.get("record", []), step.property))
            scores = {r["item"]: n for r in rows if (n := as_number(r["value"])) is not None}
        elif step.relationship is not None:
            ids = inputs.get("record", [])
            direction = self._direction(step.relationship, ids, step.label) or "both"
            rows = self._rows(*cy.related_groups(step.relationship, direction, step.label, ids))
            scores = {r["item"]: float(r["n"]) for r in rows}
        else:
            rows = self._rows(*cy.claims_about(inputs.get("claim", [])))
            scores = {k: float(v) for k, v in Counter(r["about"] for r in rows).items()}
        if not scores:
            result.entities = []
            return {}, "nothing to rank"
        best = max(scores.values()) if step.order in ("most", "highest") else min(scores.values())
        # every item at the best score: a tie is reported, never broken at random
        result.entities = self._names({"record": [k for k, v in scores.items() if v == best]})
        return {}, f"best {best:g}"

    def _answer_from_chunks(self, step, inputs, question, result):
        chunks = self._chunks_for(inputs, question)
        shown = _shown(chunks)
        _add_shown(result, shown)
        reply = self._reader.read(question, shown)
        result.entities, result.number, result.text = reply.entities, reply.number, reply.text
        result.citations += [Citation(chunk_id=c.chunk_id, quote=c.quote) for c in reply.citations]
        return {}, f"{len(shown)} chunks read"

    # --- helpers ---------------------------------------------------------------------------------------

    def _embed(self, text: str) -> list[float]:
        return self._embedder.embed([text])[0]

    def _rows(self, cypher: str, params: dict[str, object]) -> list[dict[str, object]]:
        result = self._store.run_read(cypher, params)
        if result.error:
            raise QueryPlanError(f"a step's read failed: {result.error}")
        return [dict(zip(result.keys, row, strict=True)) for row in result.rows]

    def _ids(self, cypher: str, params: dict[str, object]) -> list[str]:
        return list(dict.fromkeys(str(r["id"]) for r in self._rows(cypher, params)))

    def _direction(self, relationship: str, ids: list[str], label: str | None) -> cy.Direction | None:
        """The way `relationship` leaves the records `ids`, read from the schema: out, in, or both when it
        joins records of one label to each other; None when no pattern fits."""
        rows = self._rows(
            "MATCH (n) WHERE elementId(n) IN $ids RETURN DISTINCT labels(n) AS labels", {"ids": ids}
        )
        labels = {lab for r in rows for lab in r["labels"]}
        patterns = [r for r in self._schema.schema.relationships if r.type == relationship]
        out = any(p.source in labels and (label is None or p.target == label) for p in patterns)
        back = any(p.target in labels and (label is None or p.source == label) for p in patterns)
        if out and back:
            return "both"
        return "out" if out else ("in" if back else None)

    def _claim_words(self, words: str | None) -> list[str] | None:
        """The entities a claim's subject or object words name: spelled alike, plus the nearest in meaning
        (a candidate set: read_check or the reader decides what holds)."""
        if not words:
            return None
        found = self._linker.find(words, self._embed, {"kind"}, None, union=True)
        return [n.node_id for n in found]

    def _item_chunks(self, kind: str, ids: list[str]) -> dict[str, list[StoredChunk]]:
        if not ids:
            return {}
        pairs: dict[str, list[str]] = {}
        for cypher, params in cy.item_chunks(kind, ids):
            for r in self._rows(cypher, params):
                pairs.setdefault(str(r["item"]), [])
                if r["chunk"] not in pairs[str(r["item"])]:
                    pairs[str(r["item"])].append(str(r["chunk"]))
        stored = {c.chunk_id: c for c in self._store.chunks(sorted({c for cs in pairs.values() for c in cs}))}
        return {item: [stored[c] for c in chunks if c in stored] for item, chunks in pairs.items()}

    def _chunks_for(self, inputs: Items, question: str) -> list[StoredChunk]:
        """The top k chunks for the final reader: the input's own text nearest the question, or, without an
        input, the system's chunk source."""
        k = self._settings.top_k
        if not inputs:
            ranked, _ = self._source.ranked(question)
            return ranked[:k]
        if "chunk" in inputs:
            return self._store.chunks(inputs["chunk"][:k])
        vector = self._embed(question)
        pool: dict[str, StoredChunk] = {}
        for kind, ids in inputs.items():
            for chunks in self._item_chunks(kind, ids).values():
                pool.update({c.chunk_id: c for c in chunks})
        return rank_chunks(vector, list(pool.values()))[:k]

    def _names(self, items: Items) -> list[str]:
        names: list[str] = []
        if items.get("record"):
            rows = self._rows(*cy.record_names(items["record"], self._name_properties))
            by_id = {r["item"]: r["name"] for r in rows}
            names += [str(by_id[i]) for i in items["record"] if by_id.get(i) is not None]
        names += [self._entity_names[i] for i in items.get("entity", []) if i in self._entity_names]
        return list(dict.fromkeys(names))

    def _claim_names(self, claims: list[str], what: str) -> list[str]:
        if what in ("subject", "object"):
            rows = self._rows(*cy.claim_ends(claims, what))
            return list(dict.fromkeys(str(r["name"]) for r in rows))
        rows = self._rows(*cy.claims_about(claims))
        return self._names({"record": list(dict.fromkeys(str(r["about"]) for r in rows))})


def _shown(chunks: list[StoredChunk]) -> list[ShownChunk]:
    return [ShownChunk(chunk_id=c.chunk_id, context=c.context, text=c.text) for c in chunks]


def _add_shown(result: PlanRun, shown: list[ShownChunk]) -> None:
    known = {c.chunk_id for c in result.shown}
    result.shown += [c for c in shown if c.chunk_id not in known]
