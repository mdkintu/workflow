"""Admin registrations. The admin is operator-only and cross-tenant by
design, so every tenant-model admin reads through `.unscoped` (CLAUDE.md:
"Never use .unscoped in views, serializers, forms or the sync app" — admin
is none of those).
"""

from django.contrib import admin

from organisations.models import (
    AuditEvent,
    Location,
    Membership,
    MembershipLocation,
    Organisation,
    Shift,
    ShiftAssignment,
)


class UnscopedAdminMixin:
    def get_queryset(self, request):
        return self.model.unscoped.all()


@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "country_code", "timezone", "is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Location)
class LocationAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "organisation", "is_active", "sort_order"]
    list_filter = ["organisation", "is_active"]
    search_fields = ["name"]


@admin.register(Membership)
class MembershipAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["user", "organisation", "role", "is_active"]
    list_filter = ["organisation", "role", "is_active"]
    search_fields = ["user__name", "user__phone_e164", "display_name"]


@admin.register(MembershipLocation)
class MembershipLocationAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["membership", "location", "organisation"]
    list_filter = ["organisation"]


@admin.register(Shift)
class ShiftAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "location", "organisation", "start_time", "end_time", "is_active"]
    list_filter = ["organisation", "location", "is_active"]


@admin.register(ShiftAssignment)
class ShiftAssignmentAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["membership", "shift", "date", "organisation"]
    list_filter = ["organisation", "date"]


@admin.register(AuditEvent)
class AuditEventAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["action", "target_type", "target_id", "organisation", "created_at"]
    list_filter = ["organisation", "action"]
    readonly_fields = [f.name for f in AuditEvent._meta.fields]
