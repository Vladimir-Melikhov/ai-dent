"""PDF коммерческого предложения: позиции и цены из снапшота, без реквизитов. Скидка, если есть, видна в строках и в итоге."""

import io
import os
from decimal import Decimal

from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from apps.orders.pdf import (
    FAINT, INK, LEFT, MUTED, PAGE_H, RIGHT, SELLER, _fonts, discount_percent, discount_summary, glass, page_footer,
    page_header, paint_page, percent_text, spaced, totals_block, totals_block_height, items_table, wrap_text,
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


def generate_quote_pdf(quote) -> str:
    """Рисует PDF и сохраняет в media/quotes/; Caddy этот каталог напрямую не отдаёт."""
    doc = quote_document(quote)
    fonts = _fonts()
    reg, bold = fonts
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    footer_text = f"Действует до {quote.valid_until:%d.%m.%Y}"

    def new_page():
        page_footer(c, fonts, footer_text)
        c.showPage()
        paint_page(c)
        return PAGE_H - 26 * mm

    paint_page(c)
    page_header(c, fonts, "Коммерческое предложение", f"№ {quote.number}")

    # ─── Даты и для кого ───
    y = PAGE_H - 50 * mm
    box_h = 19 * mm
    glass(c, LEFT, y - box_h, RIGHT - LEFT, box_h)
    cells = [("Дата", f"{quote.created_at:%d.%m.%Y}"), ("Действует до", f"{quote.valid_until:%d.%m.%Y}")]
    if doc["client"]:
        cells.append(("Для", doc["client"]))
    x = LEFT + 7 * mm
    for i, (caption, value) in enumerate(cells):
        spaced(c, x, y - 6.5 * mm, caption, reg, 7, MUTED)
        c.setFont(bold, 11)
        c.setFillColor(INK)
        room = RIGHT - x - 7 * mm
        c.drawString(x, y - 13 * mm, wrap_text(c, value, bold, 11, room if i == 2 else 60 * mm, 1)[0])
        x += 42 * mm

    # ─── Позиции ───
    y = items_table(c, fonts, doc["lines"], y - box_h - 12 * mm, 50 * mm + totals_block_height(doc["discount"]) * 0, new_page)

    # ─── Итого ───
    if y - totals_block_height(doc["discount"]) < 40 * mm:
        y = new_page()
    y = totals_block(c, fonts, y + 2 * mm, doc["total"], doc["discount"])

    y -= 10 * mm
    c.setFont(reg, 8.5)
    c.setFillColor(MUTED)
    for note in doc["notes"]:
        for line in wrap_text(c, note, reg, 8.5, RIGHT - LEFT):
            if y < 30 * mm:
                y = new_page()
                c.setFont(reg, 8.5)
                c.setFillColor(MUTED)
            c.drawString(LEFT, y, line)
            y -= 4.6 * mm
        y -= 0.8 * mm

    page_footer(c, fonts, footer_text)
    c.showPage()
    c.save()

    rel_path = f"{QUOTES_DIR}/kp-{quote.id}.pdf"
    abs_dir = os.path.join(settings.MEDIA_ROOT, QUOTES_DIR)
    os.makedirs(abs_dir, exist_ok=True)
    with open(os.path.join(settings.MEDIA_ROOT, rel_path), "wb") as f:
        f.write(buf.getvalue())
    return rel_path
