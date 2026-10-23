"""Инструменты агента.

Схемы строгие (strict); ограничения, которых строгие схемы не поддерживают (min/max,
длины, размеры массивов), проверяет pydantic до выполнения. Владелец берётся из
соединения, цены в аргументах не принимаются.
"""

import json
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Text, and_, cast, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..chat.owner import Owner
from ..db.models import Brand, Category, Conversation, Product, Quote
from ..schemas.catalog import ProductListItemOut
from . import cart
from .masking import mask_pii

MAX_CHARACTERISTICS = 6
MAX_COMPARISON_ROWS = 25
MAX_DESCRIPTION = 3000


@dataclass
class ToolContext:
    owner: Owner
    session: AsyncSession


@dataclass
class ToolOutput:
    result: dict
    widget: dict | None = None  # что показать клиенту: хранятся только id, данные — при выдаче
    cart_changed: bool = False


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    schema: dict
    run: Callable[[ToolContext, BaseModel], Awaitable[ToolOutput]]
    status: str  # что показать в индикаторе, пока инструмент работает
    mutating: bool = False  # меняет данные — выполняется по очереди, в транзакции хода

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema, "strict": True}


class ToolError(Exception):
    """Ошибка, понятная модели: уходит результатом с is_error, модель может исправиться."""


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


# --- общее


def money(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def key_characteristics(characteristics: dict | None) -> dict:
    items = list((characteristics or {}).items())[:MAX_CHARACTERISTICS]
    return {str(k): v for k, v in items}


def brief(product: Product) -> dict:
    """Карточка для модели: цена — в рублях, та же, что попадёт в корзину и счёт."""
    data = {
        "id": product.id,
        "name": product.name,
        "brand": product.brand.name,
        "category": product.category.name,
        "price_rub": money(product.price_rub),
        "availability": "в наличии" if product.stock_qty > 0 else "под заказ",
        "characteristics": key_characteristics(product.characteristics),
    }
    if product.currency != "RUB":
        data["catalog_price"] = {"amount": money(product.price), "currency": product.currency}
    if product.price_rub is None:
        data["price_note"] = "цена по запросу: курса валюты нет"
    return data


def _with_relations(stmt):
    return stmt.options(selectinload(Product.brand), selectinload(Product.category), selectinload(Product.images))


async def active_products(session: AsyncSession, ids: list[int]) -> list[Product]:
    """Активные товары в порядке ids."""
    rows = (await session.scalars(_with_relations(
        select(Product).where(Product.id.in_(ids), Product.is_active.is_(True))
    ))).all()
    by_id = {p.id: p for p in rows}
    return [by_id[i] for i in ids if i in by_id]


async def product_cards(session: AsyncSession, ids: list[int]) -> list[dict]:
    """Данные карточек для виджета — в формате каталога сайта (productCard в api.js)."""
    return [ProductListItemOut.model_validate(p).model_dump(mode="json") for p in await active_products(session, ids)]


def comparison_table(products: list[Product]) -> dict:
    rows: list[str] = []
    for product in products:
        for key in (product.characteristics or {}):
            if key not in rows:
                rows.append(str(key))
    rows = rows[:MAX_COMPARISON_ROWS]
    return {
        "products": [
            {"id": p.id, "slug": p.slug, "name": p.name, "brand": p.brand.name, "price_rub": money(p.price_rub),
             "availability": "в наличии" if p.stock_qty > 0 else "под заказ"}
            for p in products
        ],
        "rows": [
            {"name": key, "values": [(p.characteristics or {}).get(key, "—") for p in products]}
            for key in rows
        ],
    }


async def hydrate_widget(session: AsyncSession, widget: dict) -> dict | None:
    """Виджет с актуальными данными каталога: в истории хранятся только id."""
    kind = widget.get("widget")
    ids = widget.get("product_ids") or []
    if kind == "product_cards":
        cards = await product_cards(session, ids)
        return {"widget": kind, "products": cards} if cards else None
    if kind == "comparison":
        products = await active_products(session, ids)
        return {"widget": kind, "table": comparison_table(products)} if len(products) >= 2 else None
    if kind == "quote":
        quote = await session.get(Quote, uuid.UUID(widget["quote_id"]))
        return cart.quote_card(quote) if quote else None
    if kind in ("lead_form", "order_form"):
        return {"widget": kind}
    return None


# --- поиск по-русски без морфологии: отрезаем окончания, ищем подстроку

WORD_RE = re.compile(r"[\w.\-]+", re.UNICODE)


def stem(word: str) -> str:
    if word.isdigit() or not word.isalpha():
        return word
    if len(word) >= 6:
        return word[:-2]
    if len(word) >= 4:
        return word[:-1]
    return word


STOP_WORDS = {"для", "и", "в", "во", "на", "с", "со", "по", "от", "до", "под", "при", "без", "или", "из", "к", "о", "об"}


def search_terms(query: str) -> list[str]:
    words = [w.lower().strip(".-") for w in WORD_RE.findall(query or "")]
    return [stem(w) for w in words if len(w) >= 2 and w not in STOP_WORDS]


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- catalog_overview


class OverviewArgs(Args):
    pass


async def run_overview(ctx: ToolContext, args: OverviewArgs) -> ToolOutput:
    stats = (await ctx.session.execute(
        select(Category.id, Category.name, Category.slug, Category.parent_id,
               func.count(Product.id), func.min(Product.price_rub), func.max(Product.price_rub))
        .outerjoin(Product, (Product.category_id == Category.id) & Product.is_active.is_(True))
        .group_by(Category.id).order_by(Category.name)
    )).all()
    names = {row[0]: row[1] for row in stats}
    brands = (await ctx.session.execute(
        select(Brand.name, Brand.slug, Brand.country, func.count(Product.id))
        .outerjoin(Product, (Product.brand_id == Brand.id) & Product.is_active.is_(True))
        .group_by(Brand.id).order_by(Brand.name)
    )).all()
    return ToolOutput({
        "categories": [
            {"name": name, "slug": slug, "parent": names.get(parent), "products": count,
             "price_rub_from": money(low), "price_rub_to": money(high)}
            for _, name, slug, parent, count, low, high in stats
        ],
        "brands": [{"name": n, "slug": s, "country": c, "products": count} for n, s, c, count in brands],
    })


# --- search_products


class SearchArgs(Args):
    query: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    in_stock_only: bool = False
    sort: Literal["relevance", "price_asc", "price_desc"] = "relevance"
    limit: int = Field(default=5, ge=1, le=10)


def _match(column_slug, column_name, value: str):
    return or_(func.lower(column_slug) == value.lower(), column_name.ilike(_like(value)))


async def run_search(ctx: ToolContext, args: SearchArgs) -> ToolOutput:
    base = (
        select(Product).join(Product.brand).join(Product.category)
        .where(Product.is_active.is_(True))
    )
    if args.category:
        base = base.where(_match(Category.slug, Category.name, args.category))
    if args.brand:
        base = base.where(_match(Brand.slug, Brand.name, args.brand))
    if args.price_min is not None:
        base = base.where(Product.price_rub >= args.price_min)
    if args.price_max is not None:
        base = base.where(Product.price_rub <= args.price_max)
    if args.in_stock_only:
        base = base.where(Product.stock_qty > 0)

    haystack = func.concat_ws(
        " ", Product.name, func.coalesce(Product.sku, ""), Brand.name, Category.name,
        Product.description, cast(Product.characteristics, Text),
    )
    terms = search_terms(args.query or "")
    if args.sort == "price_asc":
        order = (Product.price_rub.asc().nulls_last(),)
    elif args.sort == "price_desc":
        order = (Product.price_rub.desc().nulls_last(),)
    else:
        order = (Product.priority.desc(), Product.name.asc())

    async def fetch(condition):
        stmt = base.where(condition) if condition is not None else base
        return (await ctx.session.scalars(_with_relations(stmt.order_by(*order).limit(args.limit)))).all()

    loose = False
    if terms:
        products = await fetch(and_(*(haystack.ilike(_like(t)) for t in terms)))
        if not products and len(terms) > 1:
            # Ни один товар не подходит под все слова — ищем по любому из них
            loose = True
            products = await fetch(or_(*(haystack.ilike(_like(t)) for t in terms)))
    else:
        products = await fetch(None)
    result = {"products": [brief(p) for p in products]}
    if loose:
        result["note"] = "точных совпадений нет, показаны товары, подходящие под часть слов запроса"
    if not products:
        result["note"] = "в каталоге ничего не найдено"
    return ToolOutput(result)


# --- get_product


class ProductArgs(Args):
    product_id: int


def block_text(block) -> str:
    parts = [block.title, block.subtitle, block.body]
    for item in block.data or []:
        parts.append(json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item))
    return "\n".join(part for part in parts if part)


async def run_get_product(ctx: ToolContext, args: ProductArgs) -> ToolOutput:
    product = await ctx.session.scalar(
        _with_relations(select(Product).where(Product.id == args.product_id, Product.is_active.is_(True)))
        .options(selectinload(Product.content_blocks))
    )
    if product is None:
        raise ToolError(f"товара с id {args.product_id} в каталоге нет")
    data = brief(product)
    data.update({
        "sku": product.sku,
        "slug": product.slug,
        "description": (product.description or "")[:MAX_DESCRIPTION],
        "characteristics": product.characteristics or {},
        "content": [text for text in (block_text(b) for b in product.content_blocks) if text],
    })
    return ToolOutput(data)


# --- show_products / show_comparison


class ShowArgs(Args):
    product_ids: list[int] = Field(min_length=1, max_length=4)


class CompareArgs(Args):
    product_ids: list[int] = Field(min_length=2, max_length=3)


async def run_show(ctx: ToolContext, args: ShowArgs) -> ToolOutput:
    ids = list(dict.fromkeys(args.product_ids))
    products = await active_products(ctx.session, ids)
    if not products:
        raise ToolError("ни одного из этих товаров нет в каталоге")
    shown = [p.id for p in products]
    result = {"shown": [brief(p) for p in products]}
    missing = [i for i in ids if i not in shown]
    if missing:
        result["not_found"] = missing
    return ToolOutput(result, widget={"widget": "product_cards", "product_ids": shown})


async def run_compare(ctx: ToolContext, args: CompareArgs) -> ToolOutput:
    ids = list(dict.fromkeys(args.product_ids))
    products = await active_products(ctx.session, ids)
    if len(products) < 2:
        raise ToolError("для сравнения нужно минимум два товара из каталога")
    return ToolOutput(comparison_table(products),
                      widget={"widget": "comparison", "product_ids": [p.id for p in products]})


# --- корзина (общая с витриной), КП, заявка


class EmptyArgs(Args):
    pass


class CartSetArgs(Args):
    product_id: int
    quantity: int = Field(ge=0, le=999)


class QuoteItem(Args):
    product_id: int
    quantity: int = Field(ge=1, le=999)


class QuoteArgs(Args):
    source: Literal["cart", "items"]
    items: list[QuoteItem] | None = Field(default=None, max_length=20)


class LeadArgs(Args):
    topic: str = Field(min_length=1, max_length=300)


async def run_get_cart(ctx: ToolContext, args: EmptyArgs) -> ToolOutput:
    return ToolOutput(await cart.cart_summary(ctx.session, ctx.owner))


async def run_cart_set(ctx: ToolContext, args: CartSetArgs) -> ToolOutput:
    error = await cart.set_quantity(ctx.session, ctx.owner, args.product_id, args.quantity)
    if error:
        raise ToolError(error)
    return ToolOutput(await cart.cart_summary(ctx.session, ctx.owner), cart_changed=True)


async def run_create_quote(ctx: ToolContext, args: QuoteArgs) -> ToolOutput:
    if args.source == "items":
        if not args.items:
            raise ToolError("для source=items нужен список items")
        positions = [(i.product_id, i.quantity) for i in args.items]
    else:
        summary = await cart.cart_summary(ctx.session, ctx.owner)
        positions = [(i["product_id"], i["quantity"]) for i in summary["items"]]
        if not positions:
            raise ToolError("корзина пуста — сначала добавь товары или передай список items")
    try:
        quote = await cart.create_quote(ctx.session, ctx.owner, positions)
    except ValueError as error:
        raise ToolError(str(error)) from None
    return ToolOutput(
        {"number": quote.number, "total_rub": cart.money(quote.total), "valid_until": quote.valid_until.isoformat(),
         "items": [{"name": i["name"], "quantity": i["quantity"], "unit_price_rub": float(i["unit_price_rub"])}
                   for i in quote.items],
         "note": "КП показано клиенту карточкой со ссылкой на PDF; это не счёт"},
        widget={"widget": "quote", "quote_id": str(quote.id)},
    )


async def run_open_order_form(ctx: ToolContext, args: EmptyArgs) -> ToolOutput:
    """Форма оформления в ленте чата: состав — текущая корзина, данные покупателя клиент вводит сам."""
    summary = await cart.cart_summary(ctx.session, ctx.owner)
    if not summary["items"]:
        raise ToolError("корзина пуста — сначала положи товары через cart_set_item")
    return ToolOutput({"shown": True, "items_count": summary["count"], "total_rub": summary["total_rub"],
                       "note": "Форма показана; результат придёт событием"},
                      widget={"widget": "order_form"})


async def run_open_lead_form(ctx: ToolContext, args: LeadArgs) -> ToolOutput:
    # Пометка без ПДн: та же маска, что для реплик клиента
    await ctx.session.execute(
        update(Conversation).where(Conversation.id == ctx.owner.conversation_id)
        .values(pending_lead_topic=mask_pii(args.topic))
    )
    return ToolOutput({"shown": True, "note": "Форма показана; результат придёт событием"},
                      widget={"widget": "lead_form"})


# --- реестр: набор и порядок неизменны — от них зависит кэш

INT_LIST = {"type": "array", "items": {"type": "integer"}}

TOOLS: list[Tool] = [
    Tool(
        name="catalog_overview",
        description="Сводка каталога: категории с числом товаров и диапазоном цен в рублях, бренды.",
        args=OverviewArgs, schema=obj({}), run=run_overview, status="searching",
    ),
    Tool(
        name="search_products",
        description=(
            "Поиск активных товаров каталога по названию, артикулу, бренду, описанию и характеристикам. "
            "category и brand — slug или название из catalog_overview. Цены — в рублях. limit — от 1 до 10."
        ),
        args=SearchArgs,
        schema=obj({
            "query": {"type": "string", "description": "Слова для поиска"},
            "category": {"type": "string"},
            "brand": {"type": "string"},
            "price_min": {"type": "number", "description": "Минимальная цена, ₽"},
            "price_max": {"type": "number", "description": "Максимальная цена, ₽"},
            "in_stock_only": {"type": "boolean"},
            "sort": {"type": "string", "enum": ["relevance", "price_asc", "price_desc"]},
            "limit": {"type": "integer"},
        }),
        run=run_search, status="searching",
    ),
    Tool(
        name="get_product",
        description="Полная карточка товара: описание, характеристики, цена в рублях, наличие.",
        args=ProductArgs, schema=obj({"product_id": {"type": "integer"}}, ["product_id"]),
        run=run_get_product, status="searching",
    ),
    Tool(
        name="show_products",
        description=(
            "Показать клиенту в чате карточки товаров (от 1 до 4) с кнопками «Подробнее» и «В корзину». "
            "Возвращает актуальные цены."
        ),
        args=ShowArgs, schema=obj({"product_ids": INT_LIST}, ["product_ids"]),
        run=run_show, status="preparing",
    ),
    Tool(
        name="show_comparison",
        description="Показать клиенту таблицу сравнения 2–3 товаров по характеристикам. Текст сравнения пишешь сам.",
        args=CompareArgs, schema=obj({"product_ids": INT_LIST}, ["product_ids"]),
        run=run_compare, status="preparing",
    ),
    Tool(
        name="get_cart",
        description="Корзина клиента: позиции, цена за единицу в рублях, наличие, итог. Та же корзина, что на сайте.",
        args=EmptyArgs, schema=obj({}), run=run_get_cart, status="cart",
    ),
    Tool(
        name="cart_set_item",
        description=(
            "Установить количество товара в корзине клиента (0 — убрать). Идемпотентно. Товар под заказ "
            "добавлять можно — предупреди о сроке поставки. Количество — от 0 до 999."
        ),
        args=CartSetArgs,
        schema=obj({"product_id": {"type": "integer"}, "quantity": {"type": "integer"}}, ["product_id", "quantity"]),
        run=run_cart_set, status="cart", mutating=True,
    ),
    Tool(
        name="create_quote",
        description=(
            "Сформировать коммерческое предложение (PDF) по текущей корзине (source=cart) или по списку позиций "
            "(source=items, до 20 позиций). Цены — из каталога. Это не счёт: без реквизитов и данных клиента."
        ),
        args=QuoteArgs,
        schema=obj({
            "source": {"type": "string", "enum": ["cart", "items"]},
            "items": {"type": "array", "items": obj(
                {"product_id": {"type": "integer"}, "quantity": {"type": "integer"}}, ["product_id", "quantity"])},
        }, ["source"]),
        run=run_create_quote, status="quote", mutating=True,
    ),
    Tool(
        name="open_order_form",
        description=(
            "Показать в чате форму оформления заказа по текущей корзине: клиент сам вводит данные покупателя "
            "и доставки. Только при непустой корзине. Результат придёт системным сообщением."
        ),
        args=EmptyArgs, schema=obj({}), run=run_open_order_form, status="preparing",
    ),
    Tool(
        name="open_lead_form",
        description=(
            "Показать в чате форму заявки менеджеру (телефон, имя, город). topic — короткая суть вопроса "
            "без личных данных, до 300 символов. Результат придёт системным сообщением."
        ),
        args=LeadArgs, schema=obj({"topic": {"type": "string"}}, ["topic"]),
        run=run_open_lead_form, status="preparing", mutating=True,
    ),
]


def registry() -> dict[str, Tool]:
    return {tool.name: tool for tool in TOOLS}


def definitions() -> list[dict]:
    return [tool.definition() for tool in TOOLS]


def validate(tool: Tool, payload: dict) -> BaseModel:
    try:
        return tool.args.model_validate(payload)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'аргументы'}: {e['msg']}" for e in error.errors()
        )
        raise ToolError(f"некорректные аргументы — {problems}") from None

