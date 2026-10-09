"""/roster/... (docs/04-design.md §4.1). Views live in organisations.views."""

from django.urls import path

from organisations import views

app_name = "roster"

urlpatterns = [
    path("", views.roster, name="roster"),
    path("cell/", views.roster_cell, name="cell"),
    path("copy-week/", views.roster_copy_week, name="copy-week"),
]
