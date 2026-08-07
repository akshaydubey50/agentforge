import json
from datetime import datetime, timezone

from rag.config import settings
from rag.eval.golden_dataset import GoldenCase, load_golden_dataset
from rag.eval.runner import EvalReport, run_eval
from rag.ingest.chunking import ChunkingStrategy

RESULTS_DIR = settings.processed_data_dir.parent / "eval" / "results"


def compare_strategies(
    strategies: list[ChunkingStrategy] | None = None,
    cases: list[GoldenCase] | None = None,
) -> list[EvalReport]:
    strategies = strategies or list(ChunkingStrategy)
    cases = cases or load_golden_dataset()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    reports = []
    for strategy in strategies:
        report = run_eval(strategy, cases)
        reports.append(report)
        out_path = RESULTS_DIR / f"eval_{strategy.value}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)

    _write_comparison_markdown(reports)
    return reports


def _write_comparison_markdown(reports: list[EvalReport]) -> None:
    lines = [
        "# RAG Evaluation: Chunking Strategy Comparison",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        f"against {reports[0].aggregates['n_cases']} golden Q&A cases "
        "(30 single-hop, 12 multi-hop, 8 deliberately out-of-scope).",
        "",
        "| Strategy | Overall Correctness | Easy | Multi-hop | No-answer | Citation Precision | "
        "Citation Coverage | Retrieval Relevance | Avg Confidence | Errors |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for report in reports:
        a = report.aggregates
        d = a["correctness_by_difficulty"]
        lines.append(
            f"| {report.strategy} | {a['overall_correctness']} | {d.get('easy')} | "
            f"{d.get('multi_hop')} | {d.get('no_answer')} | {a['citation_precision']} | "
            f"{a['citation_coverage']} | {a['retrieval_relevance']} | {a['avg_confidence']} | "
            f"{a['errors']} |"
        )

    lines += [
        "",
        "## Metric Definitions",
        "",
        "- **Correctness**: LLM-judge score (0-1) of whether the answer conveys the expected "
        "facts for the question, or correctly declines for out-of-scope questions.",
        "- **Citation Precision**: of the claims the answer cited, what fraction were actually "
        "supported by their cited source (catches mis-citation).",
        "- **Citation Coverage**: of all factual claims made (cited + uncited), what fraction "
        "were both cited and supported.",
        "- **Retrieval Relevance**: for in-scope questions, what fraction of the expected source "
        "documents were actually retrieved and cited (excludes out-of-scope questions, which "
        "have no expected source).",
    ]

    out_path = RESULTS_DIR / "comparison.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
