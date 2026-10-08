"""The question-answering stages (R71): `kg ask`, `kg qa`, `kg qa-score` and `kg qa-compare` (R73).

Role in the pipeline: they run on a finished graph, after `kg link`; `kg qa-score` and `kg qa-compare`
need no graph and no model.
Design: wiring and logging only, like stages.py (kept apart from it, which builds the graph). Each system
answers in its own MLflow run (`qa_graph`, `qa_vector`, `qa_records_vector`), so its tokens and `cost_usd`
are its own. `kg qa`
logs what code can score at once (sets, numbers, recall@k, citation faithfulness); free-text answers wait
for the judge, and `kg qa-score` scores the answers file with the verdicts and writes one outcome row per
question, which `kg qa-compare` compares between two systems or two steps. `kg qa --plans` (R80) replays an
earlier run's plans (query/frozen.py) and logs which file they came from. Before any call, `kg qa`
refuses a gold whose evidence chunks the loaded graph lacks and logs the graph's digest (R117).
Not here: how a system is built and what it logs as params (qa_systems.py), retrieval and reading (query/),
scoring (validation/qa.py), the paired test (validation/paired.py).
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core.errors import ConfigurationError
from ..query.answers import SystemAnswer, load_system_answers, shown_texts
from ..query.frozen import load_frozen
from ..tracking.base import Run
from ..validation.paired import compare_outcomes
from ..validation.qa import QAReport, load_outcomes, load_qa_verdicts, score_qa
from ..validation.qa_gold import load_qa_gold
from .inputs import digest, input_file
from .qa_graph import check_graph
from .qa_systems import SystemSpec, build_system, check_system, log_prompts, system_params
from .stage import PipelineContext, PipelineState
from .stages import BaseStage

# the primitives whose use per answer is counted (`answers_using_<op>`)
_OPS = (
    "find_entity", "filter_records", "related", "find_claims", "read_check", "retrieve_chunks",
    "list", "count", "sum", "rank", "answer_from_chunks",
)  # fmt: skip


def _frozen_file(ctx: PipelineContext, state: PipelineState, spec: SystemSpec) -> Path | None:
    """The answers file whose plans this system replays: `<plans folder>/answers_<system>.jsonl` for a plan
    system when `--plans` is given; None otherwise (a reading system has no plans to freeze). Refuses the
    file this run is about to write: the run would overwrite the plans it replays."""
    if state.frozen_plans is None or not spec.planned:
        return None
    path = input_file(Path(state.frozen_plans) / f"answers_{spec.name}.jsonl", "frozen plans")
    if path.resolve() == (ctx.out / f"answers_{spec.name}.jsonl").resolve():
        raise ConfigurationError("--plans must name another folder than --out: the run would overwrite them")
    return path


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


def _plan_metrics(answers: list[SystemAnswer]) -> dict[str, int]:
    """How the plan systems fared (R74): plans written and refused, answers that needed the retry, filters
    dropped, read_check calls and verified candidates, the fallbacks taken (text2cypher, reading), the
    text2cypher proposals and refusals, how many answers used each primitive, and the answers whose
    plan or query was replayed (R80; in a frozen run the plans "proposed" are the replayed ones)."""
    traces = [a.plan for a in answers if a.plan is not None]
    exact = [a.exact for a in answers if a.exact is not None]
    ran = [attempt for t in traces for attempt in t.attempts if attempt.steps]
    metrics = {
        "plans_proposed": sum(len(t.attempts) for t in traces),
        "plans_refused": sum(bool(attempt.issues) for t in traces for attempt in t.attempts),
        "answers_retried": sum(len(t.attempts) > 1 for t in traces),
        "filters_dropped": sum(len(attempt.dropped) for t in traces for attempt in t.attempts),
        "read_check_calls": sum(t.checks for t in traces),
        "read_check_verified": sum(t.verified for t in traces),
        "fallback_text2cypher": sum(t.fallback == "text2cypher" for t in traces),
        "fallback_retrieval": sum(t.fallback == "retrieval" for t in traces),
        "answers_frozen": sum(t.frozen for t in traces),
        "cypher_proposals": sum(len(t.attempts) for t in exact),
        "cypher_refused": sum(bool(a.issues) for t in exact for a in t.attempts),
    }
    for op in _OPS:
        metrics[f"answers_using_{op}"] = sum(any(s.op == op for s in attempt.steps) for attempt in ran)
    return metrics


class AskStage(BaseStage):
    """Answer one question with one system and keep the answer (`kg ask`)."""

    name = "ask"
    ANSWER_FILE = "ask.json"

    def __init__(self, system: str = "graph"):
        self.system = check_system(system).name

    def params(self, ctx, state):
        question = state.need("question", "pass a question")
        return {**system_params(ctx.settings, self.system), "question": question}

    def run(self, ctx, state, run):
        log_prompts(run, self.system)
        answer = build_system(ctx, state, self.system).answer("ask", state.question)
        state.answer = answer
        run.metrics(retrieved=len(answer.retrieved), citations=len(answer.citations))
        run.artifact(ctx.write(self.ANSWER_FILE, answer.model_dump_json(indent=2)))


class QAStage(BaseStage):
    """Answer every question of a QA gold file with one system (`kg qa`); log what code can score."""

    def __init__(self, system: str):
        self.spec = check_system(system)
        self.system = system
        self.name = f"qa_{system}"
        self.answers_file = f"answers_{system}.jsonl"

    def params(self, ctx, state):
        gold = input_file(state.need("gold", "pass the QA gold file"), "QA gold file")
        frozen = _frozen_file(ctx, state, self.spec)
        return {
            **system_params(ctx.settings, self.system),
            "gold": gold,
            "gold_hash": digest(gold),
            "workers": ctx.settings.qa_workers,
            # a frozen run (R80) is compared with the run its plans come from, never with a planned one
            "frozen_plans": frozen,
            "frozen_plans_hash": digest(frozen) if frozen else None,
        }

    def run(self, ctx, state, run):
        s = ctx.settings
        log_prompts(run, self.system)
        gold = load_qa_gold(input_file(state.gold, "QA gold file"))
        # R117: the graph the answers come from; a graph without the gold's evidence is refused here, free
        run.params(graph_digest=check_graph(ctx.driver, gold).value)
        frozen_file = _frozen_file(ctx, state, self.spec)
        frozen = (
            load_frozen(frozen_file, self.system, [q.id for q in gold.questions]) if frozen_file else None
        )
        system = build_system(ctx, state, self.system, frozen)
        # questions are independent; the pool keeps the model busy while each answer waits on the network
        with ThreadPoolExecutor(max_workers=s.qa_workers) as pool:
            answers = list(pool.map(lambda q: system.answer(q.id, q.question), gold.questions))
        path = ctx.write(self.answers_file, "\n".join(a.model_dump_json() for a in answers) + "\n")
        report = score_qa(gold, answers, shown_texts(answers), s.qa_top_k, allow_unjudged=True)
        state.qa_reports[self.system] = report
        _log_report(ctx, run, report, f"qa_report_{self.system}.json")
        run.metrics(**_retrieval_metrics(answers))
        if self.spec.planned:
            run.metrics(**_plan_metrics(answers))
        run.artifact(path)
        run.artifact(Path(state.gold))
        if frozen_file:
            run.artifact(frozen_file)


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
        report = compare_outcomes(load_outcomes(a), load_outcomes(b), side_label(a), side_label(b))
        state.paired = report
        run.metrics(**report.metrics())
        run.artifact(ctx.write(self.REPORT_FILE, report.model_dump_json(indent=2)))
        run.artifact(a)
        run.artifact(b)


def side_label(path: Path) -> str:
    """A side's name in the comparison: its folder and file, which tell the run and the system apart
    (`r71_heldout/qa_outcomes_graph.jsonl`), where two steps' files share a system name."""
    return f"{path.parent.name}/{path.name}"


def _log_report(ctx: PipelineContext, run: Run, report: QAReport, file_name: str) -> None:
    run.metrics(**report.metrics())
    # the outcome rows have their own file (QAScoreStage); the report file keeps the totals
    totals = report.model_dump(mode="json", exclude={"outcomes"})
    run.artifact(ctx.write(file_name, json.dumps(totals, indent=2)))
