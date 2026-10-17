"""Цены моделей, $ за 1 млн токенов (ТЗ §7.9)."""

from dataclasses import dataclass
from decimal import Decimal

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    input: Decimal
    output: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal
    cache_read: Decimal


PRICES = {
    "claude-opus-5-5": Price(Decimal("4"), Decimal("20"), Decimal("5"), Decimal("8"), Decimal("0.20")),
    "claude-opus-5": Price(Decimal("5"), Decimal("25"), Decimal("6.25"), Decimal("10"), Decimal("0.50")),
    "claude-opus-4-8": Price(Decimal("5"), Decimal("25"), Decimal("6.25"), Decimal("10"), Decimal("0.50")),
    "claude-sonnet-5-5": Price(Decimal("2"), Decimal("10"), Decimal("2.50"), Decimal("4"), Decimal("0.20")),
}
# Для суточного бюджета попытка неизвестной модели считается по самой дорогой цене
MOST_EXPENSIVE = max(PRICES.values(), key=lambda p: p.output)


@dataclass(frozen=True)
class Tokens:
    input: int = 0
    output: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    cache_read: int = 0


def _cost(price: Price, tokens: Tokens) -> Decimal:
    total = (
        price.input * tokens.input
        + price.output * tokens.output
        + price.cache_write_5m * tokens.cache_write_5m
        + price.cache_write_1h * tokens.cache_write_1h
        + price.cache_read * tokens.cache_read
    )
    return (total / MILLION).quantize(Decimal("0.000001"))


def cost(model: str, tokens: Tokens) -> Decimal | None:
    """Стоимость попытки; None — модели нет в таблице (в лог пишется ошибка)."""
    price = PRICES.get(model)
    return _cost(price, tokens) if price else None


def budget_cost(model: str, tokens: Tokens) -> Decimal:
    return cost(model, tokens) or _cost(MOST_EXPENSIVE, tokens)
