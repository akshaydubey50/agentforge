"""Sandboxed file I/O: every task is confined to its own workspace subdirectory."""

from __future__ import annotations

from pathlib import Path

from agentsys.config import settings
from agentsys.tools.base import Tool, ToolResult


class FileIOTool(Tool):
    name = "file_io"
    description = (
        "Reads, writes, or lists files inside this task's sandboxed workspace directory. "
        "Arguments: action (str, required, one of 'read'/'write'/'list'), "
        "path (str, required, relative path within the task workspace), "
        "content (str, required only for action='write'). "
        "Do not pass task_id — it's filled in automatically."
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
        return ToolResult(success=True, output={"content": resolved.read_text(encoding="utf-8")})

    @staticmethod
    def _list(resolved: Path) -> ToolResult:
        if not resolved.exists():
            return ToolResult(success=False, error=f"directory not found: {resolved}")
        if not resolved.is_dir():
            return ToolResult(success=False, error=f"not a directory: {resolved}")
        files = sorted(str(p.relative_to(resolved)) for p in resolved.rglob("*") if p.is_file())
        return ToolResult(success=True, output={"files": files})
