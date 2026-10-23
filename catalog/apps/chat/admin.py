import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Exists, OuterRef, Q, Subquery, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.urls import reverse
from django.utils.html import format_html

from apps.leads.models import Lead
from apps.orders.models import Order
from .knowledge import activate, create_version, price_mentions
from .models import Conversation, KnowledgeBaseVersion, LLMUsage, Message, Quote


def _per_conversation(queryset, aggregate):
    """Агрегат по диалогу подзапросом: два JOIN в одном annotate перемножили бы строки."""
    return Subquery(
        queryset.filter(conversation=OuterRef("pk")).order_by().values("conversation")
        .annotate(value=aggregate).values("value")
    )


class ReadOnlyMixin:
    """Переписка в админке только для чтения."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class MessageInline(ReadOnlyMixin, admin.TabularInline):
    model = Message
    fields = ["created_at", "role", "content", "event_type"]
    readonly_fields = fields
    extra = 0

    # Служебные результаты инструментов клиенту не показываются — в админке они тоже лишние
    def get_queryset(self, request):
        return super().get_queryset(request).exclude(role=Message.Role.TOOL)


@admin.register(Conversation)
class ConversationAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["id", "owner", "status", "created_at", "last_message_at", "messages_count", "cost", "has_order", "has_lead"]
    list_filter = ["status"]
    search_fields = ["user__phone"]
    fields = ["owner", "status", "created_at", "last_message_at"]
    readonly_fields = fields
    inlines = [MessageInline]

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related("user")
            .annotate(
                messages_total=_per_conversation(
                    Message.objects.filter(role__in=[Message.Role.USER, Message.Role.ASSISTANT]), Count("id")
                ),
                cost_total=_per_conversation(LLMUsage.objects.all(), Sum("cost_usd")),
                order_exists=Exists(Order.objects.filter(conversation=OuterRef("pk"))),
                lead_exists=Exists(Lead.objects.filter(conversation=OuterRef("pk"))),
            )
        )

    @admin.display(description="Владелец")
    def owner(self, obj):
        return obj.user.phone if obj.user_id else "гость"

    @admin.display(description="Сообщений", ordering="messages_total")
    def messages_count(self, obj):
        return obj.messages_total or 0

    @admin.display(description="Стоимость, $", ordering="cost_total")
    def cost(self, obj):
        return obj.cost_total or 0

    @admin.display(description="Заказ", boolean=True)
    def has_order(self, obj):
        return obj.order_exists

    @admin.display(description="Заявка", boolean=True)
    def has_lead(self, obj):
        return obj.lead_exists


# --- база знаний


class KnowledgeBaseForm(forms.ModelForm):
    upload = forms.FileField(
        required=False, label="Или загрузите файл",
        help_text="Файл .md или .txt в кодировке UTF-8 — заменит текст выше",
    )

    class Meta:
        model = KnowledgeBaseVersion
        fields = ["content", "comment"]
        widgets = {"content": forms.Textarea(attrs={"rows": 40, "style": "width: 100%; font-family: monospace;"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["content"].required = False

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("upload")
        if upload:
            if not upload.name.lower().endswith((".md", ".txt")):
                raise forms.ValidationError("Загрузите файл .md или .txt")
            try:
                cleaned["content"] = upload.read().decode("utf-8").strip()
            except UnicodeDecodeError:
                raise forms.ValidationError("Файл не в кодировке UTF-8")
        if not (cleaned.get("content") or "").strip():
            raise forms.ValidationError("Нужен текст базы знаний или файл")
        return cleaned


@admin.register(KnowledgeBaseVersion)
class KnowledgeBaseVersionAdmin(admin.ModelAdmin):
    form = KnowledgeBaseForm
    list_display = ["id", "created_at", "is_active", "source", "size", "comment", "created_by"]
    list_display_links = ["id", "created_at"]
    readonly_fields = ["is_active", "source", "created_at", "created_by", "char_count", "tokens"]
    actions = ["make_active"]

    def get_fields(self, request, obj=None):
        fields = ["content", "upload", "comment"]
        return fields + self.readonly_fields if obj else fields

    @admin.display(description="Размер")
    def size(self, obj):
        return f"{obj.char_count} симв. · {self.tokens(obj)}"

    @admin.display(description="Токенов")
    def tokens(self, obj):
        return f"{obj.token_count} ток." if obj.token_count is not None else "токены считаются…"

    def has_delete_permission(self, request, obj=None):
        # Прежние версии нужны для отката
        return False

    def save_model(self, request, obj, form, change):
        """Сохранение никогда не меняет версию — создаёт новую и делает её активной."""
        upload = form.cleaned_data.get("upload")
        version = create_version(
            form.cleaned_data["content"].strip(),
            source=KnowledgeBaseVersion.Source.UPLOAD if upload else KnowledgeBaseVersion.Source.ADMIN,
            comment=form.cleaned_data.get("comment", ""), created_by=request.user,
        )
        for field in KnowledgeBaseVersion._meta.concrete_fields:
            setattr(obj, field.attname, getattr(version, field.attname))
        self.message_user(request, f"Сохранено как версия #{version.pk} — она активна, агент отвечает по ней "
                                   f"со следующей реплики", messages.SUCCESS)
        prices = price_mentions(version.content)
        if prices:
            self.message_user(
                request, "В тексте есть суммы в рублях (например: " + ", ".join(prices) + "). "
                "Клиенту агент называет только цены каталога.", messages.WARNING,
            )

    def response_change(self, request, obj):
        # Сообщение «изменено» от Django здесь неверно: изменённой версии нет, есть новая
        return self.response_post_save_change(request, obj)

    @admin.action(description="Сделать активной")
    def make_active(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Выберите одну версию", messages.ERROR)
            return
        version = queryset.get()
        activate(version)
        self.message_user(request, f"Версия #{version.pk} активна", messages.SUCCESS)


# --- КП


@admin.register(Quote)
class QuoteAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["number", "created_at", "total", "valid_until", "owner", "pdf", "dialog"]
    search_fields = ["number"]
    fields = ["number", "created_at", "valid_until", "total", "items", "owner", "pdf", "dialog"]
    readonly_fields = fields

    @admin.display(description="Владелец")
    def owner(self, obj):
        return obj.user.phone if obj.user_id else "гость"

    @admin.display(description="PDF")
    def pdf(self, obj):
        return format_html('<a href="{}">Скачать</a>', reverse("chat:quote_pdf", args=[obj.pk]))

    @admin.display(description="Диалог")
    def dialog(self, obj):
        if not obj.conversation_id:
            return "—"
        return format_html('<a href="{}">Диалог #{}</a>',
                           reverse("admin:chat_conversation_change", args=[obj.conversation_id]), obj.conversation_id)


# --- расход модели

MOSCOW = ZoneInfo("Europe/Moscow")
SUMMARY_DAYS = 60


def daily_usage(days: int = SUMMARY_DAYS) -> list[dict]:
    """Сводка по дням записи (по Москве) — независимо от того, сохранился ли диалог."""
    since = timezone.now() - datetime.timedelta(days=days)
    rows = (
        LLMUsage.objects.filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at", tzinfo=MOSCOW)).values("day").order_by("-day")
        .annotate(
            attempts=Count("id"), requests=Count("request_id", distinct=True),
            dialogs=Count("conversation", distinct=True),
            input=Sum("input_tokens"), output=Sum("output_tokens"), read=Sum("cache_read_tokens"),
            write=Sum("cache_write_5m_tokens") + Sum("cache_write_1h_tokens"), cost=Sum("cost_usd"),
            fallback=Count("id", filter=Q(attempt_type=LLMUsage.AttemptType.FALLBACK)),
        )
    )
    summary = []
    for row in rows:
        prompt = (row["input"] or 0) + (row["read"] or 0) + (row["write"] or 0)
        summary.append({
            **row,
            "cost": row["cost"] or Decimal(0),
            "cache_share": (row["read"] or 0) / prompt if prompt else 0,
            "fallback_share": row["fallback"] / row["attempts"] if row["attempts"] else 0,
            "per_dialog": (row["cost"] or Decimal(0)) / row["dialogs"] if row["dialogs"] else None,
        })
    return summary


@admin.register(LLMUsage)
class LLMUsageAdmin(ReadOnlyMixin, admin.ModelAdmin):
    """Сводка по дням сверху, ниже — попытки запросов (при фолбэке их две на запрос)."""

    change_list_template = "admin/chat/llmusage/change_list.html"
    list_display = ["created_at", "dialog", "model", "attempt_type", "input_tokens", "cache_read_tokens",
                    "output_tokens", "cost_usd", "stop_reason"]
    list_filter = ["attempt_type", "model"]
    date_hierarchy = "created_at"
    list_per_page = 50

    @admin.display(description="Диалог")
    def dialog(self, obj):
        if not obj.conversation_id:
            return "—"
        return format_html('<a href="{}">#{}</a>',
                           reverse("admin:chat_conversation_change", args=[obj.conversation_id]), obj.conversation_id)

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, {**(extra_context or {}), "daily": daily_usage(),
                                                 "summary_days": SUMMARY_DAYS})
