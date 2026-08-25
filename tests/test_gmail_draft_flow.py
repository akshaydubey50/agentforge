"""Phase 5 Gmail draft path through existing approval/execution/verification seams.

DB-backed but no real Gmail credentials or network. HTTP is faked at the
gmail.py boundary so the tests exercise AgentForge's runtime path rather than
Google's service.
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from sqlmodel import select

from agentsys import execution, recovery, verification
from agentsys.config import settings
from agentsys.db.models import Escalation, GoogleConnection, Subtask, SubtaskStatus, Task, ToolCall, TraceSpan, User
from agentsys.db.session import get_session, init_db
from agentsys.escalations import apply_escalation_decision
from agentsys.graph import nodes
from agentsys.graph.schemas import ToolChoice
from agentsys.integrations import google_oauth
from agentsys.integrations.google_oauth import GMAIL_COMPOSE_SCOPE
from agentsys.policy import ActionType
from agentsys.tools import gmail
from agentsys.tools.gmail import GmailCreateDraftTool
from conftest import get_test_owner_id


def setup_module() -> None:
    init_db()


class Response:
    def __init__(self, payload: dict | None = None, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.text = text
        self.request = httpx.Request("GET", "https://gmail.test")
        self.response = httpx.Response(status_code, text=text, request=self.request)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=self.response)


class RegistryShim:
    def __init__(self, tool):
        self.tool = tool

    def get(self, name):
        if name == self.tool.name:
            return self.tool
        return nodes.get_registry().get(name)

    def names(self):
        return [self.tool.name]

    def describe(self, exclude=frozenset()):
        return self.tool.description


def _install_registry(monkeypatch, tool):
    shim = RegistryShim(tool)
    monkeypatch.setattr(nodes, "get_registry", lambda: shim)


def _make_task_and_subtask() -> tuple[str, str]:
    with get_session() as session:
        task = Task(
            request_text=(
                "Create a draft email to recruiter@example.com with subject "
                "'AI Engineer Follow-up' and body 'Thank you for speaking with me...'"
            ),
            owner_id=get_test_owner_id(),
        )
        session.add(task)
        session.commit()
        subtask = Subtask(
            task_id=task.id,
            position=0,
            description="Create the Gmail draft",
            assigned_tool="gmail_create_draft",
            success_criteria="gmail_draft_created",
            status=SubtaskStatus.READY,
        )
        session.add(subtask)
        session.commit()
        return task.id, subtask.id


def _choice(args: str | None = None) -> ToolChoice:
    return ToolChoice(
        tool_name="gmail_create_draft",
        tool_input_json=args
        or (
            '{"to":"recruiter@example.com","subject":"AI Engineer Follow-up",'
            '"body":"Thank you for speaking with me..."}'
        ),
        rationale="test",
    )


def _escalation(task_id: str) -> Escalation:
    with get_session() as session:
        return session.exec(select(Escalation).where(Escalation.task_id == task_id)).first()


def _calls(subtask_id: str) -> list[ToolCall]:
    with get_session() as session:
        return list(session.exec(select(ToolCall).where(ToolCall.subtask_id == subtask_id)).all())


def _fake_gmail(monkeypatch, *, verify_status: int = 200):
    posts = []
    gets = []
    monkeypatch.setattr(gmail, "get_valid_access_token", lambda user_id, **_kw: f"token-for-{user_id}")

    def post(url, *, headers, json, timeout):
        posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response({"id": "draft-1", "message": {"id": "msg-1"}})

    def get(url, *, headers, params, timeout):
        gets.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        return Response(
            {
                "id": "draft-1",
                "message": {
                    "id": "msg-1",
                    "payload": {
                        "headers": [
                            {"name": "To", "value": "recruiter@example.com"},
                            {"name": "Subject", "value": "AI Engineer Follow-up"},
                        ]
                    },
                },
            },
            status_code=verify_status,
            text="missing" if verify_status >= 400 else "",
        )

    monkeypatch.setattr(gmail.httpx, "post", post)
    monkeypatch.setattr(gmail.httpx, "get", get)
    return posts, gets


def test_draft_creation_requires_approval_and_does_not_execute_before_approval(monkeypatch):
    _install_registry(monkeypatch, GmailCreateDraftTool())
    posts, _gets = _fake_gmail(monkeypatch)
    task_id, subtask_id = _make_task_and_subtask()

    result = nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    assert result is None
    assert posts == []
    escalation = _escalation(task_id)
    assert escalation.kind == "tool_approval"
    assert escalation.context["tool_name"] == "gmail_create_draft"
    assert escalation.context["policy"]["decision"] == "require_approval"
    assert escalation.context["policy"]["action_type"] == "external_write"
    assert escalation.context["policy"]["risk"] == "medium"
    assert escalation.context["kwargs"]["to"] == "recruiter@example.com"
    assert escalation.context["kwargs"]["user_id"] == get_test_owner_id()


def test_approved_draft_creation_executes_once_records_identity_and_verifies(monkeypatch):
    _install_registry(monkeypatch, GmailCreateDraftTool())
    posts, gets = _fake_gmail(monkeypatch)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    _, should_resume = apply_escalation_decision(_escalation(task_id).id, decision="approve", decided_by="tester")

    assert should_resume is True
    assert len(posts) == 1
    assert len(gets) == 1
    rows = _calls(subtask_id)
    assert len(rows) == 1
    assert rows[0].success is True
    assert rows[0].effect_key
    assert rows[0].output["draft_id"] == "draft-1"
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        tool_span = session.exec(
            select(TraceSpan).where(TraceSpan.task_id == task_id, TraceSpan.span_type == "tool_call")
        ).first()
    assert subtask.status is SubtaskStatus.DONE
    assert tool_span.input["body"].startswith("[redacted from trace;")
    goal = verification.verify_goal(task_id, "Create a draft email to recruiter@example.com", [subtask])
    assert goal.route is verification.VerificationRoute.PASS


def test_modified_approved_draft_args_do_not_execute(monkeypatch):
    _install_registry(monkeypatch, GmailCreateDraftTool())
    posts, _gets = _fake_gmail(monkeypatch)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())
    escalation = _escalation(task_id)

    with get_session() as session:
        stored = session.get(Escalation, escalation.id)
        mutated = dict(stored.context["kwargs"])
        mutated["subject"] = "Different subject"
        stored.context = {**stored.context, "kwargs": mutated}
        session.add(stored)
        session.commit()

    apply_escalation_decision(escalation.id, decision="approve", decided_by="tester")

    assert posts == []
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
    assert subtask.status is SubtaskStatus.FAILED
    assert "no longer match" in subtask.output


def test_verification_failure_does_not_mark_approved_draft_done(monkeypatch):
    _install_registry(monkeypatch, GmailCreateDraftTool())
    posts, gets = _fake_gmail(monkeypatch, verify_status=404)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    apply_escalation_decision(_escalation(task_id).id, decision="approve", decided_by="tester")

    assert len(posts) == 1
    assert len(gets) == 1
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
    assert subtask.status is SubtaskStatus.FAILED
    assert "could not verify Gmail draft" in subtask.output


def test_dedupe_reuses_successful_draft_effect_without_calling_gmail_again(monkeypatch):
    _install_registry(monkeypatch, GmailCreateDraftTool())
    posts, _gets = _fake_gmail(monkeypatch)
    task_id, subtask_id = _make_task_and_subtask()
    kwargs = {
        "to": "recruiter@example.com",
        "subject": "AI Engineer Follow-up",
        "body": "Thank you for speaking with me...",
        "user_id": get_test_owner_id(),
    }

    first = execution.execute_tool(
        GmailCreateDraftTool(),
        kwargs,
        task_id=task_id,
        subtask_id=subtask_id,
        action_type=ActionType.EXTERNAL_WRITE,
        user_id=get_test_owner_id(),
    )
    second = execution.execute_tool(
        GmailCreateDraftTool(),
        kwargs,
        task_id=task_id,
        subtask_id=subtask_id,
        action_type=ActionType.EXTERNAL_WRITE,
        user_id=get_test_owner_id(),
    )

    assert first.result.success is True
    assert second.deduped is True
    assert len(posts) == 1


def test_ambiguous_draft_creation_is_held_and_not_replayed(monkeypatch):
    _install_registry(monkeypatch, GmailCreateDraftTool())
    posts, _gets = _fake_gmail(monkeypatch)
    task_id, subtask_id = _make_task_and_subtask()
    kwargs = {
        "to": "recruiter@example.com",
        "subject": "AI Engineer Follow-up",
        "body": "Thank you for speaking with me...",
        "user_id": get_test_owner_id(),
    }
    key = execution.effect_key(
        "gmail_create_draft",
        kwargs,
        task_id=task_id,
        user_id=get_test_owner_id(),
    )
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        subtask.status = SubtaskStatus.RUNNING
        session.add(subtask)
        session.commit()
    execution._open_call(subtask_id, "gmail_create_draft", kwargs, key)

    counts = recovery.reconcile_orphaned_subtasks(task_id)
    outcome = execution.execute_tool(
        GmailCreateDraftTool(),
        kwargs,
        task_id=task_id,
        subtask_id=subtask_id,
        action_type=ActionType.EXTERNAL_WRITE,
        user_id=get_test_owner_id(),
    )

    assert counts == 1
    assert posts == []
    assert outcome.refused is True
    assert "outcome is unknown" in outcome.result.error


def test_existing_google_connection_without_compose_scope_requires_reconnect(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "client")
    monkeypatch.setattr(settings, "google_client_secret", "secret")
    with get_session() as session:
        suffix = uuid4().hex
        user = User(google_sub=f"phase5-scope-user-{suffix}", email=f"phase5-scope-{suffix}@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(
            GoogleConnection(
                user_id=user.id,
                access_token="old-token",
                refresh_token="refresh-token",
                scopes="openid https://www.googleapis.com/auth/gmail.readonly",
                token_expiry=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        session.commit()
        user_id = user.id

    try:
        google_oauth.get_valid_access_token(
            user_id,
            required_scope=GMAIL_COMPOSE_SCOPE,
            purpose="Gmail draft creation (gmail.compose)",
        )
    except google_oauth.GoogleOAuthError as exc:
        assert "reconnected" in exc.detail
        assert "gmail.compose" in exc.detail
    else:
        raise AssertionError("missing gmail.compose scope should require reconnect")
