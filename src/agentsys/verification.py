"""Phase 4 verification: did the observation satisfy the intended outcome?

This module deliberately stays small. It is not a verifier registry and not a
planner. It reads the evidence Phase 1-3 already produce: the Subtask, the
latest ToolCall, tool failure classification, and the task workspace.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from agentsys.config import settings
from agentsys.db.models import Subtask, SubtaskStatus, ToolCall
from agentsys.execution import FailureKind, classify_failure, is_retryable, safety_of
from agentsys.tools import registry as registry_module


class VerificationRoute(str, Enum):
    PASS = "pass"
    REVIEW = "review"
    RETRY = "retry"
    REPLAN = "replan"
    REQUIRE_HUMAN = "require_human"
    FAIL = "fail"


class VerificationResult(BaseModel):
    verified: bool
    reason: str
    retryable: bool = False
    needs_replan: bool = False
    needs_human: bool = False
    route: VerificationRoute
    method: str = "deterministic"


_RESULT_COUNT = re.compile(r"(?:result|results|row|rows|file|files|source|sources)_?count\s*>=\s*(\d+)", re.I)
_AT_LEAST = re.compile(r"at\s+least\s+(\d+)\s+(?:result|results|row|rows|file|files|source|sources)", re.I)
_EXIT_CODE = re.compile(r"exit_?code\s*(?:==|=)\s*(-?\d+)", re.I)
_FILE_EXISTS = re.compile(r"file_exists\s*:\s*(.+)", re.I)
_OUTPUT_CONTAINS = re.compile(r"output_contains\s*:\s*(.+)", re.I)
_KNOWN_IDEMPOTENT_TOOLS = {
    "web_search",
    "knowledge_search",
    "generate_tweet",
    "file_io",
    "db_query",
    "gmail_search",
    "gmail_read",
    "google_drive_search",
    "google_drive_read",
    "google_photos_pick",
}


def _is_supported_file_write_criteria(criteria: str) -> bool:
    lower = criteria.strip().lower()
    return bool(_FILE_EXISTS.search(criteria) or lower in {"file_exists", "content_matches"})


def _workspace_path(task_id: str, rel_path: str) -> Path | None:
    root = (Path(settings.workspace_dir) / task_id).resolve()
    candidate = (root / rel_path).resolve()
    if candidate == root or candidate.is_relative_to(root):
        return candidate
    return None


def _countable(output: dict) -> int | None:
    for key in ("results", "rows", "files", "sources", "items"):
        value = output.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def _required_count(criteria: str) -> int | None:
    if match := _RESULT_COUNT.search(criteria):
        return int(match.group(1))
    if match := _AT_LEAST.search(criteria):
        return int(match.group(1))
    if "nonempty" in criteria.lower():
        return 1
    return None


def _tool_failure_result(call: ToolCall, criteria: str) -> VerificationResult:
    error = str((call.output or {}).get("error") or "")
    if "outcome is unknown" in error or "not performed again" in error:
        return VerificationResult(
            verified=False,
            reason="tool outcome is ambiguous and cannot be verified safely",
            needs_human=True,
            route=VerificationRoute.REQUIRE_HUMAN,
        )

    kind = classify_failure(error)
    safety = None
    if call.tool_name in _KNOWN_IDEMPOTENT_TOOLS:
        from agentsys.execution import ExecutionSafety

        safety = ExecutionSafety.IDEMPOTENT
    elif registry_module._registry is not None:  # type: ignore[attr-defined]
        try:
            safety = safety_of(registry_module.get_registry().get(call.tool_name))
        except KeyError:
            safety = None

    if safety is not None and is_retryable(kind, safety):
        return VerificationResult(
            verified=False,
            reason=f"tool failed with retryable {kind.value} error",
            retryable=True,
            route=VerificationRoute.RETRY,
        )
    if kind in (FailureKind.AUTH, FailureKind.UNKNOWN):
        return VerificationResult(
            verified=False,
            reason=f"tool failed with {kind.value} error that needs a different approach or human input",
            needs_replan=True,
            route=VerificationRoute.REPLAN,
        )
    return VerificationResult(
        verified=False,
        reason=f"tool failed with {kind.value} error",
        needs_replan=True,
        route=VerificationRoute.REPLAN,
    )


def _verify_file_io(task_id: str, call: ToolCall, criteria: str) -> VerificationResult:
    args = call.input or {}
    output = call.output or {}
    action = args.get("action")

    if action == "write":
        if not _is_supported_file_write_criteria(criteria):
            return VerificationResult(
                verified=False,
                reason="unsupported file write success criteria; semantic review required",
                route=VerificationRoute.REVIEW,
                method="semantic_fallback",
            )
        explicit = _FILE_EXISTS.search(criteria)
        rel_path = explicit.group(1).strip() if explicit else str(args.get("path") or "")
        path = _workspace_path(task_id, rel_path)
        if path is None:
            return VerificationResult(
                verified=False,
                reason="file verification path escapes the task workspace",
                needs_human=True,
                route=VerificationRoute.REQUIRE_HUMAN,
            )
        if not path.is_file():
            return VerificationResult(
                verified=False,
                reason=f"expected file does not exist: {rel_path}",
                retryable=True,
                route=VerificationRoute.RETRY,
            )
        expected = args.get("content")
        if expected is not None and path.read_text(encoding="utf-8") != expected:
            return VerificationResult(
                verified=False,
                reason=f"file content did not match expected content: {rel_path}",
                retryable=True,
                route=VerificationRoute.RETRY,
            )
        return VerificationResult(
            verified=True,
            reason=f"verified file exists with expected content: {rel_path}",
            route=VerificationRoute.PASS,
        )

    if action == "read":
        if "content" in output:
            return VerificationResult(
                verified=False,
                reason="file read returned content; semantic review should decide usefulness",
                route=VerificationRoute.REVIEW,
                method="semantic_fallback",
            )
        return VerificationResult(
            verified=False,
            reason="file read succeeded without content",
            needs_replan=True,
            route=VerificationRoute.REPLAN,
        )

    if action == "list":
        if isinstance(output.get("files"), list):
            return _verify_count_or_review(output, criteria, "file list")
        return VerificationResult(
            verified=False,
            reason="file list succeeded without a files list",
            needs_replan=True,
            route=VerificationRoute.REPLAN,
        )

    return VerificationResult(
        verified=False,
        reason="unknown file_io action in observation",
        needs_replan=True,
        route=VerificationRoute.REPLAN,
    )


def _verify_count_or_review(output: dict, criteria: str, label: str) -> VerificationResult:
    expected = _required_count(criteria)
    if expected is None:
        return VerificationResult(
            verified=False,
            reason=f"{label} has valid structure; semantic review should decide usefulness",
            route=VerificationRoute.REVIEW,
            method="semantic_fallback",
        )
    actual = _countable(output)
    if actual is None:
        return VerificationResult(
            verified=False,
            reason=f"{label} has no countable result collection",
            needs_replan=True,
            route=VerificationRoute.REPLAN,
        )
    if actual >= expected:
        return VerificationResult(
            verified=True,
            reason=f"verified result count {actual} >= {expected}",
            route=VerificationRoute.PASS,
        )
    return VerificationResult(
        verified=False,
        reason=f"result count {actual} < required {expected}",
        needs_replan=True,
        route=VerificationRoute.REPLAN,
    )


def _verify_code_execution(call: ToolCall, criteria: str) -> VerificationResult:
    output = call.output or {}
    for key in ("stdout", "stderr", "exit_code"):
        if key not in output:
            return VerificationResult(
                verified=False,
                reason=f"code execution output missing {key}",
                needs_replan=True,
                route=VerificationRoute.REPLAN,
            )
    if match := _EXIT_CODE.search(criteria):
        expected = int(match.group(1))
        actual = output.get("exit_code")
        if actual == expected:
            return VerificationResult(
                verified=True,
                reason=f"verified exit_code == {expected}",
                route=VerificationRoute.PASS,
            )
        return VerificationResult(
            verified=False,
            reason=f"exit_code {actual} != expected {expected}",
            needs_replan=True,
            route=VerificationRoute.REPLAN,
        )
    if match := _OUTPUT_CONTAINS.search(criteria):
        needle = match.group(1).strip()
        haystack = f"{output.get('stdout') or ''}\n{output.get('stderr') or ''}"
        if needle in haystack:
            return VerificationResult(
                verified=True,
                reason=f"verified output contains {needle!r}",
                route=VerificationRoute.PASS,
            )
        return VerificationResult(
            verified=False,
            reason=f"output did not contain {needle!r}",
            needs_replan=True,
            route=VerificationRoute.REPLAN,
        )
    return VerificationResult(
        verified=False,
        reason="code execution produced structured output; semantic review should decide usefulness",
        route=VerificationRoute.REVIEW,
        method="semantic_fallback",
    )


def verify_step(task_id: str, subtask: Subtask, call: ToolCall | None, *, tool_success: bool) -> VerificationResult:
    criteria = (subtask.success_criteria or "").strip()
    if not call:
        return VerificationResult(
            verified=False,
            reason="no tool observation; semantic review should decide pure reasoning output",
            route=VerificationRoute.REVIEW,
            method="semantic_fallback",
        )
    if not tool_success or call.success is not True:
        return _tool_failure_result(call, criteria)

    if not criteria or criteria.lower() == "semantic":
        return VerificationResult(
            verified=False,
            reason="no deterministic success criteria; semantic review required",
            route=VerificationRoute.REVIEW,
            method="semantic_fallback",
        )

    output = call.output or {}
    if call.tool_name == "file_io":
        return _verify_file_io(task_id, call, criteria)
    if call.tool_name == "db_query":
        if isinstance(output.get("columns"), list) and isinstance(output.get("rows"), list):
            return _verify_count_or_review(output, criteria, "db_query result")
        return VerificationResult(
            verified=False,
            reason="db_query output missing columns/rows",
            needs_replan=True,
            route=VerificationRoute.REPLAN,
        )
    if call.tool_name == "code_execution":
        return _verify_code_execution(call, criteria)

    if isinstance(output, dict):
        return _verify_count_or_review(output, criteria, f"{call.tool_name} result")
    return VerificationResult(
        verified=False,
        reason="tool output is not structured enough to verify",
        needs_replan=True,
        route=VerificationRoute.REPLAN,
    )


_REQUESTED_FILE = re.compile(r"\b([\w.-]+\.(?:txt|md|json|csv|py|html|pdf))\b", re.I)


def verify_goal(task_id: str, request_text: str, subtasks: list[Subtask]) -> VerificationResult:
    blocking = [s for s in subtasks if s.status in {SubtaskStatus.FAILED, SubtaskStatus.ESCALATED}]
    if blocking:
        return VerificationResult(
            verified=False,
            reason=f"{len(blocking)} subtask(s) are failed or escalated",
            needs_replan=True,
            route=VerificationRoute.REPLAN,
        )

    done = [s for s in subtasks if s.status == SubtaskStatus.DONE]
    if not done:
        return VerificationResult(
            verified=False,
            reason="no verified completed subtasks exist",
            needs_replan=True,
            route=VerificationRoute.REPLAN,
        )

    if match := _REQUESTED_FILE.search(request_text or ""):
        rel_path = match.group(1)
        path = _workspace_path(task_id, rel_path)
        if path is None or not path.is_file():
            return VerificationResult(
                verified=False,
                reason=f"requested file is not present: {rel_path}",
                needs_replan=True,
                route=VerificationRoute.REPLAN,
            )
        return VerificationResult(
            verified=True,
            reason=f"requested file exists: {rel_path}",
            route=VerificationRoute.PASS,
        )

    return VerificationResult(
        verified=False,
        reason="goal completion is semantic and needs LLM judgment",
        route=VerificationRoute.REVIEW,
        method="semantic_fallback",
    )
