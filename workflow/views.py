"""Small top-level views that don't belong to one app: the field app shell,
service worker, manifest, offline page, the CA setup page and the "/" role
redirect. See docs/04-design.md §4.1.
"""

import hashlib
import json
from functools import cache
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import get_template
from django.templatetags.static import static
from django.utils.translation import gettext as _
from django.views.decorators.csrf import ensure_csrf_cookie

STATIC_DIR = Path(settings.BASE_DIR) / "static"

# What the service worker precaches (docs/02-architecture.md §4.2), as
# static paths. The app shell itself is cached separately (it needs a session).
PRECACHE_STATIC = (
    "css/app.css",
    "theme.js",
    "vendor/alpine.min.js",
    "vendor/dexie.min.js",
    "field/db.js",
    "field/photo.js",
    "field/sync.js",
    "field/app.js",
    "icons/icon-192.png",
    "icons/icon-512.png",
)
PRECACHE_PAGES = ("/offline/", "/manifest.json")


def home_redirect(request: HttpRequest):
    """Staff go to the offline field app; everyone else to the dashboard."""
    membership = getattr(request, "membership", None)
    if membership is not None and membership.role == "staff":
        return redirect("app-shell")
    if request.user.is_authenticated:
        return redirect("dashboard:dashboard")
    return redirect("accounts:login")


def field_strings() -> dict:
    """The field app's user-facing text, translated here (app.js reads it)."""
    return {
        "online": _("Online"),
        "offline": _("Offline"),
        "synced": _("Synced"),
        "syncing": _("Syncing"),
        "waiting": _("%s waiting"),
        "sync_problem": _("Sync problem"),
        "log_in_again": _("Log in again"),
        "last_sync": _("Last sync"),
        "overdue": _("Overdue"),
        "now": _("Now"),
        "later_today": _("Later today"),
        "tomorrow": _("Tomorrow"),
        "done_today": _("Done today"),
        "done_lc": _("done"),
        "show": _("show"),
        "hide": _("hide"),
        "you": _("You"),
        "photo_needed": _("Take a photo first — this task needs proof."),
        "photo_failed": _("That photo couldn't be used. Try again."),
        "reason_needed": _("Choose or write a reason."),
        "logout_pending": _("%s changes haven't been sent yet and will be lost. Log out anyway?"),
        "status": {
            "pending": _("To do"),
            "in_progress": _("In progress"),
            "done": _("Done"),
            "flagged": _("Flagged"),
            "cancelled": _("Cancelled"),
            "overdue": _("Overdue"),
        },
        # docs/04-design.md §4.5 reason codes the person may see.
        "codes": {
            "invalid_transition": _("This task had already moved on; it has been refreshed."),
            "photo_required": _("Add a photo before marking this done."),
            "not_found": _("This task was removed by your manager."),
            "forbidden": _("You can't do that."),
            "not_skippable": _("This item can't be skipped."),
            "not_yet_available": _("This checklist wasn't open yet."),
            "task_cancelled_flagged": _("Saved, but your manager had cancelled this task."),
            "already_done": _("Someone else finished this first."),
        },
    }


@cache
def _asset_version() -> str:
    """A hash of every precached file and the shell template: any change
    makes phones install the new version."""
    digest = hashlib.sha256()
    for path in PRECACHE_STATIC:
        found = finders.find(path)
        digest.update(Path(found).read_bytes() if found else path.encode())
    for name in (
        "field/app_shell.html",
        "field/_comments.html",
        "offline.html",
        "base.html",
        "_head.html",
    ):
        digest.update(Path(get_template(name).origin.name).read_bytes())
    digest.update((STATIC_DIR / "sw.js").read_bytes())
    return digest.hexdigest()[:12]


def asset_version() -> str:
    if settings.DEBUG:  # files change while developing
        _asset_version.cache_clear()
    return _asset_version()


@login_required
@ensure_csrf_cookie  # the field app sends it back as X-CSRFToken
def app_shell(request: HttpRequest) -> HttpResponse:
    """No personal data in this page: the service worker caches it."""
    response = render(
        request,
        "field/app_shell.html",
        {"strings": field_strings(), "version": asset_version()},
    )
    response["Cache-Control"] = "no-cache, private"
    return response


def setup_page(request: HttpRequest) -> HttpResponse:
    if not settings.CA_SETUP_PAGE_ENABLED:
        raise Http404
    return render(request, "setup.html")


def service_worker(request: HttpRequest) -> HttpResponse:
    precache = [static(path) for path in PRECACHE_STATIC] + list(PRECACHE_PAGES)
    content = (
        (STATIC_DIR / "sw.js")
        .read_text()
        .replace("__VERSION__", asset_version())
        .replace("__PRECACHE__", json.dumps(precache))
    )
    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


def manifest(request: HttpRequest) -> HttpResponse:
    manifest_path = STATIC_DIR / "manifest.json"
    content = manifest_path.read_text()
    return HttpResponse(content, content_type="application/json")


def offline_page(request: HttpRequest) -> HttpResponse:
    return render(request, "offline.html")


@login_required
def styleguide(request: HttpRequest) -> HttpResponse:
    """Every design-system component in every state, for visual QA (ADR-19).
    Sample content only — no organisation data."""
    return render(request, "styleguide.html")
