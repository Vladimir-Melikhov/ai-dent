"""PDF коммерческого предложения: позиции и цены из снапшота, без реквизитов и данных клиента."""

import io
import os

from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from apps.orders.pdf import INK, LINE, MUTED, SELLER, _draw_wrapped_text, _fonts
from apps.orders.services import rub_text

QUOTES_DIR = "quotes"
TERMS = [
    "Коммерческое предложение не является счётом и не содержит реквизитов для оплаты.",
    "Цены указаны в рублях с НДС, по каталогу сайта на дату предложения.",
    "Окончательная стоимость фиксируется в счёте после подтверждения заказа менеджером.",
]
CURRENCY_NOTE = "Цены товаров в иностранной валюте пересчитаны по курсу ЦБ РФ на дату КП."


def rub(value) -> str:
    # Копейки печатаются, если они есть: у валютных товаров цена в рублях — с копейками
    return rub_text(value)


def quote_document(quote) -> dict:
    """Содержимое PDF отдельно от отрисовки — его проверяют тесты."""
    notes = list(TERMS)
    if any(item.get("source_currency") not in (None, "", "RUB") for item in quote.items):
        notes.insert(1, CURRENCY_NOTE)
    return {
        "brand": SELLER["brand"],
        "tagline": SELLER["tagline"],
        "title": f"КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ № {quote.number}",
        "dates": f"от {quote.created_at:%d.%m.%Y} · действует до {quote.valid_until:%d.%m.%Y}",
        "rows": [
            (item["name"], str(item["quantity"]), rub(item["unit_price_rub"]), rub(item["line_total_rub"]))
            for item in quote.items
        ],
        "total": rub(quote.total),
        "notes": notes,
    }


def generate_quote_pdf(quote) -> str:
    """Рисует PDF и сохраняет в media/quotes/; Caddy этот каталог напрямую не отдаёт."""
    doc = quote_document(quote)
    reg, bold = _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    left, right = 22 * mm, w - 22 * mm

    c.setFillColor(INK)
    c.setFont(bold, 24)
    c.drawString(left, h - 28 * mm, doc["brand"])
    c.setFont(reg, 9)
    c.setFillColor(MUTED)
    c.drawString(left, h - 34 * mm, doc["tagline"].upper())
    c.setStrokeColor(INK)
    c.setLineWidth(1.5)
    c.line(left, h - 40 * mm, right, h - 40 * mm)

    y = h - 52 * mm
    c.setFillColor(INK)
    c.setFont(bold, 13)
    c.drawString(left, y, doc["title"])
    y -= 6 * mm
    c.setFont(reg, 9)
    c.setFillColor(MUTED)
    c.drawString(left, y, doc["dates"])

    y -= 12 * mm
    c.setFont(reg, 8)
    c.drawString(left, y, "НАИМЕНОВАНИЕ")
    c.drawString(120 * mm, y, "КОЛ-ВО")
    c.drawString(140 * mm, y, "ЦЕНА")
    c.drawRightString(right, y, "СУММА")
    y -= 3 * mm
    c.setStrokeColor(LINE)
    c.setLineWidth(1)
    c.line(left, y, right, y)
    y -= 7 * mm

    c.setFont(reg, 9.5)
    for name, quantity, price, total in doc["rows"]:
        c.setFillColor(INK)
        c.drawString(left, y, name if len(name) <= 52 else name[:49] + "…")
        c.drawString(122 * mm, y, quantity)
        c.drawString(140 * mm, y, price)
        c.drawRightString(right, y, total)
        y -= 5.5 * mm
        c.setStrokeColor(LINE)
        c.line(left, y, right, y)
        y -= 5 * mm
        if y < 70 * mm:
            c.showPage()
            y = h - 30 * mm
            c.setFont(reg, 9.5)

    y -= 2 * mm
    c.setFont(bold, 14)
    c.setFillColor(INK)
    c.drawString(120 * mm, y, "Итого:")
    c.drawRightString(right, y, doc["total"])

    y -= 14 * mm
    for note in doc["notes"]:
        y = _draw_wrapped_text(c, note, left, y, right - left, reg, 8.5, MUTED, 4.5 * mm) - 1 * mm

    c.setStrokeColor(LINE)
    c.setLineWidth(0.7)
    c.line(left, 27 * mm, right, 27 * mm)
    c.setFont(reg, 7.5)
    c.setFillColor(MUTED)
    c.drawRightString(right, 21 * mm, f"{SELLER['brand']} · {SELLER['tagline']}")

    c.showPage()
    c.save()

    rel_path = f"{QUOTES_DIR}/kp-{quote.id}.pdf"
    abs_dir = os.path.join(settings.MEDIA_ROOT, QUOTES_DIR)
    os.makedirs(abs_dir, exist_ok=True)
    with open(os.path.join(settings.MEDIA_ROOT, rel_path), "wb") as f:
        f.write(buf.getvalue())
    return rel_path
