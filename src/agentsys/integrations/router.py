"""HTTP surface for the connected Google account: status + disconnect.
Connecting/reconnecting itself happens via GET /v1/auth/google/login
(auth_router.py) -- signing in and connecting Drive/Gmail are the same
OAuth grant (see google_oauth.py's login_or_connect), so there's no
separate /connect endpoint here anymore. Both routes below require a
signed-in user and act only on that user's own GoogleConnection row.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from agentsys.auth import get_current_user
from agentsys.db.models import User
from agentsys.integrations import google_oauth
from agentsys.schemas import GoogleConnectionOut

router = APIRouter(prefix="/v1/integrations/google", tags=["integrations"])


@router.get("/status", response_model=GoogleConnectionOut)
def google_status(user: User = Depends(get_current_user)) -> GoogleConnectionOut:
    conn = google_oauth.get_connection(user.id)
    return GoogleConnectionOut(
        connected=conn is not None,
        configured=google_oauth.is_configured(),
        google_email=conn.google_email if conn else None,
        scopes=conn.scopes.split() if conn and conn.scopes else [],
        connected_at=conn.created_at if conn else None,
    )


@router.post("/disconnect", response_model=GoogleConnectionOut)
def google_disconnect(user: User = Depends(get_current_user)) -> GoogleConnectionOut:
    google_oauth.disconnect(user.id)
    return GoogleConnectionOut(connected=False, configured=google_oauth.is_configured())
