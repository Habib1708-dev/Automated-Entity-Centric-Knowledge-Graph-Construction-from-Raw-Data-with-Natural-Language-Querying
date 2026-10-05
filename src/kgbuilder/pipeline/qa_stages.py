"""The question-answering stages (R71): `kg ask`, `kg qa`, `kg qa-score` and `kg qa-compare` (R73).

Role in the pipeline: they run on a finished graph, after `kg link`; `kg qa-score` and `kg qa-compare`
need no graph and no model.
Design: wiring and logging only, like stages.py (kept apart from it, which builds the graph). Each system
answers in its own MLflow run (`qa_graph`, `qa_vector`), so its tokens and `cost_usd` are its own. `kg qa`
logs what code can score at once (sets, numbers, recall@k, citation faithfulness); free-text answers wait
for the judge, and `kg qa-score` scores the answers file with the verdicts and writes one outcome row per
question, which `kg qa-compare` compares between two systems or two steps.
Not here: retrieval and reading (query/), scoring (validation/qa.py), the paired test (validation/paired.py).
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core.errors import ConfigurationError, LLMUnavailableError
from ..llm.base import Embedder, prompt_version
from ..llm.thinking import with_thinking
from ..query import exact, reader, router
from ..query.answers import SystemAnswer, load_system_answers, shown_texts
from ..query.graph_store import Neo4jGraphStore
from ..query.systems import QASettings, QASystem, VectorBaseline, build_routed_graph
from ..tracking.base import Run
from ..validation.paired import compare_outcomes
from ..validation.qa import QAReport, load_outcomes, load_qa_verdicts, score_qa
from ..validation.qa_gold import Route, load_qa_gold
from .inputs import digest, input_file
from .stage import PipelineContext, PipelineState
from .stages import BaseStage

SYSTEMS = ("graph", "vector")


def _embedder(ctx: PipelineContext) -> Embedder:
    if ctx.embedder is None:
        raise LLMUnavailableError("question answering needs an embedding model: set GEMINI_API_KEY")
    return ctx.embedder


def build_system(ctx: PipelineContext, state: PipelineState, system: str) -> QASystem:
    """The system named `system` over the current graph, with the settings' model, reader and k."""
    s = ctx.settings
    llm = with_thinking(ctx.require_llm(), s.qa_thinking)
    answer_reader = reader.Reader(llm, s.qa_model, s.llm_temperature)
    plan = state.load_plan(ctx, required=False)
    store = Neo4jGraphStore(ctx.driver, plan, s.qa_hops, s.qa_cypher_timeout_s)
    if system == "vector":
        return VectorBaseline(store, _embedder(ctx), answer_reader, s.qa_top_k)
    settings = QASettings(
        model=s.qa_model,
        temperature=s.llm_temperature,
        top_k=s.qa_top_k,
        link_fuzzy=s.qa_link_fuzzy,
        link_neighbours=s.qa_link_neighbours,
        cypher_limit=s.qa_cypher_limit,
    )
    return build_routed_graph(store, _embedder(ctx), llm, answer_reader, settings)


def _system_params(ctx: PipelineContext, system: str) -> dict[str, object]:
    """What decides a system's answers: the model, its prompts, k, and the graph system's settings."""
    s = ctx.settings
    params: dict[str, object] = {
        "system": system,
        "model": s.qa_model,
        "thinking": s.qa_thinking,
        "temperature": s.llm_temperature,
        "prompt_version": prompt_version(reader.PROMPT),
        "qa_top_k": s.qa_top_k,
        "embed_model": s.embed_model,
    }
    if system == "graph":
        params.update(
            router_prompt_version=prompt_version(router.PROMPT),
            cypher_prompt_version=prompt_version(exact.PROMPT + exact.RETRY),
            qa_hops=s.qa_hops,
            qa_link_fuzzy=s.qa_link_fuzzy,
            qa_link_neighbours=s.qa_link_neighbours,
            qa_cypher_limit=s.qa_cypher_limit,
            qa_cypher_timeout_s=s.qa_cypher_timeout_s,
        )
    return params


def _log_prompts(run: Run, system: str) -> None:
    run.text(reader.PROMPT, "prompts/qa_reader.txt")
    if system == "graph":
        run.text(router.PROMPT, "prompts/qa_router.txt")
        run.text(exact.PROMPT + exact.RETRY, "prompts/qa_cypher.txt")


def _check_system(system: str) -> str:
    if system not in SYSTEMS:
        raise ConfigurationError(f"unknown system '{system}'; choose from {', '.join(SYSTEMS)}")
    return system


def _retrieval_metrics(answers: list[SystemAnswer]) -> dict[str, float | int]:
    """How often retrieval gave the reader nothing, and why: the first causes to check for a wrong answer."""
    traced = [a.trace for a in answers if a.trace is not None]
    return {
        "questions_nothing_shown": sum(not a.shown and a.exact is None for a in answers),
        **(
            {
                "questions_unlinked": sum(not t.linked for t in traced),
                "questions_unreached": sum(bool(t.linked) and not t.candidates for t in traced),
                "candidates_mean": sum(len(t.candidates) for t in traced) / len(traced),
            }
            if traced
            else {}
        ),
    }


def _route_metrics(answers: list[SystemAnswer]) -> dict[str, int]:
    """How the router split the questions and how the exact route fared: its answers, its fallbacks to
    retrieval, and the Cypher proposals the checks or the run refused."""
    exact = [a.exact for a in answers if a.exact is not None]
    return {
        "routed_exact": sum(a.route == Route.EXACT for a in answers),
        "routed_retrieval": sum(a.route == Route.RETRIEVAL for a in answers),
        "exact_answered": sum(t.answered for t in exact),
        "exact_fallbacks": sum(not t.answered for t in exact),
        "cypher_proposals": sum(len(t.attempts) for t in exact),
        "cypher_refused": sum(bool(a.issues) for t in exact for a in t.attempts),
    }


class AskStage(BaseStage):
    """Answer one question with one system and keep the answer (`kg ask`)."""

    name = "ask"
    ANSWER_FILE = "ask.json"

    def __init__(self, system: str = "graph"):
        self.system = _check_system(system)

    def params(self, ctx, state):
        return {**_system_params(ctx, self.system), "question": state.need("question", "pass a question")}

    def run(self, ctx, state, run):
        _log_prompts(run, self.system)
        answer = build_system(ctx, state, self.system).answer("ask", state.question)
        state.answer = answer
        run.metrics(retrieved=len(answer.retrieved), citations=len(answer.citations))
        run.artifact(ctx.write(self.ANSWER_FILE, answer.model_dump_json(indent=2)))


class QAStage(BaseStage):
    """Answer every question of a QA gold file with one system (`kg qa`); log what code can score."""

    def __init__(self, system: str):
        self.system = _check_system(system)
        self.name = f"qa_{system}"
        self.answers_file = f"answers_{system}.jsonl"

    def params(self, ctx, state):
        gold = input_file(state.need("gold", "pass the QA gold file"), "QA gold file")
        return {
            **_system_params(ctx, self.system),
            "gold": gold,
            "gold_hash": digest(gold),
            "workers": ctx.settings.qa_workers,
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        _log_prompts(run, self.system)
        gold = load_qa_gold(input_file(state.gold, "QA gold file"))
        system = build_system(ctx, state, self.system)
        # questions are independent; the pool keeps the model busy while each answer waits on the network
        with ThreadPoolExecutor(max_workers=s.qa_workers) as pool:
            answers = list(pool.map(lambda q: system.answer(q.id, q.question), gold.questions))
        path = ctx.write(self.answers_file, "\n".join(a.model_dump_json() for a in answers) + "\n")
        report = score_qa(gold, answers, shown_texts(answers), s.qa_top_k, allow_unjudged=True)
        state.qa_reports[self.system] = report
        _log_report(ctx, run, report, f"qa_report_{self.system}.json")
        run.metrics(**_retrieval_metrics(answers))
        if self.system == "graph":
            run.metrics(**_route_metrics(answers))
        run.artifact(path)
        run.artifact(Path(state.gold))


class QAScoreStage(BaseStage):
    """Score an answers file of `kg qa` with the judge's verdicts on its free-text answers (`kg qa-score`),
    without a graph and without a model; write the outcome rows (`qa_outcomes_<system>.jsonl`, R73)."""

    name = "qa_score"

    def params(self, ctx, state):
        gold = input_file(state.need("gold", "pass the QA gold file"), "QA gold file")
        answers = input_file(state.need("answers", "pass the answers file"), "answers file")
        params: dict[str, object] = {
            "gold": gold,
            "gold_hash": digest(gold),
            "answers": answers,
            "answers_hash": digest(answers),
            "qa_top_k": ctx.settings.qa_top_k,
            "verdicts": state.verdicts,
        }
        if state.verdicts:
            verdicts = input_file(state.verdicts, "verdict file")
            params.update(
                judge_model=load_qa_verdicts(verdicts).judge.model, judge_verdicts_hash=digest(verdicts)
            )
        return params

    def run(self, ctx, state, run):
        gold = load_qa_gold(input_file(state.gold, "QA gold file"))
        answers = load_system_answers(input_file(state.answers, "answers file"))
        verdicts = load_qa_verdicts(input_file(state.verdicts, "verdict file")) if state.verdicts else None
        system = answers[0].system if answers else "none"
        report = score_qa(gold, answers, shown_texts(answers), ctx.settings.qa_top_k, verdicts, system=system)
        state.qa_reports[system] = report
        _log_report(ctx, run, report, f"qa_score_{system}.json")
        outcomes = "".join(o.model_dump_json() + "\n" for o in report.outcomes)
        run.artifact(ctx.write(f"qa_outcomes_{system}.jsonl", outcomes))
        for source in (Path(state.gold), Path(state.answers), *([Path(state.verdicts)] if verdicts else [])):
            run.artifact(source)


class QACompareStage(BaseStage):
    """Compare two outcome files question by question with the exact McNemar test (`kg qa-compare`, R73),
    without a graph and without a model. System `a` is the first file, `b` the second."""

    name = "qa_compare"
    REPORT_FILE = "qa_compare.json"

    def params(self, ctx, state):
        a, b = state.need("outcomes", "pass two outcome files of kg qa-score")
        a, b = input_file(a, "outcome file"), input_file(b, "outcome file")
        return {"a": a, "a_hash": digest(a), "b": b, "b_hash": digest(b)}

    def run(self, ctx, state, run):
        a, b = (input_file(path, "outcome file") for path in state.outcomes)
        report = compare_outcomes(load_outcomes(a), load_outcomes(b), _label(a), _label(b))
        state.paired = report
        run.metrics(**report.metrics())
        run.artifact(ctx.write(self.REPORT_FILE, report.model_dump_json(indent=2)))
        run.artifact(a)
        run.artifact(b)


def _label(path: Path) -> str:
    """A side's name in the comparison: its folder and file, which tell the run and the system apart
    (`r71_heldout/qa_outcomes_graph.jsonl`), where two steps' files share a system name."""
    return f"{path.parent.name}/{path.name}"


def _log_report(ctx: PipelineContext, run: Run, report: QAReport, file_name: str) -> None:
    run.metrics(**report.metrics())
    # the outcome rows have their own file (QAScoreStage); the report file keeps the totals
    totals = report.model_dump(mode="json", exclude={"outcomes"})
    run.artifact(ctx.write(file_name, json.dumps(totals, indent=2)))
