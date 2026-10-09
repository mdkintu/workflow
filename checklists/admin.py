from django.contrib import admin

from checklists.models import (
    ChecklistItem,
    ChecklistRun,
    ChecklistRunItem,
    ChecklistRunItemTick,
    ChecklistTemplate,
    RecurrenceRule,
)


class UnscopedAdminMixin:
    def get_queryset(self, request):
        return self.model.unscoped.all()


@admin.register(ChecklistTemplate)
class ChecklistTemplateAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "organisation", "location", "is_active"]
    list_filter = ["organisation", "location", "is_active"]


@admin.register(ChecklistItem)
class ChecklistItemAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["label", "template", "order", "organisation"]
    list_filter = ["organisation"]


@admin.register(RecurrenceRule)
class RecurrenceRuleAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["template", "kind", "organisation", "is_active"]
    list_filter = ["organisation", "kind"]


@admin.register(ChecklistRun)
class ChecklistRunAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "organisation", "status", "due_at"]
    list_filter = ["organisation", "status"]


@admin.register(ChecklistRunItem)
class ChecklistRunItemAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["label", "run", "order", "organisation"]
    list_filter = ["organisation"]


@admin.register(ChecklistRunItemTick)
class ChecklistRunItemTickAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["run_item", "membership", "skipped", "trusted_time", "organisation"]
    list_filter = ["organisation", "skipped"]
