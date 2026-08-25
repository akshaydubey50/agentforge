"""Google Drive tool -- read-only search + fetch over the connected account.

Same Tool contract as every first-party tool, so it flows through the
existing TraceSpan/ToolCall/reviewer/escalation machinery unchanged (same
principle as mcp_tool.py). The only new dependency is a live Google
connection, which it gets via integrations.google_oauth.get_valid_access_token()
-- and when there ISN'T one, it returns a clean, actionable failed
ToolResult ("no account connected, connect one in Settings") rather than
raising, so the reviewer sees a normal rejectable outcome and a human can
act on it, exactly like a rate-limited web_search.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agentsys.integrations.google_oauth import GoogleOAuthError, get_valid_access_token
from agentsys.sanitize import wrap_untrusted
from agentsys.tools.base import Tool, ToolResult

_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_MAX_RESULTS = 10


class GoogleDriveSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(description="Plain words to look for in file names and contents.")
    max_results: int = Field(default=_MAX_RESULTS, ge=1, le=100)


class GoogleDriveTool(Tool):
    name = "google_drive_search"
    args_model = GoogleDriveSearchArgs
    description = (
        "Searches the connected Google Drive account for files by name or content and "
        "returns their names, types, and IDs -- read-only, cannot modify or delete "
        "anything. Requires a Google account to have been connected from the Settings "
        "page; if none is connected it will say so. Arguments: query (str, required) -- "
        "plain words to look for in file names and contents, e.g. \"Q2 revenue report\"; "
        "max_results (int, optional, default 10). Returns {files: [{name, id, "
        "mime_type, modified_time, link}, ...]}. To read a file's TEXT, use "
        "google_drive_read with an id from these results -- but only for text-like "
        "files. For an image, video, archive or any other binary, do NOT call "
        "google_drive_read: it cannot read them. Give the user the `link` instead, "
        "which is exactly what a request like \"send me those photos\" is asking for."
    )

    def run(self, query: str, user_id: str, max_results: int = _MAX_RESULTS) -> ToolResult:
        """user_id is injected by _execute_subtask (graph/nodes.py), not
        chosen by the LLM -- see that file's kwargs.setdefault for gmail_*/
        google_drive_* tool names. It's whoever owns the task, i.e. whose
        connected Drive this call acts as."""
        if not query or not query.strip():
            return ToolResult(success=False, error="query must be a non-empty string")
        try:
            token = get_valid_access_token(user_id)
        except GoogleOAuthError as e:
            return ToolResult(success=False, error=f"Google Drive not available: {e.detail}")

        # `fullText contains` matches name + body; the value must be quoted and
        # any embedded quotes escaped, or Drive rejects the query string.
        safe = query.replace("\\", "\\\\").replace("'", "\\'")
        try:
            resp = httpx.get(
                _DRIVE_FILES_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": f"fullText contains '{safe}' and trashed = false",
                    "pageSize": max(1, min(max_results, 100)),
                    # webViewLink matters for files this agent cannot read:
                    # an image or an archive can still be HANDED BACK as a
                    # link, which is usually what "get me those photos"
                    # actually wants. Without it the only possible answer to
                    # a picture is "I can't read that".
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
                },
                timeout=20.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return ToolResult(success=False, error=f"Drive search failed: {e.response.status_code} {e.response.text[:200]}")
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"Drive search failed: {e}")

        files = [
            {
                "name": f.get("name"),
                "id": f.get("id"),
                "mime_type": f.get("mimeType"),
                "modified_time": f.get("modifiedTime"),
                "link": f.get("webViewLink"),
            }
            for f in resp.json().get("files", [])
        ]
        return ToolResult(success=True, output={"files": files})


class GoogleDriveReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(description="An id from a google_drive_search result.")


class GoogleDriveReadTool(Tool):
    name = "google_drive_read"
    args_model = GoogleDriveReadArgs
    description = (
        "Reads the text contents of one file in the connected Google Drive account, "
        "by its file id (get ids from google_drive_search first). Read-only. Google "
        "Docs/Sheets/Slides are exported as plain text/CSV; other text files are "
        "returned as-is; binary files (images, PDFs, etc.) can't be read as text and "
        "will say so. Arguments: file_id (str, required). Returns {name, text}."
    )

    def run(self, file_id: str, user_id: str) -> ToolResult:
        if not file_id or not file_id.strip():
            return ToolResult(success=False, error="file_id must be a non-empty string")
        try:
            token = get_valid_access_token(user_id)
        except GoogleOAuthError as e:
            return ToolResult(success=False, error=f"Google Drive not available: {e.detail}")

        headers = {"Authorization": f"Bearer {token}"}
        try:
            meta = httpx.get(
                f"{_DRIVE_FILES_URL}/{file_id}",
                headers=headers,
                params={"fields": "name,mimeType"},
                timeout=15.0,
            )
            meta.raise_for_status()
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"could not read file metadata: {e}")

        info = meta.json()
        name, mime = info.get("name"), info.get("mimeType", "")

        # Google-native docs must be EXPORTED to a readable format; ordinary
        # uploaded files are fetched with alt=media. Picking the wrong path
        # for the mime type is the usual cause of an opaque 403 here.
        export_as = {
            "application/vnd.google-apps.document": "text/plain",
            "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "text/plain",
        }.get(mime)

        try:
            if export_as:
                resp = httpx.get(
                    f"{_DRIVE_FILES_URL}/{file_id}/export",
                    headers=headers,
                    params={"mimeType": export_as},
                    timeout=30.0,
                )
            elif mime.startswith("text/") or mime in ("application/json", "application/xml"):
                resp = httpx.get(
                    f"{_DRIVE_FILES_URL}/{file_id}",
                    headers=headers,
                    params={"alt": "media"},
                    timeout=30.0,
                )
            else:
                # An error that names the way forward, not just the wall. The
                # old message ("isn't readable as text") was true and left the
                # agent with nowhere to go, so it retried with another image
                # and burned the progress budget. Observed on a "fetch me 3
                # photos" request.
                return ToolResult(
                    success=False,
                    error=(
                        f"'{name}' is {mime} -- a binary file, so its contents cannot be read "
                        f"as text and re-trying with another file of this type will fail the "
                        f"same way. Use the `link` from google_drive_search to give the user "
                        f"the file directly instead of reading it."
                    ),
                )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"could not read file contents: {e}")

        return ToolResult(success=True, output={"name": name, "text": wrap_untrusted(resp.text, "google_drive")})
