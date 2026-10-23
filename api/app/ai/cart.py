"""Корзина и КП глазами api: та же формула цены, что в Django.

Формула: price_rub товара (её считает Django) + доплаты опций по курсу ЦБ, до копеек.
Опции сейчас не выбираются, поэтому на деле цена = price_rub.
"""

import datetime
import uuid
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..chat.owner import Owner
from ..db.models import CartItem, CurrencyRate, Product, ProductOption, Quote

MOSCOW = ZoneInfo("Europe/Moscow")
KOPECK = Decimal("0.01")
QUOTE_VALID_DAYS = 14


def today() -> datetime.date:
    return datetime.datetime.now(MOSCOW).date()


def to_rub(amount: Decimal, rate: Decimal) -> Decimal:
    return (amount * rate).quantize(KOPECK, rounding=ROUND_HALF_UP)


async def rate_on(session: AsyncSession, currency: str, date: datetime.date | None = None) -> Decimal | None:
    if currency == "RUB":
        return Decimal(1)
    return await session.scalar(
        select(CurrencyRate.rate)
        .where(CurrencyRate.currency == currency, CurrencyRate.date <= (date or today()))
        .order_by(CurrencyRate.date.desc()).limit(1)
    )


def option_ids(selected_options) -> list[int]:
    ids = (selected_options or {}).get("option_ids") if isinstance(selected_options, dict) else None
    return [int(i) for i in ids or [] if str(i).isdigit()]


async def unit_price_rub(session: AsyncSession, item: CartItem, product: Product) -> Decimal | None:
    if product.price_rub is None:
        return None
    ids = option_ids(item.selected_options)
    if not ids:
        return product.price_rub
    deltas = (await session.scalars(
        select(ProductOption.price_delta).where(ProductOption.product_id == product.id, ProductOption.id.in_(ids))
    )).all()
    if not deltas:
        return product.price_rub
    rate = await rate_on(session, product.currency)
    return product.price_rub + sum((to_rub(d, rate) for d in deltas), Decimal(0))


def owner_filter(owner: Owner):
    if owner.user_id is not None:
        return CartItem.user_id == owner.user_id
    return (CartItem.session_id == owner.session_id) & CartItem.user_id.is_(None)


def money(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


async def cart_summary(session: AsyncSession, owner: Owner) -> dict:
    items = (await session.scalars(
        select(CartItem).where(owner_filter(owner)).order_by(CartItem.created_at.desc(), CartItem.id.desc())
    )).all()
    lines = []
    total = Decimal(0)
    under_order = False
    for item in items:
        product = await session.get(Product, item.product_id, options=[selectinload(Product.brand)])
        unit = await unit_price_rub(session, item, product)
        line = unit * item.quantity if unit is not None else None
        total += line or 0
        available = product.stock_qty > 0
        under_order = under_order or not available
        lines.append({
            "product_id": product.id, "name": product.name, "brand": product.brand.name,
            "quantity": item.quantity, "unit_price_rub": money(unit), "line_total_rub": money(line),
            "availability": "в наличии" if available else "под заказ",
        })
    summary = {"items": lines, "total_rub": money(total), "count": len(lines)}
    if under_order:
        summary["note"] = "в корзине товары под заказ — предупреди клиента о сроке поставки по базе знаний"
    return summary


async def allowed_product(session: AsyncSession, product_id: int) -> tuple[Product | None, str | None]:
    """То же правило допуска, что в Django: активен и есть цена в рублях; остаток не важен."""
    product = await session.get(Product, product_id)
    if product is None or not product.is_active:
        return None, "товара нет в каталоге"
    if product.price_rub is None:
        return None, "цена по запросу (нет курса валюты) — предложи заявку менеджеру"
    return product, None


async def set_quantity(session: AsyncSession, owner: Owner, product_id: int, quantity: int) -> str | None:
    """Идемпотентно: количество устанавливается, 0 — удалить. Возвращает текст ошибки или None."""
    existing = await session.scalar(
        select(CartItem).where(owner_filter(owner), CartItem.product_id == product_id)
    )
    if quantity == 0:
        if existing is not None:
            await session.execute(delete(CartItem).where(CartItem.id == existing.id))
        return None
    _, reason = await allowed_product(session, product_id)
    if reason:
        return reason
    if existing is not None:
        existing.quantity = quantity
        existing.updated_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        session.add(CartItem(user_id=owner.user_id, session_id=None if owner.user_id is not None else owner.session_id,
                             product_id=product_id, quantity=quantity, selected_options={}))
    try:
        await session.flush()
    except IntegrityError:
        return "корзина изменилась одновременно на сайте — запроси её заново через get_cart"
    return None


async def quote_lines(session: AsyncSession, positions: list[tuple[int, int]]) -> list[dict]:
    """Снапшот позиций КП в рублях; у валютных — исходная цена и курс на дату КП."""
    lines = []
    for product_id, quantity in positions:
        product, reason = await allowed_product(session, product_id)
        if reason:
            raise ValueError(f"товар {product_id}: {reason}")
        unit = product.price_rub
        lines.append({
            "product_id": product.id, "name": product.name, "quantity": quantity,
            "unit_price_rub": str(unit), "line_total_rub": str(unit * quantity),
            "source_price": str(product.price), "source_currency": product.currency,
            "exchange_rate": str(await rate_on(session, product.currency)) if product.currency != "RUB" else None,
        })
    return lines


async def create_quote(session: AsyncSession, owner: Owner, positions: list[tuple[int, int]]) -> Quote:
    lines = await quote_lines(session, positions)
    quote_id = uuid.uuid4()
    quote = Quote(
        id=quote_id, number=f"KP-{today():%Y%m%d}-{quote_id.hex[:6].upper()}",
        conversation_id=owner.conversation_id, user_id=owner.user_id,
        session_id=None if owner.user_id is not None else owner.session_id,
        items=lines, total=sum((Decimal(l["line_total_rub"]) for l in lines), Decimal(0)),
        valid_until=today() + datetime.timedelta(days=QUOTE_VALID_DAYS),
    )
    session.add(quote)
    await session.flush()
    return quote


def quote_card(quote: Quote) -> dict:
    """Виджет КП: номер, сумма, срок, ссылка на PDF — его рендерит Django при скачивании."""
    return {
        "widget": "quote", "number": quote.number, "total": money(quote.total),
        "valid_until": quote.valid_until.isoformat(), "items_count": len(quote.items),
        "url": f"/chat/quote/{quote.id}/",
    }
