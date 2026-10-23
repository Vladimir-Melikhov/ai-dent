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
    "brand": "Ai-Dent",
    "tagline": "Оборудование для стоматологии",
}


INK = colors.HexColor("#17222c")
MUTED = colors.HexColor("#8c98a3")
LINE = colors.HexColor("#e4e9ee")
CARD = colors.HexColor("#f5f8fa")
CARD_DARK = colors.HexColor("#e9eef2")


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
