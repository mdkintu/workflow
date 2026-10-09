"""Production settings.

No defaults for required variables (SITE_URL, SECRET_KEY, ALLOWED_HOSTS,
DATABASE_URL, REDIS_URL, SMS_BACKEND): a missing one raises
ImproperlyConfigured at import time (ADR-18, CLAUDE.md "Host-agnostic").
"""

from workflow.settings.base import *  # noqa: F403
from workflow.settings.base import env

DEBUG = env.bool("DEBUG", default=False)

# --- Required: no defaults (docs/02-architecture.md §9.1 marks these ✅) ---
SECRET_KEY = env.str("SECRET_KEY")
SITE_HOST = env.str("SITE_HOST")
SITE_URL = env.str("SITE_URL")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
DATABASE_URL = env.str("DATABASE_URL")
REDIS_URL = env.str("REDIS_URL")
SMS_BACKEND = env.str("SMS_BACKEND")

CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[SITE_URL])

SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False

TRUST_PROXY_HEADERS = env.bool("TRUST_PROXY_HEADERS", default=True)
if TRUST_PROXY_HEADERS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Caddy terminates TLS in front of Gunicorn; it does not need to redirect again.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

DATABASES = {"default": env.db_url_config(DATABASE_URL)}

CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = None

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

MEDIA_ROOT = env.str("MEDIA_ROOT", default="/data/media")
MEDIA_X_ACCEL = env.bool("MEDIA_X_ACCEL", default=True)
STATIC_ROOT = env.str("STATIC_ROOT", default="/data/static")
