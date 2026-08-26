"""Task artifacts — letting you open the files the agent actually wrote.

The agent produces real deliverables. The README's flagship run ends with
`report.txt` written to the task workspace, and `artifacts.spill()` puts
every oversized tool result there too. Until now none of it was reachable
from the API: the UI could tell you a file had been written and then not
show it to you, which is the worst of both (a claim with no evidence).

TWO KINDS OF FILE, AND WHY THEY ARE LABELLED APART

    deliverable   what the agent chose to write, via file_io -- the answer
    spillover     what the HARNESS wrote to keep the prompt small, under
                  _artifacts/ (see artifacts.py)

They live in the same tree but they are not the same thing to a reader: one
is the point of the task, the other is plumbing. Collapsing them into one
undifferentiated file list would bury a two-line report under a dozen 30KB
JSON dumps.

THE PATH RULE

Every path is resolved through FileIOTool._resolve_within_sandbox -- the
same function the tool itself uses, deliberately imported rather than
reimplemented. A second copy of a security check is a second thing to get
wrong later, and this one is reachable from the network, which the tool's
own call path is not.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select

from agentsys.artifacts import ARTIFACT_DIRNAME
from agentsys.auth import get_current_user
from agentsys.config import settings
from agentsys.db.models import Task, User
from agentsys.db.session import get_session
from agentsys.tools.file_io import FileIOTool

router = APIRouter(prefix="/v1/tasks", tags=["artifacts"])

# A workspace file can be a 30MB scrape. This endpoint renders text in a
# browser panel, so it returns a bounded head and says so, rather than
# streaming something no one will read into a JSON body.
MAX_READ_CHARS = 200_000


def _owned_task_dir(task_id: str, user: User) -> Path:
    """Resolve a task's workspace, 404ing unless this user owns the task.
    Ownership is checked against Postgres, never against the path -- a task
    id is guessable and a directory existing proves nothing about who it
    belongs to."""
    with get_session() as session:
        task = session.exec(select(Task).where(Task.id == task_id, Task.owner_id == user.id)).first()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
    return Path(settings.workspace_dir) / task_id


@router.get("/{task_id}/artifacts")
def list_artifacts(task_id: str, user: User = Depends(get_current_user)) -> dict:
    """Every file in the task's workspace, newest first, split by kind.

    An empty list is a normal answer, not an error: plenty of tasks never
    touch the filesystem, and a task whose workspace was never created is
    indistinguishable from one that wrote nothing.
    """
    task_dir = _owned_task_dir(task_id, user)
    if not task_dir.is_dir():
        return {"task_id": task_id, "files": [], "total_bytes": 0}

    root = task_dir.resolve()
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        files.append(
            {
                "path": relative,
                "bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                # See the module docstring: plumbing and product, told apart.
                "kind": "spillover" if relative.startswith(f"{ARTIFACT_DIRNAME}/") else "deliverable",
            }
        )

    files.sort(key=lambda f: f["modified_at"], reverse=True)
    return {
        "task_id": task_id,
        "files": files,
        "total_bytes": sum(f["bytes"] for f in files),
    }


@router.get("/{task_id}/artifacts/content")
def read_artifact(
    task_id: str,
    path: str = Query(..., description="Path relative to the task workspace"),
    user: User = Depends(get_current_user),
) -> dict:
    """One file's text. Binary content is reported as such rather than
    mangled into a JSON string -- the agent's tools write text, so a binary
    file here is a user-uploaded attachment and saying so beats guessing an
    encoding."""
    task_dir = _owned_task_dir(task_id, user)

    resolved = FileIOTool._resolve_within_sandbox(task_dir, path)
    if resolved is None:
        # Deliberately the same shape as "not found": a traversal attempt
        # should learn nothing about what exists outside the sandbox.
        raise HTTPException(status_code=404, detail="File not found")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    size = resolved.stat().st_size
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {
            "path": path,
            "bytes": size,
            "binary": True,
            "content": None,
            "truncated": False,
        }

    truncated = len(content) > MAX_READ_CHARS
    return {
        "path": path,
        "bytes": size,
        "binary": False,
        "content": content[:MAX_READ_CHARS],
        "truncated": truncated,
    }
