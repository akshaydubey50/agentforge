"""CLI: python scripts/run_agent_eval.py

Runs the agentsys orchestrator's golden task set end-to-end (real graph
invocations, real LLM calls -- same as scripts/run_eval.py does for rag, just
for the agent loop instead of the retrieval pipeline) and writes a JSON
report to data/eval/agent_results/latest.json.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentsys.auth import get_or_create_system_user  # noqa: E402
from agentsys.db.session import init_db  # noqa: E402
from agentsys.eval.golden_dataset import load_golden_dataset  # noqa: E402
from agentsys.eval.runner import run_agent_eval  # noqa: E402


def main() -> None:
    init_db()
    # Golden tasks need a real owner_id (Task.owner_id is NOT NULL, see
    # db/models.py) but there's no human signing in to run this from a CI
    # job or a terminal -- same synthetic-user pattern worker.py's
    # ping_task health check uses.
    owner_id = get_or_create_system_user(
        "agent-eval-harness", "agent-eval@agentforge.local", "Agent Eval Harness"
    )
    cases = load_golden_dataset()
    print(f"Loaded {len(cases)} golden tasks.")

    start = time.time()
    report = run_agent_eval(cases, owner_id=owner_id)
    elapsed = time.time() - start

    results_dir = Path(__file__).resolve().parents[1] / "data" / "eval" / "agent_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / "latest.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, indent=2)

    print("\n== Aggregates ==")
    for k, v in report.aggregates.items():
        print(f"  {k}: {v}")

    failed = [c for c in report.cases if c.error or c.outcome_correctness < 0.5 or not c.escalation_correct]
    if failed:
        print("\n== Cases needing a look ==")
        for c in failed:
            print(f"  {c.case_id}: correctness={c.outcome_correctness} escalation_correct={c.escalation_correct} error={c.error}")

    print(f"\nDone in {elapsed:.1f}s. Results in {out_path}")


if __name__ == "__main__":
    main()
