"""Tamper-evident audit log.

Every security-relevant action -- who signed in, whose session was killed,
who approved running an irreversible tool -- lands in one append-only chain
of AuditEvent rows (see db/models.py), each hashing over the previous one's
hash. Editing or deleting any row invalidates every hash after it, so a
rewrite of history is visible after the fact even though Postgres itself
never stopped allowing the UPDATE.

What this buys and what it doesn't
----------------------------------
This is tamper EVIDENCE. An attacker holding full write access to this
database can recompute the chain from the row they edited forward and end
up with something self-consistent; nothing stored *inside* the database can
prevent that, by construction. What the chain does is make the cost of
detecting it one comparison instead of a full diff: chain_head() collapses
the entire history into a single hash, so publishing that hash somewhere
the database cannot reach (a scheduled job writing to an append-only sink,
a second store, a log aggregator) turns any rewrite into a mismatch. The
chain is what makes that anchor cheap enough to actually run.

One global chain, not one per user
----------------------------------
A per-user chain would be easier to write (no cross-user contention) and
strictly weaker: an empty chain is a valid chain, so deleting the whole of
one user's history would leave nothing to notice. Anchoring every event to
every prior event across all users means such a deletion leaves a seq gap
and a broken link. Volume here is auth events plus escalation decisions --
low enough that serialising writes costs nothing that matters.

Concurrency
-----------
Two writers must never read the same prev_hash. A transaction-scoped
Postgres advisory lock serialises the read-tail/append pair; it releases
automatically on commit or rollback, so a crashed writer can't wedge the
log. The UNIQUE constraints on seq and hash are the backstop: if the lock
is ever circumvented, the second insert fails loudly rather than silently
forking the chain into two branches.

Failure policy: fail open, loudly
---------------------------------
record() never raises into its caller. A logging subsystem that can take
down authentication is a worse security posture than one that occasionally
misses a record -- refusing every sign-in because an audit insert hit a
constraint would be a self-inflicted outage. The obvious objection is real:
an attacker who can reliably break audit writes gets to act unlogged. That
is why the failure path logs at ERROR rather than passing silently, and why
verify_chain() reports seq gaps -- a missing sequence number is itself the
signal that something was not recorded.

Credentials
-----------
No call site is trusted to keep credentials out. redact() strips them on
the way in, by key name, recursively -- so a future caller that carelessly
hands over a whole OAuth token payload writes REDACTED markers instead of a
replayable secret. Same reasoning as storing session token hashes rather
than tokens in db/models.py: the store must not become a source of
credentials for whoever can read it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlmodel import select

from agentsys.db.models import AuditEvent
from agentsys.db.session import get_session
from agentsys.sanitize import scrub_nul

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64
"""What the first row's prev_hash points at. A fixed sentinel rather than an
empty string, so a row whose prev_hash was blanked out is distinguishable
from the genuine head of the chain."""

_ADVISORY_LOCK_KEY = 0x41465F41554449  # "AF_AUDI" -- arbitrary but fixed

_MAX_META_STRING = 500
_MAX_META_ITEMS = 40
_MAX_META_DEPTH = 4
REDACTED = "[REDACTED]"


class Action:
    """The action vocabulary. Plain strings on the model rather than a DB
    enum: adding an event type stays a one-line change, and retiring a name
    never makes historical rows unreadable the way dropping an enum value
    would."""

    LOGIN_SUCCESS = "auth.login.success"
    LOGIN_FAILED = "auth.login.failed"
    LOGOUT = "auth.logout"
    USER_CREATED = "auth.user.created"
    SESSION_CREATED = "auth.session.created"
    SESSION_ROTATED = "auth.session.rotated"
    SESSION_REVOKED = "auth.session.revoked"
    SESSION_REVOKED_ALL = "auth.session.revoked_all"
    SESSION_EXPIRED = "auth.session.expired"
    GOOGLE_CONNECTED = "auth.google.connected"
    GOOGLE_DISCONNECTED = "auth.google.disconnected"
    ESCALATION_DECIDED = "escalation.decided"
    TOOL_APPROVAL_EXECUTED = "escalation.tool_approval.executed"


# Key-name fragments whose values are never written. Substring match on the
# lowercased key, so access_token/refresh_token/id_token are all caught by
# "token" without enumerating them.
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "private_key",
    "assertion",
    "bearer",
    "signature",
    "code_verifier",
    "code_challenge",
    "auth_code",
    "otp",
    "totp",
    "nonce",
)

# Keys that are sensitive only as themselves. Deliberately exact-match: a
# substring rule on "code" or "state" would also redact status_code,
# error_code and task_state, which are exactly the fields that make an audit
# row worth reading. A bare `code`/`state` in this codebase means the OAuth
# authorization code and CSRF state, which are not.
_SENSITIVE_KEYS_EXACT = {"code", "state", "pin", "jwt", "key"}


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SENSITIVE_KEYS_EXACT:
        return True
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively strip credentials and bound the size of a metadata blob.

    Applied inside record(), not left to call sites -- the guarantee wanted
    here is "a credential cannot reach the audit table", and a guarantee
    that depends on every future caller remembering something isn't one.

    Redaction is by key NAME, which catches the realistic mistake (splatting
    a token response dict into meta) rather than trying to recognise secrets
    by their shape, which is not reliably possible.
    """
    if _depth > _MAX_META_DEPTH:
        return "[TRUNCATED: max depth]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_META_ITEMS:
                out["[TRUNCATED]"] = f"{len(value) - _MAX_META_ITEMS} more keys"
                break
            key = str(k)
            out[key] = REDACTED if _is_sensitive(key) else redact(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        items: list[Any] = [redact(v, _depth + 1) for v in list(value)[:_MAX_META_ITEMS]]
        if len(value) > _MAX_META_ITEMS:
            items.append(f"[TRUNCATED: {len(value) - _MAX_META_ITEMS} more]")
        return items
    if isinstance(value, str):
        if len(value) > _MAX_META_STRING:
            return value[:_MAX_META_STRING] + f"... [truncated {len(value)} chars]"
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return value
    return redact(str(value), _depth)


def _canonical_ts(dt: datetime) -> str:
    """Timestamps are hashed through exactly one representation: naive UTC,
    microsecond-pinned. Rows are also WRITTEN naive so Postgres performs no
    timezone conversion on the way in -- handing psycopg an aware datetime
    for a `timestamp without time zone` column makes the stored value depend
    on the server's TimeZone setting, and a value that changes on the round
    trip would break its own hash on a differently-configured host."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="microseconds")


def compute_hash(
    *,
    id: str,
    seq: int,
    created_at: datetime,
    actor_id: str | None,
    actor_label: str,
    action: str,
    target_type: str | None,
    target_id: str | None,
    outcome: str,
    ip: str | None,
    user_agent: str | None,
    meta: dict,
    prev_hash: str,
) -> str:
    """SHA-256 over a canonical JSON serialization of the whole row.

    sort_keys + no whitespace + ensure_ascii pins the byte sequence: the
    same row must hash identically on any host, any Python build and any
    dict insertion order, or verification would fail on honest data and the
    signal would be worthless.

    The row's own id is included, so rows cannot be swapped between chain
    positions while keeping their hashes valid.
    """
    payload = {
        "id": id,
        "seq": seq,
        "created_at": _canonical_ts(created_at),
        "actor_id": actor_id,
        "actor_label": actor_label,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "outcome": outcome,
        "ip": ip,
        "user_agent": user_agent,
        "meta": meta,
        "prev_hash": prev_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def request_context(request: Any) -> dict[str, str | None]:
    """Pulls ip/user_agent off a FastAPI Request for splatting into record().

    A helper rather than a `request` parameter on record() itself, so this
    module stays free of a web-framework dependency and remains callable
    from the worker, a CLI, or a test with no request in sight."""
    if request is None:
        return {"ip": None, "user_agent": None}
    return {
        "ip": request.client.host if request.client else None,
        "user_agent": (request.headers.get("user-agent") or "")[:400] or None,
    }


def record(
    action: str,
    *,
    actor_id: str | None = None,
    actor_label: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    ip: str | None = None,
    user_agent: str | None = None,
    meta: dict | None = None,
) -> str | None:
    """Append one event to the chain. Returns the new row's hash, or None if
    the write failed (see the module docstring on why that is not raised).

    Callers pass whatever identity they have: a failed sign-in has no
    actor_id at all, and that event matters more than most.
    """
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        clean_meta = scrub_nul(redact(meta or {}))

        with get_session() as db:
            # Serialise the read-tail/append pair. Transaction-scoped, so it
            # releases on commit OR rollback and a crashed writer cannot
            # leave the log locked.
            db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})

            tail = db.exec(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)).first()
            seq = (tail.seq + 1) if tail else 1
            prev_hash = tail.hash if tail else GENESIS_HASH

            event = AuditEvent(
                seq=seq,
                created_at=now,
                actor_id=actor_id,
                actor_label=actor_label or ("anonymous" if actor_id is None else actor_id),
                action=action,
                target_type=target_type,
                target_id=target_id,
                outcome=outcome,
                ip=ip,
                user_agent=(user_agent or "")[:400] or None,
                meta=clean_meta,
                prev_hash=prev_hash,
            )
            event.hash = compute_hash(
                id=event.id,
                seq=event.seq,
                created_at=now,
                actor_id=event.actor_id,
                actor_label=event.actor_label,
                action=event.action,
                target_type=event.target_type,
                target_id=event.target_id,
                outcome=event.outcome,
                ip=event.ip,
                user_agent=event.user_agent,
                meta=clean_meta,
                prev_hash=prev_hash,
            )
            db.add(event)
            db.commit()
            return event.hash
    except Exception:  # noqa: BLE001 -- see module docstring: fail open, loudly
        logger.exception(
            "audit write failed action=%s actor=%s target=%s/%s",
            action,
            actor_id,
            target_type,
            target_id,
        )
        return None


def chain_head() -> tuple[int, str]:
    """(seq, hash) of the newest event, or (0, GENESIS_HASH) on an empty log.

    This one hash pins the entire history. Publishing it periodically
    somewhere outside this database is what upgrades the chain from "an edit
    is detectable if you still have a good copy" to "an edit is detectable,
    full stop" -- see the module docstring."""
    with get_session() as db:
        tail = db.exec(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)).first()
        if tail is None:
            return 0, GENESIS_HASH
        return tail.seq, tail.hash


def verify_chain(
    limit: int | None = None,
    *,
    start_seq: int | None = None,
    expected_prev_hash: str | None = None,
) -> dict:
    """Walk the chain and report whether it is intact.

    Checks three distinct failure modes, because they mean different things:
      - a recomputed hash that doesn't match the stored one means that row's
        CONTENT was edited;
      - a prev_hash that doesn't match the actual previous row's hash means
        a row was removed, reordered, or inserted;
      - a gap in seq means rows were deleted outright, and names precisely
        which positions are missing.

    Returns a dict rather than raising: the caller (a scheduled integrity
    job, a test) wants the full list of problems, not the first one.

    start_seq/expected_prev_hash make verification INCREMENTAL, which is what
    makes it runnable in production at all: re-hashing the entire history on
    every scheduled run gets linearly more expensive forever, so a job that
    anchored (seq, hash) yesterday passes them back today and verifies only
    what was appended since. Supplying start_seq without expected_prev_hash
    means "I trust everything before this point" -- the first row's link is
    then taken as given rather than checked, which is honest but weaker, so
    an anchoring job should always pass the hash it recorded.
    """
    problems: list[dict] = []
    with get_session() as db:
        query = select(AuditEvent).order_by(AuditEvent.seq)
        if start_seq is not None:
            query = query.where(AuditEvent.seq >= start_seq)
        if limit:
            query = query.limit(limit)
        rows = db.exec(query).all()

        if start_seq is None:
            expected_prev = GENESIS_HASH
        elif expected_prev_hash is not None:
            expected_prev = expected_prev_hash
        else:
            # Nothing to check the first link against; take it as given and
            # verify from there. Recorded in the result so a caller can tell
            # a partial check from a complete one.
            expected_prev = rows[0].prev_hash if rows else GENESIS_HASH
        expected_seq = start_seq if start_seq is not None else (rows[0].seq if rows else 1)
        for row in rows:
            if row.seq != expected_seq:
                problems.append(
                    {
                        "seq": row.seq,
                        "kind": "seq_gap",
                        "detail": f"expected seq {expected_seq}, found {row.seq}",
                    }
                )
                expected_seq = row.seq
            if row.prev_hash != expected_prev:
                problems.append(
                    {
                        "seq": row.seq,
                        "kind": "broken_link",
                        "detail": "prev_hash does not match the previous row's hash",
                    }
                )
            recomputed = compute_hash(
                id=row.id,
                seq=row.seq,
                created_at=row.created_at,
                actor_id=row.actor_id,
                actor_label=row.actor_label,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                outcome=row.outcome,
                ip=row.ip,
                user_agent=row.user_agent,
                meta=row.meta,
                prev_hash=row.prev_hash,
            )
            if recomputed != row.hash:
                problems.append(
                    {
                        "seq": row.seq,
                        "kind": "hash_mismatch",
                        "detail": "row content does not match its stored hash",
                    }
                )
            expected_prev = row.hash
            expected_seq += 1

        return {
            "ok": not problems,
            "checked": len(rows),
            "from_seq": rows[0].seq if rows else 0,
            "head_seq": rows[-1].seq if rows else 0,
            "head_hash": rows[-1].hash if rows else GENESIS_HASH,
            # Whether the first link was actually checked against something
            # known-good, or taken on trust -- see start_seq above. A caller
            # must not read ok=True from an unanchored run as proof the
            # history before start_seq is intact.
            "anchored": start_seq is None or expected_prev_hash is not None,
            "problems": problems,
        }
