import datetime
import decimal
import uuid

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Numeric, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class Base(DeclarativeBase):
    pass


class Brand(Base):
    __tablename__ = "catalog_brand"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    slug: Mapped[str]
    country: Mapped[str | None]

    products: Mapped[list["Product"]] = relationship(back_populates="brand")


class Category(Base):
    __tablename__ = "catalog_category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    slug: Mapped[str]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_category.id"))

    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(Base):
    __tablename__ = "catalog_product"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("catalog_category.id"))
    brand_id: Mapped[int] = mapped_column(ForeignKey("catalog_brand.id"))
    name: Mapped[str]
    slug: Mapped[str]
    sku: Mapped[str | None]
    description: Mapped[str]
    price: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str]
    # Цена в рублях по курсу ЦБ; считает Django
    price_rub: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 2))
    stock_qty: Mapped[int]
    characteristics: Mapped[dict] = mapped_column(JSON)
    priority: Mapped[int]
    is_active: Mapped[bool]
    created_at: Mapped[datetime.datetime]
    updated_at: Mapped[datetime.datetime]
    content_blocks: Mapped[list["ProductContentBlock"]] = relationship(
        back_populates="product", order_by="ProductContentBlock.sort_order"
    )
    category: Mapped["Category"] = relationship(back_populates="products")
    brand: Mapped["Brand"] = relationship(back_populates="products")
    images: Mapped[list["ProductImage"]] = relationship(
        back_populates="product", order_by="ProductImage.sort_order"
    )
    options: Mapped[list["ProductOption"]] = relationship(back_populates="product")

    @property
    def primary_image_path(self) -> str | None:
        if not self.images:
            return None
        primary = next((img for img in self.images if img.is_primary), None)
        return (primary or self.images[0]).image


class ProductImage(Base):
    __tablename__ = "catalog_productimage"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog_product.id"))
    image: Mapped[str]
    alt_text: Mapped[str]
    is_primary: Mapped[bool]
    sort_order: Mapped[int]

    product: Mapped["Product"] = relationship(back_populates="images")


class ProductOption(Base):
    __tablename__ = "catalog_productoption"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog_product.id"))
    name: Mapped[str]
    value: Mapped[str]
    price_delta: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2))

    product: Mapped["Product"] = relationship(back_populates="options")


class ProductContentBlock(Base):
    __tablename__ = "catalog_productcontentblock"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("catalog_product.id"))
    block_type: Mapped[str]
    title: Mapped[str]
    subtitle: Mapped[str]
    body: Mapped[str]
    image: Mapped[str | None]
    video_url: Mapped[str]
    data: Mapped[list] = mapped_column(JSON)
    sort_order: Mapped[int]

    product: Mapped["Product"] = relationship(back_populates="content_blocks")


class CurrencyRate(Base):
    __tablename__ = "catalog_currencyrate"

    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str]
    date: Mapped[datetime.date]
    rate: Mapped[decimal.Decimal] = mapped_column(Numeric(14, 6))
    fetched_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


# --- Таблицы, в которые пишет api. Миграции — только в Django.
# Значения по умолчанию Django задаёт в Python, а не в БД, поэтому здесь они продублированы.


class CartItem(Base):
    __tablename__ = "orders_cartitem"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("catalog_product.id"))
    quantity: Mapped[int]
    selected_options: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class Conversation(Base):
    __tablename__ = "chat_conversation"

    ACTIVE = "active"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str]
    prompt_epoch: Mapped[str]
    pending_lead_topic: Mapped[str]
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "chat_message"

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    EVENT = "event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chat_conversation.id"))
    role: Mapped[str]
    content: Mapped[str] = mapped_column(default="")
    api_content: Mapped[dict | list | None] = mapped_column(JSONB)
    ui: Mapped[dict | list | None] = mapped_column(JSONB)
    event_type: Mapped[str] = mapped_column(default="")
    event_payload: Mapped[dict | None] = mapped_column(JSONB)
    client_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    epoch: Mapped[str] = mapped_column(default="")
    stop_reason: Mapped[str] = mapped_column(default="")
    request_id: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class KnowledgeBaseVersion(Base):
    __tablename__ = "chat_knowledgebaseversion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    content: Mapped[str]
    is_active: Mapped[bool]
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    char_count: Mapped[int]
    token_count: Mapped[int | None]


class Quote(Base):
    __tablename__ = "chat_quote"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    number: Mapped[str]
    conversation_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    items: Mapped[list] = mapped_column(JSONB, default=list)
    total: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2))
    valid_until: Mapped[datetime.date]
    pdf_path: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LLMUsage(Base):
    __tablename__ = "chat_llmusage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(BigInteger)
    request_id: Mapped[str] = mapped_column(default="")
    configured_model: Mapped[str]
    model: Mapped[str]
    attempt_type: Mapped[str] = mapped_column(default="message")
    input_tokens: Mapped[int] = mapped_column(default=0)
    cache_write_5m_tokens: Mapped[int] = mapped_column(default=0)
    cache_write_1h_tokens: Mapped[int] = mapped_column(default=0)
    cache_read_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None]
    stop_reason: Mapped[str] = mapped_column(default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)
