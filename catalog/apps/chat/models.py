import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Текущий"
        CLOSED = "closed", "Закрыт"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="conversations",
        verbose_name="Пользователь",
    )
    session = models.ForeignKey(
        "accounts.AnonSession", null=True, blank=True, on_delete=models.CASCADE, related_name="conversations",
        verbose_name="Гостевая сессия",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, verbose_name="Статус")
    # Отпечаток «модель + промпт + версия базы знаний + инструменты»; пишет api (ТЗ §7.4)
    prompt_epoch = models.CharField(max_length=64, blank=True, default="", verbose_name="Эпоха промпта")
    pending_lead_topic = models.TextField(blank=True, default="", verbose_name="Пометка агента для заявки")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Создан")
    # Время последнего сообщения. Не auto_now: смена статуса или владельца его не трогает
    last_message_at = models.DateTimeField(default=timezone.now, verbose_name="Последнее сообщение")

    class Meta:
        verbose_name = "Диалог"
        verbose_name_plural = "Диалоги"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | models.Q(session__isnull=False),
                name="conversation_user_or_session",
            ),
            # У владельца ровно один текущий диалог (ТЗ §1.5)
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="active", user__isnull=False),
                name="conversation_one_active_per_user",
            ),
            models.UniqueConstraint(
                fields=["session"],
                condition=models.Q(status="active", session__isnull=False),
                name="conversation_one_active_per_session",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "-last_message_at"], name="chat_conv_user_last_idx"),
            models.Index(fields=["session", "-last_message_at"], name="chat_conv_session_last_idx"),
        ]

    def __str__(self):
        return f"Диалог #{self.pk}"


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Клиент"
        ASSISTANT = "assistant", "Консультант"
        TOOL = "tool", "Инструмент"
        EVENT = "event", "Событие"

    class EventType(models.TextChoices):
        ORDER_CREATED = "order_created", "Заказ оформлен"
        ORDER_CONFIRMED = "order_confirmed", "Заказ подтверждён"
        ORDER_PAID = "order_paid", "Заказ оплачен"
        ORDER_CANCELLED = "order_cancelled", "Заказ отменён"
        LEAD_CREATED = "lead_created", "Заявка создана"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=Role.choices, verbose_name="Роль")
    # Текст для человека: исходный текст клиента, итоговый текст агента, описание события
    content = models.TextField(blank=True, default="", verbose_name="Текст")
    # Точные блоки, отправленные в модель или полученные от неё (ТЗ §5.1)
    api_content = models.JSONField(null=True, blank=True, verbose_name="Блоки для модели")
    ui = models.JSONField(null=True, blank=True, verbose_name="Виджеты")
    event_type = models.CharField(max_length=30, choices=EventType.choices, blank=True, default="", verbose_name="Событие")
    event_payload = models.JSONField(null=True, blank=True, verbose_name="Данные события")
    client_message_id = models.UUIDField(null=True, blank=True, verbose_name="ID реплики на клиенте")
    epoch = models.CharField(max_length=64, blank=True, default="")
    stop_reason = models.CharField(max_length=30, blank=True, default="")
    request_id = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Создано")

    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "client_message_id"],
                name="chat_message_client_id_unique",
            ),
        ]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"


class KnowledgeBaseVersion(models.Model):
    class Source(models.TextChoices):
        ADMIN = "admin", "Админка"
        UPLOAD = "upload", "Загрузка файла"
        IMPORT = "import", "Импорт"

    content = models.TextField(verbose_name="Текст")
    comment = models.CharField(max_length=255, blank=True, default="", verbose_name="Комментарий")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.ADMIN, verbose_name="Источник")
    is_active = models.BooleanField(default=False, verbose_name="Активна")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Автор"
    )
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Создана")
    char_count = models.PositiveIntegerField(default=0, verbose_name="Символов")
    # Считает api через эндпоинт подсчёта токенов (ТЗ §9)
    token_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Токенов")

    class Meta:
        verbose_name = "Версия базы знаний"
        verbose_name_plural = "База знаний"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="chat_kb_single_active",
            ),
        ]

    def __str__(self):
        return f"Версия #{self.pk}"


class Quote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=50, unique=True, verbose_name="Номер КП")
    conversation = models.ForeignKey(
        Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes", verbose_name="Диалог"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes",
        verbose_name="Пользователь",
    )
    session = models.ForeignKey(
        "accounts.AnonSession", null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes",
        verbose_name="Гостевая сессия",
    )
    # Снапшот: товар, название, цена в рублях, исходная цена и валюта, курс, количество, сумма
    items = models.JSONField(default=list, verbose_name="Позиции")
    total = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Итог, ₽")
    valid_until = models.DateField(verbose_name="Действует до")
    pdf_path = models.CharField(max_length=255, blank=True, default="", verbose_name="Путь к PDF")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Создано")

    class Meta:
        verbose_name = "КП"
        verbose_name_plural = "КП"
        ordering = ["-created_at"]

    def __str__(self):
        return f"КП {self.number}"


class LLMUsage(models.Model):
    """Одна строка на попытку из usage.iterations (ТЗ §7.9)."""

    class AttemptType(models.TextChoices):
        MESSAGE = "message", "Основная модель"
        FALLBACK = "fallback_message", "Резервная модель"

    conversation = models.ForeignKey(
        Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="llm_usage", verbose_name="Диалог"
    )
    request_id = models.CharField(max_length=100, blank=True, default="", verbose_name="ID запроса")
    configured_model = models.CharField(max_length=100, verbose_name="Модель в настройках")
    model = models.CharField(max_length=100, verbose_name="Модель попытки")
    attempt_type = models.CharField(
        max_length=20, choices=AttemptType.choices, default=AttemptType.MESSAGE, verbose_name="Попытка"
    )
    input_tokens = models.PositiveIntegerField(default=0, verbose_name="Ввод")
    cache_write_5m_tokens = models.PositiveIntegerField(default=0, verbose_name="Запись в кэш, 5 мин")
    cache_write_1h_tokens = models.PositiveIntegerField(default=0, verbose_name="Запись в кэш, 1 ч")
    cache_read_tokens = models.PositiveIntegerField(default=0, verbose_name="Чтение из кэша")
    output_tokens = models.PositiveIntegerField(default=0, verbose_name="Вывод")
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True, verbose_name="Стоимость, $")
    latency_ms = models.PositiveIntegerField(null=True, blank=True, verbose_name="Время ответа, мс")
    stop_reason = models.CharField(max_length=30, blank=True, default="", verbose_name="Причина остановки")
    created_at = models.DateTimeField(default=timezone.now, db_index=True, verbose_name="Время")

    class Meta:
        verbose_name = "Расход модели"
        verbose_name_plural = "Расход модели"

    def __str__(self):
        return f"{self.model}: {self.cost_usd}"
