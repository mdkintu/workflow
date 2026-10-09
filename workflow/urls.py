"""Top-level URL configuration. See docs/04-design.md §4.1 for the full map."""

from django.contrib import admin
from django.urls import include, path

from tasks import views as task_views
from workflow import views

urlpatterns = [
    path("", views.home_redirect, name="home"),
    path("app/", views.app_shell, name="app-shell"),
    path("setup/", views.setup_page, name="setup"),
    path("sw.js", views.service_worker, name="service-worker"),
    path("manifest.json", views.manifest, name="manifest"),
    path("offline/", views.offline_page, name="offline"),
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("org/", include("organisations.urls")),
    path("roster/", include("organisations.roster_urls")),
    path("tasks/", include("tasks.urls")),
    path("checklists/", include("checklists.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("notifications/", include("notifications.urls")),
    path("styleguide/", views.styleguide, name="styleguide"),
    # Never a public URL: permission-checked, then streamed (docs/02 §8).
    path("media/p/<uuid:photo_id>", task_views.photo_file, name="photo"),
    path("media/p/<uuid:photo_id>/t", task_views.photo_file, {"thumb": True}, name="photo-thumb"),
    path("api/sync/", include("sync.urls")),
]
