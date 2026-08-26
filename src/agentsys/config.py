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

    # --- LLM resilience (see llm.py -- the ONE place retries happen) ---
    llm_timeout_seconds: int = 60
    """Per-request timeout passed to litellm. Nothing bounded a single model
    call before this: a provider holding a socket open was caught only by
    Celery's 20-minute wall clock, and at --concurrency=4 four hung calls
    stall the whole worker."""
    llm_max_attempts: int = 3
    """Total attempts (not retries) per call, for TRANSIENT failures only --
    rate limits, timeouts, connection resets, provider 5xx. A permanent
    error (bad key, malformed request, context window) is raised on the
    first attempt; a second identical call gets the identical rejection."""
    llm_backoff_base_seconds: float = 1.0
    llm_backoff_max_seconds: float = 20.0
    """Equal-jitter exponential backoff between attempts. A provider's own
    Retry-After header wins over this when it sends one."""
    llm_fallback_model: str = ""
    """Optional. Tried once after transient exhaustion on the primary model,
    never after a permanent error (which would fail identically anywhere).
    Empty = off. Chat models only -- embeddings never fall back."""

    database_url: str = "postgresql+psycopg://agent:agent@localhost:5432/agentsys"
    db_query_url: str = ""
    """Optional separate DSN for the db_query tool, pointing at a role with
    SELECT on the allowlisted tables and NOTHING else (see
    scripts/create_readonly_role.sql). This is the only guard that holds if
    the in-process allowlist below is ever wrong, because it is enforced by
    Postgres rather than by us. Empty = fall back to database_url, where the
    allowlist is the sole boundary.

    Not defaulted to a value because the role has to be created by someone
    with rights to create it -- a default pointing at a role that doesn't
    exist would break the tool everywhere rather than degrade."""
    db_query_allowed_tables: str = "sample_metric"
    """Comma-separated allowlist of relations db_query may read. Enforced by
    resolving the query's actual relations through EXPLAIN, not by pattern-
    matching the SQL text (see tools/db_query.py for why the regexes alone
    were never a boundary)."""

    redis_url: str = "redis://localhost:6379/0"
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    rag_api_url: str = "http://localhost:8000"
    """Base URL for src/rag's separate FastAPI service. The two packages don't
    import each other (see docs/MERGE.md) -- this is the one HTTP seam between
    them, used by tools/knowledge_search.py so the agent can actually use
    documents uploaded to Knowledge. Overridden to the docker-network hostname
    (http://rag-api:8000) in docker-compose.yml for the api/worker containers."""

    service_token: str = ""
    """Shared secret for server-to-server calls into rag-api's /v1/ask (see
    tools/knowledge_search.py and rag/auth.py). That endpoint had no
    authentication at all and is published on a host port, so anyone who
    could reach it could query the whole corpus and spend LLM budget.

    Empty = the agent presents no service credential, and rag-api rejects
    the call. Deliberately fails CLOSED: a missing secret must not silently
    reopen the hole it was added to close."""

    @property
    def db_query_allowed_tables_set(self) -> frozenset[str]:
        return frozenset(
            t.strip().lower() for t in self.db_query_allowed_tables.split(",") if t.strip()
        )

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

    enable_google_photos: bool = False
    """The Google Photos picker tool (tools/google_photos.py).

    OFF by default, and it is not a preference -- turning it on adds an OAuth
    scope, and Google records granted scopes, so every already-connected
    account must disconnect and reconnect before the tool works. A default
    that silently invalidates live connections would be the wrong default.
    It also requires the Photos Picker API to be enabled in Google Cloud
    Console, which no code here can do.

    Note what the capability IS: the user picks, the agent receives. Library
    search was removed by Google in March 2025 and cannot be restored."""

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

    deepeval_enabled: bool = False
    """Opt-in Phase 7C quality tier. False by default so normal AgentForge
    startup, local dev, and deterministic Tier 1 do not require DeepEval or a
    judge key. When true, eval/gate.py may run the optional quality tier after
    cheaper deterministic tiers have passed."""
    deepeval_judge_model: str = ""
    """Optional judge model override for Phase 7C DeepEval metrics. Empty means
    use reviewer_llm_model."""
    deepeval_sample_count: int = 0
    """Optional cap on Phase 7C quality cases. 0 means run the selected suite."""
    deepeval_dataset_subset: str = ""
    """Optional category prefix for Phase 7C quality cases, e.g. normal or
    failure. Empty means all quality cases."""

    cors_allowed_origins: str = "http://localhost:3000,http://localhost:3001"
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
    max_tool_output_chars: int = 12_000
    """Above this, a successful tool result is written to the task workspace
    and a digest + pointer goes into the prompt instead (artifacts.spill).

    Originally 2,000, set when the measured problem was one 28,783-char Drive
    read being re-sent in full on every subsequent step -- ~43k tokens of pure
    re-transmission from a single tool call. But that is the RETRANSMISSION
    problem, and context_recent_steps_full below now solves it independently
    by truncating older steps. 2,000 chars is ~500 tokens against a 128k
    window: it spilled essentially every real document, so the agent almost
    never held the thing it was asked about. 12,000 (~3k tokens) keeps a
    normal document whole and still catches the genuinely oversized."""
    summarize_spilled_output: bool = True
    """When a result is too large to keep, summarize the WHOLE of it rather
    than keeping only its opening (artifacts._digest). One cheap call, and it
    is the difference between the agent knowing what a document contains and
    knowing what its title page says. Fails open to the preview."""
    max_digest_input_chars: int = 60_000
    """Bound on what the summarizer reads, so one pathological result cannot
    turn into one pathological bill."""
    tool_output_preview_chars: int = 1_200
    """How much of a spilled result stays inline. Enough for the model to
    tell whether it needs the rest (and to answer outright when the head of
    a document is all that was needed) without carrying the whole payload."""
    max_dereference_chars: int = 60_000
    """Ceiling on a read that FOLLOWS a spill pointer (see nodes._capped).
    That one path skips spilling on purpose -- otherwise the escape hatch
    sits behind the door it exists to open, and a pointer can never be
    followed for anything above max_tool_output_chars. Generous enough for a
    real document to arrive whole; small enough that a pathological file
    can't blow the context window."""
    context_recent_steps_full: int = 3
    """Recent steps whose output stays in the prompt verbatim. Older steps
    are truncated -- recency is the cheapest useful relevance heuristic
    here, and the full text is always still in Postgres."""
    context_older_step_chars: int = 300
    conversation_summary_trigger_tokens: int = 3_000
    """When reconstructed follow-up conversation history crosses this
    approximate token count, older turns are folded into Task.rolling_summary
    and only recent turns stay verbatim. Originals remain in Postgres."""
    conversation_recent_turns: int = 4
    """Number of most recent reconstructed conversation turns kept verbatim
    after rolling-summary compaction."""
    conversation_summary_max_tokens: int = 900
    """Target ceiling for the structured rolling summary. The model prompt
    asks for compact output; deterministic code rejects empty/corrupt
    summaries and preserves the previous one."""
    memory_candidate_max_chars: int = 900
    """Hard content-size ceiling for one long-term memory candidate. Artifact
    bodies and transcripts belong in artifacts/TaskMessage rows, not memory."""
    memory_candidate_min_confidence: float = 0.55
    """Below this, a proposed memory is too uncertain to persist."""
    memory_semantic_merge_similarity: float = 0.9
    """High-confidence near-duplicate threshold. False merges are worse than
    duplicate memories, so this stays conservative."""
    agent_step_context_budget_tokens: int = 24_000
    """Approximate context-window ceiling for one agent_step call. Phase 6C
    subtracts agent_step_reserved_output_tokens to get the usable pre-call
    input budget; provider reported LlmCall.prompt_tokens remains the ground
    truth afterward."""
    agent_step_reserved_output_tokens: int = 2_000
    """Output headroom reserved when selecting agent_step input context."""
    agent_step_memory_budget_tokens: int = 900
    """Maximum approximate tokens of durable memory injected into one
    agent_step prompt. Low-relevance memory is dropped before recent
    conversation or task evidence."""
    agent_step_memory_max_items: int = 6
    """Hard cap on memory rows selected for one agent_step prompt."""

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

    enable_guardrails: bool = True
    """Phase 7D model/content guardrails. Disabling this only bypasses
    content-risk classification; it does not disable Pydantic validation,
    AgentForge policy, human approval, execution safety, or verification."""

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

    approval_expiry_seconds: int = 60 * 60
    """How long a tool_approval escalation stays executable after it was
    raised. Past this, approving it does NOT run the tool -- the step fails
    with an explanation, the agent re-proposes, and a fresh policy evaluation
    raises a fresh approval request against the current world.

    The risk this closes is the architecture audit's (5.2): a human approving
    a three-day-old request is approving arguments the model chose against a
    three-day-old world -- a query, a recipient, a filename that may since
    have become the wrong one. Measured from Escalation.created_at (when the
    agent proposed the call), not decided_at, because staleness is a property
    of the proposal, not of how long the human took to read it.

    Seconds, matching session_idle_timeout_seconds and friends. One hour is
    long enough for a person to come back from lunch and short enough that
    the arguments still describe the situation they were chosen for. It
    self-heals either way: an expired approval costs one extra round trip,
    never a stuck task."""

    # --- tool execution safety (see execution.py -- the ONE place a tool
    # call is retried) ---
    tool_max_attempts: int = 3
    """Total attempts (not retries) for ONE tool call, and only ever for a
    known-retryable failure of an IDEMPOTENT tool -- see
    execution.is_retryable. A tool that is not safe to repeat is never
    retried automatically at any count; its failure goes back to the agent
    loop, which can re-propose and get a fresh policy evaluation.

    Deliberately separate from llm_max_attempts: those attempts cost money,
    these can cost a side effect."""
    tool_backoff_base_seconds: float = 0.5
    tool_backoff_max_seconds: float = 8.0
    """Equal-jitter exponential backoff between tool attempts, the same curve
    as llm_backoff_* and much shorter -- a tool retry is buying its way past a
    blip, not past a provider's quota window. In-process (execution.py sleeps,
    as llm.py does) rather than a requeue: at these delays a queue round trip
    would cost more than the wait and would lose the step's position."""

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
    stale_pending_task_grace_seconds: int = 120
    """How old a PENDING task must be before startup recovery treats it as a
    likely lost enqueue. This covers the narrow API window after the Task row
    is committed but before Redis accepts the Celery message."""
    recovery_batch_size: int = 50
    """Maximum stale tasks a single recovery sweep re-enqueues. Recovery is a
    backstop, not an unbounded boot-time queue storm."""
    max_active_tasks: int = 1000
    max_active_tasks_per_user: int = 5
    """API-side overload guard for submitted work. Celery concurrency remains
    the worker execution bound; this prevents the API from accepting unlimited
    pending/running task rows during an outage or client loop."""

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
            # Policy classification for this server's tools (see
            # tools/mcp_tool.py). Declared because this one is THIS repo's own
            # code and every tool it exposes is a pure read -- the current
            # time, a dice roll, an office lookup. A third-party server with no
            # declaration gets Tool's fail-closed default instead, so its tools
            # are gated behind human approval until someone who knows the
            # server says otherwise.
            "action_type": "read",
            "risk": "low",
            # Every tool it exposes is a pure read (the time, a dice roll,
            # an office lookup), so repeating one is safe -- see
            # execution.ExecutionSafety. A third-party server that declares
            # nothing is never auto-retried instead.
            "execution_safety": "idempotent",
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
