from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, AnonSession, ShortlistItem


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["phone"]
    list_display = ["phone", "first_name", "role", "is_staff", "is_active"]
    list_filter = ["role", "is_staff", "is_active"]
    search_fields = ["phone", "first_name", "email"]

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Личное", {"fields": ("first_name", "email", "role")}),
        ("Права", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("phone", "password1", "password2"),
        }),
    )


@admin.register(AnonSession)
class AnonSessionAdmin(admin.ModelAdmin):
    list_display = ["session_id", "merged_into", "created_at", "last_seen_at"]


@admin.register(ShortlistItem)
class ShortlistItemAdmin(admin.ModelAdmin):
    list_display = ["product", "user", "session", "created_at"]
