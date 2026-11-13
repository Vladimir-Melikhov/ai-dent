"""Общие функции отрисовки PDF: счёт и коммерческое предложение."""

import os

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ─── Реквизиты продавца ───

SELLER = {
    "name": "Индивидуальный предприниматель Мелихов Владимир Семёнович",
    "inn": "616808573750",
    "account": "40802810120000943056",
    "bank": "ООО «Банк Точка»",
    "bik": "044525104",
    "corr_account": "30101810745374525104",
    "brand": "Ай-Дент",
    "tagline": "Оборудование для стоматологии",
}


INK = colors.HexColor("#0f1419")
SOFT = colors.HexColor("#2c323b")
MUTED = colors.HexColor("#525c6d")
FAINT = colors.HexColor("#8a94a6")
BG = colors.HexColor("#f4f3f2")
LINE = colors.HexColor("#d6d4d2")
CARD = colors.HexColor("#f5f8fa")
CARD_DARK = colors.HexColor("#e9eef2")


FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "static", "fonts", "pdf")
TEETH = os.path.join(os.path.dirname(FONT_DIR), "..", "img", "teeth.png")


def _fonts():
    """Регистрируем шрифт с кириллицей."""

    candidates_reg = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    candidates_bold = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]

    reg, bold = "Helvetica", "Helvetica-Bold"

    # Шрифт сайта лежит в репозитории: цифры и ₽ одинаковые на любом сервере
    if os.path.exists(os.path.join(FONT_DIR, "Manrope-Regular.ttf")):
        pdfmetrics.registerFont(TTFont("Manrope", os.path.join(FONT_DIR, "Manrope-Regular.ttf")))
        pdfmetrics.registerFont(TTFont("Manrope-Bold", os.path.join(FONT_DIR, "Manrope-Bold.ttf")))
        return "Manrope", "Manrope-Bold"

    for p in candidates_reg:
        if os.path.exists(p):
            pdfmetrics.registerFont(TTFont("Main", p))
            reg = "Main"
            break

    for p in candidates_bold:
        if os.path.exists(p):
            pdfmetrics.registerFont(TTFont("Main-Bold", p))
            bold = "Main-Bold"
            break

    return reg, bold


def _draw_gradient_box(c, x, y, width, height, radius=5 * mm):
    """
    Очень лёгкий светлый градиент.
    Нужен только как декоративный фон блока.
    """

    steps = 12
    step_height = height / steps

    for i in range(steps):
        t = i / max(steps - 1, 1)

        r = int(245 - (245 - 232) * t)
        g = int(248 - (248 - 238) * t)
        b = int(250 - (250 - 243) * t)

        c.setFillColor(colors.Color(
            r / 255,
            g / 255,
            b / 255,
        ))

        c.rect(
            x,
            y + i * step_height,
            width,
            step_height + 0.5,
            stroke=0,
            fill=1,
        )

    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)

    c.roundRect(
        x,
        y,
        width,
        height,
        radius,
        stroke=1,
        fill=0,
    )


def _draw_wrapped_text(
    c,
    text,
    x,
    y,
    max_width,
    font,
    size,
    color=INK,
    line_height=None,
):
    """
    Рисует текст с переносом строк.
    Возвращает новую координату Y.
    """

    if not text:
        return y

    c.setFont(font, size)
    c.setFillColor(color)

    line_height = line_height or size * 1.35

    words = str(text).split()
    lines = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()

        if c.stringWidth(test, font, size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    for line in lines:
        c.drawString(x, y, line)
        y -= line_height

    return y


# ─── Скидка ───



def discount_percent(list_price, price):
    """Процент скидки к цене по прайсу, до десятых; None — скидки нет."""
    from decimal import ROUND_HALF_UP, Decimal

    if list_price is None or price is None or Decimal(list_price) <= Decimal(price):
        return None
    pct = (Decimal(list_price) - Decimal(price)) / Decimal(list_price) * 100
    return pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def percent_text(pct) -> str:
    text = f"{pct:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") + "%"


def discount_summary(rows):
    """rows: (цена до скидки или None, цена, количество). Возвращает (сумма по прайсу, скидка ₽, скидка %) или None."""
    from decimal import Decimal

    full = sum((Decimal(lp if lp is not None and Decimal(lp) > Decimal(p) else p) * q for lp, p, q in rows), Decimal(0))
    paid = sum((Decimal(p) * q for _, p, q in rows), Decimal(0))
    if full <= paid:
        return None
    return full, full - paid, ((full - paid) / full * 100)


# ─── Общий вид документов: серо-бежевый фон сайта, стекло, чёрные акценты ───

PAGE_W, PAGE_H = 210 * mm, 297 * mm
LEFT, RIGHT = 20 * mm, 190 * mm


def paint_page(c):
    """Фон страницы и еле заметный зуб, как на сайте."""
    c.setFillColor(BG)
    c.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    if os.path.exists(TEETH):
        c.saveState()
        c.setFillAlpha(0.12)
        size = 190 * mm
        c.translate(PAGE_W - 52 * mm, 78 * mm)
        c.rotate(-8)
        c.drawImage(TEETH, -size / 2, -size / 2, width=size, height=size * 1024 / 1051, mask="auto")
        c.restoreState()


def glass(c, x, y, w, h, radius=4 * mm, dark=False):
    """Матовая карточка: светлая полупрозрачная или чёрная."""
    c.saveState()
    if dark:
        c.setFillColor(INK)
        c.roundRect(x, y, w, h, radius, stroke=0, fill=1)
    else:
        c.setFillColor(colors.white)
        c.setFillAlpha(0.72)
        c.roundRect(x, y, w, h, radius, stroke=0, fill=1)
        c.setStrokeColor(INK)
        c.setStrokeAlpha(0.09)
        c.setLineWidth(0.6)
        c.roundRect(x, y, w, h, radius, stroke=1, fill=0)
    c.restoreState()


def spaced(c, x, y, text, font, size, color, right=False, space=0.6):
    """Подпись капсом с лёгким разрядом. Разряд не должен «протекать» на следующий текст — отсюда save/restore."""
    text = text.upper()
    width = c.stringWidth(text, font, size) + space * (len(text) - 1)
    c.saveState()
    c.setFillColor(color)
    t = c.beginText(x - (width if right else 0), y)
    t.setFont(font, size)
    t.setCharSpace(space)
    t.textOut(text)
    c.drawText(t)
    c.restoreState()


def page_header(c, fonts, kind, number, sub=None):
    reg, bold = fonts
    c.setFillColor(INK)
    c.setFont(bold, 22)
    c.drawString(LEFT, PAGE_H - 27 * mm, SELLER["brand"])
    spaced(c, LEFT, PAGE_H - 33 * mm, SELLER["tagline"], reg, 7.5, MUTED)
    spaced(c, RIGHT, PAGE_H - 22 * mm, kind, reg, 7.5, MUTED, right=True)
    c.setFont(bold, 15)
    c.setFillColor(INK)
    c.drawRightString(RIGHT, PAGE_H - 29.5 * mm, number)
    if sub:
        c.setFont(reg, 8.5)
        c.setFillColor(MUTED)
        c.drawRightString(RIGHT, PAGE_H - 35 * mm, sub)
    c.setStrokeColor(INK)
    c.setLineWidth(0.8)
    c.line(LEFT, PAGE_H - 40 * mm, RIGHT, PAGE_H - 40 * mm)


def page_footer(c, fonts, left_text):
    reg, _ = fonts
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(LEFT, 24 * mm, RIGHT, 24 * mm)
    c.setFont(reg, 7.5)
    c.setFillColor(FAINT)
    c.drawString(LEFT, 18.5 * mm, left_text)
    c.drawRightString(RIGHT, 18.5 * mm, f"{SELLER['brand']} · {SELLER['tagline']}")


def wrap_text(c, text, font, size, width, max_lines=None):
    """Перенос по словам; слово длиннее строки режется, чтобы не вылезать за рамку."""
    def pieces(word):
        while c.stringWidth(word, font, size) > width and len(word) > 1:
            cut = len(word) - 1
            while cut > 1 and c.stringWidth(word[:cut], font, size) > width:
                cut -= 1
            yield word[:cut]
            word = word[cut:]
        yield word

    lines, current = [], ""
    for word in str(text).split():
        for part in pieces(word):
            test = f"{current} {part}".strip()
            if c.stringWidth(test, font, size) <= width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = part
    if current:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and c.stringWidth(lines[-1] + "…", font, size) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines or [""]


def items_table(c, fonts, lines, y, bottom, new_page):
    """Позиции: числовые колонки по ширине самых длинных значений — цифры не наезжают друг на друга.

    lines: name, quantity, price, total, list_price (до скидки или None), discount ("−15%" или None).
    new_page() закрывает страницу и возвращает y на новой."""
    reg, bold = fonts
    size, gap = 9.5, 7 * mm
    width = lambda text, font=reg, pt=size: c.stringWidth(text, font, pt)  # noqa: E731
    has_discount = any(l["discount"] for l in lines)

    total_w = max([width(l["total"], bold) for l in lines] + [width("СУММА", reg, 7)])
    price_w = max([width(l["price"], bold) for l in lines] + [width(l["list_price"], reg, 7.5) for l in lines if l["list_price"]]
                  + [width("ЦЕНА", reg, 7)])
    pill_w = max([width(l["discount"], bold, 8) for l in lines if l["discount"]] + [0]) + 5 * mm
    qty_w = max([width(l["quantity"]) for l in lines] + [width("КОЛ-ВО", reg, 7)])

    sum_r = RIGHT - 2 * mm
    price_r = sum_r - total_w - gap
    disc_r = price_r - price_w - gap
    qty_r = (disc_r - pill_w if has_discount else price_r - price_w) - gap
    name_w = qty_r - qty_w - gap - LEFT

    def head(at):
        spaced(c, LEFT, at, "Наименование", reg, 7, MUTED)
        spaced(c, qty_r, at, "Кол-во", reg, 7, MUTED, right=True)
        spaced(c, price_r, at, "Цена", reg, 7, MUTED, right=True)
        if has_discount:
            spaced(c, disc_r, at, "Скидка", reg, 7, MUTED, right=True)
        spaced(c, sum_r, at, "Сумма", reg, 7, MUTED, right=True)
        c.setStrokeColor(INK)
        c.setLineWidth(0.5)
        c.line(LEFT, at - 3 * mm, RIGHT, at - 3 * mm)

    head(y)
    y -= 10 * mm
    for line in lines:
        name_lines = wrap_text(c, line["name"], reg, size, name_w, 3)
        height = max(len(name_lines) * 4.8 * mm, 4.8 * mm + (4.4 * mm if line["list_price"] else 0))
        if y - height < bottom:
            y = new_page()
            head(y)
            y -= 10 * mm
        top = y
        c.setFont(reg, size)
        c.setFillColor(INK)
        for text in name_lines:
            c.drawString(LEFT, y, text)
            y -= 4.8 * mm
        c.drawRightString(qty_r, top, line["quantity"])
        c.setFont(bold, size)
        c.drawRightString(price_r, top, line["price"])
        c.drawRightString(sum_r, top, line["total"])
        if line["list_price"]:
            c.setFont(reg, 7.5)
            c.setFillColor(FAINT)
            c.drawRightString(price_r, top - 4.3 * mm, line["list_price"])
            lw = c.stringWidth(line["list_price"], reg, 7.5)
            c.setStrokeColor(FAINT)
            c.setLineWidth(0.5)
            c.line(price_r - lw, top - 3.3 * mm, price_r, top - 3.3 * mm)
            # скидка — чёрная «таблетка», как кнопки на сайте
            c.setFillColor(INK)
            c.roundRect(disc_r - pill_w, top - 1.4 * mm, pill_w, 5.2 * mm, 2.6 * mm, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont(bold, 8)
            c.drawCentredString(disc_r - pill_w / 2, top + 0.1 * mm, line["discount"])
            y = min(y, top - 4.3 * mm - 3.6 * mm)
        y -= 1.8 * mm
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(LEFT, y, RIGHT, y)
        y -= 5 * mm
    return y


def totals_block_height(discount) -> float:
    return (31 if discount else 20) * mm


def totals_block(c, fonts, y, total_text, discount=None):
    """Светлая карточка с итогом справа; со скидкой — сумма по прайсу и размер скидки."""
    reg, bold = fonts
    w = 84 * mm
    h = totals_block_height(discount)
    x = RIGHT - w
    glass(c, x, y - h, w, h)
    pad = 6 * mm
    ty = y - 7.5 * mm
    if discount:
        c.setFont(reg, 9)
        c.setFillColor(MUTED)
        c.drawString(x + pad, ty, "Сумма по прайсу")
        c.drawRightString(RIGHT - pad, ty, discount["full"])
        ty -= 6 * mm
        c.setFillColor(INK)
        c.drawString(x + pad, ty, f"Скидка {discount['percent']}")
        c.drawRightString(RIGHT - pad, ty, discount["saved"])
        ty -= 3.5 * mm
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(x + pad, ty, RIGHT - pad, ty)
        ty -= 7 * mm
    spaced(c, x + pad, ty + 0.5 * mm, "Итого", reg, 8, MUTED)
    c.setFont(bold, 15)
    c.setFillColor(INK)
    c.drawRightString(RIGHT - pad, ty, total_text)
    return y - h
