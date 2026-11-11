from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()

NBSP = " "


def _group(number: Decimal, places: int) -> str:
    quantum = Decimal(1).scaleb(-places)
    text = f"{number.quantize(quantum, rounding=ROUND_HALF_UP):,.{places}f}"
    return text.replace(",", NBSP).replace(".", ",")


@register.filter
def rub(value, places=0):
    """12345.6 → «12 346 ₽»."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return "—"
    return f"{_group(number, int(places))}{NBSP}₽"


@register.filter
def signed_rub(value, sign):
    return f"{sign}{rub(value)}"


@register.filter
def pct(value):
    if value is None:
        return "—"
    return f"{value:.0f}%"


@register.filter
def initials(value):
    words = [w for w in str(value or "").split() if w]
    return (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper() if words else "•"
