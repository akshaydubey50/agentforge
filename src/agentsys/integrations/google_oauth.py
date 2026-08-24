"""Google OAuth 2.0 (Authorization Code flow) -- doubles as both 'Sign in
with Google' (identity) and 'Connect Google Account' (Drive/Gmail access
for the tools), because they're the same consent: a user who signs in has
necessarily just granted the openid/email/profile + drive.readonly/
gmail.readonly scopes in DEFAULT_SCOPES below, so there is no reason to make
them click through a second, separate authorization.

Why this exists / the shape it takes:
  The dashboard's Sign in with Google button must send the user's own
  browser to Google's consent screen (a full-page redirect, NOT a fetch --
  Google will not render its consent UI inside an XHR/CORS request), Google
  redirects back to this API's /callback with a one-time code, and this
  module trades that code for tokens, upserts the User + their
  GoogleConnection, and mints a session (see agentsys/auth.py). From then on
  the Drive/Gmail tools call get_valid_access_token(user_id), which silently
  refreshes an expired access token using the stored refresh token -- so the
  user signs in once and the agent keeps working without re-consenting.

  No Google SDK dependency is added: the three HTTP calls involved (auth URL
  is just a query string, token exchange and refresh are one POST each) are
  simpler and lighter to do with httpx, which is already a dependency, than
  to pull in google-auth/google-api-python-client. The Drive/Gmail tools
  likewise call the REST APIs over httpx.

CSRF: the OAuth `state` parameter is a random token stored in Redis with a
short TTL and verified on callback, so a forged callback with an attacker's
code can't be planted against this deployment.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlmodel import select

from agentsys import audit
from agentsys.config import settings
from agentsys.db.models import GoogleConnection, User
from agentsys.db.session import get_session
from agentsys.memory.short_term import get_redis

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"

_STATE_TTL_S = 600  # a consent screen the user leaves open longer than this is stale
_REFRESH_SKEW_S = 120  # refresh a token this many seconds before it actually expires

# openid/email/profile establish who's signing in; drive.readonly/
# gmail.readonly are what the tools use. Read-only to start: the agent can
# look things up in Drive/Gmail but cannot send mail or modify/delete files.
# Widening this is a deliberate, reviewable change (and forces a re-consent,
# since Google records granted scopes).
DEFAULT_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",  # name + picture, for the signed-in-as UI
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]


class GoogleOAuthError(Exception):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def _require_configured() -> None:
    if not is_configured():
        raise GoogleOAuthError(
            "Google integration is not configured on the server (GOOGLE_CLIENT_ID / "
            "GOOGLE_CLIENT_SECRET are unset).",
            status_code=503,
        )


def build_auth_url() -> str:
    """The URL the browser is redirected to. Stores a fresh CSRF state in
    Redis first. access_type=offline + prompt=consent together are what make
    Google actually return a refresh_token (without them, a returning user
    who already consented gets only a short-lived access token and the
    connection can't survive its ~1h expiry)."""
    _require_configured()
    state = secrets.token_urlsafe(32)
    get_redis().setex(f"google_oauth_state:{state}", _STATE_TTL_S, "1")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(DEFAULT_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{_AUTH_ENDPOINT}?{urlencode(params)}"


def _consume_state(state: str) -> None:
    key = f"google_oauth_state:{state}"
    client = get_redis()
    # GETDEL-style: a state is single-use, so delete it as we validate it --
    # a replayed callback with the same state then fails.
    exists = client.delete(key)
    if not exists:
        raise GoogleOAuthError("Invalid or expired OAuth state -- please start the connect flow again.")


def login_or_connect(code: str, state: str) -> tuple[User, GoogleConnection]:
    """Callback handler core: verify CSRF state, trade the code for tokens,
    fetch the account's identity (email/sub/name/picture), upsert the User
    by google_sub, and upsert that user's GoogleConnection. One grant does
    both jobs -- see the module docstring for why signing in and connecting
    Drive/Gmail aren't split into two separate flows."""
    _require_configured()
    _consume_state(state)

    try:
        resp = httpx.post(
            _TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise GoogleOAuthError(f"Google token exchange failed: {e.response.status_code} {e.response.text[:200]}")
    except httpx.HTTPError as e:
        raise GoogleOAuthError(f"Google token exchange failed: {e}")

    payload = resp.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise GoogleOAuthError("Google did not return an access token.")

    userinfo = _fetch_userinfo(access_token)
    if not userinfo or not userinfo.get("id") or not userinfo.get("email"):
        # Unlike email in the old singleton flow, identity is NOT optional
        # here -- with no reliable google_sub/email there is no user to log
        # in as, so this must fail the whole sign-in rather than proceed
        # with a connection nothing owns.
        raise GoogleOAuthError("Could not fetch account identity from Google.")

    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)))
    now = datetime.now(timezone.utc)

    with get_session() as session:
        user = session.exec(select(User).where(User.google_sub == userinfo["id"])).first()
        is_new_user = user is None
        if user is None:
            user = User(google_sub=userinfo["id"], email=userinfo["email"])
        user.email = userinfo["email"]
        user.name = userinfo.get("name") or user.name
        user.picture_url = userinfo.get("picture") or user.picture_url
        user.last_login_at = now
        session.add(user)
        session.commit()
        session.refresh(user)

        conn = session.exec(select(GoogleConnection).where(GoogleConnection.user_id == user.id)).first()
        if conn is None:
            conn = GoogleConnection(user_id=user.id, access_token=access_token)
        conn.access_token = access_token
        # Google omits refresh_token on re-consent in some cases -- never
        # overwrite a good stored one with None, or the connection silently
        # loses its ability to refresh.
        if payload.get("refresh_token"):
            conn.refresh_token = payload["refresh_token"]
        conn.scopes = payload.get("scope", " ".join(DEFAULT_SCOPES))
        conn.token_expiry = expiry
        conn.google_email = userinfo["email"]
        conn.updated_at = now
        session.add(conn)
        session.commit()
        session.refresh(conn)
        session.refresh(user)
        session.expunge(user)
        session.expunge(conn)

    # Recorded after the transaction commits, so the log never claims an
    # account exists that was actually rolled back. Note what is NOT in
    # meta: no access_token, no refresh_token, no authorization code. The
    # granted scopes are, because "what access was granted, and when" is the
    # question this row exists to answer.
    if is_new_user:
        audit.record(
            audit.Action.USER_CREATED,
            actor_id=user.id,
            actor_label=user.email,
            target_type="user",
            target_id=user.id,
            meta={"provider": "google"},
        )
    audit.record(
        audit.Action.GOOGLE_CONNECTED,
        actor_id=user.id,
        actor_label=user.email,
        target_type="google_connection",
        target_id=conn.id,
        meta={
            "google_email": conn.google_email,
            "scopes": conn.scopes.split(),
            "has_refresh_token": bool(conn.refresh_token),
        },
    )
    return user, conn


def _fetch_userinfo(access_token: str) -> dict | None:
    try:
        resp = httpx.get(
            _USERINFO_ENDPOINT,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError:
        return None


def get_connection(user_id: str) -> GoogleConnection | None:
    with get_session() as session:
        return session.exec(select(GoogleConnection).where(GoogleConnection.user_id == user_id)).first()


def disconnect(user_id: str) -> bool:
    with get_session() as session:
        conn = session.exec(select(GoogleConnection).where(GoogleConnection.user_id == user_id)).first()
        if conn is None:
            return False
        conn_id, google_email = conn.id, conn.google_email
        session.delete(conn)
        session.commit()
    audit.record(
        audit.Action.GOOGLE_DISCONNECTED,
        actor_id=user_id,
        target_type="google_connection",
        target_id=conn_id,
        meta={"google_email": google_email},
    )
    return True


def _refresh(conn: GoogleConnection) -> GoogleConnection:
    if not conn.refresh_token:
        raise GoogleOAuthError(
            "The Google connection has no refresh token -- please disconnect and connect again.",
            status_code=401,
        )
    try:
        resp = httpx.post(
            _TOKEN_ENDPOINT,
            data={
                "refresh_token": conn.refresh_token,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type": "refresh_token",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise GoogleOAuthError(f"Google token refresh failed: {e}", status_code=401)

    payload = resp.json()
    with get_session() as session:
        fresh = session.get(GoogleConnection, conn.id)
        fresh.access_token = payload["access_token"]
        fresh.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 3600)))
        fresh.updated_at = datetime.now(timezone.utc)
        session.add(fresh)
        session.commit()
        session.refresh(fresh)
        return fresh


def get_valid_access_token(user_id: str) -> str:
    """The one function the Drive/Gmail tools call. Returns a usable access
    token for the given user, transparently refreshing an expired/
    near-expired one. Raises GoogleOAuthError (which the tools convert into
    a clean 'not connected / reconnect' ToolResult) when that user has no
    connection to use. user_id comes from the task's owner (see
    graph/state.py's owner_id, threaded in from Task.owner_id at
    invocation) -- Gmail/Drive tools act as whoever owns the task, not a
    single shared account."""
    _require_configured()
    conn = get_connection(user_id)
    if conn is None:
        raise GoogleOAuthError("No Google account is connected.", status_code=401)

    expiry = conn.token_expiry
    if expiry is not None:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if time.time() < expiry.timestamp() - _REFRESH_SKEW_S:
            return conn.access_token

    return _refresh(conn).access_token
