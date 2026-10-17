from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    """Заявки (ТЗ §12): статус и заметку ведёт менеджер, остальное — только чтение."""

    list_display = ["number", "created_at", "status", "source", "phone", "name", "city", "short_topic", "dialog"]
    list_filter = ["status", "source", "created_at"]
    list_editable = ["status"]
    search_fields = ["number", "phone", "name", "city"]
    fields = ["number", "status", "manager_note", "phone", "name", "city", "topic", "source", "dialog",
              "pd_consent_at", "created_at", "updated_at"]
    readonly_fields = ["number", "phone", "name", "city", "topic", "source", "dialog", "pd_consent_at",
                       "created_at", "updated_at"]

    def has_add_permission(self, request):
        return False

    @admin.display(description="Суть вопроса")
    def short_topic(self, obj):
        return (obj.topic[:80] + "…") if len(obj.topic) > 80 else obj.topic

    @admin.display(description="Диалог")
    def dialog(self, obj):
        if not obj.conversation_id:
            return "—"
        url = reverse("admin:chat_conversation_change", args=[obj.conversation_id])
        return format_html('<a href="{}">Диалог #{}</a>', url, obj.conversation_id)
