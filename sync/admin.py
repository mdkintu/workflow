from django.contrib import admin

from sync.models import OfflineSyncLog


@admin.register(OfflineSyncLog)
class OfflineSyncLogAdmin(admin.ModelAdmin):
    list_display = ["id", "organisation", "kind", "result_status", "received_at"]
    list_filter = ["organisation", "result_status", "kind"]

    def get_queryset(self, request):
        return OfflineSyncLog.unscoped.all()
