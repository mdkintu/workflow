"""Settings shared by dev and prod.

Host-specific values (domain, TLS, database, secrets, ports, ...) are NOT
read here — they are read in settings/dev.py (with LAN defaults) and
settings/prod.py (no defaults; missing vars raise ImproperlyConfigured).
See CLAUDE.md "Host-agnostic (ADR-18)" and docs/02-architecture.md §9.1.
"""

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# ENV_FILE lets a caller point at a different (or nonexistent) file instead of
# the project's real `.env` — used by tests/test_host_agnostic.py so the
# "prod settings require these vars" check isn't defeated by a real `.env`
# sitting in a contributor's checkout (needed for `make dev`, see CLAUDE.md).
_env_file = Path(os.environ.get("ENV_FILE", str(BASE_DIR / ".env")))
if _env_file.exists():
    environ.Env.read_env(str(_env_file))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "accounts",
    "organisations",
    "tasks",
    "checklists",
    "notifications",
    "dashboard",
    "sync",
    "workflow",  # template tags only: {% icon %} (ADR-20)
]

MIDDLEWARE = [
    # First: answers /healthz and /healthz/ready before host/CSRF/auth checks.
    "workflow.health.HealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # After AuthenticationMiddleware, per docs/02-architecture.md §3.
    "organisations.middleware.TenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "workflow.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "organisations.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "workflow.wsgi.application"
ASGI_APPLICATION = "workflow.asgi.application"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.PinBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"

# PINs are validated by accounts.phone / the PIN setup flow, not by these.
AUTH_PASSWORD_VALIDATORS = []

USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en"
USE_I18N = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Tenant models use UUID PKs of their own (organisations.tenancy.TenantModel).

STATIC_URL = "/static/"
# Source directories collectstatic gathers from. STATIC_ROOT (the collected
# output) is set per-environment and must not overlap with these.
STATICFILES_DIRS = [BASE_DIR / "frontend", BASE_DIR / "static"]

MEDIA_URL = "/media/"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # docs/04-design.md §4.2: {"code", "detail"} bodies; 401 when logged out.
    "EXCEPTION_HANDLER": "sync.exceptions.api_exception_handler",
    # Per membership (throttles key on the user), docs/04-design.md §4.2.
    "DEFAULT_THROTTLE_RATES": {
        "sync-pull": "60/min",
        "sync-push": "30/min",
        "sync-photos": "60/min",
    },
}

# --- Non-host-specific, still environment-sourced settings (ADR-18) ---
MEDIA_INTERNAL_PREFIX = env.str("MEDIA_INTERNAL_PREFIX", default="/_protected/media/")
# Hand photo files to Caddy with X-Accel-Redirect (true behind Caddy) instead
# of streaming them from Django (dev mode, no Caddy). docs/02 §7-8.
MEDIA_X_ACCEL = env.bool("MEDIA_X_ACCEL", default=False)
CA_SETUP_PAGE_ENABLED = env.bool("CA_SETUP_PAGE_ENABLED", default=False)

AT_USERNAME = env.str("AT_USERNAME", default="")
AT_API_KEY = env.str("AT_API_KEY", default="")
AT_SENDER_ID = env.str("AT_SENDER_ID", default="")
AT_API_URL = env.str("AT_API_URL", default="https://api.africastalking.com/version1/messaging")
SMS_DELIVERY_WEBHOOK_ENABLED = env.bool("SMS_DELIVERY_WEBHOOK_ENABLED", default=False)
WHATSAPP_BACKEND = env.str("WHATSAPP_BACKEND", default="none")

LOG_LEVEL = env.str("LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_phone_numbers": {
            "()": "workflow.logging_utils.RedactPhoneNumbersFilter",
        },
    },
    "formatters": {
        "json": {
            "()": "workflow.logging_utils.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["redact_phone_numbers"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
}

CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = "default"
# Sends go on their own queue so a slow SMS provider never delays other jobs.
CELERY_TASK_ROUTES = {"notifications.tasks.send_notification": {"queue": "notifications"}}
