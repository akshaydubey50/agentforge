"""Google Photos, through the Picker API -- the only route that still exists.

WHAT CHANGED, AND WHY THIS FILE LOOKS LIKE THIS

Google removed `photoslibrary.readonly` on 2025-03-31; it now returns 403 for
every client. `mediaItems.search` survives but only ever returns media THIS
app created, so for an app that has never uploaded a photo it returns an empty
list forever. There is no third-party route to "search the user's library"
any more, and no amount of scope-widening brings one back.

The replacement is the Picker: the app opens a session, the USER chooses items
in Google's own UI, and the app receives only those. That is a different shape
of capability, not a smaller version of the old one -- so the tool built on it
promises "let me show you a picker", never "let me find your photos".

THE FLOW, AND WHERE IT MEETS THIS CODEBASE

    create_session()      -> {id, pickerUri, pollingConfig}
    (human opens pickerUri, selects, done)
    get_session(id)       -> mediaItemsSet: true
    list_picked(id)       -> the chosen items

The middle step is a human acting outside the process, which is exactly what
Escalation already models: pause, a person does something, a fresh graph
invocation resumes. So the tool escalates rather than polling in a loop -- a
worker blocked on a person is a worker not doing anything, and this system
already has a better answer for that.

TWO THINGS THE PICKER DOES THAT ARE EASY TO GET WRONG

  * `pollingConfig` is Google telling you how often it is willing to be asked.
    Ignoring it and polling tightly is how an integration gets throttled.
  * A `baseUrl` is NOT a downloadable URL on its own. Bytes need `=d`
    appended (or `=w{n}-h{n}` for a resize) AND the OAuth bearer token. A
    plain GET of baseUrl returns 403, which reads like a permissions bug and
    is not one.
"""

from __future__ import annotations

import logging

import httpx

from agentsys.integrations.google_oauth import GoogleOAuthError, get_valid_access_token

logger = logging.getLogger(__name__)

_PICKER_API = "https://photospicker.googleapis.com/v1"

# The one scope the Picker needs. Kept here rather than in DEFAULT_SCOPES so
# that adding Photos is a deliberate opt-in: widening the grant forces every
# existing user to re-consent (Google records granted scopes), and silently
# invalidating live connections is not something a config default should do.
PICKER_SCOPE = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"

DEFAULT_TIMEOUT = 20.0


class PhotosError(Exception):
    """Raised with a message written for the agent to relay, not a stack trace."""


def _headers(user_id: str) -> dict:
    try:
        return {"Authorization": f"Bearer {get_valid_access_token(user_id)}"}
    except GoogleOAuthError as e:
        raise PhotosError(f"Google account not available: {e.detail}") from e


def _request(method: str, path: str, user_id: str, **kwargs) -> dict:
    try:
        resp = httpx.request(
            method, f"{_PICKER_API}{path}", headers=_headers(user_id), timeout=DEFAULT_TIMEOUT, **kwargs
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status in (401, 403):
            # By far the most common failure, and the least self-explanatory:
            # the account was connected before Photos was ever asked for, so
            # the stored token simply does not carry this scope. Say the fix.
            raise PhotosError(
                "Google Photos access has not been granted. Disconnect and reconnect the "
                "Google account from Settings so the Photos permission is included -- an "
                "account connected before Photos was enabled does not carry it."
            ) from e
        raise PhotosError(f"Google Photos request failed ({status}): {e.response.text[:200]}") from e
    except httpx.HTTPError as e:
        raise PhotosError(f"Google Photos request failed: {e}") from e


def create_session(user_id: str) -> dict:
    """Open a picking session. Returns {id, picker_uri, poll_seconds}.

    `pickerUri` is what a HUMAN opens; it is not fetchable by the backend."""
    data = _request("POST", "/sessions", user_id, json={})
    polling = data.get("pollingConfig") or {}
    # "5s" -> 5. Google sends a duration string; a bare int is what callers want.
    raw = str(polling.get("pollInterval", "5s")).rstrip("s")
    try:
        poll_seconds = max(1, int(float(raw)))
    except ValueError:
        poll_seconds = 5
    return {
        "id": data.get("id"),
        "picker_uri": data.get("pickerUri"),
        "poll_seconds": poll_seconds,
        "expire_time": data.get("expireTime"),
    }


def get_session(session_id: str, user_id: str) -> dict:
    """Session state. `media_items_set` is the flag that says the human has
    finished choosing -- it is the only reliable "are they done" signal."""
    data = _request("GET", f"/sessions/{session_id}", user_id)
    return {
        "id": data.get("id"),
        "picker_uri": data.get("pickerUri"),
        "media_items_set": bool(data.get("mediaItemsSet")),
        "expire_time": data.get("expireTime"),
    }


def list_picked(session_id: str, user_id: str, limit: int = 50) -> list[dict]:
    """The items the user chose, flattened to what a caller actually uses.

    `download_url` already carries the `=d` suffix, because a bare baseUrl
    403s and that failure reads like a permissions problem rather than a
    missing parameter. It still needs the bearer token to fetch."""
    items: list[dict] = []
    page_token = None
    while len(items) < limit:
        params = {"sessionId": session_id, "pageSize": min(100, limit - len(items))}
        if page_token:
            params["pageToken"] = page_token
        data = _request("GET", "/mediaItems", user_id, params=params)
        for raw in data.get("mediaItems", []):
            media = raw.get("mediaFile") or {}
            base = media.get("baseUrl")
            items.append(
                {
                    "id": raw.get("id"),
                    "filename": media.get("filename"),
                    "mime_type": media.get("mimeType"),
                    "created_time": (raw.get("createTime") or ""),
                    "download_url": f"{base}=d" if base else None,
                }
            )
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return items[:limit]


def delete_session(session_id: str, user_id: str) -> None:
    """Best-effort cleanup. A leaked session is harmless and expires on its
    own, so this never raises into a task."""
    try:
        _request("DELETE", f"/sessions/{session_id}", user_id)
    except PhotosError as exc:
        logger.debug("could not delete picker session %s: %s", session_id, exc)
