from __future__ import annotations

import hashlib
import re
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from sqlmodel import select

from agentsys.config import settings
from agentsys.db.models import MemoryEntry, Subtask, Task
from agentsys.db.session import get_session
from agentsys.graph.schemas import MemoryCandidate
from agentsys.memory import long_term

SUPPORTED_KINDS = {"semantic", "episodic", "pinned_decision", "preference", "artifact_reference", "fact"}
SUPPORTED_SCOPES = {"task", "conversation", "user"}
_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


@dataclass
class CandidateDecision:
    candidate_index: int
    outcome: str
    reason: str
    memory_id: str | None = None
    kind: str | None = None


@dataclass
class CurationResult:
    task_id: str
    owner_id: str | None
    candidate_count: int = 0
    stored_count: int = 0
    merged_count: int = 0
    ignored_count: int = 0
    index_failed_count: int = 0
    decisions: list[CandidateDecision] = field(default_factory=list)

    def to_trace(self) -> dict:
        return {
            "candidate_count": self.candidate_count,
            "stored_count": self.stored_count,
            "merged_count": self.merged_count,
            "ignored_count": self.ignored_count,
            "index_failed_count": self.index_failed_count,
            "decisions": [
                {
                    "outcome": d.outcome,
                    "reason": d.reason,
                    "memory_id": d.memory_id,
                    "kind": d.kind,
                }
                for d in self.decisions
            ],
        }


@dataclass
class _ValidatedCandidate:
    kind: str
    scope: str
    content: str
    importance: int
    confidence: float
    source: dict
    normalized_hash: str


def normalize_content(content: str) -> str:
    collapsed = re.sub(r"\s+", " ", content.strip().lower())
    return collapsed.translate(_PUNCT_TABLE).strip()


def normalized_hash(content: str) -> str:
    return hashlib.sha256(normalize_content(content).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_kind(kind: str) -> str:
    return "semantic" if kind == "fact" else kind


def _artifact_refs(source: dict) -> list[dict]:
    refs = source.get("artifact_refs") or source.get("artifacts") or []
    if not isinstance(refs, list):
        return []
    allowed = {"task_id", "path", "kind", "bytes", "content_hash", "summary"}
    sanitized: list[dict] = []
    for ref in refs:
        if isinstance(ref, dict):
            sanitized.append({key: value for key, value in ref.items() if key in allowed})
    return sanitized


def _safe_source(source: dict) -> dict:
    blocked = {"body", "content", "raw", "output", "full_text", "artifact_body"}
    safe = {key: value for key, value in source.items() if key not in blocked and key not in {"artifacts"}}
    if "artifact_refs" in source or "artifacts" in source:
        safe["artifact_refs"] = _artifact_refs(source)
    return safe


def _validate_candidate(candidate: MemoryCandidate, *, task: Task, known_subtask_ids: set[str]) -> tuple[_ValidatedCandidate | None, str]:
    kind = _canonical_kind(candidate.kind)
    if candidate.kind not in SUPPORTED_KINDS or kind not in SUPPORTED_KINDS:
        return None, "unsupported_kind"
    if candidate.scope not in SUPPORTED_SCOPES:
        return None, "unsupported_scope"

    content = candidate.content.strip()
    if not content:
        return None, "empty_content"
    if len(content) > settings.memory_candidate_max_chars:
        return None, "oversized_content"
    if candidate.confidence < settings.memory_candidate_min_confidence:
        return None, "low_confidence"

    source = dict(candidate.source or {})
    source["source_task_id"] = task.id
    source["owner_id"] = task.owner_id

    subtask_ids = source.get("subtask_ids") or []
    if subtask_ids and (
        not isinstance(subtask_ids, list)
        or any(not isinstance(item, str) or item not in known_subtask_ids for item in subtask_ids)
    ):
        return None, "invalid_subtask_source"

    if kind == "artifact_reference":
        refs = _artifact_refs(source)
        if not refs:
            return None, "missing_artifact_reference"
        for ref in refs:
            if not isinstance(ref, dict):
                return None, "invalid_artifact_reference"
            ref_task_id = ref.get("task_id") or task.id
            if ref_task_id != task.id:
                return None, "cross_task_artifact_reference"
            path = ref.get("path")
            if not isinstance(path, str) or not path.strip() or len(path) > 500:
                return None, "invalid_artifact_path"
        source["artifact_refs"] = refs

    importance = 5 if kind == "pinned_decision" else candidate.importance
    return (
        _ValidatedCandidate(
            kind=kind,
            scope=candidate.scope,
            content=content,
            importance=importance,
            confidence=candidate.confidence,
            source=_safe_source(source),
            normalized_hash=normalized_hash(content),
        ),
        "",
    )


def _exact_duplicate(candidate: _ValidatedCandidate, *, owner_id: str) -> MemoryEntry | None:
    with get_session() as session:
        rows = session.exec(
            select(MemoryEntry).where(MemoryEntry.owner_id == owner_id, MemoryEntry.kind == candidate.kind)
        ).all()
    for row in rows:
        meta = row.meta or {}
        if (
            meta.get("scope", "user") == candidate.scope
            and meta.get("normalized_hash") == candidate.normalized_hash
        ):
            return row
    return None


def _semantic_duplicate(candidate: _ValidatedCandidate, *, owner_id: str) -> MemoryEntry | None:
    try:
        matches = long_term.retrieve_relevant(
            candidate.content,
            owner_id=owner_id,
            k=1,
            kind=candidate.kind,
            similarity_floor=settings.memory_semantic_merge_similarity,
        )
    except Exception:
        return None
    if not matches:
        return None
    match = matches[0]
    if match.similarity < settings.memory_semantic_merge_similarity:
        return None
    with get_session() as session:
        row = session.get(MemoryEntry, match.id)
        if row and row.owner_id == owner_id and row.kind == candidate.kind:
            return row
    return None


def _merge_into(existing: MemoryEntry, candidate: _ValidatedCandidate) -> tuple[str, bool]:
    with get_session() as session:
        entry = session.get(MemoryEntry, existing.id)
        if not entry:
            return existing.id, False
        meta = dict(entry.meta or {})
        sources = list(meta.get("sources") or [])
        sources.append(candidate.source)
        meta["sources"] = sources[-10:]
        meta["last_source"] = candidate.source
        meta["scope"] = meta.get("scope") or candidate.scope
        meta["confidence"] = max(float(meta.get("confidence") or 0), candidate.confidence)
        meta["reinforcement_count"] = int(meta.get("reinforcement_count") or 0) + 1
        meta["normalized_hash"] = meta.get("normalized_hash") or candidate.normalized_hash
        entry.importance = max(entry.importance, candidate.importance)
        entry.meta = meta
        entry.updated_at = _now()
        session.add(entry)
        session.commit()
    try:
        long_term.index_memory(existing.id)
        return existing.id, True
    except Exception:
        return existing.id, False


def _store(candidate: _ValidatedCandidate, *, owner_id: str, task_id: str) -> tuple[str, bool]:
    meta = {
        "scope": candidate.scope,
        "confidence": candidate.confidence,
        "normalized_hash": candidate.normalized_hash,
        "source_task_id": task_id,
        "sources": [candidate.source],
        "reinforcement_count": 1,
        "status": "active",
    }
    if candidate.kind == "artifact_reference":
        meta["artifact_refs"] = _artifact_refs(candidate.source)
    memory_id = long_term.add_memory(
        candidate.content,
        kind=candidate.kind,
        owner_id=owner_id,
        task_id=task_id,
        importance=candidate.importance,
        meta=meta,
    )
    with get_session() as session:
        entry = session.get(MemoryEntry, memory_id)
        indexed = bool((entry.meta or {}).get("chroma_indexed")) if entry else False
    return memory_id, indexed


def _mark_superseded_if_explicit(candidate: _ValidatedCandidate, *, owner_id: str, superseded_by: str) -> None:
    supersedes_id = candidate.source.get("supersedes_memory_id")
    if not isinstance(supersedes_id, str) or not supersedes_id:
        return
    with get_session() as session:
        existing = session.get(MemoryEntry, supersedes_id)
        if not existing or existing.owner_id != owner_id:
            return
        meta = dict(existing.meta or {})
        meta["status"] = "superseded"
        meta["superseded_by"] = superseded_by
        meta["superseded_at"] = _now().isoformat()
        existing.meta = meta
        existing.updated_at = _now()
        session.add(existing)
        session.commit()


def curate_task_memory(task_id: str, candidates: Iterable[MemoryCandidate]) -> CurationResult:
    candidate_list = list(candidates)
    with get_session() as session:
        task = session.get(Task, task_id)
        if not task:
            return CurationResult(task_id=task_id, owner_id=None, candidate_count=len(candidate_list))
        owner_id = task.owner_id
        subtasks = session.exec(select(Subtask).where(Subtask.task_id == task_id)).all()
        known_subtask_ids = {s.id for s in subtasks}

    result = CurationResult(task_id=task_id, owner_id=owner_id, candidate_count=len(candidate_list))
    if not owner_id:
        result.ignored_count = len(candidate_list)
        result.decisions.extend(
            CandidateDecision(i, "ignored", "missing_owner") for i, _ in enumerate(candidate_list)
        )
        return result

    for index, candidate in enumerate(candidate_list):
        validated, reason = _validate_candidate(candidate, task=task, known_subtask_ids=known_subtask_ids)
        if not validated:
            result.ignored_count += 1
            result.decisions.append(CandidateDecision(index, "ignored", reason, kind=candidate.kind))
            continue

        existing = _exact_duplicate(validated, owner_id=owner_id)
        merge_reason = "exact_duplicate"
        if not existing:
            existing = _semantic_duplicate(validated, owner_id=owner_id)
            merge_reason = "semantic_duplicate" if existing else ""

        if existing:
            memory_id, indexed = _merge_into(existing, validated)
            result.merged_count += 1
            if not indexed:
                result.index_failed_count += 1
            result.decisions.append(
                CandidateDecision(index, "merged", merge_reason, memory_id=memory_id, kind=validated.kind)
            )
            continue

        memory_id, indexed = _store(validated, owner_id=owner_id, task_id=task_id)
        _mark_superseded_if_explicit(validated, owner_id=owner_id, superseded_by=memory_id)
        result.stored_count += 1
        if not indexed:
            result.index_failed_count += 1
        result.decisions.append(CandidateDecision(index, "stored", "new_memory", memory_id=memory_id, kind=validated.kind))

    return result
