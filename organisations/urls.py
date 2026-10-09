from django.urls import path

from organisations import views

app_name = "organisations"

urlpatterns = [
    path("switch/", views.org_switch, name="org-switch"),
    path("people/", views.people_list, name="people"),
    path("people/invite/", views.people_invite, name="people-invite"),
    path(
        "people/<uuid:membership_id>/reset-pin/",
        views.people_reset_pin,
        name="people-reset-pin",
    ),
    path("locations/", views.locations, name="locations"),
    path("shifts/", views.shifts, name="shifts"),
]
