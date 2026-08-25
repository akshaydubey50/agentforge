import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    """Deliberately stopped by the user (POST /v1/tasks/{id}/cancel), as
    distinct from FAILED -- a cancelled task didn't go wrong, so it
    shouldn't show up as an error in the UI or drag down eval/analytics
    success rates. See cancellation.py for how the running worker finds
    out."""


class SubtaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    NEEDS_REVISION = "needs_revision"
    DONE = "done"
    ESCALATED = "escalated"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReviewVerdict(str, Enum):
    PASS = "pass"
    REJECT = "reject"
    ESCALATE = "escalate"


class EscalationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TOOK_OVER = "took_over"


class SubAgentRunStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class User(SQLModel, table=True):
    """A signed-in account, established via Google Sign-In (see
    integrations/google_oauth.py's login_or_connect). Deliberately named
    app_user, not user -- `user` collides with Postgres's reserved
    CURRENT_USER-style identifier and some tooling special-cases it.

    Every Task (and, through it, every Subtask/Escalation/TraceSpan/etc.) is
    owned by exactly one User via Task.owner_id -- that's the isolation
    boundary the API enforces (see main.py's Depends(get_current_user) use).
    """

    __tablename__ = "app_user"

    id: str = Field(default_factory=_uuid, primary_key=True)
    google_sub: str = Field(unique=True, index=True)
    """Google's stable subject identifier (the `sub` claim) -- the actual
    identity key. email is also unique for convenience lookups/display, but
    google_sub is what survives an email change on the Google account."""
    email: str = Field(unique=True, index=True)
    name: str | None = None
    picture_url: str | None = None
    created_at: datetime = Field(default_factory=_now)
    last_login_at: datetime = Field(default_factory=_now)


class UserSession(SQLModel, table=True):
    """One signed-in session. Durable in Postgres rather than Redis-only,
    for three reasons Redis can't cover: a user's sessions can be enumerated
    (so "sign out everywhere" and "here are your active devices" are
    possible at all), the history survives a cache flush (which is exactly
    the evidence an incident investigation needs), and each row carries the
    timestamps that make a real expiry policy expressible.

    Redis stays on the hot validation path as a cache -- see auth.py -- so
    the per-request cost is unchanged; this table is the source of truth.

    Two independent expiries, because they defend different things:
      - expires_at is ABSOLUTE, fixed at creation. It caps how long a
        session can live no matter how actively it's used. The previous
        Redis-only design had no such concept: every request refreshed the
        TTL, so a stolen cookie stayed valid indefinitely as long as the
        attacker kept using it.
      - last_seen_at drives the IDLE timeout: an abandoned session dies
        even though its absolute window hasn't closed.
    """

    __tablename__ = "user_session"

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="app_user.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    """SHA-256 of the session token, never the token itself. Read access to
    this table must not yield credentials that can be replayed -- the same
    reasoning that makes storing password hashes rather than passwords
    non-negotiable. Lookup still works because the hash is deterministic
    (unlike a password hash, which is deliberately salted per row)."""
    created_at: datetime = Field(default_factory=_now)
    last_seen_at: datetime = Field(default_factory=_now)
    expires_at: datetime
    revoked_at: datetime | None = None
    """Set rather than deleting the row, so a revoked session remains
    visible to an audit ("this session was killed at T") instead of simply
    vanishing from history."""
    ip: str | None = None
    user_agent: str | None = None
    mfa_satisfied_at: datetime | None = None
    """When this session last proved a second factor. Step-up re-auth
    (see Phase 3) checks the age of this, not merely whether MFA is
    enrolled -- proving a factor at login is not proof of presence an hour
    later when an irreversible action is approved."""


class Task(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="app_user.id", index=True)
    request_text: str
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    final_output: str | None = None
    is_eval: bool = Field(default=False)
    """True for tasks created by the eval harness (agentsys/eval/runner.py)
    rather than a real user request -- excluded from list_tasks/analytics by
    default so golden-set runs don't pollute the dashboard."""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Subtask(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    position: int
    description: str
    depends_on: list[str] = Field(default_factory=list, sa_column=Column(JSONB))
    assigned_tool: str | None = None
    status: SubtaskStatus = Field(default=SubtaskStatus.PENDING)
    output: str | None = None
    attempt_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ToolCall(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    subtask_id: str = Field(foreign_key="subtask.id", index=True)
    tool_name: str
    input: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    output: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    success: bool
    latency_ms: int
    created_at: datetime = Field(default_factory=_now)


class Review(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    subtask_id: str = Field(foreign_key="subtask.id", index=True)
    score: int
    verdict: ReviewVerdict
    feedback: str
    created_at: datetime = Field(default_factory=_now)


class Escalation(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    subtask_id: str | None = Field(default=None, foreign_key="subtask.id")
    kind: str = Field(default="plan")
    """One of: plan (low sketch confidence), review (reviewer rejected past
    retry budget), budget (step cap exceeded), tool_approval (a
    requires_approval tool is about to run -- see graph/nodes.py's
    _execute_subtask and Tool.requires_approval in tools/base.py). The first
    three resume by marking the subtask/task done as-is on approve; "approve"
    for tool_approval instead puts the subtask back to READY so it's
    re-selected and the gated tool actually executes -- see
    escalations.apply_escalation_decision."""
    reason: str
    context: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    status: EscalationStatus = Field(default=EscalationStatus.PENDING)
    decision_note: str | None = None
    decided_by: str | None = None
    created_at: datetime = Field(default_factory=_now)
    decided_at: datetime | None = None


class TraceSpan(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    subtask_id: str | None = Field(default=None, foreign_key="subtask.id")
    span_type: str
    """One of: sketch, agent_step, tool_selection, tool_call, reasoning,
    review, memory, escalation, synthesize."""
    name: str
    input: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    output: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    status: str = "ok"
    started_at: datetime = Field(default_factory=_now)
    ended_at: datetime | None = None


class SampleMetric(SQLModel, table=True):
    """Small seeded business-data table the db_query tool is allowed to read
    from — gives the agent something concrete to query during the demo
    scenario without touching the app's own operational tables."""

    __tablename__ = "sample_metric"

    id: int | None = Field(default=None, primary_key=True)
    company: str
    quarter: str
    revenue_usd: int
    headcount: int


class SubAgentRun(SQLModel, table=True):
    """A bounded, isolated tool-use loop delegated to from one subtask via
    the delegate_subagent tool (tools/delegate_subagent.py). Deliberately a
    separate table from Subtask -- it's a runtime-spawned execution, not a
    step the main agent_step_node loop itself created, and must never be
    visible to that loop's own step-counting/step-budget logic."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    subtask_id: str = Field(foreign_key="subtask.id", index=True)
    depth: int
    goal: str
    status: SubAgentRunStatus = Field(default=SubAgentRunStatus.RUNNING)
    output: str | None = None
    created_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None


class LlmCall(SQLModel, table=True):
    """One row per OpenAI call, written by agentsys.cost.record_llm_call right
    after every call site in graph/nodes.py and tools/delegate_subagent.py --
    this is what /v1/analytics' cost figures are aggregated from."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    subtask_id: str | None = Field(default=None, foreign_key="subtask.id")
    purpose: str
    """One of: plan, tool_selection, reasoning, review, synthesize, subagent_step."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int | None = None
    """How many of prompt_tokens were served from the provider's prompt cache
    and billed at the discounted rate (OpenAI reports this as
    usage.prompt_tokens_details.cached_tokens; it is half price).

    NULL means "not recorded", not "none were cached" -- rows written before
    this column existed genuinely cannot say, and pricing.py treats the two
    differently rather than silently charging full rate for a cached call.
    Without this, deriving cost from prompt_tokens alone overstates spend on
    exactly the long-prompt agent_step calls that cache best (measured ~3%
    high across this project's own history)."""
    cost_usd: float
    """A CACHE of what pricing.py said at write time, not the source of
    truth. Never SUM this column -- see cost.spend_from_rows, which re-derives
    from tokens so a corrected rate fixes history."""
    created_at: datetime = Field(default_factory=_now)


class TaskMessage(SQLModel, table=True):
    """A user-authored follow-up on a task that has already reached a
    terminal state (completed/failed) -- lets the conversation continue
    past the original request_text instead of the task being a dead end.
    Deliberately a separate append-only table rather than mutating
    Task.request_text: the feed needs to render each follow-up as its own
    "you said X" message, and agent_step_node needs to tell an original
    request apart from what was added afterward (see
    _gather_conversation_history / _turn_start in graph/nodes.py)."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    role: str = Field(default="user")
    """Only 'user' is written today -- the assistant side of each turn is
    already captured by the 'synthesize' TraceSpan for that turn, so it
    doesn't need a duplicate row here."""
    content: str
    created_at: datetime = Field(default_factory=_now)


class GoogleConnection(SQLModel, table=True):
    """A connected Google account's OAuth tokens for one User's Drive/Gmail
    access, established the same OAuth grant as sign-in itself (see
    integrations/google_oauth.py's login_or_connect) -- signing in with
    Google and connecting Drive/Gmail are the same consent, so this row is
    created/refreshed at login time, not from a separate button.

    One row per user (unique user_id) -- was a single global singleton row
    before per-user auth existed; each user now gets their own connection,
    and tools resolve the acting user's row via user_id (threaded through
    graph state, see graph/state.py).

    refresh_token is what actually persists the connection -- access tokens
    expire in ~1h, and integrations/google_oauth.py trades the refresh token
    for a fresh access token on demand. Google only returns a refresh token
    on the FIRST consent (or when access_type=offline + prompt=consent is
    forced), which is why the auth URL sets both."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(foreign_key="app_user.id", unique=True, index=True)
    google_email: str | None = None
    """The connected account's email, shown in the UI so the user can see
    which account is linked. Purely informational."""
    access_token: str
    refresh_token: str | None = None
    scopes: str = ""
    """Space-delimited, exactly as Google returns them -- lets the UI show
    what access was granted and lets a tool check a scope is present before
    calling an API that needs it."""
    token_expiry: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MemoryEntry(SQLModel, table=True):
    """Metadata row for a long-term memory; the embedding + text live in Chroma
    under the same id, so this table is queryable structured metadata while
    Chroma handles semantic retrieval."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    owner_id: str = Field(foreign_key="app_user.id", index=True)
    """task_id is nullable (a memory can be global, not tied to one task),
    so ownership can't be inherited through the task join the way it is for
    Subtask/ToolCall/etc. -- this column is the isolation boundary instead,
    same role Task.owner_id plays for everything under a task."""
    task_id: str | None = Field(default=None, foreign_key="task.id")
    kind: str
    """One of: episodic (task summary), fact, preference."""
    content: str
    importance: int = Field(default=3)
    created_at: datetime = Field(default_factory=_now)
    last_accessed_at: datetime = Field(default_factory=_now)


class AuditEvent(SQLModel, table=True):
    """One tamper-evident record of a security-relevant action, linked into a
    single global hash chain. See agentsys/audit.py for how rows are written
    and verified; this is just the shape they take.

    Why a chain rather than an ordinary log table: an append-only table is
    only append-only by convention -- anyone with UPDATE/DELETE can rewrite
    history and nothing about the result looks wrong afterwards. Chaining
    each row's hash over the previous row's means editing or removing any
    row invalidates every hash after it, so the tampering is visible even
    though the database itself never stopped allowing the write.

    What this does NOT claim: it is tamper EVIDENCE, not tamper proof. An
    attacker with full write access can recompute the entire chain from the
    edited row forward and produce a self-consistent result. Detecting that
    requires an anchor outside this database -- which is what audit.chain_head()
    exists to be published to (a scheduled job, an append-only log sink, a
    second store). The chain is what makes such an anchor cheap: one hash
    pins the entire history.

    Deliberately NOT foreign-keyed to app_user, unlike every other table
    here. An audit log has to outlive the subject it describes: a FK would
    either block deleting a user (holding the account hostage to its own
    audit trail) or, with a cascade, delete exactly the evidence that a
    deletion happened. actor_id is a plain indexed string for that reason,
    and the per-user read filter in main.py works on string equality.
    """

    __tablename__ = "audit_event"

    id: str = Field(default_factory=_uuid, primary_key=True)
    seq: int = Field(unique=True, index=True)
    """Position in the chain, assigned under an advisory lock so two
    concurrent writers can't claim the same slot. Carries information the
    chain by itself doesn't: a gap proves rows were removed even in the
    (impossible without the private detail below) case that the remaining
    hashes still line up. UNIQUE is the backstop if the lock is ever
    circumvented -- a duplicate seq fails the insert rather than silently
    forking the chain."""
    created_at: datetime = Field(default_factory=_now, index=True)
    actor_id: str | None = Field(default=None, index=True)
    """The app_user.id who acted, when known. None for events with no
    established identity yet -- a failed sign-in is the important case:
    there is no user to attribute it to, and that's precisely the event
    worth keeping."""
    actor_label: str = Field(default="anonymous")
    """Human-readable actor for reading the log without a join: an email, or
    'anonymous'/'system'. Email is PII, but an audit log that can't say who
    did something isn't an audit log."""
    action: str = Field(index=True)
    """Dotted verb, e.g. auth.login.success, auth.session.revoked,
    escalation.decided. See audit.py's Action constants -- string rather
    than an Enum so a new event type is one call site, and so historical
    rows never become unreadable because a name was retired from the enum."""
    target_type: str | None = None
    target_id: str | None = Field(default=None, index=True)
    """What was acted ON (session/escalation/task/user + its id), as opposed
    to who acted. Indexed so "everything that ever happened to escalation X"
    is a single lookup."""
    outcome: str = Field(default="success")
    """success | failure | denied. Separate from action so a query for all
    sign-in attempts doesn't have to know both auth.login.success and
    auth.login.failure exist."""
    ip: str | None = None
    user_agent: str | None = None
    meta: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    """Event-specific detail. Named meta, not metadata -- SQLAlchemy's
    declarative base reserves `metadata` on every mapped class, so the
    obvious name silently shadows the MetaData object and breaks mapping.
    Passed through audit.redact() on the way in, so a credential cannot
    reach this column even if a call site carelessly hands one over."""
    prev_hash: str
    """The previous row's hash, or GENESIS_HASH for the first row."""
    hash: str = Field(unique=True)
    """SHA-256 over the canonical serialization of every field above,
    including prev_hash. UNIQUE both catches an accidental duplicate write
    and means two rows can never claim the same chain position."""
