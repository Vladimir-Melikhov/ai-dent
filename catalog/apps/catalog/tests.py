"""Курсы ЦБ и цена в рублях."""

import datetime
from decimal import Decimal
from unittest import mock

import pytest
from django.core.management import call_command

from .currency import fetch_cbr, rate_on, stale_currencies, update_rates
from .models import Brand, Category, CurrencyRate, Product

pytestmark = pytest.mark.django_db

CBR_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="07.10.2026" name="Foreign Currency Market">
<Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Name>Доллар США</Name><Value>95,5000</Value></Valute>
<Valute ID="R01239"><NumCode>978</NumCode><CharCode>EUR</CharCode><Nominal>1</Nominal><Name>Евро</Name><Value>104,2500</Value></Valute>
<Valute ID="R01375"><NumCode>156</NumCode><CharCode>CNY</CharCode><Nominal>10</Nominal><Name>Юань</Name><Value>133,1000</Value></Valute>
</ValCurs>""".encode("windows-1251")


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def eur_product():
    return Product.objects.create(name="Лупа Lercher", brand=Brand.objects.create(name="Lercher"),
                                  category=Category.objects.create(name="Лупы"), price=Decimal("1000"), currency="EUR")


def test_fetch_parses_cbr_xml():
    with mock.patch("urllib.request.urlopen", return_value=FakeResponse(CBR_XML)):
        date, rates = fetch_cbr(datetime.date(2026, 10, 7))
    assert date == datetime.date(2026, 10, 7)
    assert rates == {"USD": Decimal("95.500000"), "EUR": Decimal("104.250000")}


def test_update_rates_recalculates_prices(eur_product):
    assert eur_product.price_rub is None  # курса ещё нет — цена по запросу
    with mock.patch("urllib.request.urlopen", return_value=FakeResponse(CBR_XML)):
        result = update_rates(datetime.date(2026, 10, 7))
    eur_product.refresh_from_db()
    assert eur_product.price_rub == Decimal("104250.00") and result["recalculated"] == 1


def test_rate_on_weekend_uses_last_set_rate():
    CurrencyRate.objects.create(currency="USD", date=datetime.date(2026, 10, 9), rate=Decimal("90"))  # пятница
    assert rate_on("USD", datetime.date(2026, 10, 11)) == Decimal("90")  # воскресенье
    assert rate_on("USD", datetime.date(2026, 10, 8)) is None
    assert rate_on("RUB") == Decimal(1)


def test_stale_rate_warns_in_telegram(eur_product):
    CurrencyRate.objects.create(currency="EUR", date=datetime.date(2026, 9, 1), rate=Decimal("100"))
    assert stale_currencies(datetime.date(2026, 10, 7)) == ["EUR"]
    with mock.patch("urllib.request.urlopen", side_effect=OSError("нет сети")), \
            mock.patch("apps.catalog.management.commands.update_currency_rates.send_telegram") as telegram:
        call_command("update_currency_rates")
    assert "EUR" in telegram.call_args.args[0]


def test_admin_rates_page(admin_client):
    page = admin_client.get("/admin/catalog/currencyrate/").content.decode()
    assert "Обновить курсы ЦБ" in page


# --- оптимизация фото

def _upload(size, fmt="PNG", name="big.png"):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


@pytest.mark.django_db
def test_uploaded_photo_becomes_small_webp(eur_product, settings, tmp_path):
    from PIL import Image

    from .models import ProductImage

    settings.MEDIA_ROOT = tmp_path
    image = ProductImage.objects.create(product=eur_product, image=_upload((4000, 3000)))
    assert image.image.name.endswith(".webp")
    with Image.open(image.image.path) as opened:
        assert opened.format == "WEBP" and max(opened.size) == 1600


def test_product_admin_has_loupe_checkbox_and_rows(admin_client, eur_product):
    response = admin_client.get(f"/admin/catalog/product/{eur_product.pk}/change/")
    page = response.content.decode()
    assert response.status_code == 200
    assert 'id="id_is_loupe"' in page and 'id="id_loupe_kind"' in page
    assert "loupe_options-TOTAL_FORMS" in page and "loupe_distances-TOTAL_FORMS" in page and "loupe_ipds-TOTAL_FORMS" in page


def test_loupe_migration_copies_json_to_tables():
    from importlib import import_module

    migration = import_module("apps.catalog.migrations.0007_loupe_relational")
    assert callable(migration.copy_json_to_tables)


def test_loupe_admin_fields_take_plain_numbers_and_add_units():
    from django import forms

    from .admin_fields import CharacteristicsField, NumberField, RangeField

    zoom = NumberField("×", glue="")
    assert zoom.clean("2,8") == "2.8×" and zoom.clean("3.0") == "3×" and zoom.prepare_value("2.8×") == "2.8"
    assert NumberField("мм").clean("200") == "200 мм"
    tilt = RangeField("°", single=lambda n, u: f"0–{n}°", allow_fixed=True)
    assert tilt.clean("0-40") == "0–40°" and tilt.clean("фикс") == "фикс"
    try:
        zoom.clean("abc")
        raise AssertionError("нечисловое увеличение должно отклоняться")
    except forms.ValidationError:
        pass
    chars = CharacteristicsField()
    data = {"Гарантия": "12 мес", "Оптика": ["Увеличение: 2.8x"]}
    assert chars.to_python(chars.prepare_value(data)) == data


def test_loupe_product_needs_all_parameters(admin_client, eur_product):
    from .admin import ProductForm

    form = ProductForm(instance=eur_product, data={
        "name": "L", "brand": eur_product.brand_id, "category": eur_product.category_id, "price": "1",
        "currency": "RUB", "stock_qty": "1", "is_loupe": "on", "is_active": "on", "priority": "0", "slug": "l",
    })
    assert not form.is_valid()
    assert {"loupe_kind", "loupe_depth", "loupe_weight", "loupe_tilt", "loupe_vertical", "loupe_optics",
            "loupe_mount"} <= set(form.errors)
