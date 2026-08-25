"""Gmail tool -- read-only search + fetch over the connected account.

Same contract and same not-connected degradation as google_drive.py. The
Gmail REST API returns message bodies as base64url-encoded MIME parts, so
the only real work beyond the HTTP call is walking the payload tree to pull
out the readable text part.
"""

from __future__ import annotations

import base64

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agentsys.integrations.google_oauth import GoogleOAuthError, get_valid_access_token
from agentsys.policy import ActionType, Risk
from agentsys.sanitize import wrap_untrusted
from agentsys.tools.base import Tool, ToolResult

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_MAX_RESULTS = 10


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
