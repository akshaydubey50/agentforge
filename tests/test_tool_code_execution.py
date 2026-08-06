"""Real tests against a live Docker daemon -- each spins up an actual container.

These are slow (seconds each, not milliseconds) by design: the whole point of this
tool is Docker-enforced isolation, and mocking the Docker SDK would only prove the
mock was configured correctly, not that network_disabled=True or the timeout kill
path actually work.
"""

from __future__ import annotations

import time

import docker
import pytest

from agentsys.tools.code_execution import CodeExecutionTool


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="docker daemon not reachable")


@pytest.fixture
def tool():
    return CodeExecutionTool()


def test_simple_expression_runs_and_captures_stdout(tool):
    result = tool.run(code="print(2 + 2)")

    assert result.success is True
    assert result.output["exit_code"] == 0
    assert "4" in result.output["stdout"]


def test_nonzero_exit_reported_without_crashing_tool(tool):
    result = tool.run(code="import sys; sys.exit(1)")

    assert result.success is True
    assert result.output["exit_code"] == 1
    assert result.error is None


def test_uncaught_exception_captured_in_stderr(tool):
    result = tool.run(code="raise ValueError('boom')")

    assert result.success is True
    assert result.output["exit_code"] == 1
    assert "ValueError" in result.output["stderr"]
    assert "boom" in result.output["stderr"]


def test_network_is_disabled(tool):
    code = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('http://example.com', timeout=3)\n"
        "    print('NETWORK_REACHED')\n"
        "except Exception as e:\n"
        "    print('NETWORK_BLOCKED', type(e).__name__)\n"
    )

    result = tool.run(code=code, timeout_s=10)

    assert result.success is True
    assert "NETWORK_BLOCKED" in result.output["stdout"]
    assert "NETWORK_REACHED" not in result.output["stdout"]


def test_timeout_kills_container_instead_of_waiting_it_out(tool):
    start = time.monotonic()
    result = tool.run(code="import time; time.sleep(30)", timeout_s=3)
    elapsed = time.monotonic() - start

    assert elapsed < 10, f"timeout was not enforced, call took {elapsed:.1f}s"
    assert result.success is False
    assert result.error is not None
    assert "timed out" in result.error


def test_docker_unreachable_returns_failure_not_exception(tool, monkeypatch):
    def _raise_from_env(*args, **kwargs):
        raise docker.errors.DockerException("simulated: daemon unreachable")

    monkeypatch.setattr(docker, "from_env", _raise_from_env)

    result = tool.run(code="print('hi')")

    assert result.success is False
    assert result.error is not None
