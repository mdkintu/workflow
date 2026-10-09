from django.urls import path

from sync import views

app_name = "sync"

urlpatterns = [
    path("me", views.MeView.as_view(), name="me"),
    path("pull", views.PullView.as_view(), name="pull"),
    path("push", views.PushView.as_view(), name="push"),
    path("photos/<uuid:photo_id>", views.PhotoView.as_view(), name="photo"),
]
