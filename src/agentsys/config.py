import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    reviewer_llm_model: str = "gpt-4o"
    """Deliberately a different tier than llm_model, not the same model the
    Specialist/Supervisor use -- a reviewer sharing blind spots with the
    thing it's reviewing can miss what a genuinely independent judge would
    catch. Honest scope note: this is a different model *tier*, not yet a
    different *provider* -- llm.py only wires up OpenAI."""

    database_url: str = "postgresql+psycopg://agent:agent@localhost:5432/agentsys"
    redis_url: str = "redis://localhost:6379/0"
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    rag_api_url: str = "http://localhost:8000"
    """Base URL for src/rag's separate FastAPI service. The two packages don't
    import each other (see docs/MERGE.md) -- this is the one HTTP seam between
    them, used by tools/knowledge_search.py so the agent can actually use
    documents uploaded to Knowledge. Overridden to the docker-network hostname
    (http://rag-api:8000) in docker-compose.yml for the api/worker containers."""

    cors_allowed_origins: str = "http://localhost:3000"
    """Comma-separated, not a JSON list -- a human typing this into a
    deployment platform's env-var UI (Railway, etc.) shouldn't have to get
    JSON-array syntax right. Split via cors_allowed_origins_list below."""

    enable_code_execution: bool = True
    """code_execution sandboxes Python by spawning a sibling Docker container,
    which needs the host's Docker socket (see docker-compose.yml's worker
    volume mount) -- unavailable on platforms like Railway that don't expose
    one. Set false there so the tool degrades out of the registry cleanly
    (same shape as every other tool's ImportError handling in registry.py)
    instead of failing every call. True is the right default everywhere this
    actually has Docker access, which is every environment except a
    Docker-socket-less cloud deploy."""

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    max_subtask_retries: int = 2
    plan_confidence_escalation_threshold: int = 3
    review_escalation_threshold: int = 2
    max_parallel_subtasks: int = 4
    """Caps how many dependency-ready subtasks select_subtask_node fans out to
    concurrently in one wave -- bounds LLM/tool concurrency for cost and
    rate-limit safety, not a correctness requirement."""

    docker_sandbox_image: str = "python:3.11-slim"
    workspace_dir: str = str(PROJECT_ROOT / "data" / "workspace")

    max_delegation_depth: int = 1
    """A sub-agent at this depth cannot itself call delegate_subagent --
    bounds recursion to exactly one real nested level rather than allowing
    unbounded sub-agent spawning."""
    max_subagent_steps: int = 4
    """Caps a single sub-agent run's own tool-call loop."""

    mcp_servers: list[dict] = [
        {
            "name": "company_internal",
            "command": sys.executable,
            "args": ["-m", "agentsys.mcp_servers.company_internal"],
            "env": {"PYTHONPATH": str(PROJECT_ROOT / "src")},
        }
    ]
    """External MCP servers whose tools are discovered at startup and registered
    alongside the first-party ones. This list IS the plugin interface: adding
    someone else's server -- any language, any tool set -- is an entry here, not
    a code change.

    The default entry is this repo's own demo server, so the capability is live
    out of the box rather than only exercised in tests. sys.executable plus an
    explicit PYTHONPATH is used rather than a bare "python" so the same default
    works both locally (where agentsys is only on the path via src/) and in
    Docker (where it's pip-installed) -- and so the subprocess always uses the
    same interpreter/venv as its parent."""

    llm_pricing: dict[str, dict[str, float]] = {
        "gpt-4o-mini": {"prompt": 0.15 / 1_000_000, "completion": 0.60 / 1_000_000},
        "gpt-4o": {"prompt": 2.50 / 1_000_000, "completion": 10.00 / 1_000_000},
    }
    """Approximate USD per token, prompt vs completion (they're priced
    differently). Used only to surface a cost estimate in /v1/analytics --
    not billing-accurate, just directionally useful."""


settings = Settings()
