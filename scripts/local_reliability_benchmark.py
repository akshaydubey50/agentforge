"""Local synthetic reliability benchmark for Phase 8.

No real model or external API is used. The benchmark creates deterministic
Task/Subtask rows, executes a fake idempotent tool through AgentForge's real
execution ledger, and deliberately submits duplicate logical effects so the
duplicate-execution count can be measured.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentsys.auth import get_or_create_system_user
from agentsys.db.models import Subtask, SubtaskStatus, Task
from agentsys.db.session import get_session, init_db
from agentsys.execution import ExecutionSafety, execute_tool
from agentsys.policy import ActionType, Risk
from agentsys.tools.base import Tool, ToolResult


class SyntheticTool(Tool):
    name = "phase8_synthetic_tool"
    description = "Synthetic local benchmark tool."
    action_type = ActionType.LOCAL_WRITE
    risk = Risk.LOW
    execution_safety = ExecutionSafety.IDEMPOTENT

    def __init__(self) -> None:
        self.calls = 0

    def validate_args(self, proposed: dict) -> dict:
        return dict(proposed)

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, output={"key": kwargs["key"], "call": self.calls})


def _create_rows(owner_id: str, index: int) -> tuple[str, str]:
    with get_session() as session:
        task = Task(request_text=f"phase8 synthetic task {index}", owner_id=owner_id, is_eval=True)
        session.add(task)
        session.commit()
        subtask = Subtask(
            task_id=task.id,
            position=0,
            description="synthetic effect",
            status=SubtaskStatus.RUNNING,
        )
        session.add(subtask)
        session.commit()
        return task.id, subtask.id


def _run_one(tool: SyntheticTool, owner_id: str, index: int) -> dict:
    task_id, subtask_id = _create_rows(owner_id, index)
    started = time.perf_counter()
    first = execute_tool(
        tool,
        {"key": f"effect-{index}"},
        task_id=task_id,
        subtask_id=subtask_id,
        action_type=ActionType.LOCAL_WRITE,
    )
    duplicate = execute_tool(
        tool,
        {"key": f"effect-{index}"},
        task_id=task_id,
        subtask_id=subtask_id,
        action_type=ActionType.LOCAL_WRITE,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "latency_ms": elapsed_ms,
        "ok": bool(first.result.success and duplicate.result.success),
        "duplicate_executed": not duplicate.deduped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local synthetic Phase 8 reliability benchmark.")
    parser.add_argument("--tasks", type=int, default=100)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    init_db()
    owner_id = get_or_create_system_user(
        "phase8-reliability-benchmark",
        "phase8-reliability@agentforge.local",
        "Phase 8 Reliability Benchmark",
    )
    tool = SyntheticTool()
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_run_one, tool, owner_id, index) for index in range(args.tasks)]
        for future in as_completed(futures):
            results.append(future.result())
    elapsed = time.perf_counter() - started

    latencies = [r["latency_ms"] for r in results]
    errors = sum(1 for r in results if not r["ok"])
    duplicate_count = sum(1 for r in results if r["duplicate_executed"])
    p50 = statistics.median(latencies) if latencies else 0.0
    p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies or [0.0])

    print("local synthetic reliability benchmark")
    print(f"tasks={args.tasks} workers={args.workers}")
    print(f"throughput_tasks_per_s={args.tasks / elapsed:.2f}")
    print(f"p50_latency_ms={p50:.1f}")
    print(f"p95_latency_ms={p95:.1f}")
    print(f"errors={errors}")
    print(f"duplicate_execution_count={duplicate_count}")
    print(f"actual_tool_runs={tool.calls}")
    return 0 if errors == 0 and duplicate_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
