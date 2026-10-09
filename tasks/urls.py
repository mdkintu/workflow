from django.urls import path

from tasks import views
from tasks.transitions import Action

app_name = "tasks"

urlpatterns = [
    path("", views.task_list, name="list"),
    path("my/", views.my_tasks, name="my"),
    path("new/", views.task_create, name="create"),
    path("new/self/", views.task_create_self, name="create_self"),
    path("partials/assignees", views.assignee_options, name="assignees"),
    path("<uuid:task_id>/", views.task_detail, name="detail"),
    path("<uuid:task_id>/edit/", views.task_edit, name="edit"),
    path("<uuid:task_id>/photos/", views.task_photo_upload, name="photos"),
    path("<uuid:task_id>/comments/", views.comment_add, name="comment-add"),
    path(
        "<uuid:task_id>/comments/<uuid:comment_id>/delete/",
        views.comment_delete,
        name="comment-delete",
    ),
    # One POST route per transition, e.g. /tasks/<id>/resolve-flag/ (docs/04 §4.1).
    *[
        path(
            f"<uuid:task_id>/{action.value.replace('_', '-')}/",
            views.task_action,
            {"action": action},
            name=action.value,
        )
        for action in Action
    ],
]
