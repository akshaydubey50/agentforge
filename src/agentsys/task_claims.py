"""Runtime execution claims backed by Postgres advisory locks.

These claims coordinate workers; they do not replace durable business state.
The important property is that no database transaction is held while the graph
does slow work. A session-level advisory lock pins one DB connection for the
duration of a graph invocation, serialising duplicate Celery deliveries for the
same task without adding a lease table.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from hashlib import sha256
from typing import Iterator

from sqlalchemy import text

from agentsys.db.session import get_engine

logger = logging.getLogger(__name__)

_LOCAL_LOCKS: dict[int, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


def _lock_key(namespace: str, value: str) -> int:
    payload = f"{namespace}:{value}".encode("utf-8")
    return int.from_bytes(sha256(payload).digest()[:8], "big", signed=True)


def _local_lock(key: int) -> threading.Lock:
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCAL_LOCKS[key] = lock
        return lock


@contextmanager
def advisory_claim(namespace: str, value: str) -> Iterator[bool]:
    """Try to claim one logical unit of work.

    Returns `True` when this process owns the claim. A `False` claim means
    another worker already owns it and the caller must not execute the work.
    For non-Postgres test databases, falls back to a process-local lock so
    deterministic unit tests can still prove duplicate suppression.
    """
    key = _lock_key(namespace, value)
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        lock = _local_lock(key)
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
        return

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar())
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            except Exception as exc:  # noqa: BLE001
                # Closing the connection releases a session advisory lock. This
                # log is still useful because an unlock failure can indicate a
                # broken DB connection during shutdown.
                logger.warning("failed to release advisory claim %s:%s: %s", namespace, value, exc)


def task_claim(task_id: str):
    return advisory_claim("agentsys.task", task_id)


def recovery_claim():
    return advisory_claim("agentsys.recovery", "startup-sweep")
