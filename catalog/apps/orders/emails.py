import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_order_email(order):
    """Отправить уведомление о заказе клиенту и менеджеру."""
    items = order.items.select_related("product").all()

    context = {
        "order": order,
        "items": items,
        "manager_email": settings.MANAGER_EMAIL,
    }

    html_body = render_to_string("orders/email_order.html", context)
    text_body = render_to_string("orders/email_order.txt", context)

    subject = f"Заказ №{order.number} — Ai-Dent"

    recipients = []
    if order.contact_email:
        recipients.append(order.contact_email)
    if settings.MANAGER_EMAIL:
        recipients.append(settings.MANAGER_EMAIL)

    if not recipients:
        logger.warning("Заказ %s: нет получателей для письма", order.number)
        return

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    msg.attach_alternative(html_body, "text/html")

    try:
        msg.send()
        logger.info("Заказ %s: письмо отправлено %s", order.number, recipients)
    except Exception as e:
        logger.error("Заказ %s: ошибка отправки письма: %s", order.number, e)
