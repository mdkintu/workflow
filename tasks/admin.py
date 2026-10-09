from django.contrib import admin

from tasks.models import Task, TaskComment, TaskPhoto


class UnscopedAdminMixin:
    def get_queryset(self, request):
        return self.model.unscoped.all()


@admin.register(Task)
class TaskAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["title", "organisation", "location", "status", "due_at"]
    list_filter = ["organisation", "status", "location"]
    search_fields = ["title"]


@admin.register(TaskPhoto)
class TaskPhotoAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["id", "organisation", "task", "bytes", "linked_at"]
    list_filter = ["organisation"]


@admin.register(TaskComment)
class TaskCommentAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["id", "organisation", "task", "checklist_run", "is_flag", "device_time"]
    list_filter = ["organisation", "is_flag"]
