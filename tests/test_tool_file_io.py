from __future__ import annotations

from pathlib import Path

import pytest

from agentsys.config import settings
from agentsys.tools.file_io import FileIOTool

TASK_ID = "test-task-123"


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    return tmp_path


@pytest.fixture
def tool():
    return FileIOTool()


def test_write_then_read_roundtrip(tool):
    write_result = tool.run(action="write", task_id=TASK_ID, path="notes.txt", content="hello world")
    assert write_result.success

    read_result = tool.run(action="read", task_id=TASK_ID, path="notes.txt")
    assert read_result.success
    assert read_result.output["content"] == "hello world"


def test_write_creates_parent_directories(tool):
    result = tool.run(action="write", task_id=TASK_ID, path="nested/dir/file.txt", content="data")
    assert result.success

    read_result = tool.run(action="read", task_id=TASK_ID, path="nested/dir/file.txt")
    assert read_result.success
    assert read_result.output["content"] == "data"


def test_list_includes_written_file(tool):
    tool.run(action="write", task_id=TASK_ID, path="a.txt", content="a")
    tool.run(action="write", task_id=TASK_ID, path="subdir/b.txt", content="b")

    result = tool.run(action="list", task_id=TASK_ID, path="")
    assert result.success
    files = set(result.output["files"])
    assert "a.txt" in files
    assert str(Path("subdir") / "b.txt") in files


def test_read_nonexistent_file_returns_error_not_exception(tool):
    result = tool.run(action="read", task_id=TASK_ID, path="does_not_exist.txt")
    assert result.success is False
    assert result.error is not None


def test_path_traversal_relative_is_blocked(tool, isolated_workspace):
    # task dir is <workspace_dir>/<task_id>, so one level up lands in workspace_dir itself
    escape_target = isolated_workspace / "evil.txt"

    result = tool.run(action="write", task_id=TASK_ID, path="../evil.txt", content="pwned")

    assert result.success is False
    assert result.error == "path escapes the task workspace"
    assert not escape_target.exists()


def test_path_traversal_absolute_is_blocked(tool, tmp_path):
    outside_dir = tmp_path.parent / "outside-sandbox"
    outside_dir.mkdir(exist_ok=True)
    escape_target = outside_dir / "evil.txt"

    result = tool.run(
        action="write",
        task_id=TASK_ID,
        path=str(escape_target),
        content="pwned",
    )

    assert result.success is False
    assert result.error == "path escapes the task workspace"
    assert not escape_target.exists()


def test_different_tasks_are_isolated(tool):
    tool.run(action="write", task_id="task-a", path="secret.txt", content="a-only")

    result = tool.run(action="read", task_id="task-b", path="secret.txt")
    assert result.success is False


def test_unknown_action_returns_error(tool):
    result = tool.run(action="delete", task_id=TASK_ID, path="x.txt")
    assert result.success is False
    assert result.error is not None
