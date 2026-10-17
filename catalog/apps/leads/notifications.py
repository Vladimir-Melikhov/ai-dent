"""Уведомления о заявке (ТЗ §10.6): в Telegram и письме внутри компании — полные данные заявки.

Решение владельца 07.10.2026: менеджеру сразу видно, кому звонить. Telegram — зарубежный
сервис; это учитывается в юридической оценке (Q3). Уведомления о заказах — по-прежнему без ПДн.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage

from apps.orders.telegram import send_telegram

logger = logging.getLogger(__name__)


def admin_link(path: str) -> str:
    return f"{settings.SITE_ORIGIN}/{settings.ADMIN_URL}{path}"


def internal_recipients() -> list[str]:
    return [a for a in [settings.MANAGER_EMAIL, *settings.NOTIFY_EMAILS] if a]


def lead_lines(lead) -> list[str]:
    return [
        f"Имя: {lead.name or '—'}",
        f"Телефон: {lead.phone}",
        f"Город: {lead.city or '—'}",
        f"Суть запроса: {lead.topic or '—'}",
    ]


def notify_lead(lead) -> None:
    link = admin_link(f"leads/lead/{lead.pk}/change/")
    telegram = [f"Новая заявка № {lead.number}", *lead_lines(lead)]
    # Q6: ссылка в админку раскрывает её путь стороннему сервису — отключается настройкой; идёт последней
    if settings.TELEGRAM_ADMIN_LINKS:
        telegram += ["", link]
    send_telegram("\n".join(telegram))

    recipients = internal_recipients()
    if not recipients:
        logger.warning("Заявка %s: нет получателей для письма", lead.number)
        return
    body = "\n".join([
        f"Заявка № {lead.number} ({lead.get_source_display()})",
        *lead_lines(lead),
        "",
        f"Заявка в админке: {link}",
    ])
    try:
        EmailMessage(f"Заявка № {lead.number} — Ai-Dent", body, settings.DEFAULT_FROM_EMAIL, recipients).send()
    except Exception:
        logger.exception("Заявка %s: ошибка отправки письма", lead.number)
