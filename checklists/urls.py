from django.urls import path

from checklists import views

app_name = "checklists"

urlpatterns = [
    # Templates and schedules (Manager+)
    path("", views.template_list, name="templates"),
    path("new/", views.template_create, name="template-create"),
    path("<uuid:template_id>/", views.template_edit, name="template"),
    path("<uuid:template_id>/items/", views.item_add, name="item-add"),
    path("<uuid:template_id>/items/<uuid:item_id>/move/", views.item_move, name="item-move"),
    path("<uuid:template_id>/items/<uuid:item_id>/remove/", views.item_remove, name="item-remove"),
    path("<uuid:template_id>/rules/", views.rule_add, name="rule-add"),
    path("rules/<uuid:rule_id>/toggle/", views.rule_toggle, name="rule-toggle"),
    # Runs
    path("runs/", views.run_list, name="runs"),
    path("runs/<uuid:run_id>/", views.run_detail, name="run"),
    path("runs/<uuid:run_id>/flag/", views.run_flag, name="run-flag"),
    path("runs/<uuid:run_id>/resolve-flag/", views.run_resolve_flag, name="run-resolve-flag"),
    path("runs/<uuid:run_id>/cancel/", views.run_cancel, name="run-cancel"),
    path("runs/<uuid:run_id>/comments/", views.run_comment_add, name="run-comment-add"),
    path(
        "runs/<uuid:run_id>/comments/<uuid:comment_id>/delete/",
        views.run_comment_delete,
        name="run-comment-delete",
    ),
    path("items/<uuid:item_id>/tick/", views.item_tick, name="item-tick"),
    path("items/<uuid:item_id>/skip/", views.item_skip, name="item-skip"),
]
