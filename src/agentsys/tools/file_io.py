"""Sandboxed file I/O: every task is confined to its own workspace subdirectory."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentsys.config import settings
from agentsys.policy import ActionType, Risk
from agentsys.sanitize import wrap_untrusted
from agentsys.tools.base import Tool, ToolResult


class FileIOArgs(BaseModel):
    """task_id is absent on purpose: it selects WHICH task's sandbox this call
    is confined to, so it is injected from the running task (graph/nodes.py's
    _injected_kwargs) and is not something the model may name. It used to be a
    setdefault, which meant a proposal supplying task_id won."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["read", "write", "list"]
    path: str = Field(description="Relative path within the task workspace -- no leading slash, no '..'.")
    content: str | None = Field(default=None, description="Required for action='write', ignored otherwise.")


class FileIOTool(Tool):
    name = "file_io"
    args_model = FileIOArgs
    action_type = ActionType.READ
    risk = Risk.LOW
    """The BASELINE is the read: action="read"/"list" touch nothing. A call
    with action="write" is raised to LOCAL_WRITE/MEDIUM by policy._effective
    and gated -- this is the one registered tool whose decision genuinely
    depends on its arguments, and it needed that before policy existed (it
    shipped a needs_approval override for exactly this, now deleted).

    Sandbox escapes are NOT policy's call: _resolve_within_sandbox below
    refuses any path outside the task directory, and a second copy of that
    check in policy would be the weaker one."""
    description = (
        "Reads, writes, or lists files inside this task's sandboxed workspace directory. "
        "Arguments: action (str, required, one of 'read'/'write'/'list'), "
        "path (str, required, relative path within the task workspace -- no leading "
        "slash and no '..'), content (str, required only for action='write'). "
        "Do not pass task_id — it's filled in automatically. Examples: write a file "
        "with {\"action\": \"write\", \"path\": \"report.txt\", \"content\": \"...\"}; "
        "read it back with {\"action\": \"read\", \"path\": \"report.txt\"} (returns "
        "{content: \"...\"}); see what's already there with "
        "{\"action\": \"list\", \"path\": \".\"} (returns {files: [...]})."
    )

    def run(self, action: str, task_id: str, path: str, content: str | None = None) -> ToolResult:
        task_dir = Path(settings.workspace_dir) / task_id
        resolved = self._resolve_within_sandbox(task_dir, path)
        if resolved is None:
            return ToolResult(success=False, error="path escapes the task workspace")

        if action == "write":
            return self._write(resolved, content)
        if action == "read":
            return self._read(resolved)
        if action == "list":
            return self._list(resolved)
        return ToolResult(success=False, error=f"unknown action '{action}'")

    @staticmethod
    def _resolve_within_sandbox(task_dir: Path, path: str) -> Path | None:
        task_dir_resolved = task_dir.resolve()
        candidate = (task_dir / path).resolve()
        if candidate != task_dir_resolved and not candidate.is_relative_to(task_dir_resolved):
            return None
        return candidate

    @staticmethod
    def _write(resolved: Path, content: str | None) -> ToolResult:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content or "", encoding="utf-8")
        return ToolResult(success=True, output={"path": str(resolved)})

    @staticmethod
    def _read(resolved: Path) -> ToolResult:
        if not resolved.is_file():
            return ToolResult(success=False, error=f"file not found: {resolved}")
        # A file in the workspace could be a user-supplied attachment or the
        # output of an earlier web_search/gmail/drive call written back to
        # disk -- neither is the agent's own trusted instruction, so it's
        # framed the same as any other fetched content (see
        # sanitize.wrap_untrusted).
        content = wrap_untrusted(resolved.read_text(encoding="utf-8"), "file_io")
        return ToolResult(success=True, output={"content": content})

    @staticmethod
    def _list(resolved: Path) -> ToolResult:
        if not resolved.exists():
            return ToolResult(success=False, error=f"directory not found: {resolved}")
        if not resolved.is_dir():
            return ToolResult(success=False, error=f"not a directory: {resolved}")
        files = sorted(str(p.relative_to(resolved)) for p in resolved.rglob("*") if p.is_file())
        return ToolResult(success=True, output={"files": files})
