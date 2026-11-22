"""Optional password protection for a hosted deployment (D-63).

If APP_PASSWORD is set, every request needs HTTP Basic auth (username APP_USERNAME, default
"supplysure"). The browser asks once and then sends it with every page, API call and voice
upload. /health stays open for the host's health check. Locally, with no APP_PASSWORD, nothing
changes.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

OPEN_PATHS = {"/health"}


def _expected() -> tuple[str, str] | None:
    password = os.environ.get("APP_PASSWORD", "").strip()
    if not password:
        return None
    return os.environ.get("APP_USERNAME", "supplysure").strip() or "supplysure", password


def _authorised(request: Request, user: str, password: str) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic" or not value:
        return False
    try:
        given_user, _, given_pw = base64.b64decode(value).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    # Compare both, always, in constant time.
    ok_user = secrets.compare_digest(given_user.encode(), user.encode())
    ok_pw = secrets.compare_digest(given_pw.encode(), password.encode())
    return ok_user and ok_pw


class PasswordMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        expected = _expected()
        if expected is None or request.url.path in OPEN_PATHS:
            return await call_next(request)
        if not _authorised(request, *expected):
            return PlainTextResponse(
                "Sign in to SupplySure.",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="SupplySure", charset="UTF-8"'},
            )
        return await call_next(request)
