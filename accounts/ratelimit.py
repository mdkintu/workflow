"""Per-IP throttling for auth endpoints (docs/01-requirements.md NFR-S3,
X.1: "Login attempts are also rate-limited per IP address"). This is a
broader guard on top of accounts.backends.PinBackend's per-account lockout —
it stops one address from hammering many different phone numbers.

Cache-based, not database-based: it's a soft limit, cheap to check on every
request, and fine to lose on a cache restart (the per-account lockout is the
one that must be durable).
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

MAX_ATTEMPTS_PER_IP = 20
WINDOW_SECONDS = 15 * 60


def get_client_ip(request: HttpRequest) -> str:
    """Every request reaches Django through Caddy (docs/02-architecture.md
    §7), so `REMOTE_ADDR` alone would always be Caddy's own address. When
    TRUST_PROXY_HEADERS is on (the same setting that trusts
    X-Forwarded-Proto), trust the client IP Caddy reports too."""
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _cache_key(request: HttpRequest, scope: str) -> str:
    return f"ratelimit:{scope}:{get_client_ip(request)}"


def is_ip_rate_limited(request: HttpRequest, scope: str) -> bool:
    return cache.get(_cache_key(request, scope), 0) >= MAX_ATTEMPTS_PER_IP


def record_login_attempt(request: HttpRequest, scope: str) -> None:
    key = _cache_key(request, scope)
    cache.add(key, 0, WINDOW_SECONDS)  # a no-op once the key already exists
    cache.incr(key)
