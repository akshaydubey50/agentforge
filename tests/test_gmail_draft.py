"""Phase 5 Gmail draft tool: pure deterministic checks.

No real Gmail credentials, no network, no database. The DB-backed approval /
ledger path is covered in tests/test_gmail_draft_flow.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
import pytest

from agentsys import execution, policy
from agentsys.db.models import Subtask, SubtaskStatus, ToolCall
from agentsys.execution import ExecutionSafety, FailureKind, run_with_retry
from agentsys.policy import ActionType, PolicyDecisionType, Risk
from agentsys.integrations.google_oauth import GMAIL_COMPOSE_SCOPE, GoogleOAuthError
from agentsys.tools import gmail
from agentsys.tools.base import ToolValidationError, validated_kwargs
from agentsys.tools.gmail import GmailCreateDraftTool
from agentsys.verification import VerificationRoute, verify_step


class Response:
    def __init__(self, payload: dict | None = None, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.text = text
        self.request = httpx.Request("POST", "https://gmail.test")
        self.response = httpx.Response(status_code, text=text, request=self.request)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=self.response)


def test_valid_draft_args_are_accepted():
    kwargs = validated_kwargs(
        GmailCreateDraftTool(),
        '{"to":"recruiter@example.com","subject":"AI Engineer Follow-up","body":"Thank you."}',
    )

    assert kwargs == {
        "to": "recruiter@example.com",
        "subject": "AI Engineer Follow-up",
        "body": "Thank you.",
    }


@pytest.mark.parametrize(
    "payload, field",
    [
        ('{"subject":"s","body":"b"}', "to"),
        ('{"to":"not-an-email","subject":"s","body":"b"}', "to"),
        ('{"to":"a@example.com,b@example.com","subject":"s","body":"b"}', "to"),
        ('{"to":"a@example.com; b@example.com","subject":"s","body":"b"}', "to"),
        ('{"to":"A <a@example.com> B <b@example.com>","subject":"s","body":"b"}', "to"),
        ('{"to":"a@example.com\\nBcc: b@example.com","subject":"s","body":"b"}', "to"),
        ('{"to":"a@example.com","subject":"","body":"b"}', "subject"),
        ('{"to":"a@example.com","subject":"hi\\nBcc: b@example.com","body":"b"}', "subject"),
        ('{"to":"a@example.com","subject":"s","body":""}', "body"),
    ],
)
def test_invalid_draft_args_are_rejected(payload, field):
    with pytest.raises(ToolValidationError) as caught:
        validated_kwargs(GmailCreateDraftTool(), payload)

    assert caught.value.field == field


def test_oversized_draft_fields_are_rejected():
    subject = "x" * 999

    with pytest.raises(ToolValidationError) as caught:
        validated_kwargs(GmailCreateDraftTool(), f'{{"to":"a@example.com","subject":"{subject}","body":"b"}}')

    assert caught.value.field == "subject"


def test_gmail_draft_policy_and_execution_safety_are_conservative():
    tool = GmailCreateDraftTool()
    decision = policy.decide(tool, {"to": "a@example.com", "subject": "s", "body": "b"})

    assert decision.decision is PolicyDecisionType.REQUIRE_APPROVAL
    assert decision.action_type is ActionType.EXTERNAL_WRITE
    assert decision.risk is Risk.MEDIUM
    assert execution.safety_of(tool) is ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT


def test_create_draft_calls_gmail_once_and_returns_resource_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(gmail, "get_valid_access_token", lambda user_id, **_kw: f"token-for-{user_id}")

    def post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response({"id": "draft-1", "message": {"id": "msg-1"}})

    monkeypatch.setattr(gmail.httpx, "post", post)

    result = GmailCreateDraftTool().run(
        to="recruiter@example.com",
        subject="AI Engineer Follow-up",
        body="Thank you for speaking with me.",
        user_id="user-a",
    )

    assert result.success is True
    assert len(calls) == 1
    assert calls[0]["headers"]["Authorization"] == "Bearer token-for-user-a"
    assert "raw" in calls[0]["json"]["message"]
    assert result.output["draft_id"] == "draft-1"
    assert result.output["message_id"] == "msg-1"
    assert result.output["to"] == "recruiter@example.com"
    assert result.output["subject"] == "AI Engineer Follow-up"
    assert "body" not in result.output


def test_missing_compose_scope_returns_reconnect_without_calling_gmail(monkeypatch):
    calls = []

    def token(user_id, *, required_scope=None, purpose=None):
        assert required_scope == GMAIL_COMPOSE_SCOPE
        assert "draft" in purpose.lower()
        raise GoogleOAuthError("Google account must be reconnected to grant Gmail draft creation (gmail.compose).")

    monkeypatch.setattr(gmail, "get_valid_access_token", token)
    monkeypatch.setattr(gmail.httpx, "post", lambda *a, **k: calls.append(1))

    result = GmailCreateDraftTool().run(to="a@example.com", subject="s", body="b", user_id="owner-a")

    assert result.success is False
    assert "reconnected" in result.error
    assert calls == []


def test_user_id_selects_the_connected_account(monkeypatch):
    seen = []

    def token(user_id, **_kw):
        seen.append(user_id)
        return "token"

    monkeypatch.setattr(gmail, "get_valid_access_token", token)
    monkeypatch.setattr(gmail.httpx, "post", lambda *a, **k: Response({"id": "d"}))

    GmailCreateDraftTool().run(to="a@example.com", subject="s", body="b", user_id="owner-a")
    GmailCreateDraftTool().run(to="a@example.com", subject="s", body="b", user_id="owner-b")

    assert seen == ["owner-a", "owner-b"]


def test_rate_limit_is_not_blindly_retried_for_draft_creation(monkeypatch):
    calls = []
    monkeypatch.setattr(gmail, "get_valid_access_token", lambda user_id, **_kw: "token")

    def post(*_args, **_kwargs):
        calls.append(1)
        return Response(status_code=429, text="rate limit")

    monkeypatch.setattr(gmail.httpx, "post", post)

    outcome = run_with_retry(
        GmailCreateDraftTool(),
        {"to": "a@example.com", "subject": "s", "body": "b", "user_id": "owner-a"},
    )

    assert len(calls) == 1
    assert outcome.result.success is False
    assert outcome.failure is FailureKind.RATE_LIMIT


def test_timeout_is_ambiguous_and_not_blindly_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(gmail, "get_valid_access_token", lambda user_id, **_kw: "token")

    def post(*_args, **_kwargs):
        calls.append(1)
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(gmail.httpx, "post", post)

    outcome = run_with_retry(
        GmailCreateDraftTool(),
        {"to": "a@example.com", "subject": "s", "body": "b", "user_id": "owner-a"},
    )

    assert len(calls) == 1
    assert outcome.result.success is False
    assert outcome.failure is FailureKind.TIMEOUT


def _draft_subtask() -> Subtask:
    return Subtask(
        task_id="task-1",
        position=0,
        description="create draft",
        assigned_tool="gmail_create_draft",
        status=SubtaskStatus.RUNNING,
    )


def _draft_call() -> ToolCall:
    return ToolCall(
        subtask_id="sub-1",
        tool_name="gmail_create_draft",
        input={
            "to": "recruiter@example.com",
            "subject": "AI Engineer Follow-up",
            "body": "Thank you.",
            "user_id": "owner-a",
        },
        output={"draft_id": "draft-1", "message_id": "msg-1"},
        success=True,
        latency_ms=1,
    )


def test_draft_verification_fetches_and_matches_recipient_subject(monkeypatch):
    monkeypatch.setattr(
        gmail,
        "get_draft",
        lambda user_id, draft_id: {
            "id": draft_id,
            "message": {
                "payload": {
                    "headers": [
                        {"name": "To", "value": "Recruiter <recruiter@example.com>"},
                        {"name": "Subject", "value": "AI Engineer Follow-up"},
                    ]
                }
            },
        },
    )

    result = verify_step("task-1", _draft_subtask(), _draft_call(), tool_success=True)

    assert result.route is VerificationRoute.PASS
    assert result.verified is True


def test_draft_verification_mismatch_requires_human(monkeypatch):
    monkeypatch.setattr(
        gmail,
        "get_draft",
        lambda user_id, draft_id: {
            "id": draft_id,
            "message": {
                "payload": {
                    "headers": [
                        {"name": "To", "value": "other@example.com"},
                        {"name": "Subject", "value": "AI Engineer Follow-up"},
                    ]
                }
            },
        },
    )

    result = verify_step("task-1", _draft_subtask(), _draft_call(), tool_success=True)

    assert result.route is VerificationRoute.REQUIRE_HUMAN
    assert result.needs_human is True


def test_draft_verification_rejects_extra_recipient(monkeypatch):
    monkeypatch.setattr(
        gmail,
        "get_draft",
        lambda user_id, draft_id: {
            "id": draft_id,
            "message": {
                "payload": {
                    "headers": [
                        {"name": "To", "value": "recruiter@example.com, other@example.com"},
                        {"name": "Subject", "value": "AI Engineer Follow-up"},
                    ]
                }
            },
        },
    )

    result = verify_step("task-1", _draft_subtask(), _draft_call(), tool_success=True)

    assert result.route is VerificationRoute.REQUIRE_HUMAN
