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

import json
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


_POINTER_KEYS = {"_truncated", "preview", "full_output_path", "hint"}


def for_synthesis(output_text: str | None) -> str:
    """Strip spill plumbing out of a step output before it reaches synthesis.

    The pointer dict spill() returns is a CONTROL AFFORDANCE FOR THE LOOP, not
    content for the answer. Its `hint` is literally an instruction addressed
    to the model ("read it with file_io using {...}"), and it rides inside the
    tool output's data. agent_step needs that -- it is how the model knows it
    can go get the rest. synthesize does not: it writes prose for a human who
    has no workspace, no file_io and no idea what an artifact is.

    Observed live: a Drive lookup answered with "you may need to read it from
    the saved file at the path _artifacts/06b01af8-...json", which is a
    correct sentence addressed to entirely the wrong reader.

    So the preview survives (that is real content) and the mechanics do not.
    Anything that isn't a spill pointer passes through untouched.
    """
    if not output_text:
        return output_text or ""
    try:
        parsed = json.loads(output_text)
    except (TypeError, ValueError):
        return output_text
    if not isinstance(parsed, dict) or not parsed.get("_truncated"):
        return output_text

    preview = parsed.get("preview", "")
    total = parsed.get("total_chars")
    note = (
        f"\n[This result was long ({total} characters); the excerpt above is its "
        f"beginning.]" if total else ""
    )
    # Any sibling keys the tool itself produced (a filename, an id) are kept:
    # they are genuine content, and only the four plumbing keys are dropped.
    extra = {k: v for k, v in parsed.items() if k not in _POINTER_KEYS}
    if extra:
        return f"{json.dumps(extra)}\n{preview}{note}"
    return f"{preview}{note}"
