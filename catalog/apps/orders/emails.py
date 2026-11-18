import logging

from django.conf import settings
from django.template.loader import render_to_string

from .invoice import buyer_lines, invoice_lines
from .letters import letter, rows
from .services import rub_text

logger = logging.getLogger(__name__)


def order_buyer_rows(order) -> list[tuple[str, str]]:
    """Карточка «Покупатель» в письме о заказе: контакты, способ оплаты, реквизиты (текст из формы)."""
    result = rows(buyer_lines(order))
    if order.payment_method:
        result.append(("Способ оплаты", order.get_payment_method_display()))
    if order.requisites:
        requisites = order.requisites
        if isinstance(requisites, dict):
            requisites = "\n".join(f"{k}: {v}" for k, v in requisites.items())
        result.append(("Реквизиты", str(requisites)))
    return result


def send_order_email(order):
    """Отправить уведомление о заказе клиенту и менеджеру."""
    items = order.items.select_related("product").all()

    text_body = render_to_string("orders/email_order.txt", {
        "order": order, "items": items, "manager_email": settings.MANAGER_EMAIL,
    })
    subject = f"Заказ №{order.number} — Ай-Дент"

    recipients = []
    if order.contact_email:
        recipients.append(order.contact_email)
    if settings.MANAGER_EMAIL:
        recipients.append(settings.MANAGER_EMAIL)

    if not recipients:
        logger.warning("Заказ %s: нет получателей для письма", order.number)
        return

    msg = letter(subject, text_body, recipients, "orders/email_order.html", {
        "order": order, "buyer": order_buyer_rows(order), "lines": invoice_lines(order),
        "total": rub_text(order.total_amount or 0),
    })

    try:
        msg.send()
        logger.info("Заказ %s: письмо отправлено %s", order.number, recipients)
    except Exception as e:
        logger.error("Заказ %s: ошибка отправки письма: %s", order.number, e)
