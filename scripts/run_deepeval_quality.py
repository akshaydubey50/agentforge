"""CLI: python scripts/run_deepeval_quality.py --run-agent --with-deepeval

Phase 7C quality eval. Dry-run is the default: it validates and summarizes the
dataset without running AgentForge tasks or judge calls.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentsys.config import settings  # noqa: E402
from agentsys.eval.deepeval_runner import deepeval_available  # noqa: E402
from agentsys.eval.quality_dataset import load_quality_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-agent", action="store_true", help="execute AgentForge quality cases")
    parser.add_argument("--with-deepeval", action="store_true", help="run optional DeepEval metrics")
    parser.add_argument("--category", default="", help="category prefix to run, e.g. normal or failure")
    parser.add_argument("--case-id", action="append", default=[], help="specific case id to run")
    parser.add_argument("--max-cases", type=int, default=0, help="maximum cases to execute")
    parser.add_argument("--judge-model", default="", help="override DeepEval judge model")
    args = parser.parse_args()

    cases = load_quality_dataset()
    if args.category:
        cases = [case for case in cases if case.category.startswith(args.category)]
    if args.case_id:
        wanted = set(args.case_id)
        cases = [case for case in cases if case.id in wanted]
    if args.max_cases:
        cases = cases[: args.max_cases]

    print(f"Loaded {len(cases)} Phase 7C quality case(s).")
    print(f"DeepEval installed: {deepeval_available()}")
    if not args.run_agent:
        for case in cases:
            print(f"  {case.id:<36} {case.category:<28} metrics={','.join(case.metrics)}")
        print("\nDry run only. Add --run-agent to execute cases.")
        return 0

    from agentsys.auth import get_or_create_system_user  # noqa: E402
    from agentsys.db.session import init_db  # noqa: E402
    from agentsys.eval.quality_runner import run_quality_cases  # noqa: E402

    init_db()
    owner_id = get_or_create_system_user(
        "agent-eval-harness", "agent-eval@agentforge.local", "Agent Eval Harness"
    )
    report = run_quality_cases(
        cases,
        owner_id=owner_id,
        with_deepeval=args.with_deepeval,
        judge_model=args.judge_model or settings.reviewer_llm_model,
        max_cases=args.max_cases or None,
    )
    print(f"\nStatus: {report.status}")
    print(f"Detail: {report.detail}")
    for key, value in report.aggregates.items():
        print(f"  {key}: {value}")
    return 0 if report.status != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
