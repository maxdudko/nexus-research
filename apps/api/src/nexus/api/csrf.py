"""Reject cookie-authenticated unsafe requests that fail the origin or CSRF check.

Requests without the session cookie are unchanged, so anonymous research calls
keep working until ownership is enforced.
"""

import hmac

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from nexus.core.config import Settings
from nexus.identity.cookies import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE

_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOGIN_PATH = "/api/v1/auth/login"
_AUTH_PREFIX = "/api/v1/auth"


class SessionCsrfMiddleware:
    """Header-only guard. It does not read or buffer the request body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        if _rejected(request):
            response = JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "forbidden",
                        "message": "The request was rejected.",
                    }
                },
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, _with_auth_cache(scope, send))


def origin_allowed(request: Request, settings: Settings) -> bool:
    """Match the browser Origin exactly. Referer is not a substitute."""
    origin = request.headers.get("origin")
    return origin is not None and origin in settings.trusted_origins


def _rejected(request: Request) -> bool:
    if request.method not in _UNSAFE:
        return False
    if SESSION_COOKIE not in request.cookies:
        return False
    if request.url.path == _LOGIN_PATH:
        return False
    settings: Settings = request.app.state.settings
    if not origin_allowed(request, settings):
        return True
    return not _csrf_matches(request)


def _csrf_matches(request: Request) -> bool:
    header = request.headers.get(CSRF_HEADER) or ""
    cookie = request.cookies.get(CSRF_COOKIE) or ""
    if not header or not cookie or len(header) != len(cookie):
        return False
    return hmac.compare_digest(header, cookie)


def _with_auth_cache(scope: Scope, send: Send) -> Send:
    path = scope.get("path")
    if not isinstance(path, str) or not path.startswith(_AUTH_PREFIX):

        async def forward(message: Message) -> None:
            await send(message)

        return forward

    async def send_nostore(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = MutableHeaders(scope=message)
            headers["cache-control"] = "no-store"
        await send(message)

    return send_nostore
