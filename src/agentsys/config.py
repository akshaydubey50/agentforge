import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    """Not required today -- llm.py routes to it only if llm_model/
    reviewer_llm_model is ever pointed at an "anthropic/..." model, or a
    future claude_web_search tool is added. Present now so that switching
    providers is a config change, not a code change."""
    tavily_api_key: str = ""
    """Powers tools/web_search.py. Falls back to OpenAI's own hosted search,
    then a DuckDuckGo HTML scrape, in that order -- see that file's
    docstring for the full fallback chain and why it exists."""
    llm_model: str = "openai/gpt-4o-mini"
    reviewer_llm_model: str = "openai/gpt-4o"
    """Deliberately a different tier than llm_model, not the same model the
    Specialist/Supervisor use -- a reviewer sharing blind spots with the
    thing it's reviewing can miss what a genuinely independent judge would
    catch. Both route through litellm.py (see llm.py) -- the "provider/"
    prefix is how LiteLLM picks which API to call, so pointing this at
    "anthropic/claude-..." (with ANTHROPIC_API_KEY set) is a one-line change,
    not a code change."""

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

    web_app_url: str = "http://localhost:3000"
    """Where the browser gets sent back to after a Google OAuth
    connect/callback round trip (see integrations/router.py) -- the
    dashboard's own origin, not this API's. Same reasoning as rag_api_url:
    one HTTP seam, overridden per environment."""

    google_client_id: str = ""
    google_client_secret: str = ""
    """OAuth 2.0 Web application credentials from Google Cloud Console
    (APIs & Services > Credentials). Both empty by default -- the Connect
    Google Account button and the Drive/Gmail tools all degrade to a clear
    "not configured" error rather than a crash when these aren't set, same
    graceful-degradation convention as every other optional integration in
    this codebase (see tools/registry.py)."""
    google_oauth_redirect_uri: str = "http://localhost:8100/v1/auth/google/callback"
    """Must be registered byte-for-byte as an Authorized redirect URI on the
    OAuth client in Google Cloud Console, or Google rejects the callback.
    Points at /v1/auth/google/callback (auth.py's router), not
    /v1/integrations/google/callback -- signing in and connecting Drive/Gmail
    are the same OAuth grant (see google_oauth.login_or_connect), so there is
    only one callback."""

    session_cookie_name: str = "af_session"
    session_idle_timeout_seconds: int = 60 * 30
    """How long a session survives WITHOUT use. Refreshed on each
    authenticated request (see auth.get_current_user), so an active user is
    never logged out mid-work, but an abandoned session on a shared or
    stolen machine closes in half an hour."""
    session_absolute_lifetime_seconds: int = 60 * 60 * 12
    """Hard cap from session creation, NOT refreshed by use. This is the one
    that matters against a stolen cookie: without it, an attacker who keeps
    making requests holds the session forever, which is exactly what the
    previous sliding-only design allowed. 12h means a session cannot outlive
    a working day, so a token lifted today is dead tomorrow regardless."""
    session_cookie_secure: bool = False
    """False for local http:// dev. Set true in any environment served over
    HTTPS (Railway, etc.) -- a session cookie without Secure would ride
    along over plain HTTP if the deployment ever downgrades. Also gates the
    __Host- cookie prefix (see session_cookie below) and the HSTS header
    (see security_headers.py), both of which are meaningful only over
    HTTPS."""

    @property
    def session_cookie(self) -> str:
        """The actual cookie name to set and read. NEVER read
        session_cookie_name directly -- it is the unprefixed stem.

        The __Host- prefix is a browser-enforced contract: a cookie carrying
        it is rejected unless it is Secure, has Path=/, and has NO Domain
        attribute. That last part is the point. Without the prefix, a
        cookie named af_session can be set for the parent domain by ANY
        subdomain -- so a compromised or attacker-controlled
        anything.example.com can plant a session cookie that the app at
        example.com will read back and honour, which is session fixation.
        __Host- makes that impossible: the cookie is locked to the exact
        origin that set it and no subdomain can write it.

        Falls back to the bare name when session_cookie_secure is false,
        because a browser would silently drop a __Host- cookie sent over
        plain http:// and local dev would simply stop working.

        Note this changes the cookie name on an HTTPS deployment, so live
        sessions are invalidated once at rollout. That is deliberate: the
        alternative -- accepting BOTH names during a transition -- would
        keep honouring exactly the unprefixed cookie a subdomain is able to
        forge, which hands back the attack this prefix exists to stop."""
        if self.session_cookie_secure:
            return f"__Host-{self.session_cookie_name}"
        return self.session_cookie_name

    hsts_max_age_seconds: int = 60 * 60 * 24 * 365
    """One year, the value the HSTS preload list requires as a minimum and
    the de-facto standard. Shorter windows weaken the guarantee: the header
    only protects a browser that has already seen it, so a short max-age
    means a returning visitor spends part of every visit unprotected."""
    hsts_preload: bool = False
    """Adds the `preload` directive, which is a request to be hardcoded into
    browsers as HTTPS-only. Off by default because it is effectively
    irreversible -- removal takes months to reach users -- and because it
    binds every present and future subdomain. Turn on only for a domain
    whose HTTPS posture is settled."""

    otel_exporter_otlp_endpoint: str = ""
    """Turns on OpenTelemetry export (see otel.py). Empty means off, and off
    means the SDK is never imported -- a deployment that doesn't want this
    pays nothing for it. Set to a collector's gRPC endpoint
    (e.g. http://localhost:4317) to get the same runs as a span tree in
    Phoenix / Langfuse / Datadog / Jaeger. The standard OTEL_ variable name
    is used deliberately so the usual tooling picks it up unchanged."""
    otel_service_name: str = "agentsys"

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

    # --- context engineering (see artifacts.py and nodes._gather_prior_context) ---
    max_tool_output_chars: int = 2_000
    """Above this, a successful tool result is written to the task workspace
    and only a preview + pointer goes into the prompt (artifacts.spill).
    Measured motivation: one 28,783-char Drive read was re-sent in full on
    every subsequent step of a task, ~43k tokens of pure re-transmission
    from a single tool call."""
    tool_output_preview_chars: int = 1_200
    """How much of a spilled result stays inline. Enough for the model to
    tell whether it needs the rest (and to answer outright when the head of
    a document is all that was needed) without carrying the whole payload."""
    context_recent_steps_full: int = 3
    """Recent steps whose output stays in the prompt verbatim. Older steps
    are truncated -- recency is the cheapest useful relevance heuristic
    here, and the full text is always still in Postgres."""
    context_older_step_chars: int = 300

    enable_triage: bool = True
    """The front door (graph/nodes.py's triage_node). Every turn used to pay
    sketch + at least one agent_step + synthesize, so "thanks" cost three
    model calls and a row of subtasks.

    On by default because the failure is bounded in the safe direction: the
    fast path has no tools, writes no subtasks, and its prompt forbids
    asserting anything not already in the conversation, so a misrouted turn
    answers unhelpfully rather than acting wrongly. Every error inside triage
    falls open to the full path. Set false to restore the previous
    always-full behaviour exactly."""
    triage_model: str = "openai/gpt-4o-mini"
    """The classifier and the fast-path reply. A separate setting from
    llm_model so the cheap front door stays cheap if the main model is ever
    pointed at something expensive -- the whole point is that a greeting
    doesn't wake the big one."""

    # --- loop engineering (see deadcalls.py and nodes.agent_step_node) ---
    max_unproductive_steps: int = 3
    """Consecutive steps whose tool call failed or was blocked as a known
    dead call, before the task escalates instead of continuing. A loop that
    stops making progress doesn't error -- it just bills, so this is the
    stop condition for "still running, no longer getting anywhere"."""
    max_task_cost_usd: float = 1.0
    """The fourth stop condition, alongside max_task_steps and the worker's
    wall-clock limits. Spend was already recorded per call (see cost.py's
    LlmCall rows) but never capped -- this is what turns that record into a
    ceiling. Generous next to measured real tasks ($0.05-$0.10)."""

    max_request_text_length: int = 20_000
    """Character cap on a submitted request/follow-up (see schemas.py). A
    100KB body is a perfectly valid HTTP request and a ruinously expensive
    prompt -- this is the cheapest place to stop that, at the API boundary,
    before it ever reaches a model."""

    task_rate_limit_per_hour: int = 30
    """Per-user cap on endpoints that enqueue real agent work (see
    ratelimit.py). Generous for genuine interactive use; low enough that a
    runaway client loop or a stuck retry burns a bounded amount before
    getting 429'd. Web backends rate-limit to protect capacity; this one is
    protecting the LLM bill, which doesn't refill for free."""

    idempotency_ttl_seconds: int = 60 * 60 * 24
    """How long a client-supplied Idempotency-Key maps to its original task
    (see idempotency.py). 24h matches the usual convention -- long enough to
    cover a double-click, a client retry, or a backgrounded mobile request
    resubmitting, without keeping keys forever."""

    task_soft_time_limit_seconds: int = 1200
    task_time_limit_seconds: int = 1500
    """Wall-clock backstops on a single task run (Celery
    task_soft_time_limit/task_time_limit, see worker.py). max_task_steps
    bounds how many STEPS a task takes but nothing bounds how LONG one step
    can hang -- a provider holding a socket open or a tool waiting on a
    connection stalls forever and bills the whole time. Soft raises
    SoftTimeLimitExceeded inside the task so it can record a readable cause;
    hard SIGKILLs the child process when something swallows the soft one.
    Hard must stay comfortably above soft so the soft handler gets to run."""

    max_subtask_retries: int = 2
    plan_confidence_escalation_threshold: int = 3
    review_escalation_threshold: int = 2
    max_task_steps: int = 12
    """Hard cap on how many times agent_step_node is allowed to decide "act"
    (i.e. create and run a new subtask) before the task is forced to
    escalate rather than loop indefinitely. Analogous to max_subagent_steps,
    but for the top-level task loop rather than one delegated sub-agent run.
    A subtask's own reject-and-retry cycle does NOT count against this cap
    -- only new subtask creation does."""

    docker_sandbox_image: str = "python:3.11-slim"
    workspace_dir: str = str(PROJECT_ROOT / "data" / "workspace")

    stranded_task_grace_seconds: int = 120
    """How stale (seconds since last update) a Task stuck in RUNNING must be
    before the worker-startup recovery sweep re-enqueues it (see
    recovery.recover_stranded_tasks). The grace window keeps the sweep from
    racing Celery's own acks_late redelivery of a task that ONLY just crashed
    -- a fresh crash gets redelivered on its own; only genuinely stale rows,
    whose message was actually lost, need the sweep."""

    max_delegation_depth: int = 1
    """A sub-agent at this depth cannot itself call delegate_subagent --
    bounds recursion to exactly one real nested level rather than allowing
    unbounded sub-agent spawning."""
    max_subagent_steps: int = 4
    """Caps a single sub-agent run's own tool-call loop."""

    max_tweet_iterations: int = 5
    """Hard cap on the generate_tweet tool's generate -> evaluate -> optimize
    loop (tools/tweet_workshop.py) -- prevents an infinite loop when no draft
    ever gets approved. Each iteration is one draft + one strict evaluation;
    once this many rounds pass without approval the tool stops and returns its
    best attempt flagged approved=false rather than looping forever. Override
    with MAX_TWEET_ITERATIONS in .env."""

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


settings = Settings()
