"""Response security headers.

Deliberately a duplicate of agentsys/security_headers.py, not an import --
the two packages don't import each other (see docs/MERGE.md), the same
reason rag/auth.py duplicates the cookie check. Both are browser-facing
HTTP surfaces on the same deployment, so hardening only one of them would
leave the softer half reachable and the hardening largely notional. Keep
the two files in step.

Every response leaving this API carries the same baseline set. Headers are
the cheapest security control there is -- they cost one dict lookup per
response and they instruct the browser to refuse whole categories of attack
that the server otherwise cannot see, let alone stop.

Why a pure-ASGI middleware rather than @app.middleware("http")
--------------------------------------------------------------
The decorator form wraps every request in Starlette's BaseHTTPMiddleware,
which runs the downstream app inside an anyio task group and buffers the
response through a memory stream. That breaks StreamingResponse (the body
is consumed before it reaches the client) and drops BackgroundTasks in some
versions, and it does all of that to support middleware that needs to READ
or REWRITE the body. This middleware needs neither -- it only adds headers
to the response-start message -- so wrapping send() directly costs a
closure per request instead of a task group, and cannot break streaming.

setdefault, not assignment
--------------------------
A route that deliberately sets its own value for one of these wins. That is
not politeness, it's required: a route serving user-uploaded files needs a
far stricter CSP than the API-wide default, and a blanket overwrite here
would silently downgrade it back.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from rag.config import settings

# Directives that matter for a JSON API, and no more.
#
# Deliberately NOT `default-src 'none'`: both this app and rag expose
# Swagger UI at /docs, which loads its script and stylesheet from a CDN, and
# a blanket default-src would break it. The three directives below lose
# nothing by their absence -- a JSON response is never rendered as a
# document, so script-src/style-src have nothing to govern, while these
# three govern things that apply regardless of content type:
#   frame-ancestors  -- nobody may frame this origin (clickjacking, and the
#                       modern replacement for X-Frame-Options)
#   base-uri         -- an injected <base> cannot repoint relative URLs
#   form-action      -- a form on this origin cannot post somewhere else
_CSP = "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

# Everything the API has no use for. An empty allowlist -- feature=() --
# denies the capability to this origin and to everything it embeds. Listing
# them explicitly rather than relying on browser defaults matters because
# the defaults differ per browser and change over time.
_PERMISSIONS_POLICY = (
    "accelerometer=(), ambient-light-sensor=(), autoplay=(), battery=(), "
    "camera=(), display-capture=(), document-domain=(), encrypted-media=(), "
    "fullscreen=(), geolocation=(), gyroscope=(), magnetometer=(), "
    "microphone=(), midi=(), payment=(), picture-in-picture=(), "
    "publickey-credentials-get=(), screen-wake-lock=(), serial=(), "
    "usb=(), xr-spatial-tracking=()"
)


def build_headers() -> dict[str, str]:
    """The header set, resolved once at startup from config.

    Computed eagerly rather than per response: none of these depend on the
    request, so recomputing them 10,000 times a second would be pure waste.
    """
    headers = {
        # Stops the browser second-guessing a declared Content-Type. Without
        # it, a JSON response containing attacker-controlled text can be
        # sniffed as HTML and executed on this origin.
        "X-Content-Type-Options": "nosniff",
        # Full URLs here contain task and escalation ids. strict-origin-when-
        # cross-origin sends the path only to this same origin, and bare
        # origin to anyone else -- so an outbound link (the OAuth redirect to
        # Google, most obviously) never leaks which resource was being viewed.
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": _PERMISSIONS_POLICY,
        "Content-Security-Policy": _CSP,
        # Superseded by frame-ancestors above, kept for browsers that never
        # implemented it. Costs 22 bytes; the alternative is a clickjacking
        # hole on exactly the old clients least able to defend themselves.
        "X-Frame-Options": "DENY",
        # This API is not a browsable site, and cross-origin reads of its
        # JSON have no legitimate use -- the dashboard reads it via CORS,
        # which this does not affect.
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }

    if settings.session_cookie_secure:
        # Gated on the same flag as the Secure cookie attribute, because it
        # answers the same question: is this deployment actually served over
        # HTTPS? Browsers ignore HSTS on a plain-HTTP response anyway, but
        # sending it from a local dev server that someone later fronts with
        # a self-signed cert would pin localhost to HTTPS in that browser
        # for a year, which is a genuinely painful thing to undo.
        directives = [f"max-age={settings.hsts_max_age_seconds}", "includeSubDomains"]
        if settings.hsts_preload:
            # Off by default on purpose. Preload is a one-way door: removal
            # from the browser-vendor list takes months to propagate, and it
            # commits every current and future subdomain to HTTPS. That is a
            # deployment decision, not a default.
            directives.append("preload")
        headers["Strict-Transport-Security"] = "; ".join(directives)

    return headers


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.headers = build_headers()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Websocket and lifespan messages have no response headers to
            # decorate; passing them straight through avoids inspecting
            # message types that will never match.
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                for key, value in self.headers.items():
                    response_headers.setdefault(key, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
