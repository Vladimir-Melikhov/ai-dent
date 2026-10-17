import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import AnonSession
from apps.catalog.models import Brand, Category, Product
from .models import CartItem, Order, OrderItem

pytestmark = pytest.mark.django_db


@pytest.fixture
def product():
    return Product.objects.create(
        name="Лупа", brand=Brand.objects.create(name="Бренд"),
        category=Category.objects.create(name="Лупы"), price=500,
    )


@pytest.mark.parametrize("quantity", [0, 1000])
def test_cart_quantity_limited_in_db(product, quantity):
    """Правило количества 1–999 закреплено в БД — одно для витрины и агента (ТЗ D16)."""
    with pytest.raises(IntegrityError), transaction.atomic():
        CartItem.objects.create(session=AnonSession.objects.create(), product=product, quantity=quantity)


def test_rub_product_gets_price_rub(product):
    assert product.price_rub == product.price


def test_order_lines(product):
    order = Order.objects.create(number="AD-1")
    OrderItem.objects.create(order=order, product=product, product_name="Лупа", unit_price=500, line_total=500)
    OrderItem.objects.create(
        order=order, kind=OrderItem.Kind.DELIVERY, product_name="Доставка", unit_price=300, line_total=300,
    )
    # Строка доставки в заказе одна
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderItem.objects.create(
            order=order, kind=OrderItem.Kind.DELIVERY, product_name="Доставка", unit_price=1, line_total=1,
        )
    # Товарная строка без товара невозможна
    with pytest.raises(IntegrityError), transaction.atomic():
        OrderItem.objects.create(order=order, product_name="?", unit_price=1, line_total=1)


# --- единая формула цены, правило допуска, версия корзины (ТЗ §10.1, T7, T15)

import datetime
import json
from decimal import Decimal

from django.utils import timezone

from apps.catalog.models import CurrencyRate, ProductOption
from .models import MAX_ITEM_QUANTITY
from .services import CartChanged, cart_lines, cart_version, create_order_from_cart


@pytest.fixture
def usd_product():
    CurrencyRate.objects.create(currency="USD", date=timezone.localdate(), rate=Decimal("95.5"))
    return Product.objects.create(
        name="Микроскоп импортный", brand=Brand.objects.create(name="Labomed"),
        category=Category.objects.create(name="Микроскопы"), price=Decimal("1000"), currency="USD", stock_qty=0,
    )


def cart_json(client):
    return client.get("/cart/").json()


def test_usd_product_priced_in_rubles(usd_product):
    assert usd_product.price_rub == Decimal("95500.00")


def test_no_rate_means_no_price_and_cannot_buy(client):
    product = Product.objects.create(
        name="Без курса", brand=Brand.objects.create(name="B"), category=Category.objects.create(name="C"),
        price=Decimal("10"), currency="EUR",
    )
    assert product.price_rub is None
    response = client.post("/cart/add/", data=json.dumps({"product_id": product.pk}), content_type="application/json")
    assert response.status_code == 400 and "цена по запросу" in response.json()["error"]


def test_out_of_stock_can_be_added(client, usd_product):
    """«Под заказ» можно везде (D16): остаток не проверяется."""
    response = client.post("/cart/add/", data=json.dumps({"product_id": usd_product.pk}), content_type="application/json")
    assert response.status_code == 200
    item = cart_json(client)["items"][0]
    assert item["under_order"] is True and item["currency"] == "USD" and item["price"] == 95500.0


def test_formula_with_options_in_cart_and_order(client, product, usd_product):
    """T7: опции, заданные напрямую, и валюта дают одну цену и в корзине, и в заказе."""
    client.post("/cart/add/", data=json.dumps({"product_id": product.pk, "quantity": 2}), content_type="application/json")
    client.post("/cart/add/", data=json.dumps({"product_id": usd_product.pk}), content_type="application/json")
    option = ProductOption.objects.create(product=usd_product, name="Вариоскоп", value="да", price_delta=Decimal("10"))
    CartItem.objects.filter(product=usd_product).update(selected_options={"option_ids": [option.pk]})

    data = cart_json(client)
    prices = {i["name"]: (i["price"], i["line_total"]) for i in data["items"]}
    assert prices["Лупа"] == (500.0, 1000.0)  # без опций — как раньше
    assert prices["Микроскоп импортный"] == (96455.0, 96455.0)  # 95 500 + 10 × 95,5
    assert data["total"] == 97455.0 and data["has_foreign_currency"] is True

    owner = {"session": CartItem.objects.first().session}
    order = create_order_from_cart(owner, {"contact_phone": "+79990000001"}, Order.CreatedBy.CATALOG, data["version"])
    lines = {i.product_name: i for i in order.items.all()}
    imported = lines["Микроскоп импортный"]
    assert imported.unit_price == Decimal("96455.00") and imported.source_price == Decimal("1000.00")
    assert imported.source_currency == "USD" and imported.exchange_rate == Decimal("95.5")
    assert imported.selected_options == [{"id": option.pk, "name": "Вариоскоп", "value": "да", "price_delta": "10.00"}]
    assert order.total_amount == order.total_at_creation == Decimal("97455.00")
    assert order.number.startswith("AD-") and not CartItem.objects.exists()


def test_checkout_rejects_changed_cart(client, product):
    """T15: состав или цена изменились между показом и отправкой — 409, заказ не создан."""
    client.post("/cart/add/", data=json.dumps({"product_id": product.pk}), content_type="application/json")
    version = cart_json(client)["version"]
    product.price = Decimal("600")
    product.save()

    body = {"contact_phone": "+7 999 000 00 01", "payment_method": "invoice", "cart_version": version, "consent": True}
    response = client.post("/cart/checkout/", data=json.dumps(body), content_type="application/json")
    assert response.status_code == 409 and response.json()["error"] == "cart_changed"
    assert not Order.objects.exists()

    body["cart_version"] = cart_json(client)["version"]
    response = client.post("/cart/checkout/", data=json.dumps(body), content_type="application/json")
    assert response.status_code == 200
    assert Order.objects.get().total_amount == Decimal("600.00")


def test_checkout_requires_version(client, product):
    client.post("/cart/add/", data=json.dumps({"product_id": product.pk}), content_type="application/json")
    body = {"contact_phone": "+7 999 000 00 01", "payment_method": "invoice", "consent": True}
    assert client.post("/cart/checkout/", data=json.dumps(body), content_type="application/json").status_code == 400


def test_checkout_requires_consent(client, product):
    """Согласие на обработку ПДн проверяет сервер, а не только галочка на странице; время согласия — в заказе."""
    client.post("/cart/add/", data=json.dumps({"product_id": product.pk}), content_type="application/json")
    body = {"contact_phone": "+7 999 000 00 01", "payment_method": "invoice", "cart_version": cart_json(client)["version"]}
    response = client.post("/cart/checkout/", data=json.dumps(body), content_type="application/json")
    assert response.status_code == 400 and "согласие" in response.json()["error"]
    assert not Order.objects.exists()

    body["consent"] = True
    assert client.post("/cart/checkout/", data=json.dumps(body), content_type="application/json").status_code == 200
    assert Order.objects.get().pd_consent_at is not None


def test_version_changes_with_quantity(client, product):
    client.post("/cart/add/", data=json.dumps({"product_id": product.pk}), content_type="application/json")
    first = cart_json(client)["version"]
    client.post("/cart/add/", data=json.dumps({"product_id": product.pk}), content_type="application/json")
    assert cart_json(client)["version"] != first


def test_shortlist_price_in_rubles(client, usd_product):
    client.post("/accounts/shortlist/toggle/", data=json.dumps({"product_id": usd_product.pk}),
                content_type="application/json")
    assert client.get("/accounts/shortlist/").json()["items"][0]["price"] == 95500.0
