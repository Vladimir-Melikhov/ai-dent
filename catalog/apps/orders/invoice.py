"""PDF счёта: выставляется при подтверждении заказа, по составу на этот момент."""

import io
import os

from django.conf import settings
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .models import Order, OrderItem
from .pdf import INK, LINE, MUTED, SELLER, _draw_gradient_box, _draw_wrapped_text, _fonts
from .services import rub_text

INVOICES_DIR = "invoices"
CONFIRMED_TEXT = "Заказ подтверждён. Оплатите счёт по реквизитам выше."
FOOTER_TEXT = "Счёт выставлен после подтверждения заказа менеджером."
CURRENCY_NOTE = "Цены товаров в иностранной валюте пересчитаны в рубли по курсу ЦБ РФ на дату счёта."
# Правый край колонки «Цена»: суммы с копейками до миллионов не наезжают на «Кол-во» и «Сумму»
PRICE_RIGHT = 158 * mm


def invoice_items(order) -> list[OrderItem]:
    """Товары в порядке оформления, строка доставки — последней перед итогом."""
    return sorted(order.items.all(), key=lambda item: (item.kind == OrderItem.Kind.DELIVERY, item.pk))


def buyer_lines(order) -> list[str]:
    """Блок «Покупатель» по типу; заказ с /checkout/ — как раньше: имя и реквизиты отдельно."""
    lines = []
    if order.buyer_type == Order.BuyerType.LEGAL:
        lines += [order.company_name, f"ИНН: {order.inn}", f"Контактное лицо: {order.contact_name}"]
    elif order.buyer_type == Order.BuyerType.IP:
        lines += [f"ИП {order.buyer_name}", f"ИНН: {order.inn}"]
    elif order.buyer_type == Order.BuyerType.INDIVIDUAL:
        lines.append(order.buyer_name)
    elif order.contact_name:
        lines.append(order.contact_name)
    lines.append(f"Телефон: {order.contact_phone}")
    if order.contact_email:
        lines.append(f"Email: {order.contact_email}")
    return lines


def invoice_notes(order) -> list[str]:
    foreign = any(item.source_currency not in ("", "RUB") for item in order.items.all())
    return [CURRENCY_NOTE] if foreign else []


def generate_invoice_pdf(order, issued_at) -> str:
    """Рисует счёт и сохраняет в media/invoices/; запись Invoice создаёт сервис подтверждения."""
    reg, bold = _fonts()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    w, h = A4

    left = 22 * mm
    right = w - 22 * mm
    content_width = right - left

    # ─── Шапка ───

    c.setFillColor(INK)
    c.setFont(bold, 24)
    c.drawString(
        left,
        h - 28 * mm,
        SELLER["brand"],
    )

    c.setFont(reg, 9)
    c.setFillColor(MUTED)
    c.drawString(
        left,
        h - 34 * mm,
        SELLER["tagline"].upper(),
    )

    c.setFont(bold, 11)
    c.setFillColor(INK)
    c.drawRightString(
        right,
        h - 26 * mm,
        f"СЧЁТ № {order.number}",
    )

    c.setFont(reg, 9)
    c.setFillColor(MUTED)
    c.drawRightString(
        right,
        h - 32 * mm,
        f"от {timezone.localtime(issued_at):%d.%m.%Y}",
    )

    # линия под шапкой

    c.setStrokeColor(INK)
    c.setLineWidth(1.5)
    c.line(
        left,
        h - 40 * mm,
        right,
        h - 40 * mm,
    )

    # ─── Продавец ───

    y = h - 52 * mm

    c.setFont(reg, 8)
    c.setFillColor(MUTED)
    c.drawString(
        left,
        y,
        "ПОСТАВЩИК",
    )

    y -= 6 * mm

    y = _draw_wrapped_text(
        c,
        SELLER["name"],
        left,
        y,
        content_width,
        bold,
        10,
        INK,
        4.5 * mm,
    )

    y -= 1 * mm

    c.setFont(reg, 9)
    c.setFillColor(INK)
    c.drawString(
        left,
        y,
        f"ИНН: {SELLER['inn']}",
    )

    y -= 5 * mm

    c.drawString(
        left,
        y,
        f"Расчётный счёт: {SELLER['account']}",
    )

    # ─── Покупатель ───

    y -= 10 * mm

    c.setFont(reg, 8)
    c.setFillColor(MUTED)
    c.drawString(
        left,
        y,
        "ПОКУПАТЕЛЬ",
    )

    y -= 6 * mm

    for line in buyer_lines(order):
        y = _draw_wrapped_text(
            c,
            line,
            left,
            y,
            content_width,
            reg,
            10,
            INK,
            5.5 * mm,
        )

    if order.requisites and not order.buyer_type:
        y = _draw_wrapped_text(
            c,
            order.requisites[:150],
            left,
            y,
            content_width,
            reg,
            9,
            MUTED,
            4.5 * mm,
        )

    # ─── Таблица позиций ───

    y -= 7 * mm

    c.setFillColor(MUTED)
    c.setFont(reg, 8)

    c.drawString(
        left,
        y,
        "НАИМЕНОВАНИЕ",
    )

    c.drawString(
        120 * mm,
        y,
        "КОЛ-ВО",
    )

    c.drawRightString(
        PRICE_RIGHT,
        y,
        "ЦЕНА",
    )

    c.drawRightString(
        right,
        y,
        "СУММА",
    )

    y -= 3 * mm

    c.setStrokeColor(LINE)
    c.setLineWidth(1)

    c.line(
        left,
        y,
        right,
        y,
    )

    y -= 7 * mm

    c.setFont(reg, 9.5)

    for item in invoice_items(order):

        c.setFillColor(INK)

        name = (
            item.product_name
            if len(item.product_name) <= 52
            else item.product_name[:49] + "…"
        )

        c.drawString(
            left,
            y,
            name,
        )

        c.drawString(
            122 * mm,
            y,
            str(item.quantity),
        )

        c.drawRightString(
            PRICE_RIGHT,
            y,
            rub_text(item.unit_price),
        )

        c.drawRightString(
            right,
            y,
            rub_text(item.line_total),
        )

        y -= 5.5 * mm

        c.setStrokeColor(LINE)

        c.line(
            left,
            y,
            right,
            y,
        )

        y -= 5 * mm

        # Таблица закончилась слишком низко — продолжаем на новой странице

        if y < 70 * mm:
            c.showPage()

            y = h - 28 * mm

            c.setFont(bold, 13)
            c.setFillColor(INK)
            c.drawString(
                left,
                y,
                f"Счёт № {order.number}",
            )

            y -= 9 * mm

            c.setFont(reg, 8)
            c.setFillColor(MUTED)
            c.drawString(
                left,
                y,
                "ПРОДОЛЖЕНИЕ ПОЗИЦИЙ",
            )

            y -= 8 * mm

            c.setFont(reg, 9.5)

    # ─── Итого ───

    y -= 2 * mm

    c.setFont(bold, 14)
    c.setFillColor(INK)

    c.drawString(
        120 * mm,
        y,
        "Итого:",
    )

    c.drawRightString(
        right,
        y,
        rub_text(order.total_amount),
    )

    # ─── Пересчёт валюты ───

    for note in invoice_notes(order):
        y -= 7 * mm
        y = _draw_wrapped_text(
            c,
            note,
            left,
            y,
            content_width,
            reg,
            8.5,
            MUTED,
            4.5 * mm,
        )

    # ─── Реквизиты для оплаты ───

    y -= 11 * mm

    payment_height = 30 * mm
    payment_y = y - payment_height + 5 * mm

    # Если до футера не хватает места — переносим блок.

    if payment_y < 42 * mm:
        c.showPage()
        y = h - 35 * mm
        payment_y = y - payment_height

    _draw_gradient_box(
        c,
        left,
        payment_y,
        content_width,
        payment_height,
    )

    text_x = left + 7 * mm
    text_y = payment_y + payment_height - 8 * mm

    c.setFont(bold, 9)
    c.setFillColor(INK)
    c.drawString(
        text_x,
        text_y,
        "РЕКВИЗИТЫ ДЛЯ ОПЛАТЫ",
    )

    text_y -= 6 * mm

    c.setFont(reg, 8)
    c.setFillColor(MUTED)

    c.drawString(
        text_x,
        text_y,
        f"ИП: {SELLER['name']}",
    )

    text_y -= 4.5 * mm

    c.drawString(
        text_x,
        text_y,
        f"ИНН: {SELLER['inn']}",
    )

    text_y -= 4.5 * mm

    c.drawString(
        text_x,
        text_y,
        f"Расчётный счёт: {SELLER['account']}",
    )

    # ─── Спасибо за заказ ───

    thanks_y = payment_y - 24 * mm

    c.setFont(bold, 15)
    c.setFillColor(INK)

    c.drawString(
        left,
        thanks_y,
        "Спасибо за заказ.",
    )

    c.setFont(reg, 8.5)
    c.setFillColor(MUTED)

    c.drawString(
        left,
        thanks_y - 5.5 * mm,
        CONFIRMED_TEXT,
    )

    # ─── Футер ───

    c.setStrokeColor(LINE)
    c.setLineWidth(0.7)

    c.line(
        left,
        27 * mm,
        right,
        27 * mm,
    )

    c.setFont(reg, 7.5)
    c.setFillColor(MUTED)

    c.drawString(
        left,
        21 * mm,
        FOOTER_TEXT,
    )

    c.drawRightString(
        right,
        21 * mm,
        f"{SELLER['brand']} · {SELLER['tagline']}",
    )

    # ─── Сохранение ───

    c.showPage()
    c.save()

    buf.seek(0)

    rel_dir = INVOICES_DIR

    abs_dir = os.path.join(
        settings.MEDIA_ROOT,
        rel_dir,
    )

    os.makedirs(
        abs_dir,
        exist_ok=True,
    )

    filename = f"invoice-{order.number}.pdf"

    abs_path = os.path.join(
        abs_dir,
        filename,
    )

    with open(abs_path, "wb") as f:
        f.write(buf.read())

    return f"{rel_dir}/{filename}"
