"""Security response headers and the __Host- session cookie.

Headers are the kind of control that silently stops being applied -- a
middleware registered in the wrong order, a route that builds its own
Response, a config flag that quietly defaults the wrong way -- and nothing
about the app looks broken when it happens. These assert the headers are
actually on the wire, on the paths that matter: authenticated, anonymous,
error, and CORS preflight.

Fully deterministic -- no LLM calls, no network.
"""

from __future__ import annotations

import importlib
import uuid

import pytest
from fastapi.testclient import TestClient

from agentsys import auth, security_headers
from agentsys.config import settings
from agentsys.db.models import User
from agentsys.db.session import get_session, init_db
from agentsys.main import app

client = TestClient(app)


@pytest.fixture
def user() -> User:
    init_db()
    sub = f"test-headers-{uuid.uuid4()}"
    with get_session() as db:
        u = User(google_sub=sub, email=f"{sub}@example.com", name="Headers Test")
        db.add(u)
        db.commit()
        db.refresh(u)
        db.expunge(u)
        return u


# --------------------------------------------------------------------------
# The headers reach real responses
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header,expected",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("X-Frame-Options", "DENY"),
        ("Cross-Origin-Opener-Policy", "same-origin"),
        ("Cross-Origin-Resource-Policy", "same-origin"),
    ],
)
def test_baseline_headers_are_present(header: str, expected: str):
    assert client.get("/health").headers[header] == expected


def test_csp_forbids_framing_and_base_and_form_hijacking():
    csp = client.get("/health").headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "form-action 'none'" in csp


def test_permissions_policy_denies_the_capabilities_an_api_never_needs():
    policy = client.get("/health").headers["Permissions-Policy"]
    for feature in ("camera", "microphone", "geolocation", "payment", "usb"):
        assert f"{feature}=()" in policy


def test_headers_are_present_on_an_unauthenticated_401():
    """The error paths matter as much as the happy one -- a 401 is still a
    response a browser renders and acts on."""
    response = client.get("/v1/tasks")
    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_headers_are_present_on_a_404():
    response = client.get("/no-such-route-exists")
    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_headers_are_present_on_an_authenticated_response(user: User):
    token = auth.create_session(user.id)
    c = TestClient(app)
    c.cookies.set(settings.session_cookie, token)
    response = c.get("/v1/auth/me")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_headers_are_present_on_a_cors_preflight():
    """The reason this middleware is registered AFTER CORSMiddleware: CORS
    answers an OPTIONS preflight itself and never calls downstream, so a
    middleware registered inside it would never run for one."""
    response = client.options(
        "/v1/tasks",
        headers={
            "Origin": settings.cors_allowed_origins_list[0],
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_cors_still_works_alongside_the_headers():
    """A security header set that breaks the dashboard's own fetches would
    just get reverted, so assert the two coexist."""
    origin = settings.cors_allowed_origins_list[0]
    response = client.get("/health", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"


# --------------------------------------------------------------------------
# HSTS is gated on actually being served over HTTPS
# --------------------------------------------------------------------------


def test_hsts_is_absent_when_not_served_over_https():
    """Pinning localhost to HTTPS for a year in a developer's browser is a
    genuinely painful thing to undo, so this stays off unless the deployment
    says it is secure."""
    assert settings.session_cookie_secure is False, "test assumes the dev default"
    assert "Strict-Transport-Security" not in client.get("/health").headers


def test_hsts_is_set_with_a_long_max_age_when_secure(monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    headers = security_headers.build_headers()
    hsts = headers["Strict-Transport-Security"]
    assert f"max-age={settings.hsts_max_age_seconds}" in hsts
    assert "includeSubDomains" in hsts
    assert settings.hsts_max_age_seconds >= 31_536_000


def test_preload_is_off_by_default_because_it_is_irreversible(monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    assert settings.hsts_preload is False
    assert "preload" not in security_headers.build_headers()["Strict-Transport-Security"]


def test_preload_is_emitted_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    monkeypatch.setattr(settings, "hsts_preload", True)
    assert "preload" in security_headers.build_headers()["Strict-Transport-Security"]


# --------------------------------------------------------------------------
# A route may state a stricter policy and keep it
# --------------------------------------------------------------------------


def test_a_route_can_override_the_default_policy():
    """setdefault, not assignment. rag's raw-document route depends on this:
    it serves user-uploaded bytes inline and needs a far stricter CSP than
    the API-wide default, which a blanket overwrite would silently undo."""
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient as RawClient

    from starlette.applications import Starlette
    from starlette.routing import Route

    async def strict(_request):
        return JSONResponse({}, headers={"Content-Security-Policy": "default-src 'none'; sandbox"})

    sub = Starlette(routes=[Route("/strict", strict)])
    sub.add_middleware(security_headers.SecurityHeadersMiddleware)

    response = RawClient(sub).get("/strict")
    assert response.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
    # The headers it did NOT set still get the default.
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# --------------------------------------------------------------------------
# __Host- cookie prefix
# --------------------------------------------------------------------------


def test_cookie_name_is_unprefixed_in_plain_http_dev():
    """A __Host- cookie sent over http:// is silently dropped by the browser,
    which would break local dev entirely rather than visibly."""
    assert settings.session_cookie_secure is False, "test assumes the dev default"
    assert settings.session_cookie == settings.session_cookie_name
    assert not settings.session_cookie.startswith("__Host-")


def test_cookie_gains_the_host_prefix_when_secure(monkeypatch):
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    assert settings.session_cookie == f"__Host-{settings.session_cookie_name}"


def test_the_cookie_satisfies_every_host_prefix_requirement(user: User, monkeypatch):
    """__Host- is a contract the browser enforces: Secure, Path=/, and no
    Domain. Violate any one and the cookie is rejected outright -- which
    presents as "login silently does nothing", not as an error."""
    monkeypatch.setattr(settings, "session_cookie_secure", True)

    from fastapi import Response

    response = Response()
    auth.set_session_cookie(response, auth.create_session(user.id))
    header = response.headers["set-cookie"]

    assert header.startswith("__Host-")
    assert "Secure" in header
    assert "Path=/" in header
    assert "Domain=" not in header
    assert "HttpOnly" in header


def test_rag_mirrors_the_same_cookie_name():
    """The two packages can't import each other, so the names are mirrored
    by hand -- and a mismatch presents as every rag endpoint 401ing for a
    perfectly valid session, which is exactly the bug this pairing had."""
    from rag.config import settings as rag_settings

    assert rag_settings.session_cookie == settings.session_cookie


def test_rag_and_agentsys_agree_on_the_session_cache_key():
    """rag looks sessions up in the cache agentsys writes. If the two derive
    the key differently, rag rejects every valid session -- which is the bug
    that shipped when sessions moved to hashed tokens and this file kept
    looking up the raw one."""
    from rag import auth as rag_auth

    token = "a-representative-session-token"
    assert rag_auth._hash_token(token) == auth.hash_token(token)


def test_rag_accepts_a_session_agentsys_actually_created(user: User):
    """End-to-end across the package seam, through real Redis: the exact
    failure mode a unit test on either side alone would miss."""
    from fastapi import Request

    from rag.auth import require_session

    token = auth.create_session(user.id)
    scope = {
        "type": "http",
        "headers": [(b"cookie", f"{settings.session_cookie}={token}".encode())],
    }
    assert require_session(Request(scope)) == user.id


def test_rag_rejects_a_revoked_session(user: User):
    from fastapi import HTTPException, Request

    from rag.auth import require_session

    token = auth.create_session(user.id)
    auth.revoke_session(token)

    scope = {
        "type": "http",
        "headers": [(b"cookie", f"{settings.session_cookie}={token}".encode())],
    }
    with pytest.raises(HTTPException) as excinfo:
        require_session(Request(scope))
    assert excinfo.value.status_code == 401
