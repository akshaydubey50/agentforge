"""Integrity properties of the hash-chained audit log.

These assert what an attacker would try to violate rather than that the
happy path writes a row: that editing a recorded event is detectable, that
deleting one is detectable, that credentials cannot reach the table however
carelessly a call site behaves, and that concurrent writers cannot fork the
chain into two valid-looking branches.

Fully deterministic -- no LLM calls, no network.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlmodel import select

from agentsys import audit
from agentsys.db.models import AuditEvent
from agentsys.db.session import get_session, init_db


@pytest.fixture(scope="module", autouse=True)
def _clean_chain():
    """Starts this module against an empty log.

    Necessary, not merely tidy: the tamper tests below deliberately corrupt
    the chain, and a global chain stays unverifiable from the corrupted row
    onward -- by design. Without a clean start, every whole-chain assertion
    here would pass exactly once, on a fresh database, and fail on every
    re-run against the same one.

    Scoped to the module rather than each test so that the ordering-dependent
    assertions (a clean verify BEFORE the tamper tests run) still mean what
    they say.
    """
    init_db()
    with get_session() as db:
        db.execute(text("DELETE FROM audit_event"))
        db.commit()


def _record(**kwargs) -> AuditEvent:
    """record() returns the hash; the tests want the row."""
    h = audit.record(kwargs.pop("action", "test.event"), **kwargs)
    assert h is not None, "audit write failed"
    with get_session() as db:
        row = db.exec(select(AuditEvent).where(AuditEvent.hash == h)).one()
        db.expunge(row)
        return row


# --------------------------------------------------------------------------
# Chain construction
# --------------------------------------------------------------------------


def test_events_link_to_their_predecessor():
    a = _record(action="test.link.a")
    b = _record(action="test.link.b")
    assert b.prev_hash == a.hash
    assert b.seq == a.seq + 1


def test_chain_head_matches_the_newest_row():
    row = _record(action="test.head")
    seq, head = audit.chain_head()
    assert (seq, head) == (row.seq, row.hash)


def test_verify_passes_on_an_untouched_chain():
    _record(action="test.verify.clean")
    result = audit.verify_chain()
    assert result["ok"], result["problems"]
    assert result["checked"] >= 1


# --------------------------------------------------------------------------
# Tamper detection -- the whole point of the chain
# --------------------------------------------------------------------------


def test_editing_a_recorded_event_is_detected():
    """The realistic attack: an approval that happened gets quietly rewritten
    to say something else. The row's own hash no longer matches its content,
    and every row after it is now unverifiable too."""
    row = _record(action="test.tamper.edit", outcome="success", meta={"decision": "reject"})
    _record(action="test.tamper.after")  # a later row, so the break propagates

    with get_session() as db:
        target = db.get(AuditEvent, row.id)
        target.outcome = "success"
        target.meta = {"decision": "approve"}  # the lie
        db.add(target)
        db.commit()

    result = audit.verify_chain()
    assert not result["ok"]
    kinds = {p["kind"] for p in result["problems"] if p["seq"] == row.seq}
    assert "hash_mismatch" in kinds


def test_deleting_an_event_is_detected_as_both_a_gap_and_a_broken_link():
    """Removing a row entirely -- the "this never happened" attack. Two
    independent signals fire, which is deliberate: the seq gap names exactly
    which position vanished, the broken link proves the survivors were not
    simply written that way."""
    _record(action="test.tamper.delete.first")
    victim = _record(action="test.tamper.delete.victim")
    _record(action="test.tamper.delete.after")

    with get_session() as db:
        db.delete(db.get(AuditEvent, victim.id))
        db.commit()

    result = audit.verify_chain()
    assert not result["ok"]
    kinds = {p["kind"] for p in result["problems"]}
    assert "seq_gap" in kinds
    assert "broken_link" in kinds


def test_reassigning_an_event_to_a_different_actor_is_detected():
    """actor_id is inside the hash, so "it wasn't me, it was them" requires
    breaking the chain."""
    row = _record(action="test.tamper.actor", actor_id="alice", actor_label="alice@example.com")

    with get_session() as db:
        target = db.get(AuditEvent, row.id)
        target.actor_id = "mallory"
        db.add(target)
        db.commit()

    problems = audit.verify_chain()["problems"]
    assert any(p["seq"] == row.seq and p["kind"] == "hash_mismatch" for p in problems)


def test_hash_is_stable_across_recomputation():
    """Verification is worthless if an honest row fails it. The canonical
    serialization has to produce identical bytes for the stored row and the
    recomputed one, including through the Postgres round trip."""
    row = _record(
        action="test.stable",
        actor_id="u1",
        meta={"z": 1, "a": {"nested": ["x", 2, None]}, "unicode": "cliché ✓"},
    )
    with get_session() as db:
        stored = db.get(AuditEvent, row.id)
        recomputed = audit.compute_hash(
            id=stored.id,
            seq=stored.seq,
            created_at=stored.created_at,
            actor_id=stored.actor_id,
            actor_label=stored.actor_label,
            action=stored.action,
            target_type=stored.target_type,
            target_id=stored.target_id,
            outcome=stored.outcome,
            ip=stored.ip,
            user_agent=stored.user_agent,
            meta=stored.meta,
            prev_hash=stored.prev_hash,
        )
    assert recomputed == stored.hash


# --------------------------------------------------------------------------
# Credentials must not reach the table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "client_secret",
        "api_key",
        "authorization",
        "cookie",
        "code",
        "state",
        "code_verifier",
        "totp",
    ],
)
def test_credential_shaped_keys_are_redacted(key: str):
    row = _record(action="test.redact", meta={key: "super-secret-value"})
    assert row.meta[key] == audit.REDACTED
    assert "super-secret-value" not in str(row.meta)


def test_redaction_reaches_nested_structures():
    """A call site splatting a whole token response is the realistic mistake,
    and it is rarely flat."""
    row = _record(
        action="test.redact.nested",
        meta={"response": {"tokens": [{"access_token": "leak-me"}]}, "ok": True},
    )
    assert "leak-me" not in str(row.meta)
    assert row.meta["ok"] is True


def test_useful_fields_are_not_over_redacted():
    """Redaction that eats status_code/error_code/task_state would make the
    log unreadable, which is its own kind of failure -- this is why those
    keys are exact-matched, not substring-matched."""
    row = _record(
        action="test.redact.precision",
        meta={"status_code": 403, "error_code": "denied", "task_state": "running", "tool_name": "code_execution"},
    )
    assert row.meta["status_code"] == 403
    assert row.meta["error_code"] == "denied"
    assert row.meta["task_state"] == "running"
    assert row.meta["tool_name"] == "code_execution"


def test_oversized_values_are_bounded():
    row = _record(action="test.redact.size", meta={"blob": "x" * 10_000})
    assert len(row.meta["blob"]) < 1_000
    assert "truncated" in row.meta["blob"]


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------


def test_concurrent_writers_do_not_fork_the_chain():
    """Without the advisory lock, two writers read the same tail and both
    claim the same seq/prev_hash -- producing two rows that each look valid
    in isolation. This is the test that would fail if the lock were removed."""
    marker = f"test.concurrent.{uuid.uuid4()}"
    with ThreadPoolExecutor(max_workers=8) as pool:
        hashes = list(pool.map(lambda i: audit.record(marker, meta={"i": i}), range(8)))

    assert all(h is not None for h in hashes), "an audit write failed under contention"
    assert len(set(hashes)) == 8, "two writers produced identical rows"

    with get_session() as db:
        rows = db.exec(
            select(AuditEvent).where(AuditEvent.action == marker).order_by(AuditEvent.seq)
        ).all()
        seqs = [r.seq for r in rows]
        first_prev = rows[0].prev_hash

    assert len(set(seqs)) == 8, "two writers claimed the same chain position"
    assert seqs == list(range(seqs[0], seqs[0] + 8)), "chain positions are not contiguous"

    # Verified as a SEGMENT, not from the start of the table: earlier tests in
    # this file deliberately corrupt the chain, and a global chain stays
    # unverifiable from that point on -- which is the design working, not a
    # flaw. Anchoring on the first row's own prev_hash checks exactly what
    # this test is about (that these 8 concurrent writes link cleanly).
    result = audit.verify_chain(start_seq=seqs[0], expected_prev_hash=first_prev)
    assert result["ok"], result["problems"]
    assert result["anchored"]


# --------------------------------------------------------------------------
# Failure policy
# --------------------------------------------------------------------------


def test_a_failed_write_does_not_raise_into_the_caller(monkeypatch):
    """Fail open: an audit failure must not be able to take down the auth
    path it is observing. It returns None rather than propagating."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(audit, "get_session", boom)
    assert audit.record("test.failopen") is None


# --------------------------------------------------------------------------
# Incremental verification -- what makes the integrity check runnable in prod
# --------------------------------------------------------------------------


def test_incremental_verification_checks_only_the_new_tail():
    """A scheduled job anchors (seq, hash), then re-verifies only what was
    appended since. Re-hashing all of history every run is what makes such a
    job quietly stop being run."""
    anchor_seq, anchor_hash = audit.chain_head()
    for i in range(3):
        _record(action="test.incremental", meta={"i": i})

    result = audit.verify_chain(start_seq=anchor_seq + 1, expected_prev_hash=anchor_hash)
    assert result["ok"], result["problems"]
    assert result["checked"] == 3
    assert result["anchored"]


def test_incremental_verification_catches_a_break_at_the_anchor_boundary():
    """The seam between the anchored point and the new tail is exactly where
    an attacker would splice, so it has to be checked, not assumed."""
    anchor_seq, _anchor_hash = audit.chain_head()
    _record(action="test.incremental.break")

    result = audit.verify_chain(
        start_seq=anchor_seq + 1, expected_prev_hash="deadbeef" * 8
    )
    assert not result["ok"]
    assert any(p["kind"] == "broken_link" for p in result["problems"])


def test_unanchored_verification_is_reported_as_such():
    """ok=True from a run with no anchor says nothing about the history
    before it, and the result has to admit that rather than look clean."""
    anchor_seq, _ = audit.chain_head()
    _record(action="test.incremental.unanchored")
    result = audit.verify_chain(start_seq=anchor_seq + 1)
    assert result["ok"]
    assert result["anchored"] is False
