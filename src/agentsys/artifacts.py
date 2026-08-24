"""Spillover for oversized tool results -- the context-engineering half of
keeping the agent's prompt small.

The problem this exists for, measured on a real run: a single 28,783-char
Google Drive read landed in subtask.output at step 6, and _gather_prior_context
then re-sent it, in full, on every later step -- roughly 43,000 tokens of
re-transmission from one tool call, on a task whose prompt:output ratio
ended up at 35.8:1.

The fix is progressive disclosure. The full payload goes to a file in the
task's own workspace; the prompt gets a preview plus a path. If the preview
answers the question the agent moves on having paid ~1.2k chars instead of
~29k; if it doesn't, the agent reads the file back with file_io, which is
one deliberate step rather than a permanent tax on every step.

Written with Path directly, NOT through FileIOTool: file_io's write action
is approval-gated (Tool.needs_approval), and this is the harness persisting
a result the agent already legitimately fetched, not the agent choosing to
write. Routing it through the tool would pop a human approval prompt on
every large tool call. Reads stay through file_io, which is ungated, so
dereferencing a pointer needs nothing special.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentsys.config import settings

ARTIFACT_DIRNAME = "_artifacts"

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")


def _safe_component(value: str) -> str:
    """Tool names are first-party constants today, but MCP servers can
    contribute tool names from third-party config (see registry's
    _register_mcp_tools) -- so anything reaching a filesystem path gets
    sanitized rather than trusted.

    Dots are stripped along with everything else non-alphanumeric: the
    ".json" suffix is appended by spill() itself, so a component never needs
    one, and disallowing them outright means no input can produce a ".." in
    the path at all -- rather than relying on "there's no slash, so the dots
    are harmless", which is true but only incidentally."""
    return _UNSAFE_FILENAME_CHARS.sub("_", value)[:80]


def artifact_dir(task_id: str) -> Path:
    return Path(settings.workspace_dir) / task_id / ARTIFACT_DIRNAME


def spill(task_id: str, subtask_id: str, tool_name: str, output_text: str) -> dict:
    """Writes output_text to the task's artifact directory and returns the
    pointer dict that goes into the prompt in its place. The returned
    `full_output_path` is relative to the task workspace, which is exactly
    what file_io's `path` argument expects -- the agent can paste it
    straight back without knowing anything about absolute paths."""
    directory = artifact_dir(task_id)
    directory.mkdir(parents=True, exist_ok=True)

    filename = f"{_safe_component(subtask_id)}_{_safe_component(tool_name)}.json"
    (directory / filename).write_text(output_text, encoding="utf-8")

    relative_path = f"{ARTIFACT_DIRNAME}/{filename}"
    return {
        "_truncated": True,
        "total_chars": len(output_text),
        "preview": output_text[: settings.tool_output_preview_chars],
        "full_output_path": relative_path,
        "hint": (
            f"This result was {len(output_text)} characters, too large to keep in context in "
            f"full. Only the first {settings.tool_output_preview_chars} characters are shown "
            f"above. The COMPLETE result is saved in this task's workspace -- if the preview "
            f"doesn't contain what you need, read it with file_io using "
            f'{{"action": "read", "path": "{relative_path}"}}. Do not re-run the original tool '
            f"call to see the rest; it will just be truncated again."
        ),
    }
