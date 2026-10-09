"""Liveness/readiness middleware.

Placed FIRST in MIDDLEWARE so container healthchecks can hit /healthz and
/healthz/ready without an ALLOWED_HOSTS entry: it never calls
request.get_host() and never routes through URLconf/ALLOWED_HOSTS checks.
"""

import json

from django.conf import settings
from django.http import HttpRequest, HttpResponse


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "GET" and request.path == "/healthz":
            return self._liveness()
        if request.method == "GET" and request.path == "/healthz/ready":
            return self._readiness()
        return self.get_response(request)

    def _liveness(self) -> HttpResponse:
        return HttpResponse(json.dumps({"status": "ok"}), content_type="application/json")

    def _readiness(self) -> HttpResponse:
        checks = {}
        ok = True

        try:
            from django.db import connection

            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001
            ok = False
            checks["database"] = f"error: {exc}"

        redis_url = getattr(settings, "REDIS_URL", None)
        if redis_url and not settings.DEBUG:
            try:
                import redis

                client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
                client.ping()
                checks["redis"] = "ok"
            except Exception as exc:  # noqa: BLE001
                ok = False
                checks["redis"] = f"error: {exc}"

        status = 200 if ok else 503
        body = {"status": "ok" if ok else "error", "checks": checks}
        return HttpResponse(json.dumps(body), status=status, content_type="application/json")
