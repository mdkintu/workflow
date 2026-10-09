from django.urls import path

from dashboard import views

app_name = "dashboard"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("partials/summary", views.summary_partial, name="summary"),
    path("list", views.drill_down, name="list"),
    path("reassign/<uuid:task_id>/", views.reassign, name="reassign"),
]
