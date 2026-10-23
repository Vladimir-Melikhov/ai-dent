from decimal import Decimal
from pydantic import BaseModel, ConfigDict


class BrandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str


class ProductImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    image: str
    alt_text: str
    is_primary: bool
    sort_order: int


class ProductOptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    value: str
    price_delta: Decimal


class ContentBlockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    block_type: str
    title: str
    subtitle: str
    body: str
    image: str | None = None
    video_url: str
    data: list
    sort_order: int


class ProductListItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    price: Decimal  # исходная цена в валюте каталога
    currency: str
    # Цена в рублях по курсу ЦБ — её показывают карточки, она попадает в корзину и счёт
    price_rub: Decimal | None
    stock_qty: int
    priority: int
    category: CategoryOut
    brand: BrandOut
    primary_image_path: str | None = None


class ProductDetailOut(ProductListItemOut):
    description: str
    characteristics: dict
    images: list[ProductImageOut]
    options: list[ProductOptionOut]
    content_blocks: list[ContentBlockOut] = []


class ProductPageOut(BaseModel):
    items: list[ProductListItemOut]
    page: int
    page_size: int
    total: int
    total_pages: int
