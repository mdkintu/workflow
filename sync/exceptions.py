"""Error bodies for the sync API: always {"code": ..., "detail": ...}
(docs/04-design.md §4.2), so the phone can act on the code.

DRF answers an unauthenticated request with 403 under SessionAuthentication
(there's no WWW-Authenticate scheme to offer); the phone needs 401 to know
it should keep its outbox and ask the person to log in again.
"""

from __future__ import annotations

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

_CODES = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    415: "unsupported_media_type",
    426: "client_outdated",
    429: "rate_limited",
}


def error(status_code: int, code: str | None = None, detail: str = "") -> Response:
    return Response(
        {"code": code or _CODES.get(status_code, "error"), "detail": detail}, status=status_code
    )


def api_exception_handler(exc, context):
    if isinstance(exc, exceptions.NotAuthenticated | exceptions.AuthenticationFailed):
        return error(status.HTTP_401_UNAUTHORIZED, "not_authenticated", str(exc.detail))
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    detail = getattr(exc, "detail", "")
    code = _CODES.get(response.status_code, "error")
    if isinstance(exc, exceptions.PermissionDenied) and "CSRF" in str(detail):
        code = "csrf_failed"
    response.data = {"code": code, "detail": str(detail)}
    return response
