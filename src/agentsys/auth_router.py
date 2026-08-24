"""HTTP surface for sign-in: GET /v1/auth/google/login (redirect to Google),
GET /v1/auth/google/callback (Google sends the user back here), GET
/v1/auth/me (who's signed in), POST /v1/auth/logout.

Same redirect-not-JSON reasoning as the old integrations/router.py: OAuth
consent is a full-page browser navigation, never a fetch()/XHR, so login and
callback deal in redirects and the frontend points a normal <a href> /
window.location at /v1/auth/google/login, never an api-client call.

This single flow also connects Drive/Gmail (see google_oauth.py's
login_or_connect) -- Settings' "Reconnect Google" affordance points at the
same /v1/auth/google/login URL rather than a separate connect endpoint.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlmodel import select

from agentsys import audit
from agentsys.auth import (
    clear_session_cookie,
    create_session,
    get_current_user,
    hash_token,
    revoke_all_sessions,
    revoke_session,
    revoke_session_by_hash,
    set_session_cookie,
)
from agentsys.config import settings
from agentsys.db.models import User, UserSession
from agentsys.db.session import get_session
from agentsys.integrations import google_oauth
from agentsys.schemas import SessionOut, UserOut

router = APIRouter(prefix="/v1/auth", tags=["auth"])

_NEXT_COOKIE = "af_login_next"


def _login_redirect(**params: str) -> RedirectResponse:
    """Send the browser to the dashboard's login page, carrying a status
    flag it reads to show a success/error banner -- same convention the old
    integrations/router.py used for its settings-page redirect."""
    query = urlencode({**params})
    return RedirectResponse(url=f"{settings.web_app_url}/login?{query}")


@router.get("/google/login")
def google_login(next: str = "/") -> RedirectResponse:
    """next is where to land after a successful sign-in -- defaults to the
    dashboard root, but the Integrations page's "Reconnect" link passes
    next=/integrations so re-authorizing Drive/Gmail lands back where the
    user clicked it from, not somewhere unrelated. Carried via a short-lived
    cookie (not the OAuth `state` param, which google_oauth.py already owns
    for CSRF) so this file doesn't need to touch that module's internals."""
    try:
        auth_url = google_oauth.build_auth_url()
    except google_oauth.GoogleOAuthError as e:
        return _login_redirect(status="error", reason=e.detail)
    response = RedirectResponse(url=auth_url)
    # Only a relative path is ever honored on the way back (see
    # google_callback) -- this cookie is just a same-origin round trip
    # convenience, not something that should be able to redirect off-site.
    response.set_cookie(_NEXT_COOKIE, next, max_age=600, httponly=True, samesite="lax")
    return response


@router.get("/google/callback")
def google_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    next_path = request.cookies.get(_NEXT_COOKIE) or "/"
    if not next_path.startswith("/"):
        next_path = "/"  # only ever redirect same-origin, never to an absolute/external URL

    # Every exit from this handler that is NOT a signed-in user is recorded.
    # A failed sign-in has no actor to attribute it to, which is exactly what
    # makes it worth keeping: a burst of them from one address is the shape
    # of an attack, and it is invisible if only successes are logged.
    ctx = audit.request_context(request)

    if error:
        audit.record(
            audit.Action.LOGIN_FAILED,
            outcome="failure",
            **ctx,
            meta={"provider": "google", "stage": "consent", "error": error},
        )
        return _login_redirect(status="cancelled", reason=error)
    if not code or not state:
        audit.record(
            audit.Action.LOGIN_FAILED,
            outcome="failure",
            **ctx,
            meta={"provider": "google", "stage": "callback", "error": "missing code or state"},
        )
        return _login_redirect(status="error", reason="missing code or state from Google")

    try:
        user, _conn = google_oauth.login_or_connect(code, state)
    except google_oauth.GoogleOAuthError as e:
        # e.detail is a server-authored message, never the code/state values
        # themselves -- and audit.redact() would strip them regardless.
        audit.record(
            audit.Action.LOGIN_FAILED,
            outcome="failure",
            **ctx,
            meta={"provider": "google", "stage": "exchange", "error": e.detail},
        )
        return _login_redirect(status="error", reason=e.detail)

    # ip/user-agent are recorded on the session so a user can recognise
    # their own devices in the session list, and so an unfamiliar one is
    # visible after the fact.
    token = create_session(
        user.id,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    audit.record(
        audit.Action.LOGIN_SUCCESS,
        actor_id=user.id,
        actor_label=user.email,
        target_type="user",
        target_id=user.id,
        **ctx,
        meta={"provider": "google"},
    )
    response = RedirectResponse(url=f"{settings.web_app_url}{next_path}")
    set_session_cookie(response, token)
    response.delete_cookie(_NEXT_COOKIE)
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, email=user.email, name=user.name, picture_url=user.picture_url)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get(settings.session_cookie)
    if token:
        # revoke_session records the session.revoked event itself; this adds
        # the human-level fact on top. They are not the same thing -- a
        # session can be revoked without anyone logging out (expiry, "sign
        # out everywhere", a future admin action).
        revoke_session(token, reason="logout")
        audit.record(audit.Action.LOGOUT, **audit.request_context(request))
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(request: Request, user: User = Depends(get_current_user)) -> list[SessionOut]:
    """A user's own active sessions. This is the visibility half of session
    security -- without it, neither the user nor an investigator can tell
    how many live sessions exist or where they came from."""
    current_hash = hash_token(request.cookies.get(settings.session_cookie) or "")
    with get_session() as db:
        rows = db.exec(
            select(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at == None)  # noqa: E711
            .order_by(UserSession.last_seen_at.desc())
        ).all()
        return [
            SessionOut(
                id=r.id,
                created_at=r.created_at,
                last_seen_at=r.last_seen_at,
                expires_at=r.expires_at,
                ip=r.ip,
                user_agent=r.user_agent,
                current=r.token_hash == current_hash,
            )
            for r in rows
        ]


@router.delete("/sessions/{session_id}")
def revoke_one_session(session_id: str, user: User = Depends(get_current_user)) -> dict[str, bool]:
    with get_session() as db:
        row = db.get(UserSession, session_id)
        # 404 rather than 403 on someone else's session id, consistent with
        # every other ownership check in this codebase -- a probe must not
        # confirm the id exists.
        if not row or row.user_id != user.id:
            raise HTTPException(status_code=404, detail="session not found")
        token_hash = row.token_hash
    revoke_session_by_hash(token_hash)
    return {"ok": True}


@router.post("/sessions/revoke-all")
def revoke_all(request: Request, user: User = Depends(get_current_user)) -> dict[str, int]:
    """Sign out everywhere. Deliberately keeps the CALLING session alive:
    the common case is a user who suspects compromise, and logging them out
    of the browser they're currently using to secure the account is hostile.
    An explicit logout is one more click away."""
    current_hash = hash_token(request.cookies.get(settings.session_cookie) or "")
    revoked = revoke_all_sessions(user.id, except_token_hash=current_hash)
    return {"revoked": revoked}
