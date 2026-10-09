from django.contrib import admin

from accounts.models import PinSetupToken, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """User is global (not tenant-scoped); no `.unscoped` needed here.

    A custom User with USERNAME_FIELD="phone_e164" doesn't fit Django's
    stock UserCreationForm/UserChangeForm, so this is a plain ModelAdmin.
    Creating staff accounts is done through `seed_demo` / the future
    "invite" feature, not the admin "add" form.
    """

    ordering = ["name"]
    list_display = ["phone_e164", "name", "is_staff", "is_active", "locked_until"]
    search_fields = ["phone_e164", "name"]
    exclude = ["password"]
    readonly_fields = ["date_joined", "last_login"]


@admin.register(PinSetupToken)
class PinSetupTokenAdmin(admin.ModelAdmin):
    list_display = ["user", "expires_at", "used_at", "created_by"]
    autocomplete_fields = ["user", "created_by"]
