"""ADR-18 "host-agnostic deployment": prod settings require the core
variables, no hostnames are hardcoded in code/templates/JS, and SMS links
are built from settings.SITE_URL.
"""

import pathlib
import subprocess
import sys

from django.test import override_settings

from notifications.sms import render_sms

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent

FORBIDDEN_LITERALS = ["localhost", "workflow.lan", "127.0.0.1", "192.168."]
SCAN_EXTENSIONS = {".py", ".html", ".js", ".json", ".txt"}
SCAN_ROOTS = [
    "accounts",
    "organisations",
    "tasks",
    "checklists",
    "notifications",
    "dashboard",
    "sync",
    "workflow",
    "templates",
    "frontend/field",
    "static",
]
EXTRA_FILES = ["Caddyfile", "Dockerfile"]
EXCLUDED_FILES = {PROJECT_ROOT / "workflow" / "settings" / "dev.py"}
EXCLUDED_PATH_SEGMENTS = ["/migrations/"]


def _scanned_files():
    for root_name in SCAN_ROOTS:
        root = PROJECT_ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SCAN_EXTENSIONS:
                continue
            if path in EXCLUDED_FILES:
                continue
            if any(seg in path.as_posix() for seg in EXCLUDED_PATH_SEGMENTS):
                continue
            yield path
    for name in EXTRA_FILES:
        path = PROJECT_ROOT / name
        if path.exists():
            yield path


def test_prod_settings_require_core_env_vars():
    """(a) Importing prod settings with core vars missing raises
    ImproperlyConfigured. Run in a subprocess with a clean env AND pointed at
    a nonexistent ENV_FILE, so a real `.env` in a contributor's checkout
    (needed for `make dev`, see CLAUDE.md) can't mask a missing variable —
    workflow/settings/base.py reads the file straight off disk regardless of
    what `env=` is passed to subprocess.run()."""
    code = (
        "from django.core.exceptions import ImproperlyConfigured\n"
        "try:\n"
        "    import workflow.settings.prod\n"
        "except ImproperlyConfigured:\n"
        "    print('RAISED')\n"
        "else:\n"
        "    print('NOT_RAISED')\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=str(PROJECT_ROOT),
        env={"PATH": "/usr/bin:/bin", "ENV_FILE": "/nonexistent/workflow-test.env"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert "RAISED" in result.stdout, f"stdout={result.stdout!r} stderr={result.stderr!r}"


def test_no_hardcoded_hostnames_or_private_ips():
    """(b) No literal host/IP appears in application code, templates, JS,
    JSON, text, the Caddyfile or the Dockerfile (dev.py is allowed LAN
    defaults; vendored JS under frontend/vendor/ isn't scanned)."""
    offenders = []
    for path in _scanned_files():
        text = path.read_text(errors="ignore")
        for literal in FORBIDDEN_LITERALS:
            if literal in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}: {literal!r}")
    assert not offenders, "Hardcoded host literals found:\n" + "\n".join(offenders)


@override_settings(SITE_URL="https://override.example.ug")
def test_sms_rendering_uses_site_url_setting():
    """(c) SMS bodies are built from settings.SITE_URL, not a hardcoded host."""
    from django.conf import settings

    body = render_sms("pin_setup", {"code": "123456", "site_url": settings.SITE_URL})
    assert settings.SITE_URL in body
    assert "override.example.ug" in body
