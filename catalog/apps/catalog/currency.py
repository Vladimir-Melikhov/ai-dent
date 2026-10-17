"""Курсы ЦБ РФ и цена в рублях (ТЗ §10.7, D17)."""

import datetime
import logging
import urllib.request
import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .models import CurrencyRate, Product

logger = logging.getLogger(__name__)

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp?date_req={date:%d/%m/%Y}"
CURRENCIES = ("USD", "EUR")
KOPECK = Decimal("0.01")
STALE_AFTER = datetime.timedelta(days=7)


def to_rub(amount: Decimal, rate: Decimal) -> Decimal:
    """Округление до копеек, половина вверх — за единицу товара (ТЗ §10.7)."""
    return (amount * rate).quantize(KOPECK, rounding=ROUND_HALF_UP)


def rate_on(currency: str, date: datetime.date | None = None) -> Decimal | None:
    """Курс на дату — последний установленный не позже неё (в выходные действует пятничный)."""
    if currency == Product.Currency.RUB:
        return Decimal(1)
    date = date or timezone.localdate()
    row = CurrencyRate.objects.filter(currency=currency, date__lte=date).order_by("-date").first()
    return row.rate if row else None


def price_rub(product: Product, date: datetime.date | None = None) -> Decimal | None:
    rate = rate_on(product.currency, date)
    return to_rub(product.price, rate) if rate is not None else None


def fetch_cbr(date: datetime.date) -> tuple[datetime.date, dict[str, Decimal]]:
    """Официальный ежедневный XML ЦБ: курс за одну единицу = значение / номинал."""
    with urllib.request.urlopen(CBR_URL.format(date=date), timeout=10) as response:
        root = ET.fromstring(response.read())
    rates_date = datetime.datetime.strptime(root.attrib["Date"], "%d.%m.%Y").date()
    rates = {}
    for valute in root.iter("Valute"):
        code = valute.findtext("CharCode")
        if code in CURRENCIES:
            value = Decimal(valute.findtext("Value").replace(",", "."))
            nominal = Decimal(valute.findtext("Nominal"))
            rates[code] = (value / nominal).quantize(Decimal("0.000001"))
    return rates_date, rates


@transaction.atomic
def store_rates(rates_date: datetime.date, rates: dict[str, Decimal]) -> None:
    for currency, rate in rates.items():
        CurrencyRate.objects.update_or_create(currency=currency, date=rates_date, defaults={"rate": rate})


def recalc_prices() -> int:
    """Пересчитать price_rub у валютных товаров по текущему курсу. Возвращает число изменённых."""
    changed = 0
    for product in Product.objects.exclude(currency=Product.Currency.RUB):
        new = price_rub(product)
        if new != product.price_rub:
            Product.objects.filter(pk=product.pk).update(price_rub=new)
            changed += 1
    return changed


def stale_currencies(today: datetime.date | None = None) -> list[str]:
    """Валюты каталога, для которых курса нет или он старше 7 дней."""
    today = today or timezone.localdate()
    used = set(Product.objects.exclude(currency=Product.Currency.RUB).values_list("currency", flat=True))
    stale = []
    for currency in sorted(used):
        latest = CurrencyRate.objects.filter(currency=currency).order_by("-date").first()
        if latest is None or today - latest.date > STALE_AFTER:
            stale.append(currency)
    return stale


def update_rates(today: datetime.date | None = None) -> dict:
    """Загрузить курсы ЦБ (на сегодня и завтра — ЦБ публикует завтрашний курс днём) и пересчитать цены."""
    today = today or timezone.localdate()
    loaded = {}
    for day in (today, today + datetime.timedelta(days=1)):
        try:
            rates_date, rates = fetch_cbr(day)
        except Exception:
            logger.warning("Не удалось загрузить курсы ЦБ на %s", day, exc_info=True)
            continue
        store_rates(rates_date, rates)
        loaded[rates_date] = rates
    return {"loaded": loaded, "recalculated": recalc_prices(), "stale": stale_currencies(today)}
