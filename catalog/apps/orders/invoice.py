"""PDF счёта: выставляется при подтверждении заказа, по составу на этот момент."""

import io
import os

from django.conf import settings
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .models import Order, OrderItem
from .pdf import (
    INK, LEFT, MUTED, PAGE_H, RIGHT, SELLER, _fonts, discount_percent, discount_summary, glass, items_table,
    page_footer, page_header, paint_page, percent_text, spaced, totals_block, totals_block_height, wrap_text,
)
from .services import rub_text

INVOICES_DIR = "invoices"
CONFIRMED_TEXT = "Заказ подтверждён. Оплатите счёт по реквизитам выше."
FOOTER_TEXT = "Счёт выставлен после подтверждения заказа менеджером."
CURRENCY_NOTE = "Цены товаров в иностранной валюте пересчитаны в рубли по курсу ЦБ РФ на дату счёта."


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
    if order.contact_phone:
        lines.append(f"Телефон: {order.contact_phone}")
    if order.contact_email:
        lines.append(f"Email: {order.contact_email}")
    return lines


def invoice_notes(order) -> list[str]:
    foreign = any(item.source_currency not in ("", "RUB") for item in order.items.all())
    return [CURRENCY_NOTE] if foreign else []


def invoice_lines(order) -> list[dict]:
    lines = []
    for item in invoice_items(order):
        pct = discount_percent(item.list_unit_price, item.unit_price) if item.kind == OrderItem.Kind.PRODUCT else None
        lines.append({
            "name": item.product_name, "quantity": str(item.quantity), "price": rub_text(item.unit_price),
            "total": rub_text(item.line_total),
            "list_price": rub_text(item.list_unit_price) if pct is not None else None,
            "discount": f"−{percent_text(pct)}" if pct is not None else None,
        })
    return lines


def generate_invoice_pdf(order, issued_at) -> str:
    """Рисует счёт и сохраняет в media/invoices/; запись Invoice создаёт сервис подтверждения."""
    fonts = _fonts()
    reg, bold = fonts
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    def new_page():
        page_footer(c, fonts, FOOTER_TEXT)
        c.showPage()
        paint_page(c)
        return PAGE_H - 26 * mm

    paint_page(c)
    page_header(c, fonts, "Счёт на оплату", f"№ {order.number}", f"от {timezone.localtime(issued_at):%d.%m.%Y}")

    # ─── Поставщик и покупатель ───
    top = PAGE_H - 50 * mm
    gap = 6 * mm
    col_w = (RIGHT - LEFT - gap) / 2
    inner = col_w - 12 * mm
    blocks = [
        ("Поставщик", [(SELLER["name"], bold, 9.5), (f"ИНН: {SELLER['inn']}", reg, 9)]),
        ("Покупатель", [(line, bold if i == 0 else reg, 9.5 if i == 0 else 9) for i, line in enumerate(buyer_lines(order))]),
    ]
    if order.requisites and not order.buyer_type:
        blocks[1][1].append((str(order.requisites)[:150], reg, 8.5))
    laid = []
    for caption, rows in blocks:
        out = []
        for text, font, size in rows:
            for line in wrap_text(c, text, font, size, inner):
                out.append((line, font, size))
        laid.append((caption, out))
    box_h = 14 * mm + max(len(rows) for _, rows in laid) * 4.8 * mm
    for n, (caption, rows) in enumerate(laid):
        x = LEFT + n * (col_w + gap)
        glass(c, x, top - box_h, col_w, box_h)
        spaced(c, x + 6 * mm, top - 7 * mm, caption, reg, 7, MUTED)
        ty = top - 13.5 * mm
        for line, font, size in rows:
            c.setFont(font, size)
            c.setFillColor(INK)
            c.drawString(x + 6 * mm, ty, line)
            ty -= 4.8 * mm

    # ─── Позиции ───
    y = items_table(c, fonts, invoice_lines(order), top - box_h - 12 * mm, 40 * mm, new_page)

    # ─── Итого со скидкой ───
    summary = discount_summary(
        [(i.list_unit_price, i.unit_price, i.quantity) for i in invoice_items(order) if i.kind == OrderItem.Kind.PRODUCT]
    )
    discount = None
    if summary:
        full, saved, pct = summary
        discount = {"full": rub_text(full), "saved": "−" + rub_text(saved), "percent": percent_text(pct)}
    if y - totals_block_height(discount) < 40 * mm:
        y = new_page()
    y = totals_block(c, fonts, y + 2 * mm, rub_text(order.total_amount), discount)

    c.setFont(reg, 8.5)
    c.setFillColor(MUTED)
    for note in invoice_notes(order):
        y -= 7 * mm
        for line in wrap_text(c, note, reg, 8.5, RIGHT - LEFT):
            c.drawString(LEFT, y, line)
            y -= 4.6 * mm

    # ─── Реквизиты для оплаты ───
    pay_h = 42 * mm
    y -= 10 * mm
    if y - pay_h < 28 * mm:
        y = new_page()
    glass(c, LEFT, y - pay_h, RIGHT - LEFT, pay_h)
    spaced(c, LEFT + 7 * mm, y - 8 * mm, "Реквизиты для оплаты", bold, 8, INK)
    ty = y - 15 * mm
    c.setFont(reg, 8.5)
    c.setFillColor(MUTED)
    for text in (
        f"Получатель: {SELLER['name']}", f"ИНН: {SELLER['inn']} · Расчётный счёт: {SELLER['account']}",
        f"Банк: {SELLER['bank']} · БИК: {SELLER['bik']}", f"Корр. счёт: {SELLER['corr_account']}",
    ):
        c.drawString(LEFT + 7 * mm, ty, text)
        ty -= 5 * mm
    y -= pay_h + 12 * mm

    if y > 36 * mm:
        c.setFont(bold, 14)
        c.setFillColor(INK)
        c.drawString(LEFT, y, "Спасибо за заказ.")
        c.setFont(reg, 8.5)
        c.setFillColor(MUTED)
        c.drawString(LEFT, y - 5.5 * mm, CONFIRMED_TEXT)

    page_footer(c, fonts, FOOTER_TEXT)
    c.showPage()
    c.save()

    buf.seek(0)
    abs_dir = os.path.join(settings.MEDIA_ROOT, INVOICES_DIR)
    os.makedirs(abs_dir, exist_ok=True)
    filename = f"invoice-{order.number}.pdf"
    with open(os.path.join(abs_dir, filename), "wb") as f:
        f.write(buf.read())
    return f"{INVOICES_DIR}/{filename}"
