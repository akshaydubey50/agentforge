"""Small model/content guardrail seam for Phase 7D.

This module deliberately does not authorize actions. It detects content risk
at model boundaries; tool execution still belongs to Pydantic/JSON Schema,
policy.decide(), approval, execution, and verification.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agentsys.config import settings


class GuardrailDecision(str, Enum):
    ALLOW = "allow"
    FLAG = "flag"
    BLOCK = "block"


class GuardrailRiskType(str, Enum):
    NONE = "none"
    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    SECRET = "secret"
    PII = "pii"
    MCP_POISONING = "mcp_poisoning"
    APPROVAL_BYPASS = "approval_bypass"


class GuardrailStage(str, Enum):
    INPUT = "input"
    RETRIEVAL = "retrieval"
    OUTPUT = "output"
    MCP_DESCRIPTION = "mcp_description"
    MCP_OUTPUT = "mcp_output"
    MEMORY = "memory"


@dataclass(frozen=True)
class GuardrailResult:
    decision: GuardrailDecision
    risk_type: GuardrailRiskType = GuardrailRiskType.NONE
    reason: str = "no_risk_detected"
    confidence: float = 0.0
    detector: str = "deterministic"
    stage: GuardrailStage = GuardrailStage.INPUT
    metadata: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0

    @property
    def blocked(self) -> bool:
        return self.decision is GuardrailDecision.BLOCK

    def to_trace(self) -> dict[str, Any]:
        safe_meta = {
            str(k): v
            for k, v in (self.metadata or {}).items()
            if str(k) in _SAFE_TRACE_METADATA_KEYS and isinstance(v, (str, int, float, bool))
        }
        return {
            "stage": self.stage.value,
            "decision": self.decision.value,
            "risk_type": self.risk_type.value,
            "reason": self.reason[:120],
            "confidence": round(float(self.confidence), 3),
            "detector": self.detector,
            "blocked": self.blocked,
            "latency_ms": self.latency_ms,
            "metadata": safe_meta,
        }


_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{12,}")),
    ("openai_api_key", re.compile(r"(?i)\bsk-[a-z0-9_-]{16,}")),
    ("oauth_token", re.compile(r"(?i)\bya29\.[a-z0-9._-]{10,}")),
    ("github_token", re.compile(r"(?i)\bgithub_pat_[a-z0-9_]{20,}|\bgh[pousr]_[a-z0-9_]{20,}")),
    ("slack_token", re.compile(r"(?i)\bxox[baprs]-[a-z0-9-]{16,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "credential_assignment",
        re.compile(
            r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
            r"password|passwd)\s*[:=]\s*['\"]?[a-z0-9._~+/=-]{10,}"
        ),
    ),
]
_PLACEHOLDER_SECRET_MARKERS = ("example", "placeholder", "dummy", "fake", "redacted")
_SAFE_TRACE_METADATA_KEYS = {
    "candidate_kind",
    "disabled",
    "server",
    "sink",
    "source",
    "tool",
    "trust_status",
}
_CREDENTIAL_FIELD_NAMES = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "bearer",
    "bearer_token",
    "client_secret",
    "credential",
    "credentials",
    "oauth",
    "oauth_token",
    "passwd",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d(). -]{8,}\d)(?!\d)")

_BENIGN_SECURITY_CONTEXT = re.compile(
    r"(?is)(?=.*\b(prompt injection|jailbreak|red[- ]team|owasp|security research|threat model)\b)"
    r"(?=.*\b(example|article|documentation|research|discuss|explain|detect|defend|mitigation|guide|"
    r"techniques|recommends|protecting)\b)"
)

_INJECTION_PATTERNS: list[tuple[GuardrailRiskType, str, re.Pattern[str], float]] = [
    (
        GuardrailRiskType.PROMPT_INJECTION,
        "fake_system_message",
        re.compile(
            r"(?is)\b(system|developer)\s*(message|instruction|prompt)\s*:\s*"
            r"(ignore|reveal|send|call|execute|disable|override)\b"
        ),
        0.88,
    ),
    (
        GuardrailRiskType.PROMPT_INJECTION,
        "act_as_system_reveal_secrets",
        re.compile(
            r"(?is)\bact\s+as\s+(the\s+)?system\b.{0,80}"
            r"\b(reveal|leak|show|print)\b.{0,80}\b(secrets?|credentials?|tokens?|private)\b"
        ),
        0.9,
    ),
    (
        GuardrailRiskType.APPROVAL_BYPASS,
        "approval_bypass_instruction",
        re.compile(r"(?is)\b(bypass|skip|disable|ignore)\b.{0,80}\b(approval|human approval|policy)\b"),
        0.95,
    ),
    (
        GuardrailRiskType.PROMPT_INJECTION,
        "ignore_higher_priority_instructions",
        re.compile(r"(?is)\b(ignore|disregard|override)\b.{0,80}\b(previous|prior|system|developer|policy|instructions)\b"),
        0.9,
    ),
    (
        GuardrailRiskType.JAILBREAK,
        "roleplay_jailbreak",
        re.compile(r"(?is)\b(developer mode|dan mode|jailbreak|no restrictions|uncensored mode)\b"),
        0.86,
    ),
    (
        GuardrailRiskType.PROMPT_INJECTION,
        "exfiltration_instruction",
        re.compile(r"(?is)\b(send|upload|post|exfiltrate|leak)\b.{0,120}\b(candidate|private|secret|credential|all data|company data|records)\b"),
        0.92,
    ),
    (
        GuardrailRiskType.MCP_POISONING,
        "tool_poisoning_instruction",
        re.compile(
            r"(?is)\b(always call|call me first|use this tool first|always pass|pass the user's)\b.{0,120}"
            r"\b(credentials?|secrets?|tokens?|passwords?|api keys?|all data)\b"
        ),
        0.94,
    ),
]


def _elapsed_ms(start: float) -> int:
    return max(0, int((time.perf_counter() - start) * 1000))


def _allow(stage: GuardrailStage, start: float, *, metadata: dict[str, Any] | None = None) -> GuardrailResult:
    return GuardrailResult(stage=stage, decision=GuardrailDecision.ALLOW, metadata=metadata or {}, latency_ms=_elapsed_ms(start))


def _result(
    *,
    stage: GuardrailStage,
    decision: GuardrailDecision,
    risk_type: GuardrailRiskType,
    reason: str,
    confidence: float,
    start: float,
    metadata: dict[str, Any] | None = None,
) -> GuardrailResult:
    return GuardrailResult(
        stage=stage,
        decision=decision,
        risk_type=risk_type,
        reason=reason,
        confidence=confidence,
        metadata=metadata or {},
        latency_ms=_elapsed_ms(start),
    )


def _secret_match(text: str) -> tuple[str, float] | None:
    for name, pattern in _SECRET_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        matched = match.group(0).lower()
        if any(marker in matched for marker in _PLACEHOLDER_SECRET_MARKERS):
            continue
        if name == "bearer_token" and matched.startswith("bearer authentication"):
            continue
        return name, 1.0
    return None


def redact_secrets(text: str, *, replacement: str = "[REDACTED_FOR_TELEMETRY]") -> str:
    redacted = str(text)
    for _name, pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(
            lambda match: match.group(0)
            if any(marker in match.group(0).lower() for marker in _PLACEHOLDER_SECRET_MARKERS)
            else replacement,
            redacted,
        )
    return redacted


def is_credential_field_name(name: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    if normalized in _CREDENTIAL_FIELD_NAMES:
        return True
    return any(
        normalized.endswith(f"_{suffix}")
        for suffix in (
            "api_key",
            "access_token",
            "authorization",
            "bearer_token",
            "client_secret",
            "oauth_token",
            "password",
            "private_key",
            "refresh_token",
        )
    )


def _injection_match(text: str) -> tuple[GuardrailRiskType, str, float] | None:
    if _BENIGN_SECURITY_CONTEXT.search(text or ""):
        return None
    for risk_type, reason, pattern, confidence in _INJECTION_PATTERNS:
        if pattern.search(text or ""):
            return risk_type, reason, confidence
    return None


def _pii_match(text: str) -> str | None:
    if _EMAIL_RE.search(text or ""):
        return "email_address"
    if _PHONE_RE.search(text or ""):
        return "phone_number"
    return None


def check_input(text: str, metadata: dict[str, Any] | None = None) -> GuardrailResult:
    start = time.perf_counter()
    metadata = metadata or {}
    if not settings.enable_guardrails:
        return _allow(GuardrailStage.INPUT, start, metadata={"source": metadata.get("source", "user_input"), "disabled": True})
    secret = _secret_match(text)
    if secret:
        name, confidence = secret
        return _result(
            stage=GuardrailStage.INPUT,
            decision=GuardrailDecision.BLOCK,
            risk_type=GuardrailRiskType.SECRET,
            reason=name,
            confidence=confidence,
            start=start,
            metadata={"source": metadata.get("source", "user_input")},
        )

    injection = _injection_match(text)
    if injection:
        risk_type, reason, confidence = injection
        decision = GuardrailDecision.BLOCK if risk_type in {GuardrailRiskType.APPROVAL_BYPASS, GuardrailRiskType.PROMPT_INJECTION, GuardrailRiskType.JAILBREAK} else GuardrailDecision.FLAG
        return _result(
            stage=GuardrailStage.INPUT,
            decision=decision,
            risk_type=risk_type,
            reason=reason,
            confidence=confidence,
            start=start,
            metadata={"source": metadata.get("source", "user_input")},
        )
    return _allow(GuardrailStage.INPUT, start, metadata={"source": metadata.get("source", "user_input")})


def check_retrieved_content(
    text: str,
    *,
    source: str,
    metadata: dict[str, Any] | None = None,
) -> GuardrailResult:
    start = time.perf_counter()
    stage = GuardrailStage.MEMORY if source == "memory" else GuardrailStage.MCP_OUTPUT if source == "mcp" else GuardrailStage.RETRIEVAL
    metadata = {"source": source, **(metadata or {})}
    if not settings.enable_guardrails:
        return _allow(stage, start, metadata={**metadata, "disabled": True})

    secret = _secret_match(text)
    if secret:
        name, confidence = secret
        return _result(
            stage=stage,
            decision=GuardrailDecision.BLOCK,
            risk_type=GuardrailRiskType.SECRET,
            reason=name,
            confidence=confidence,
            start=start,
            metadata=metadata,
        )

    injection = _injection_match(text)
    if injection:
        risk_type, reason, confidence = injection
        return _result(
            stage=stage,
            decision=GuardrailDecision.FLAG,
            risk_type=risk_type,
            reason=reason,
            confidence=confidence,
            start=start,
            metadata=metadata,
        )
    return _allow(stage, start, metadata=metadata)


def check_output(text: str, metadata: dict[str, Any] | None = None) -> GuardrailResult:
    start = time.perf_counter()
    metadata = metadata or {}
    if not settings.enable_guardrails:
        return _allow(GuardrailStage.OUTPUT, start, metadata={"sink": metadata.get("sink", "user"), "disabled": True})
    secret = _secret_match(text)
    if secret:
        name, confidence = secret
        return _result(
            stage=GuardrailStage.OUTPUT,
            decision=GuardrailDecision.BLOCK,
            risk_type=GuardrailRiskType.SECRET,
            reason=name,
            confidence=confidence,
            start=start,
            metadata={"sink": metadata.get("sink", "user")},
        )

    if not metadata.get("allow_pii"):
        pii = _pii_match(text)
        if pii:
            return _result(
                stage=GuardrailStage.OUTPUT,
                decision=GuardrailDecision.FLAG,
                risk_type=GuardrailRiskType.PII,
                reason=pii,
                confidence=0.75,
                start=start,
                metadata={"sink": metadata.get("sink", "user")},
            )

    injection = _injection_match(text)
    if injection:
        risk_type, reason, confidence = injection
        return _result(
            stage=GuardrailStage.OUTPUT,
            decision=GuardrailDecision.FLAG,
            risk_type=risk_type,
            reason=reason,
            confidence=confidence,
            start=start,
            metadata={"sink": metadata.get("sink", "user")},
        )
    return _allow(GuardrailStage.OUTPUT, start, metadata={"sink": metadata.get("sink", "user")})


def check_mcp_description(
    description: str,
    *,
    server_name: str,
    tool_name: str,
) -> GuardrailResult:
    start = time.perf_counter()
    if not settings.enable_guardrails:
        return _allow(
            GuardrailStage.MCP_DESCRIPTION,
            start,
            metadata={"server": server_name, "tool": tool_name, "disabled": True},
        )
    injection = _injection_match(description)
    if injection:
        risk_type, reason, confidence = injection
        return _result(
            stage=GuardrailStage.MCP_DESCRIPTION,
            decision=GuardrailDecision.BLOCK,
            risk_type=risk_type if risk_type is GuardrailRiskType.MCP_POISONING else GuardrailRiskType.MCP_POISONING,
            reason=reason,
            confidence=confidence,
            start=start,
            metadata={"server": server_name, "tool": tool_name},
        )
    return _allow(
        GuardrailStage.MCP_DESCRIPTION,
        start,
        metadata={"server": server_name, "tool": tool_name},
    )


def mcp_fingerprint(
    *,
    server_name: str,
    tool_name: str,
    description: str,
    input_schema: dict | None,
    action_type: str | None = None,
    risk: str | None = None,
    execution_safety: str | None = None,
) -> str:
    payload = {
        "server": server_name,
        "tool": tool_name,
        "description": description or "",
        "input_schema": input_schema or {},
        "declared_action_type": str(action_type or ""),
        "declared_risk": str(risk or ""),
        "declared_execution_safety": str(execution_safety or ""),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
