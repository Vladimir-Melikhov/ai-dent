"""Уведомления о смене статуса заказа.

Telegram — тип, номер, статус и ссылка, без ПДн. Письмо внутри компании — администратору и рассылке
одним письмом; клиенту — отдельное письмо. При подтверждении к обоим прикладывается PDF счёта.
Всё отправляется после коммита; сбой отправки не откатывает заказ.
"""

import logging

from django.conf import settings
from django.utils import timezone

from apps.leads.notifications import admin_link, internal_recipients
from .invoice import buyer_lines, invoice_items, invoice_lines
from .letters import letter, rows
from .models import Invoice, Order
from .services import invoice_path, invoice_url, rub_text
from .telegram import send_telegram

logger = logging.getLogger(__name__)

STATUS_WORD = {
    Order.Status.CONFIRMED: "подтверждён",
    Order.Status.PAID: "оплачен",
    Order.Status.CANCELLED: "отменён",
}


def order_admin_link(order) -> str:
    return admin_link(f"orders/order/{order.pk}/change/")


def telegram_order(order, text: str) -> None:
    lines = [text]
    if settings.TELEGRAM_ADMIN_LINKS:
        lines += ["", order_admin_link(order)]
    send_telegram("\n".join(lines))


def item_lines(order) -> list[str]:
    return [f"- {i.product_name} × {i.quantity} — {rub_text(i.line_total)}" for i in invoice_items(order)]


def send_invoice_emails(order) -> bool:
    """Письма с PDF счёта: внутри компании и клиенту. True — все письма ушли; отметка emailed_at."""
    invoice = Invoice.objects.get(order=order)
    with open(invoice_path(invoice), "rb") as f:
        pdf = f.read()
    filename = f"schet-{order.number}.pdf"
    sent, failed = False, False
    # HTML-версия письма — в стиле счёта: шапка с номером, состав, итог, зуб на фоне
    html = {"order": order, "invoice": invoice, "issued": timezone.localtime(invoice.issued_at),
            "lines": invoice_lines(order), "total": rub_text(order.total_amount)}

    internal = internal_recipients()
    if internal:
        body = "\n".join([
            f"Заказ № {order.number} подтверждён, выставлен счёт {invoice.number}.",
            "",
            "Покупатель:", *buyer_lines(order),
            f"Город доставки: {order.delivery_city or '—'}",
            f"Адрес: {order.delivery_address or '—'}",
            f"Комментарий: {order.comment or '—'}",
            "",
            "Состав:", *item_lines(order),
            f"Итого: {rub_text(order.total_amount)}",
            "",
            f"Заказ в админке: {order_admin_link(order)}",
        ])
        message = letter(f"Счёт {invoice.number} — заказ № {order.number} подтверждён", body, internal,
                         "emails/invoice_internal.html", {
                             **html, "buyer": rows(buyer_lines(order)), "admin_url": order_admin_link(order),
                             "delivery": [("Город", order.delivery_city or "—"), ("Адрес", order.delivery_address or "—"),
                                          ("Комментарий", order.comment or "—")],
                         })
        message.attach(filename, pdf, "application/pdf")
        try:
            message.send()
            sent = True
        except Exception:
            failed = True
            logger.exception("Заказ %s: письмо со счётом внутри компании не отправлено", order.number)

    # Клиенту — отдельное письмо: внутренних адресов он не видит
    if order.contact_email:
        body = "\n".join([
            "Здравствуйте!",
            "",
            f"Ваш заказ № {order.number} подтверждён. Счёт — во вложении.",
            "",
            *item_lines(order),
            f"Итого: {rub_text(order.total_amount)}",
            "",
            f"Скачать счёт на сайте: {settings.SITE_ORIGIN}{invoice_url(order)}",
            "",
            "Ай-Дент · Оборудование для стоматологии",
        ])
        message = letter(f"Счёт по заказу № {order.number} — Ай-Дент", body, [order.contact_email],
                         "emails/invoice_client.html",
                         {**html, "download_url": f"{settings.SITE_ORIGIN}{invoice_url(order)}"})
        message.attach(filename, pdf, "application/pdf")
        try:
            message.send()
            sent = True
        except Exception:
            failed = True
            logger.exception("Заказ %s: письмо со счётом клиенту не отправлено", order.number)

    if failed:
        telegram_order(order, f"Письмо со счётом по заказу № {order.number} не отправлено")
        return False
    if not sent:
        logger.warning("Заказ %s: нет адресов для письма со счётом", order.number)
        return False
    Invoice.objects.filter(pk=invoice.pk).update(emailed_at=timezone.now())
    return True


def notify_status(order_id, status) -> None:
    order = Order.objects.get(pk=order_id)
    telegram_order(order, f"Заказ № {order.number}: {STATUS_WORD[status]}")
    if status == Order.Status.CONFIRMED:
        try:
            send_invoice_emails(order)
        except Exception:
            logger.exception("Заказ %s: письма со счётом не отправлены", order.number)
        return

    recipients = internal_recipients()
    if not recipients:
        return
    body = "\n".join([
        f"Заказ № {order.number}: {STATUS_WORD[status]}.",
        f"Сумма: {rub_text(order.total_amount or 0)}",
        "",
        f"Заказ в админке: {order_admin_link(order)}",
    ])
    try:
        letter(f"Заказ № {order.number} {STATUS_WORD[status]} — Ай-Дент", body, recipients, "emails/order_status.html", {
            "order": order, "word": STATUS_WORD[status], "now": timezone.localtime(), "admin_url": order_admin_link(order),
            "rows": [("Сумма", rub_text(order.total_amount or 0)), ("Статус", STATUS_WORD[status])],
        }).send()
    except Exception:
        logger.exception("Заказ %s: письмо о смене статуса не отправлено", order.number)
