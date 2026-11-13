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
    "КП не является счётом и не содержит реквизитов для оплаты.",
    "Цены в рублях с НДС, по каталогу сайта на дату предложения.",
    "Итоговая стоимость фиксируется в счёте после подтверждения заказа.",
]
CURRENCY_NOTE = "Валютные цены пересчитаны по курсу ЦБ РФ на дату КП."


def rub(value) -> str:
    # Копейки печатаются, если они есть: у валютных товаров цена в рублях — с копейками
    return rub_text(value)


def item_list_price(item):
    """Цена по прайсу до скидки или None, если скидки по позиции нет."""
    listed = item.get("list_price_rub")
    if listed is None:
        return None
    return listed if Decimal(str(listed)) > Decimal(str(item["unit_price_rub"])) else None


def item_groups(item):
    """Группы «Главного о товаре» из снапшота; у КП, собранных раньше, — из прежних полей комплектации и характеристик."""
    if item.get("groups"):
        return item["groups"]
    groups = []
    if item.get("specs"):
        groups.append(["Характеристики", [f"{k}: {v}" for k, v in item["specs"]]])
    if item.get("kit"):
        groups.append(["Комплектация", item["kit"]])
    return groups


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
        "dates": f"от {quote.created_at:%d.%m.%Y}"
                 + (f" · ред. {quote.revision} от {quote.revised_at:%d.%m.%Y}" if quote.revision > 1 and quote.revised_at else "")
                 + f" · действует до {quote.valid_until:%d.%m.%Y}",
        "client": quote.client_name or "",
        "lines": lines,
        # комплектация и характеристики из снапшота КП; пусто — блока нет
        "details": [
            {"name": item["name"], "groups": item_groups(item)}
            for item in quote.items if item_groups(item)
        ],
        "rows": [(l["name"], l["quantity"], l["price"], l["total"]) for l in lines],
        "total": rub(quote.total),
        "discount": None,
        "notes": notes,
    }
    if summary:
        full, saved, pct = summary
        doc["discount"] = {"full": rub(full), "saved": "−" + rub(saved), "percent": percent_text(pct)}
    return doc


# Нижняя граница содержимого: ниже — условия и подвал
NOTES_TOP = 40 * mm

# Плотность блока «Главное о товаре»: (размер шрифта, колонок). Берётся первая, при которой КП остаётся на одной странице
DENSITY = [(8.5, 2), (8, 3), (7.5, 3), (7, 3), (6.8, 4)]


def render_details(c, fonts, details, y, new_page, cols, size, draw=True):
    """«Главное о товаре» целиком: карточка на товар, группы «заголовок + пункты» в несколько колонок.

    Возвращает y после блока. Не влезло на страницу — продолжается на следующей («продолжение»)."""
    reg, bold = fonts
    line_h = size * 0.5 * mm
    item_gap, group_gap = 0.8 * mm, 3.6 * mm
    pad, col_gap = 5 * mm, 6 * mm
    col_w = (RIGHT - LEFT - 2 * pad - (cols - 1) * col_gap) / cols
    col_x = [LEFT + pad + i * (col_w + col_gap) for i in range(cols)]
    bottom, head_h = NOTES_TOP, 11 * mm
    text_w = col_w - 3.5 * mm

    def blocks_of(group):
        """Группа → куски не выше 24 мм: длинный список можно разнести по колонкам и страницам, а не вылезти за рамку."""
        title, items = group
        wrapped = [wrap_text(c, text, reg, size, text_w) for text in items]
        title_lines = wrap_text(c, title, bold, size, col_w) if title else []
        pieces, current, height = [], [], len(title_lines) * line_h + (1 * mm if title else 0)
        head = title_lines
        for lines in wrapped:
            item_h = len(lines) * line_h + item_gap
            if current and height + item_h > 24 * mm:
                pieces.append({"title": head, "items": current, "h": height + group_gap})
                current, height, head = [], 0, []
            current.append(lines)
            height += item_h
        if current or head:
            pieces.append({"title": head, "items": current, "h": height + group_gap})
        return pieces

    fresh = False

    def next_page():
        nonlocal fresh
        fresh = True
        return new_page()

    if y - 6 * mm - head_h - 20 * mm < bottom:
        y = next_page()
    if draw:
        spaced(c, LEFT, y, "Главное о товаре", bold, 8, INK)
    y -= 6 * mm

    for item in details:
        blocks = [piece for g in item["groups"] for piece in blocks_of(g)]
        first = True
        while blocks:
            room = y - bottom - head_h - 2 * mm
            if room < 18 * mm or (blocks[0]["h"] > room and not fresh):
                y = next_page()
                room = y - bottom - head_h - 2 * mm
            fresh = False
            heights, columns = [0] * cols, [[] for _ in range(cols)]
            # каждый кусок — в самую короткую колонку, в порядке сайта; что не влезло по высоте — на следующую страницу
            while blocks:
                col = heights.index(min(heights))
                if heights[col] + blocks[0]["h"] > room and any(columns):
                    break
                heights[col] += blocks[0]["h"]
                columns[col].append(blocks.pop(0))
            card_h = head_h + max(heights) + 1 * mm
            if draw:
                glass(c, LEFT, y - card_h, RIGHT - LEFT, card_h, radius=3.5 * mm)
                c.setFont(bold, 9)
                c.setFillColor(INK)
                name = item["name"] if first else f"{item['name']} (продолжение)"
                c.drawString(LEFT + pad, y - 7 * mm, wrap_text(c, name, bold, 9, RIGHT - LEFT - 2 * pad, 1)[0])
                for x, col_blocks in zip(col_x, columns):
                    ty = y - 13 * mm
                    for b in col_blocks:
                        c.setFont(bold, size)
                        c.setFillColor(INK)
                        for line in b["title"]:
                            c.drawString(x, ty, line)
                            ty -= line_h
                        ty -= 1 * mm if b["title"] else 0
                        for lines in b["items"]:
                            for n, line in enumerate(lines):
                                c.setFont(reg, size)
                                if n == 0:
                                    c.setFillColor(FAINT)
                                    c.drawString(x, ty, "—")
                                c.setFillColor(INK)
                                c.drawString(x + 3.5 * mm, ty, line)
                                ty -= line_h
                            ty -= item_gap
                        ty -= group_gap
            y -= card_h + 3 * mm
            first = False
            if blocks:
                y = next_page()
    return y


def draw_details(c, fonts, details, y, new_page):
    """Выбирает самую крупную плотность, при которой блок помещается на текущую страницу."""
    chosen = DENSITY[-1]
    for size, cols in DENSITY:
        pages = []
        render_details(c, fonts, details, y, lambda: pages.append(1) or PAGE_H - 26 * mm, cols, size, draw=False)
        if not pages:
            chosen = (size, cols)
            break
    return render_details(c, fonts, details, y, new_page, chosen[1], chosen[0])


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
    box_h = 17 * mm
    glass(c, LEFT, y - box_h, RIGHT - LEFT, box_h)
    cells = [("Дата", f"{quote.created_at:%d.%m.%Y}"), ("Действует до", f"{quote.valid_until:%d.%m.%Y}")]
    if doc["client"]:
        cells.append(("Для", doc["client"]))
    x = LEFT + 7 * mm
    for i, (caption, value) in enumerate(cells):
        spaced(c, x, y - 5.5 * mm, caption, reg, 7, MUTED)
        c.setFont(bold, 11)
        c.setFillColor(INK)
        room = RIGHT - x - 7 * mm
        c.drawString(x, y - 12 * mm, wrap_text(c, value, bold, 11, room if i == 2 else 60 * mm, 1)[0])
        x += 42 * mm

    # ─── Позиции ───
    y = items_table(c, fonts, doc["lines"], y - box_h - 10 * mm, NOTES_TOP + 10 * mm, new_page)

    # ─── Итог ───
    need = totals_block_height(doc["discount"])
    if y - need < NOTES_TOP:
        y = new_page()
    y = totals_block(c, fonts, y + 2 * mm, doc["total"], doc["discount"]) - 6 * mm

    if doc["details"]:
        y = draw_details(c, fonts, doc["details"], y, new_page)

    # условия — одним абзацем в две строки у нижнего края
    c.setFont(reg, 7)
    c.setFillColor(MUTED)
    ny = 31 * mm
    for line in wrap_text(c, "  ·  ".join(doc["notes"]), reg, 7, RIGHT - LEFT, 3):
        c.drawString(LEFT, ny, line)
        ny -= 3.6 * mm

    page_footer(c, fonts, footer_text)
    c.showPage()
    c.save()

    rel_path = f"{QUOTES_DIR}/kp-{quote.id}.pdf"
    abs_dir = os.path.join(settings.MEDIA_ROOT, QUOTES_DIR)
    os.makedirs(abs_dir, exist_ok=True)
    with open(os.path.join(settings.MEDIA_ROOT, rel_path), "wb") as f:
        f.write(buf.getvalue())
    return rel_path
