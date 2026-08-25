"""The release gate — one command that answers "can I ship this?".

    python scripts/run_gate.py        (or: make gate)

Three tiers, run in order, cheapest first, and never mixed:

  1. unit      pure functions, no API key, no network, no model.  MUST PASS 100%.
  2. battery   real graph runs, scored 0/1 by a pure function.    MUST PASS 100%.
  3. judge     real graph runs, scored 0.0-1.0 by a model.        MUST CLEAR A THRESHOLD.

Ordering is not cosmetic. Tier 1 is free, so a broken scorer or a broken
router should never cost an LLM call to discover. Tier 2 costs money but its
verdict is deterministic. Tier 3 costs money AND its verdict moves when the
judge model changes underneath you, which is exactly why it reports a score
instead of blocking on a boolean.

WHY A THRESHOLD ONLY ON THE LAST TIER

A tier that blocks on a model's opinion will eventually block a good release
because a provider shipped a new checkpoint. So the gate is closed by things
that can't drift, and the drifting tier is held to a floor you set
deliberately, reported every run, and tracked over time in eval_runs.jsonl --
so a slow decline is visible as a trend instead of arriving as one surprising
red build.

EVERY RUN IS RECORDED. eval_report.json is the latest verdict; eval_runs.jsonl
is append-only history. A gate with no history can tell you today is fine but
never that today is worse than last week.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import select

from agentsys.config import PROJECT_ROOT, settings
from agentsys.db.models import Escalation, Subtask, Task, ToolCall
from agentsys.db.session import get_session
from agentsys.eval.battery import BatteryCase, CaseVerdict, check_case, load_battery

# Tests that need no API key and no external service. Kept as an explicit
# list, not "everything under tests/": most of this repo's suite deliberately
# hits real Postgres, real Docker and real LLMs (see the README), and a tier
# whose whole promise is "free and deterministic" must not quietly include
# those. A new pure test file has to be added here on purpose.
UNIT_SUITES = [
    "tests/test_eval_battery.py",
    "tests/test_eval_grounding.py",
    "tests/test_graph_routing.py",
    "tests/test_pricing.py",
    "tests/test_spill_loop.py",
    "tests/test_synthesis_redaction.py",
    # Phase 0. Both verified to pass with Postgres and Redis unreachable and
    # no API key set -- the provider is mocked in one and never reached in the
    # other, so they keep tier 1's "free and deterministic" promise.
    "tests/test_llm_resilience.py",
    "tests/test_rag_ask_auth.py",
    # Phase 1. Pure: instantiates tools directly rather than through the
    # registry (no MCP subprocess, no Google config probe) and stubs the one
    # owner lookup, so it needs no Postgres, no Redis and no API key.
    "tests/test_tool_contracts.py",
    # Phase 2. The policy ruleset is a pure function of (tool, validated args)
    # -- policy.py imports no database, no settings and no LLM, deliberately,
    # so the whole decision table is free to verify. The DB-backed half
    # (tests/test_policy_approval_flow.py, which drives the real escalation
    # flow) is NOT here: it needs Postgres and Redis, so it belongs with the
    # rest of the integration suite, not in a tier whose promise is "free".
    "tests/test_policy.py",
    # Phase 3. Effect identity, failure classification and the retry matrix are
    # pure functions of their inputs -- execution.py touches the database only
    # in the ledger half, which is tests/test_execution_ledger.py and needs
    # Postgres, so it stays out of a tier whose promise is "free".
    "tests/test_execution_safety.py",
    # Phase 4. Deterministic verification rules over tool observations and
    # local workspace evidence. The DB-backed graph seam tests stay out of
    # tier 1 for the same reason as the Phase 3 ledger tests.
    "tests/test_verification.py",
    # Phase 5. Gmail draft validation, policy classification and conservative
    # retry semantics with fake HTTP only. The approval/ledger flow tests are
    # DB-backed and therefore intentionally stay out of tier 1.
    "tests/test_gmail_draft.py",
    # Phase 6B. Pure memory normalization/dedupe helpers only. DB-backed
    # curation and rolling-summary seam tests run separately.
    "tests/test_phase6b_memory_pure.py",
    # Phase 6D. Pure model-window and representative context benchmark
    # assertions; lifecycle and retrieval reinforcement need Postgres and run
    # in the focused Phase 6D suite instead.
    "tests/test_phase6d_context_pure.py",
    # Phase 7C. Pure dataset parsing, runtime-record adapter, deterministic
    # expected-tool/args checks, DeepEval skip/failure handling, and focused
    # Phase 6/7B/action-safety regressions. DeepEval itself is mocked or absent.
    "tests/test_phase7c_evaluation.py",
    # Phase 7D. Pure guardrail seam checks, red-team dataset validation, MCP
    # description/output security, memory poisoning rejection, telemetry
    # metadata, and policy/approval invariants. No external guardrail service.
    "tests/test_phase7d_security.py",
]

JUDGE_THRESHOLD = 0.7
QUALITY_THRESHOLD = 0.7


@dataclass
class TierResult:
    name: str
    status: str  # pass | fail | skipped
    detail: str = ""
    passed: int = 0
    failed: int = 0
    score: float | None = None
    cases: list[dict] = field(default_factory=list)


def _has_api_key() -> bool:
    return bool(settings.openai_api_key or settings.anthropic_api_key)


def run_unit_tier() -> TierResult:
    """Tier 1: pytest over the pure suites. Counts come off the -q summary --
    no plugin, no JSON report file to keep in sync."""
    import re

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *UNIT_SUITES],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    print(proc.stdout, end="")
    if proc.returncode:
        print(proc.stderr, end="", file=sys.stderr)

    counts = {
        key: (int(m.group(1)) if (m := re.search(rf"(\d+) {key}", proc.stdout)) else 0)
        for key in ("passed", "failed")
    }
    return TierResult(
        name="unit",
        status="pass" if proc.returncode == 0 else "fail",
        passed=counts["passed"],
        failed=counts["failed"],
        detail=f"{counts['passed']} passed, {counts['failed']} failed",
    )


def _observed(task_id: str) -> tuple[list[dict], str | None, bool, int]:
    """What actually happened on a run: tool calls in order, the final answer,
    and whether it escalated. Read back from Postgres rather than threaded out
    of the graph, so the battery grades the same record a human would
    inspect afterwards."""
    with get_session() as session:
        task = session.get(Task, task_id)
        subtask_ids = [
            s.id for s in session.exec(select(Subtask).where(Subtask.task_id == task_id)).all()
        ]
        calls = (
            session.exec(
                select(ToolCall)
                .where(ToolCall.subtask_id.in_(subtask_ids))
                .order_by(ToolCall.created_at)
            ).all()
            if subtask_ids
            else []
        )
        escalated = bool(
            session.exec(select(Escalation).where(Escalation.task_id == task_id)).first()
        )
        return (
            [{"tool": c.tool_name, "args": c.input} for c in calls],
            task.final_output,
            escalated or task.status.value == "awaiting_approval",
            len(subtask_ids),
        )


def run_one_case(case: BatteryCase, owner_id: str) -> CaseVerdict:
    from agentsys.graph.runner import run_task

    with get_session() as session:
        task = Task(request_text=case.request_text, owner_id=owner_id, is_eval=True)
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    try:
        run_task(task_id)
    except Exception as exc:  # a crashed run is a failed case, not a crashed gate
        return CaseVerdict(case.id, case.category, False, f"run raised {type(exc).__name__}: {exc}")

    tool_calls, final_output, escalated, steps = _observed(task_id)
    return check_case(case, tool_calls, final_output, escalated, steps)


def run_battery_tier(owner_id: str, cases: list[BatteryCase] | None = None) -> TierResult:
    """Tier 2. Sequential on purpose: these share one seeded database and one
    tool registry, and a parallel run would make a flaky case indistinguishable
    from a contended one."""
    cases = cases if cases is not None else load_battery()
    if not cases:
        return TierResult(name="battery", status="skipped", detail="no cases in agent_battery.jsonl")

    verdicts = [run_one_case(case, owner_id) for case in cases]
    for verdict in verdicts:
        mark = "PASS" if verdict.passed else "FAIL"
        print(f"  [{mark}] {verdict.case_id:<34} {verdict.reason}")

    failed = [v for v in verdicts if not v.passed]
    return TierResult(
        name="battery",
        status="pass" if not failed else "fail",
        passed=len(verdicts) - len(failed),
        failed=len(failed),
        detail=f"{len(verdicts) - len(failed)}/{len(verdicts)} cases",
        cases=[v.to_dict() for v in verdicts],
    )


def run_judge_tier(owner_id: str) -> TierResult:
    """Tier 3: the existing golden set, scored by a model. Reports, and holds
    a floor -- see the module docstring for why this tier alone gets a
    threshold rather than a pass/fail."""
    from agentsys.eval.golden_dataset import load_golden_dataset
    from agentsys.eval.runner import run_agent_eval

    cases = load_golden_dataset()
    if not cases:
        return TierResult(name="judge", status="skipped", detail="no golden tasks")

    report = run_agent_eval(cases, owner_id=owner_id)
    score = report.aggregates.get("overall_outcome_correctness", 0.0)
    return TierResult(
        name="judge",
        status="pass" if score >= JUDGE_THRESHOLD else "fail",
        score=score,
        detail=f"outcome correctness {score:.3f} (floor {JUDGE_THRESHOLD})",
        cases=[{"case_id": c.case_id, "correctness": c.outcome_correctness} for c in report.cases],
    )


def run_deepeval_quality_tier(owner_id: str) -> TierResult:
    """Optional Phase 7C quality tier. It is deliberately outside Tier 1 and
    disabled by default: DeepEval is eval tooling, not runtime infrastructure,
    and stochastic quality scoring must never be needed for deterministic
    safety checks."""
    from agentsys.eval.deepeval_runner import deepeval_available
    from agentsys.eval.quality_dataset import load_quality_dataset

    if not deepeval_available():
        return TierResult(
            name="deepeval_quality",
            status="skipped",
            detail="deepeval is not installed; install requirements-eval.txt",
        )

    cases = load_quality_dataset()
    if settings.deepeval_dataset_subset:
        cases = [
            case for case in cases if case.category.startswith(settings.deepeval_dataset_subset)
        ]
    max_cases = settings.deepeval_sample_count or None
    if not cases:
        return TierResult(name="deepeval_quality", status="skipped", detail="no quality cases")

    from agentsys.eval.quality_runner import run_quality_cases

    report = run_quality_cases(
        cases,
        owner_id=owner_id,
        with_deepeval=True,
        judge_model=settings.deepeval_judge_model or settings.reviewer_llm_model,
        max_cases=max_cases,
    )
    score = report.aggregates.get("deepeval_avg_score")
    if report.status == "pass" and score is not None and score < QUALITY_THRESHOLD:
        status = "fail"
        detail = f"DeepEval average {score:.3f} (floor {QUALITY_THRESHOLD})"
    else:
        status = report.status
        detail = report.detail

    return TierResult(
        name="deepeval_quality",
        status=status,
        score=score,
        detail=detail,
        cases=report.to_dict()["cases"],
    )


def record(tiers: list[TierResult], verdict: str) -> Path:
    """Latest verdict plus append-only history, both under data/eval/."""
    out_dir = PROJECT_ROOT / "data" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)

    entry = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": verdict,
        "model": settings.llm_model,
        "reviewer_model": settings.reviewer_llm_model,
        "tiers": [asdict(t) for t in tiers],
    }
    (out_dir / "eval_report.json").write_text(json.dumps(entry, indent=2), encoding="utf-8")
    with (out_dir / "eval_runs.jsonl").open("a", encoding="utf-8") as f:
        # History carries the summary, not every case -- it is read as a trend
        # line, and the full detail of the latest run is next to it.
        f.write(json.dumps({**entry, "tiers": [
            {k: v for k, v in asdict(t).items() if k != "cases"} for t in tiers
        ]}) + "\n")
    return out_dir / "eval_report.json"


def main() -> int:
    from agentsys.auth import get_or_create_system_user
    from agentsys.db.session import init_db

    init_db()
    tiers: list[TierResult] = []

    print("\n=== tier 1: unit (no key, no network) ===")
    unit = run_unit_tier()
    tiers.append(unit)
    if unit.status == "fail":
        record(tiers, "closed")
        print("\nGATE CLOSED — unit tier failed. Nothing else was run.")
        return 1

    if not _has_api_key():
        tiers.append(TierResult("battery", "skipped", "no API key"))
        tiers.append(TierResult("judge", "skipped", "no API key"))
        record(tiers, "partial")
        print("\nGATE PARTIAL — unit tier passed; battery and judge need an API key.")
        return 0

    owner_id = get_or_create_system_user(
        "agent-eval-harness", "agent-eval@agentforge.local", "Agent Eval Harness"
    )

    print("\n=== tier 2: battery (deterministic scoring) ===")
    battery = run_battery_tier(owner_id)
    tiers.append(battery)
    if battery.status == "fail":
        record(tiers, "closed")
        print(f"\nGATE CLOSED — battery: {battery.detail}. Judge tier not run.")
        return 1

    print("\n=== tier 3: judge (scored) ===")
    judge = run_judge_tier(owner_id)
    tiers.append(judge)

    verdict = "open" if judge.status != "fail" else "closed"
    if verdict == "open" and settings.deepeval_enabled:
        print("\n=== tier 3b: deepeval quality (optional, scored) ===")
        quality = run_deepeval_quality_tier(owner_id)
        tiers.append(quality)
        if quality.status == "fail":
            verdict = "closed"

    path = record(tiers, verdict)
    print(f"\n{judge.detail}")
    print(f"Report: {path}")
    if verdict == "closed":
        reason = "judge score below floor" if judge.status == "fail" else "quality tier failed"
        print(f"\nGATE CLOSED — {reason}.")
        return 1
    print("\nGATE OPEN — safe to release.")
    return 0
