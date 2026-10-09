"""Local development settings.

May default host-specific values to LAN/localhost values, unlike prod.py.
"""

from workflow.settings.base import *  # noqa: F403
from workflow.settings.base import BASE_DIR, env

DEBUG = env.bool("DEBUG", default=True)
SECRET_KEY = env.str("SECRET_KEY", default="dev-insecure-secret-key-do-not-use-in-prod")

SITE_HOST = env.str("SITE_HOST", default="localhost")
SITE_URL = env.str("SITE_URL", default="https://localhost")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[SITE_URL])

SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
TRUST_PROXY_HEADERS = env.bool("TRUST_PROXY_HEADERS", default=False)
if TRUST_PROXY_HEADERS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

DATABASE_URL = env.str(
    "DATABASE_URL", default="postgres://workflow:workflow@localhost:5432/workflow"
)
DATABASES = {"default": env.db_url_config(DATABASE_URL)}

REDIS_URL = env.str("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = env.str("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = None
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

MEDIA_ROOT = env.str("MEDIA_ROOT", default=str(BASE_DIR / "mediafiles"))
STATIC_ROOT = env.str("STATIC_ROOT", default=str(BASE_DIR / "staticfiles"))

SMS_BACKEND = env.str("SMS_BACKEND", default="console")

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
