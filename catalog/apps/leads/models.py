from django.conf import settings
from django.db import models


class Lead(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "Новая"
        IN_PROGRESS = "in_progress", "В работе"
        CLOSED = "closed", "Закрыта"

    class Source(models.TextChoices):
        AI = "ai", "ИИ-консультант"
        FALLBACK = "fallback", "Кнопка при сбое"

    number = models.CharField(max_length=50, unique=True, verbose_name="Номер")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW, verbose_name="Статус")
    phone = models.CharField(max_length=30, verbose_name="Телефон")
    name = models.CharField(max_length=255, blank=True, default="", verbose_name="Имя")
    city = models.CharField(max_length=255, blank=True, default="", verbose_name="Город")
    topic = models.TextField(blank=True, default="", verbose_name="Пометка агента")
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.AI, verbose_name="Источник")
    conversation = models.ForeignKey(
        "chat.Conversation", null=True, blank=True, on_delete=models.SET_NULL, related_name="leads",
        verbose_name="Диалог",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads",
        verbose_name="Пользователь",
    )
    session = models.ForeignKey(
        "accounts.AnonSession", null=True, blank=True, on_delete=models.SET_NULL, related_name="leads",
        verbose_name="Гостевая сессия",
    )
    pd_consent_at = models.DateTimeField(verbose_name="Согласие на обработку ПДн")
    manager_note = models.TextField(blank=True, default="", verbose_name="Заметка менеджера")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создана")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлена")

    class Meta:
        verbose_name = "Заявка"
        verbose_name_plural = "Заявки"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Заявка №{self.number}"
