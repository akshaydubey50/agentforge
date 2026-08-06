"""Sandboxed Python code execution inside an ephemeral, network-isolated Docker container.

Security posture (be explicit about what this does and does not buy you): a Docker
container is a real, meaningful boundary against accidental damage and casual abuse —
no network access, capped memory, a capped process count, and no state that survives
past the single run. It is *not* a boundary against a determined attacker. Container
escapes (kernel exploits, misconfigured capabilities, shared-kernel side channels) are
a known risk class, and this tool makes no attempt to add gVisor/Kata-style
hardening, seccomp/apparmor profiles, or a rootless runtime on top of the Docker
default. For a portfolio/interview context the honest framing is: "meaningfully more
sandboxed than bare subprocess execution, not a substitute for a hardened multi-tenant
sandbox (Firecracker microVMs, gVisor, etc.) if this ever ran genuinely hostile code
at scale."

docker-py timeout mechanics, verified empirically (see module tests): the blocking
`containers.run()` call does not enforce a wall-clock timeout on the *container* at
all. The pattern used here is `run(detach=True)` followed by `container.wait(timeout=)`.
That `timeout` is a client-side HTTP read timeout on the docker-py/requests connection
to the daemon -- when it fires, docker-py raises (a `requests.exceptions.ConnectionError`
wrapping a read timeout); the container itself is untouched and keeps running. So a
timeout is only real once we catch that exception and explicitly `container.kill()`
it ourselves, which is what `_run_container` below does.

Convention for `success` vs. `error`: `success=True` means the tool successfully ran
the code to completion inside the sandbox, *regardless of the script's own exit code*
-- a script that raises or calls `sys.exit(1)` is a normal, successful tool call whose
`output["exit_code"]` is nonzero. `success=False` is reserved for the tool failing to
do its job: Docker being unreachable, the image missing, or the run timing out (timeout
is treated as a tool-level failure with `error="timed out after {timeout_s}s"`, since
"we don't know what the code would have produced" is a distinct outcome from "the code
ran and told us its result").
"""

from __future__ import annotations

import docker
import docker.errors

from agentsys.config import settings
from agentsys.tools.base import Tool, ToolResult

MEMORY_LIMIT = "128m"
PIDS_LIMIT = 64
KILL_GRACE_S = 5


class CodeExecutionTool(Tool):
    name = "code_execution"
    description = (
        "Runs untrusted Python code in an ephemeral, network-isolated Docker container "
        "(no network, memory/process caps, hard timeout). A real sandbox against "
        "accidents and casual abuse, not a hardened boundary against a determined "
        "attacker -- container escapes remain a known risk class. "
        "Arguments: code (str, required, a Python script), "
        "timeout_s (int, optional, default 10). "
        "IMPORTANT: this runs as a script, not a REPL — a bare expression like "
        "`result` produces no visible output. You MUST call print(result) to see "
        "any value."
    )

    def run(self, code: str, timeout_s: int = 10) -> ToolResult:
        try:
            client = docker.from_env()
        except docker.errors.DockerException as exc:
            return ToolResult(success=False, error=f"could not connect to docker: {exc}")

        try:
            return self._run_container(client, code, timeout_s)
        except docker.errors.DockerException as exc:
            return ToolResult(success=False, error=f"docker error: {exc}")
        finally:
            client.close()

    def _run_container(self, client: docker.DockerClient, code: str, timeout_s: int) -> ToolResult:
        container = client.containers.run(
            image=settings.docker_sandbox_image,
            command=["python", "-c", code],
            detach=True,
            network_disabled=True,
            mem_limit=MEMORY_LIMIT,
            pids_limit=PIDS_LIMIT,
        )
        try:
            timed_out = False
            exit_code: int | None
            try:
                wait_result = container.wait(timeout=timeout_s)
                exit_code = wait_result.get("StatusCode")
            except Exception:
                # container.wait's timeout is a client-side HTTP read timeout, not a
                # container-side one: the container is still running here and must be
                # killed explicitly, otherwise it would run forever.
                timed_out = True
                exit_code = None
                container.kill()
                try:
                    wait_result = container.wait(timeout=KILL_GRACE_S)
                    exit_code = wait_result.get("StatusCode")
                except Exception:
                    pass

            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            output = {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
            if timed_out:
                return ToolResult(success=False, output=output, error=f"timed out after {timeout_s}s")
            return ToolResult(success=True, output=output)
        finally:
            container.remove(force=True)
