import math
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db.engine import get_session
from ..db.models import Brand, Category, Product
from ..schemas.catalog import BrandOut, CategoryOut, ProductDetailOut, ProductListItemOut, ProductPageOut

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

SortOption = Literal["priority", "price_asc", "price_desc", "new"]


def _apply_filters(
    stmt: Select,
    category: str | None,
    brand: str | None,
    price_min: float | None,
    price_max: float | None,
    in_stock: bool | None,
    q: str | None,
) -> Select:
    stmt = stmt.where(Product.is_active.is_(True))
    if category:
        stmt = stmt.join(Product.category).where(Category.slug == category)
    if brand:
        stmt = stmt.join(Product.brand).where(Brand.slug == brand)
    # Цены сравниваются в рублях: у валютных товаров price — в долларах или евро
    if price_min is not None:
        stmt = stmt.where(Product.price_rub >= price_min)
    if price_max is not None:
        stmt = stmt.where(Product.price_rub <= price_max)
    if in_stock:
        stmt = stmt.where(Product.stock_qty > 0)
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    return stmt


@router.get("/products", response_model=ProductPageOut)
async def list_products(
    session: AsyncSession = Depends(get_session),
    category: str | None = Query(None, description="slug категории"),
    brand: str | None = Query(None, description="slug бренда"),
    price_min: float | None = Query(None, ge=0),
    price_max: float | None = Query(None, ge=0),
    in_stock: bool | None = Query(None),
    q: str | None = Query(None, max_length=200, description="поиск по названию"),
    sort: SortOption = Query("priority"),
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1),
):
    settings = get_settings()
    effective_page_size = min(page_size or settings.default_page_size, settings.max_page_size)

    count_stmt = _apply_filters(
        select(func.count(Product.id)), category, brand, price_min, price_max, in_stock, q
    )
    total = (await session.execute(count_stmt)).scalar_one()

    data_stmt = _apply_filters(
        select(Product).options(
            selectinload(Product.category),
            selectinload(Product.brand),
            selectinload(Product.images),
        ),
        category, brand, price_min, price_max, in_stock, q,
    )

    if sort == "price_asc":
        data_stmt = data_stmt.order_by(Product.price_rub.asc().nulls_last())
    elif sort == "price_desc":
        data_stmt = data_stmt.order_by(Product.price_rub.desc().nulls_last())
    elif sort == "new":
        data_stmt = data_stmt.order_by(Product.created_at.desc())
    else:
        data_stmt = data_stmt.order_by(Product.priority.desc(), Product.name.asc())

    data_stmt = data_stmt.offset((page - 1) * effective_page_size).limit(effective_page_size)
    products = (await session.execute(data_stmt)).scalars().all()

    return ProductPageOut(
        items=[ProductListItemOut.model_validate(p) for p in products],
        page=page,
        page_size=effective_page_size,
        total=total,
        total_pages=max(1, math.ceil(total / effective_page_size)) if total else 1,
    )


@router.get("/products/{slug}", response_model=ProductDetailOut)
async def get_product(slug: str, session: AsyncSession = Depends(get_session)):
    stmt = (
        select(Product)
        .where(Product.slug == slug, Product.is_active.is_(True))
        .options(
            selectinload(Product.category),
            selectinload(Product.brand),
            selectinload(Product.images),
            selectinload(Product.options),
            selectinload(Product.content_blocks), 
        )
    )
    product = (await session.execute(stmt)).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Товар не найден")
    return ProductDetailOut.model_validate(product)


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Category).order_by(Category.name))
    return result.scalars().all()


@router.get("/brands", response_model=list[BrandOut])
async def list_brands(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Brand).order_by(Brand.name))
    return result.scalars().all()
