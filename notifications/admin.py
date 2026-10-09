from django.contrib import admin

from notifications.models import Notification, SmsUsage


class UnscopedAdminMixin:
    def get_queryset(self, request):
        return self.model.unscoped.all()


@admin.register(Notification)
class NotificationAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["kind", "organisation", "status", "channel", "send_after", "sent_at"]
    list_filter = ["organisation", "status", "kind", "channel"]
    readonly_fields = ["dedupe_key"]


@admin.register(SmsUsage)
class SmsUsageAdmin(UnscopedAdminMixin, admin.ModelAdmin):
    list_display = ["organisation", "month", "count", "warned_80"]
    list_filter = ["organisation"]
