from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

from rag.eval.golden_dataset import GoldenCase
from rag.eval.metrics import citation_precision, judge_correctness, retrieval_relevance
from rag.generation.pipeline import answer_query
from rag.ingest.chunking import ChunkingStrategy
from rag.retrieval.retriever import RetrievalConfig


@dataclass
class EvalCaseResult:
    case_id: str
    question: str
    category: str
    difficulty: str
    correctness: float
    correctness_reasoning: str
    citation_precision: float
    citation_coverage: float
    retrieval_relevance: float | None
    confidence_overall: float
    answer_text: str
    error: str | None = None


@dataclass
class EvalReport:
    strategy: str
    cases: list[EvalCaseResult]
    aggregates: dict

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "aggregates": self.aggregates,
            "cases": [asdict(c) for c in self.cases],
        }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _evaluate_case(case: GoldenCase, config: RetrievalConfig) -> EvalCaseResult:
    try:
        result = answer_query(case.question, config)
        judgment = judge_correctness(case, result)
        return EvalCaseResult(
            case_id=case.id,
            question=case.question,
            category=case.category,
            difficulty=case.difficulty,
            correctness=judgment.correctness,
            correctness_reasoning=judgment.reasoning,
            citation_precision=citation_precision(result),
            citation_coverage=result.confidence.citation_coverage,
            retrieval_relevance=retrieval_relevance(case, result),
            confidence_overall=result.confidence.overall,
            answer_text=result.answer.text,
        )
    except Exception as exc:  # keep the batch alive if one case fails
        return EvalCaseResult(
            case_id=case.id,
            question=case.question,
            category=case.category,
            difficulty=case.difficulty,
            correctness=0.0,
            correctness_reasoning="",
            citation_precision=0.0,
            citation_coverage=0.0,
            retrieval_relevance=None,
            confidence_overall=0.0,
            answer_text="",
            error=str(exc),
        )


def _aggregate(results: list[EvalCaseResult]) -> dict:
    by_difficulty = {}
    for difficulty in {"easy", "multi_hop", "no_answer"}:
        subset = [r.correctness for r in results if r.difficulty == difficulty]
        by_difficulty[difficulty] = round(_mean(subset), 3) if subset else None

    relevance_vals = [r.retrieval_relevance for r in results if r.retrieval_relevance is not None]

    return {
        "n_cases": len(results),
        "errors": sum(1 for r in results if r.error),
        "overall_correctness": round(_mean([r.correctness for r in results]), 3),
        "correctness_by_difficulty": by_difficulty,
        "citation_precision": round(_mean([r.citation_precision for r in results]), 3),
        "citation_coverage": round(_mean([r.citation_coverage for r in results]), 3),
        "retrieval_relevance": round(_mean(relevance_vals), 3) if relevance_vals else None,
        "avg_confidence": round(_mean([r.confidence_overall for r in results]), 3),
    }


def run_eval(
    strategy: ChunkingStrategy,
    cases: list[GoldenCase],
    *,
    max_workers: int = 6,
    top_k: int = 4,
) -> EvalReport:
    config = RetrievalConfig(strategy=strategy, top_k=top_k)
    results: list[EvalCaseResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_evaluate_case, case, config): case for case in cases}
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: r.case_id)
    return EvalReport(strategy=strategy.value, cases=results, aggregates=_aggregate(results))
