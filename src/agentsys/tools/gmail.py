"""Gmail tools over the connected account.

Search/read are read-only. Draft creation is the first external mutation this
system supports: it creates a Gmail draft, never sends it. All tools use the
same runtime-owned user_id injection, so the model never chooses whose Gmail
account is touched.
"""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agentsys.integrations.google_oauth import GMAIL_COMPOSE_SCOPE, GoogleOAuthError, get_valid_access_token
from agentsys.execution import ExecutionSafety
from agentsys.policy import ActionType, Risk
from agentsys.sanitize import wrap_untrusted
from agentsys.tools.base import Tool, ToolResult

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_MAX_RESULTS = 10
_EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_body(payload: dict) -> str:
    """Gmail nests the body in a MIME tree; pull the first text/plain part
    (falling back to text/html stripped-ish), decoding base64url."""
    def walk(part: dict) -> str | None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for sub in part.get("parts", []) or []:
            found = walk(sub)
            if found:
                return found
        if mime == "text/html" and data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return None

    return walk(payload) or ""


def _normalised_email(value: str) -> str:
    _display, addr = parseaddr(value)
    return addr.strip().lower()


def get_draft(user_id: str, draft_id: str) -> dict:
    """Fetch one Gmail draft using the same connected-account boundary as the
    tools. Kept small and module-local so Phase 4 verification can check a
    created draft without inventing another tool or table."""
    token = get_valid_access_token(user_id)
    resp = httpx.get(
        f"{_GMAIL_BASE}/drafts/{draft_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"format": "full"},
        timeout=20.0,
    )
    resp.raise_for_status()
    return resp.json()


class GmailSearchArgs(BaseModel):
    """user_id decides WHOSE mailbox this reads. It is injected from the task's
    owner and is deliberately not a field, here and in every other Google tool
    -- it is the per-user isolation boundary, not an argument."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="Gmail search syntax, e.g. 'from:jane@acme.com invoice'.")
    max_results: int = Field(default=_MAX_RESULTS, ge=1, le=50)


class GmailSearchTool(Tool):
    name = "gmail_search"
    args_model = GmailSearchArgs
    action_type = ActionType.READ
    risk = Risk.MEDIUM
    """MEDIUM: reads a real person's mailbox. The boundary that decides WHOSE
    is the injected user_id (graph/nodes.py's _injected_kwargs), not anything
    policy can see in the arguments -- so the READ/MEDIUM rule allows the call
    and that injection remains the whole of the access control, unchanged."""
    execution_safety = ExecutionSafety.IDEMPOTENT
    """users.messages.list. A search neither marks anything read nor
    changes a label, so repeating it is safe."""
    description = (
        "Searches the connected Gmail account and returns matching messages' senders, "
        "subjects, dates, snippets, and ids -- read-only, cannot send, reply, or "
        "delete. Requires a Google account to have been connected from the Settings "
        "page; if none is connected it will say so. Arguments: query (str, required) -- "
        "uses Gmail's own search syntax, e.g. \"from:jane@acme.com invoice\" or "
        "\"subject:report after:2026/01/01\"; max_results (int, optional, default 10). "
        "Returns {messages: [{id, from, subject, date, snippet}, ...]}. To read a full "
        "message body, use gmail_read with an id from these results."
    )

    def run(self, query: str, user_id: str, max_results: int = _MAX_RESULTS) -> ToolResult:
        """user_id is injected by _execute_subtask (graph/nodes.py), not
        chosen by the LLM -- see that file's kwargs.setdefault for gmail_*/
        google_drive_* tool names. It's whoever owns the task, i.e. whose
        connected Gmail this call acts as."""
        if not query or not query.strip():
            return ToolResult(success=False, error="query must be a non-empty string")
        try:
            token = get_valid_access_token(user_id)
        except GoogleOAuthError as e:
            return ToolResult(success=False, error=f"Gmail not available: {e.detail}")

        headers = {"Authorization": f"Bearer {token}"}
        try:
            listing = httpx.get(
                f"{_GMAIL_BASE}/messages",
                headers=headers,
                params={"q": query, "maxResults": max(1, min(max_results, 50))},
                timeout=20.0,
            )
            listing.raise_for_status()
        except httpx.HTTPStatusError as e:
            return ToolResult(success=False, error=f"Gmail search failed: {e.response.status_code} {e.response.text[:200]}")
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"Gmail search failed: {e}")

        messages = []
        for stub in listing.json().get("messages", []):
            try:
                detail = httpx.get(
                    f"{_GMAIL_BASE}/messages/{stub['id']}",
                    headers=headers,
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                    timeout=15.0,
                )
                detail.raise_for_status()
            except httpx.HTTPError:
                continue  # one unreadable message shouldn't fail the whole search
            d = detail.json()
            hs = d.get("payload", {}).get("headers", [])
            messages.append(
                {
                    "id": d.get("id"),
                    "from": _header(hs, "From"),
                    "subject": _header(hs, "Subject"),
                    "date": _header(hs, "Date"),
                    # An email's sender is fully untrusted (arbitrary third
                    # party) -- wrap the fetched text, not the ids/headers
                    # this tool itself extracted from it.
                    "snippet": wrap_untrusted(d.get("snippet", ""), "gmail"),
                }
            )
        return ToolResult(success=True, output={"messages": messages})


class GmailReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(description="An id from a gmail_search result.")


class GmailReadTool(Tool):
    name = "gmail_read"
    args_model = GmailReadArgs
    action_type = ActionType.READ
    risk = Risk.MEDIUM
    execution_safety = ExecutionSafety.IDEMPOTENT
    """users.messages.get with format=full. Read-only at Gmail's end too:
    fetching a message does not mark it read."""
    description = (
        "Reads one full email message from the connected Gmail account by its id (get "
        "ids from gmail_search first). Read-only. Arguments: message_id (str, required). "
        "Returns {from, to, subject, date, body}."
    )

    def run(self, message_id: str, user_id: str) -> ToolResult:
        if not message_id or not message_id.strip():
            return ToolResult(success=False, error="message_id must be a non-empty string")
        try:
            token = get_valid_access_token(user_id)
        except GoogleOAuthError as e:
            return ToolResult(success=False, error=f"Gmail not available: {e.detail}")

        try:
            resp = httpx.get(
                f"{_GMAIL_BASE}/messages/{message_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"format": "full"},
                timeout=20.0,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"could not read message: {e}")

        d = resp.json()
        payload = d.get("payload", {})
        hs = payload.get("headers", [])
        return ToolResult(
            success=True,
            output={
                "from": _header(hs, "From"),
                "to": _header(hs, "To"),
                "subject": _header(hs, "Subject"),
                "date": _header(hs, "Date"),
                # The highest-risk field this tool returns -- a full email
                # body from an arbitrary sender, which is exactly the shape
                # of content a prompt-injection attempt would arrive in.
                "body": wrap_untrusted(_decode_body(payload), "gmail"),
            },
        )


class GmailCreateDraftArgs(BaseModel):
    """Only user-authored message fields are model-controlled. user_id is
    runtime-injected from Task.owner_id and is deliberately not a field."""

    model_config = ConfigDict(extra="forbid")

    to: str = Field(min_length=3, max_length=320, description="One recipient email address, e.g. jane@example.com.")
    subject: str = Field(min_length=1, max_length=998, description="Draft subject line.")
    body: str = Field(min_length=1, max_length=100_000, description="Plain text draft body.")

    @field_validator("to")
    @classmethod
    def valid_recipient(cls, value: str) -> str:
        candidate = value.strip()
        if "\r" in candidate or "\n" in candidate:
            raise ValueError("to must not contain line breaks")
        if "," in candidate:
            raise ValueError("exactly one recipient is supported")
        if ";" in candidate:
            raise ValueError("exactly one recipient is supported")
        parsed = getaddresses([candidate])
        non_empty = [(display, addr) for display, addr in parsed if addr]
        if len(parsed) != 1 or len(non_empty) != 1:
            raise ValueError("exactly one recipient is supported")
        _display, addr = non_empty[0]
        if not _EMAIL_RE.match(addr):
            raise ValueError("to must be a valid email address")
        return candidate

    @field_validator("subject")
    @classmethod
    def valid_subject(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("must not be blank")
        if "\r" in candidate or "\n" in candidate:
            raise ValueError("subject must not contain line breaks")
        return value

    @field_validator("body")
    @classmethod
    def not_blank(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("must not be blank")
        return value


class GmailCreateDraftTool(Tool):
    name = "gmail_create_draft"
    args_model = GmailCreateDraftArgs
    action_type = ActionType.EXTERNAL_WRITE
    risk = Risk.MEDIUM
    """Creates a draft in the user's Gmail account. It does not send mail, but
    it still mutates an external system and therefore goes through the Phase 2
    approval gate."""
    execution_safety = ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT
    """Gmail's drafts.create endpoint does not accept an idempotency key this
    system can rely on. A timeout or worker crash after the request is sent may
    have created the draft, so Phase 3 must not blindly retry it."""
    description = (
        "Creates a plain-text Gmail draft in the connected Gmail account. It does NOT send "
        "email. Requires human approval because it changes Gmail. Arguments: to (str, "
        "required, one email address), subject (str, required), body (str, required, plain "
        "text). Example: {\"to\":\"recruiter@example.com\",\"subject\":\"AI Engineer "
        "Follow-up\",\"body\":\"Thank you for speaking with me...\"}. Returns "
        "{draft_id, message_id, to, subject, verified:false}; verification checks the draft "
        "exists and matches recipient/subject after creation."
    )

    def run(self, to: str, subject: str, body: str, user_id: str) -> ToolResult:
        try:
            token = get_valid_access_token(
                user_id,
                required_scope=GMAIL_COMPOSE_SCOPE,
                purpose="Gmail draft creation (gmail.compose)",
            )
        except GoogleOAuthError as e:
            return ToolResult(success=False, error=f"Gmail not available: {e.detail}")

        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

        try:
            resp = httpx.post(
                f"{_GMAIL_BASE}/drafts",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"message": {"raw": raw}},
                timeout=20.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return ToolResult(success=False, error=f"Gmail draft creation failed: {e.response.status_code} {e.response.text[:200]}")
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"Gmail draft creation failed: {e}")

        payload = resp.json()
        message_payload = payload.get("message") or {}
        return ToolResult(
            success=True,
            output={
                "draft_id": payload.get("id"),
                "message_id": message_payload.get("id"),
                "to": to,
                "to_normalized": _normalised_email(to),
                "subject": subject,
                "verified": False,
            },
        )
