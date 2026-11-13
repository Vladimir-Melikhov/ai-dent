"""PDF коммерческого предложения: позиции и цены из снапшота, без реквизитов. Скидка, если есть, видна в строках и в итоге."""

import io
import os
from decimal import Decimal

from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from apps.orders.pdf import (
    ACCENT, CARD, DISCOUNT, INK, LINE, MUTED, SELLER, _draw_gradient_box, _draw_wrapped_text, _fonts,
    discount_percent, discount_summary, percent_text,
)
from apps.orders.services import rub_text

QUOTES_DIR = "quotes"
TERMS = [
    "Коммерческое предложение не является счётом и не содержит реквизитов для оплаты.",
    "Цены указаны в рублях с НДС, по каталогу сайта на дату предложения.",
    "Окончательная стоимость фиксируется в счёте после подтверждения заказа менеджером.",
]
CURRENCY_NOTE = "Цены товаров в иностранной валюте пересчитаны по курсу ЦБ РФ на дату КП."
DISCOUNT_NOTE = "Цены в таблице указаны с учётом скидки; цена по прайсу показана для сравнения."


def rub(value) -> str:
    # Копейки печатаются, если они есть: у валютных товаров цена в рублях — с копейками
    return rub_text(value)


def item_list_price(item):
    """Цена по прайсу до скидки или None, если скидки по позиции нет."""
    listed = item.get("list_price_rub")
    if listed is None:
        return None
    return listed if Decimal(str(listed)) > Decimal(str(item["unit_price_rub"])) else None


def quote_document(quote) -> dict:
    """Содержимое PDF отдельно от отрисовки — его проверяют тесты."""
    notes = list(TERMS)
    if any(item.get("source_currency") not in (None, "", "RUB") for item in quote.items):
        notes.insert(1, CURRENCY_NOTE)
    lines = []
    for item in quote.items:
        listed = item_list_price(item)
        pct = discount_percent(listed, item["unit_price_rub"]) if listed is not None else None
        lines.append({
            "name": item["name"], "quantity": str(item["quantity"]), "price": rub(item["unit_price_rub"]),
            "total": rub(item["line_total_rub"]),
            "list_price": rub(listed) if listed is not None else None,
            "discount": f"−{percent_text(pct)}" if pct is not None else None,
        })
    summary = discount_summary([
        (item_list_price(item), Decimal(str(item["unit_price_rub"])), int(item["quantity"])) for item in quote.items
    ])
    doc = {
        "brand": SELLER["brand"],
        "tagline": SELLER["tagline"],
        "title": f"КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ № {quote.number}",
        "dates": f"от {quote.created_at:%d.%m.%Y} · действует до {quote.valid_until:%d.%m.%Y}",
        "client": quote.client_name or "",
        "lines": lines,
        "rows": [(l["name"], l["quantity"], l["price"], l["total"]) for l in lines],
        "total": rub(quote.total),
        "discount": None,
        "notes": notes,
    }
    if summary:
        full, saved, pct = summary
        doc["discount"] = {"full": rub(full), "saved": "−" + rub(saved), "percent": percent_text(pct)}
        doc["notes"].insert(0, DISCOUNT_NOTE)
    return doc


def _wrap(c, text, font, size, width, max_lines=2):
    """Название в одну-две строки по ширине колонки."""
    lines, current = [], ""
    for word in str(text).split():
        test = f"{current} {word}".strip()
        if c.stringWidth(test, font, size) <= width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and c.stringWidth(lines[-1] + "…", font, size) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines or [""]


def generate_quote_pdf(quote) -> str:
    """Рисует PDF и сохраняет в media/quotes/; Caddy этот каталог напрямую не отдаёт."""
    doc = quote_document(quote)
    has_discount = any(l["discount"] for l in doc["lines"])
    reg, bold = _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    left, right = 22 * mm, w - 22 * mm
    # колонки таблицы: у позиций со скидкой появляется своя колонка
    qty_x = 116 * mm
    price_r = 146 * mm if has_discount else 158 * mm
    disc_r = 164 * mm
    name_w = (qty_x - left) - 6 * mm

    def footer():
        c.setStrokeColor(LINE)
        c.setLineWidth(0.7)
        c.line(left, 27 * mm, right, 27 * mm)
        c.setFont(reg, 7.5)
        c.setFillColor(MUTED)
        c.drawString(left, 21 * mm, f"Действует до {quote.valid_until:%d.%m.%Y}")
        c.drawRightString(right, 21 * mm, f"{SELLER['brand']} · {SELLER['tagline']}")

    # ─── Шапка ───
    c.setFillColor(ACCENT)
    c.rect(0, h - 6 * mm, w, 6 * mm, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont(bold, 24)
    c.drawString(left, h - 26 * mm, doc["brand"])
    c.setFont(reg, 9)
    c.setFillColor(MUTED)
    c.drawString(left, h - 32 * mm, doc["tagline"].upper())
    c.setFont(reg, 8)
    c.drawRightString(right, h - 22 * mm, "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ")
    c.setFont(bold, 15)
    c.setFillColor(INK)
    c.drawRightString(right, h - 29 * mm, f"№ {quote.number}")
    c.setStrokeColor(INK)
    c.setLineWidth(1.5)
    c.line(left, h - 38 * mm, right, h - 38 * mm)

    # ─── Для кого и сроки ───
    y = h - 48 * mm
    box_h = 18 * mm
    _draw_gradient_box(c, left, y - box_h + 4 * mm, right - left, box_h)
    cells = [("ДАТА", f"{quote.created_at:%d.%m.%Y}"), ("ДЕЙСТВУЕТ ДО", f"{quote.valid_until:%d.%m.%Y}")]
    if doc["client"]:
        cells.append(("ДЛЯ", doc["client"]))
    x = left + 6 * mm
    for i, (caption, value) in enumerate(cells):
        c.setFont(reg, 7.5)
        c.setFillColor(MUTED)
        c.drawString(x, y - 2 * mm, caption)
        c.setFont(bold, 11)
        c.setFillColor(INK)
        shown = value if i < 2 else _wrap(c, value, bold, 11, right - x - 6 * mm, 1)[0]
        c.drawString(x, y - 8.5 * mm, shown)
        x += 38 * mm

    # ─── Таблица ───
    y -= box_h + 8 * mm
    c.setFillColor(CARD)
    c.roundRect(left, y - 3 * mm, right - left, 9 * mm, 2 * mm, stroke=0, fill=1)

    def table_head(at):
        c.setFont(reg, 7.5)
        c.setFillColor(MUTED)
        c.drawString(left + 3 * mm, at, "НАИМЕНОВАНИЕ")
        c.drawString(qty_x, at, "КОЛ-ВО")
        c.drawRightString(price_r, at, "ЦЕНА")
        if has_discount:
            c.drawRightString(disc_r, at, "СКИДКА")
        c.drawRightString(right - 3 * mm, at, "СУММА")

    table_head(y)
    y -= 11 * mm
    for line in doc["lines"]:
        name_lines = _wrap(c, line["name"], reg, 9.5, name_w - 3 * mm)
        height = max(len(name_lines) * 4.6 * mm, 4.6 * mm) + (4 * mm if line["list_price"] else 0)
        if y - height < 58 * mm:
            footer()
            c.showPage()
            c.setFillColor(ACCENT)
            c.rect(0, h - 6 * mm, w, 6 * mm, stroke=0, fill=1)
            y = h - 24 * mm
            table_head(y)
            y -= 10 * mm
        top = y
        c.setFont(reg, 9.5)
        c.setFillColor(INK)
        for text in name_lines:
            c.drawString(left + 3 * mm, y, text)
            y -= 4.6 * mm
        c.drawString(qty_x, top, line["quantity"])
        c.setFont(bold if line["discount"] else reg, 9.5)
        c.drawRightString(price_r, top, line["price"])
        c.setFont(bold, 9.5)
        c.drawRightString(right - 3 * mm, top, line["total"])
        if line["list_price"]:
            # цена по прайсу под ценой, зачёркнутая
            c.setFont(reg, 8)
            c.setFillColor(MUTED)
            c.drawRightString(price_r, top - 4.4 * mm, line["list_price"])
            width = c.stringWidth(line["list_price"], reg, 8)
            c.setStrokeColor(MUTED)
            c.setLineWidth(0.5)
            c.line(price_r - width, top - 3.4 * mm, price_r, top - 3.4 * mm)
            c.setFont(bold, 9.5)
            c.setFillColor(DISCOUNT)
            c.drawRightString(disc_r, top, line["discount"])
            y = min(y, top - 4.4 * mm - 3 * mm)
        y -= 1.6 * mm
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(left, y, right, y)
        y -= 5.5 * mm

    # ─── Итого ───
    need = 34 * mm if doc["discount"] else 20 * mm
    if y - need < 58 * mm:
        footer()
        c.showPage()
        c.setFillColor(ACCENT)
        c.rect(0, h - 6 * mm, w, 6 * mm, stroke=0, fill=1)
        y = h - 24 * mm
    y -= 1 * mm
    if doc["discount"]:
        c.setFont(reg, 9.5)
        c.setFillColor(MUTED)
        c.drawString(qty_x, y, "Сумма по прайсу")
        c.drawRightString(right - 3 * mm, y, doc["discount"]["full"])
        y -= 6 * mm
        c.setFillColor(DISCOUNT)
        c.drawString(qty_x, y, f"Скидка {doc['discount']['percent']}")
        c.drawRightString(right - 3 * mm, y, doc["discount"]["saved"])
        y -= 3 * mm
        c.setStrokeColor(LINE)
        c.line(qty_x, y, right, y)
        y -= 7 * mm
    c.setFont(bold, 14)
    c.setFillColor(INK)
    c.drawString(qty_x, y, "Итого:")
    c.drawRightString(right - 3 * mm, y, doc["total"])

    y -= 14 * mm
    for note in doc["notes"]:
        y = _draw_wrapped_text(c, note, left, y, right - left, reg, 8.5, MUTED, 4.5 * mm) - 1 * mm

    footer()
    c.showPage()
    c.save()

    rel_path = f"{QUOTES_DIR}/kp-{quote.id}.pdf"
    abs_dir = os.path.join(settings.MEDIA_ROOT, QUOTES_DIR)
    os.makedirs(abs_dir, exist_ok=True)
    with open(os.path.join(settings.MEDIA_ROOT, rel_path), "wb") as f:
        f.write(buf.getvalue())
    return rel_path
