"""google_photos_pick -- show the user a picker, then work with what they chose.

The honest shape of this tool, stated in its description because the model
will otherwise assume otherwise: IT CANNOT SEARCH. Google removed
library-wide access in March 2025, so no third-party app can look through
someone's photos. What remains is asking them to choose.

HOW IT SPANS A HUMAN

The picker needs a person to open a URL, select items, and finish -- which can
take a minute or an hour. Polling in a loop would hold a Celery worker hostage
for the duration, so instead this returns `_awaiting_human` and the graph
turns that into an Escalation (see nodes.py). The task pauses, the person
picks, they approve, and a FRESH graph invocation re-runs this same tool.

That is why the tool is IDEMPOTENT ON THE SESSION. The session id lives in the
task's short-term memory, so the second call doesn't open a second picker --
it looks at the first one and finds the items waiting. Without that, every
resume would hand the user a new empty picker, which is the same
can't-follow-the-pointer shape that broke the Drive read path.
"""

from __future__ import annotations

from agentsys.integrations import google_photos as api
from agentsys.memory import short_term
from agentsys.tools.base import Tool, ToolResult

_SESSION_FIELD = "photos_picker_session"


class GooglePhotosPickTool(Tool):
    name = "google_photos_pick"
    description = (
        "Asks the user to choose photos or videos from their Google Photos library, then "
        "returns what they chose. Arguments: reason (str, required) -- a short, specific "
        "sentence shown to the user explaining WHAT to pick and why, e.g. 'Pick the photos "
        "from the Goa trip you want in the carousel'; max_items (int, optional, default 20). "
        "Returns {items: [{id, filename, mime_type, created_time, download_url}, ...]}. "
        "IMPORTANT -- THIS TOOL CANNOT SEARCH. Google removed library-wide access in March "
        "2025, so no app can look through someone's photos, filter by date, place or "
        "content, or fetch a photo the user has not personally selected. If the request is "
        "'find photos of X' or 'get every photo from Y', you cannot do it: say so plainly, "
        "and offer this picker as the alternative. The first call pauses the task while the "
        "user picks; it resumes by itself afterwards, so call it once and do not retry."
    )

    def run(self, reason: str, user_id: str, task_id: str, max_items: int = 20) -> ToolResult:
        """user_id and task_id are injected by _execute_subtask, not chosen by
        the model -- same convention as the Drive and Gmail tools."""
        if not reason or not reason.strip():
            return ToolResult(success=False, error="reason must be a non-empty string")

        try:
            session_id = short_term.get_value(task_id, _SESSION_FIELD)

            if session_id:
                # Resuming: the person has been to the picker. Ask whether they
                # actually finished rather than assuming approval means picked.
                state = api.get_session(session_id, user_id)
                if not state["media_items_set"]:
                    return ToolResult(
                        success=False,
                        error=(
                            "No photos were selected yet. Open the picker link and choose the "
                            f"items, then approve again: {state['picker_uri']}"
                        ),
                    )
                items = api.list_picked(session_id, user_id, limit=max_items)
                api.delete_session(session_id, user_id)
                short_term.set_value(task_id, _SESSION_FIELD, None)
                if not items:
                    return ToolResult(
                        success=False,
                        error="The picker session finished but no items came back; nothing was selected.",
                    )
                return ToolResult(success=True, output={"items": items, "count": len(items)})

            # First call: open a picker and hand the task to the human.
            session = api.create_session(user_id)
            short_term.set_value(task_id, _SESSION_FIELD, session["id"])
            return ToolResult(
                success=True,
                output={
                    "_awaiting_human": {
                        "kind": "photo_pick",
                        "reason": reason.strip(),
                        "context": {
                            "picker_uri": session["picker_uri"],
                            "session_id": session["id"],
                            "expires": session.get("expire_time"),
                        },
                    },
                    "picker_uri": session["picker_uri"],
                },
            )
        except api.PhotosError as exc:
            return ToolResult(success=False, error=str(exc))
