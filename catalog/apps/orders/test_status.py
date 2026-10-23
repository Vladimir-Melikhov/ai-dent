"""Заказ из чата, статусы, счёт при подтверждении, правка состава."""

import datetime
import json
import os
from decimal import Decimal
from html.parser import HTMLParser
from smtplib import SMTPException
from unittest import mock

import pytest
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.utils import timezone

from apps.catalog.models import Brand, Category, CurrencyRate, Product
from apps.chat.models import Conversation, Message
from conftest import next_published
from .models import Invoice, Order, OrderChange, OrderItem, OrderStatusHistory
from .invoice import buyer_lines, invoice_items, invoice_notes
from .services import (
    InvoiceError, OrderEditError, TransitionError, change_status, edit_order_items, invoice_available, valid_inn,
)

pytestmark = pytest.mark.django_db
S = Order.Status
LEGAL = {
    "buyer_type": "legal", "company_name": "ООО «Улыбка»", "inn": "1234567894", "contact_name": "Пётр Сидоров",
    "contact_phone": "+7 999 111 22 33", "contact_email": "client@example.com", "delivery_city": "Казань",
    "delivery_address": "ул. Баумана, 1", "comment": "Позвонить заранее", "consent": True,
}


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    settings.MANAGER_EMAIL = "admin@example.com"
    settings.NOTIFY_EMAILS = ["sales@example.com"]


@pytest.fixture(autouse=True)
def no_telegram():
    with mock.patch("apps.orders.notifications.send_telegram") as status, \
            mock.patch("apps.orders.telegram.send_telegram") as failure, \
            mock.patch("apps.orders.views.send_order_telegram"):
        yield {"status": status, "failure": failure}


@pytest.fixture
def products():
    CurrencyRate.objects.create(currency="USD", date=timezone.localdate(), rate=Decimal("95.5"))
    brand, category = Brand.objects.create(name="Zumax"), Category.objects.create(name="Оптика")
    return {
        "loupe": Product.objects.create(name="Лупа Галилей", brand=brand, category=category, price=48500, stock_qty=3),
        "usd": Product.objects.create(name="Микроскоп Labomed", brand=brand, category=category,
                                      price=Decimal("1000"), currency="USD", stock_qty=0),
    }


def post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


def add(client, product, quantity=1):
    assert post(client, "/cart/add/", {"product_id": product.pk, "quantity": quantity}).status_code == 200


def version(client):
    return client.get("/cart/").json()["version"]


@pytest.fixture
def dialog(client, redis_client):
    """Гость открыл чат — у сессии есть текущий диалог."""
    return Conversation.objects.get(pk=post(client, "/chat/ticket/", {}).json()["conversation_id"])


def chat_order(client, **fields):
    return post(client, "/chat/order/", {**LEGAL, "cart_version": version(client), **fields})


def make_order(client, products, capture, **fields):
    add(client, products["loupe"], 2)
    add(client, products["usd"])
    with capture(execute=True):
        response = chat_order(client, **fields)
    assert response.status_code == 200, response.content
    return Order.objects.get(number=response.json()["number"])


# --- ИНН и поля покупателя


def test_inn_checksum():
    assert valid_inn("1234567894", 10) and valid_inn("771234567859", 12)
    assert not valid_inn("1234567895", 10) and not valid_inn("771234567850", 12)
    assert not valid_inn("1234567894", 12) and not valid_inn("77070838ab", 10)


def test_chat_order_full_path(client, products, dialog, channel, django_capture_on_commit_callbacks):
    """Заказ в pending_confirmation, created_by = ai, цены из каталога, связь с диалогом, уведомления."""
    listener = channel(dialog.pk)
    order = make_order(client, products, django_capture_on_commit_callbacks)

    assert order.status == S.PENDING_CONFIRMATION and order.created_by == Order.CreatedBy.AI
    assert order.conversation == dialog and order.payment_method == Order.PaymentMethod.INVOICE
    assert (order.buyer_type, order.company_name, order.inn, order.contact_name) == (
        "legal", "ООО «Улыбка»", "1234567894", "Пётр Сидоров")
    assert order.contact_phone == "+79991112233" and order.delivery_city == "Казань" and order.pd_consent_at
    # Сумма — по каталогу, другой у агента взяться неоткуда
    assert order.total_amount == order.total_at_creation == Decimal("48500") * 2 + Decimal("95500")
    assert order.status_history.get().actor == OrderStatusHistory.Actor.AI
    assert not hasattr(order, "invoice") and not invoice_available(order)

    # В диалог — только номер и сумма; данные покупателя в событие не попадают
    event = next_published(listener)
    assert event["event_type"] == "order_created"
    assert event["payload"] == {"number": order.number, "total": 192500.0}
    stored = Message.objects.get(role="event")
    assert "Улыбка" not in json.dumps(stored.event_payload, ensure_ascii=False) + stored.content
    assert mail.outbox[0].subject.startswith(f"Заказ №{order.number}")  # письмо о новом заказе — как раньше


@pytest.mark.parametrize("fields, error", [
    ({"inn": "1234567895"}, "ИНН организации не проходит проверку"),
    ({"inn": "12345"}, "ИНН организации — 10 цифр"),
    ({"contact_name": ""}, "контактное лицо"),
    ({"company_name": ""}, "название организации"),
    ({"buyer_type": "ip", "buyer_name": "Иванов Иван Иванович", "inn": "1234567894"}, "ИНН ИП — 12 цифр"),
    ({"buyer_type": "individual", "buyer_name": ""}, "ФИО"),
    ({"delivery_city": ""}, "город"),
    ({"consent": False}, "согласие"),
    ({"contact_phone": "123"}, "телефон"),
    ({"contact_email": "не почта"}, "email"),
    ({"buyer_type": "robot"}, "тип покупателя"),
])
def test_chat_order_validates_buyer_by_type(client, products, fields, error):
    add(client, products["loupe"])
    response = chat_order(client, **fields)
    assert response.status_code == 400 and error.lower() in response.json()["error"].lower()
    assert not Order.objects.exists()


def test_ip_and_individual_contact_is_buyer(client, products, django_capture_on_commit_callbacks):
    ip = make_order(client, products, django_capture_on_commit_callbacks,
                    buyer_type="ip", buyer_name="Иванов Иван Иванович", inn="771234567859", company_name="лишнее")
    assert (ip.contact_name, ip.company_name, ip.inn) == ("Иванов Иван Иванович", "", "771234567859")
    assert buyer_lines(ip)[:2] == ["ИП Иванов Иван Иванович", "ИНН: 771234567859"]

    person = make_order(client, products, django_capture_on_commit_callbacks,
                        buyer_type="individual", buyer_name="Петрова Анна", inn="1234567894")
    assert (person.contact_name, person.inn) == ("Петрова Анна", "")
    assert buyer_lines(person)[0] == "Петрова Анна"


def test_chat_order_rejects_changed_cart(client, products):
    """Заказ из чата: состав изменился после показа формы — 409, заказ не создан."""
    add(client, products["loupe"])
    stale = version(client)
    add(client, products["loupe"])
    response = post(client, "/chat/order/", {**LEGAL, "cart_version": stale})
    assert response.status_code == 409 and response.json()["error"] == "cart_changed"
    assert not Order.objects.exists()


def test_under_order_product_can_be_ordered(client, products, django_capture_on_commit_callbacks):
    """Товар «под заказ» оформляется."""
    add(client, products["usd"])
    with django_capture_on_commit_callbacks(execute=True):
        assert chat_order(client).status_code == 200
    assert Order.objects.get().items.get().product == products["usd"]


# --- статусы


@pytest.fixture
def order(client, products, django_capture_on_commit_callbacks):
    return make_order(client, products, django_capture_on_commit_callbacks)


ALLOWED = {(S.PENDING_CONFIRMATION, S.CONFIRMED), (S.PENDING_CONFIRMATION, S.CANCELLED),
           (S.CONFIRMED, S.PAID), (S.CONFIRMED, S.CANCELLED)}


@pytest.mark.parametrize("start", [S.PENDING_CONFIRMATION, S.CONFIRMED, S.PAID, S.CANCELLED, S.DRAFT])
@pytest.mark.parametrize("target", [S.CONFIRMED, S.PAID, S.CANCELLED, S.PENDING_CONFIRMATION])
def test_only_allowed_transitions(order, start, target, django_capture_on_commit_callbacks):
    Order.objects.filter(pk=order.pk).update(status=start)
    if (start, target) in ALLOWED:
        with django_capture_on_commit_callbacks(execute=True):
            change_status(order, target)
        assert Order.objects.get(pk=order.pk).status == target
    else:
        with pytest.raises(TransitionError):
            change_status(order, target)
        assert Order.objects.get(pk=order.pk).status == start


def test_confirm_issues_invoice_and_sends_it(client, dialog, order, channel, no_telegram, admin_user,
                                              django_capture_on_commit_callbacks):
    """Заказ из чата: до подтверждения счёта нет, после — есть, письма с PDF ушли, событие в диалоге."""
    url = f"/cart/invoice/{order.number}/"
    assert client.get(url).status_code == 404
    listener = channel(dialog.pk)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, S.CONFIRMED, actor_user=admin_user)

    order.refresh_from_db()
    invoice = order.invoice
    assert order.status == S.CONFIRMED and order.payment_status == Order.PaymentStatus.AWAITING and order.confirmed_at
    assert invoice.issued_at == order.confirmed_at and not invoice.legacy and invoice.emailed_at
    assert client.get(url).status_code == 200
    history = order.status_history.get(to_status=S.CONFIRMED)
    assert history.actor == OrderStatusHistory.Actor.MANAGER and history.actor_user == admin_user

    internal, personal = mail.outbox
    assert internal.to == ["admin@example.com", "sales@example.com"] and personal.to == ["client@example.com"]
    for letter in (internal, personal):
        name, content, mimetype = letter.attachments[0]
        assert name == f"schet-{order.number}.pdf" and mimetype == "application/pdf" and content.startswith(b"%PDF")
    assert f"http://testserver{url}" in personal.body and "admin@example.com" not in personal.body
    assert "1234567894" in internal.body  # внутри компании — полные данные

    event = next_published(listener)
    assert event["event_type"] == "order_confirmed"
    assert event["payload"] == {"number": order.number, "total": float(order.total_amount), "invoice_url": url}
    # Telegram — без ПДн: тип, номер, статус, ссылка
    text = no_telegram["status"].call_args.args[0]
    assert text.startswith(f"Заказ № {order.number}: подтверждён") and "Улыбка" not in text and "+7" not in text


def test_checkout_order_gets_invoice_after_confirmation(client, products, django_capture_on_commit_callbacks):
    """Заказ с /checkout/: при оформлении счёта нет, в ответе нет invoice_url; после подтверждения — есть."""
    add(client, products["loupe"])
    body = {"contact_phone": "+7 999 000 00 01", "contact_email": "buyer@example.com", "payment_method": "invoice",
            "cart_version": version(client), "contact_name": "Анна", "requisites": "ООО Ромашка, ИНН 1234567894",
            "consent": True}
    with django_capture_on_commit_callbacks(execute=True):
        data = post(client, "/cart/checkout/", body).json()
    assert data["ok"] and "invoice_url" not in data
    order = Order.objects.get(number=data["number"])
    assert not Invoice.objects.exists() and client.get(f"/cart/invoice/{order.number}/").status_code == 404

    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, S.CONFIRMED)
    assert client.get(f"/cart/invoice/{order.number}/").status_code == 200
    assert [m.to for m in mail.outbox] == [["admin@example.com", "sales@example.com"], ["buyer@example.com"]]
    # Покупатель с /checkout/ — как раньше: имя, контакты, реквизиты текстом
    assert buyer_lines(order)[0] == "Анна"


def test_pdf_failure_keeps_order_pending(order, no_telegram, django_capture_on_commit_callbacks):
    with mock.patch("apps.orders.invoice.generate_invoice_pdf", side_effect=OSError("диск переполнен")), \
            django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(InvoiceError, match="Счёт не сформирован, заказ не подтверждён: диск переполнен"):
            change_status(order, S.CONFIRMED)
    order.refresh_from_db()
    assert order.status == S.PENDING_CONFIRMATION and order.confirmed_at is None
    assert not Invoice.objects.exists() and not order.status_history.filter(to_status=S.CONFIRMED).exists()
    assert not Message.objects.filter(event_type="order_confirmed").exists() and not callbacks
    no_telegram["failure"].assert_called_once_with(f"Не удалось сформировать счёт по заказу № {order.number}")


class FailingBackend(BaseEmailBackend):
    def send_messages(self, messages):
        raise SMTPException("SMTP недоступен")


def test_smtp_failure_and_resend(order, settings, no_telegram, admin_client, django_capture_on_commit_callbacks):
    settings.EMAIL_BACKEND = "apps.orders.test_status.FailingBackend"
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, S.CONFIRMED)
    order.refresh_from_db()
    assert order.status == S.CONFIRMED and order.invoice.emailed_at is None
    assert no_telegram["status"].call_args.args[0].startswith(f"Письмо со счётом по заказу № {order.number} не отправлено")

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()
    response = admin_client.post("/admin/orders/order/", {"action": "resend_invoices", "_selected_action": [order.pk]},
                                 follow=True)
    assert "счёт отправлен повторно" in response.content.decode()
    order.invoice.refresh_from_db()
    assert order.invoice.emailed_at and len(mail.outbox) == 2


def test_paid_and_cancelled(client, order, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, S.CONFIRMED)
        change_status(order, S.PAID)
    order.refresh_from_db()
    assert order.payment_status == Order.PaymentStatus.PAID and order.paid_at
    assert client.get(f"/cart/invoice/{order.number}/").status_code == 200
    assert not Message.objects.exists()  # заказ без диалога — событий нет
    with pytest.raises(TransitionError):
        change_status(order, S.CANCELLED)  # оплаченный — конечный статус


def test_cancelled_order_has_no_invoice(client, order, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, S.CONFIRMED)
        change_status(order, S.CANCELLED)
    assert client.get(f"/cart/invoice/{order.number}/").status_code == 404


def test_legacy_invoice(client, order, settings, django_capture_on_commit_callbacks):
    """Старый счёт по неподтверждённому заказу скачивается; при подтверждении перевыпускается."""
    os.makedirs(os.path.join(settings.MEDIA_ROOT, "invoices"))
    with open(os.path.join(settings.MEDIA_ROOT, "invoices", "old.pdf"), "wb") as f:
        f.write(b"%PDF old")
    old = Invoice.objects.create(order=order, number=f"INV-{order.number}", pdf_path="invoices/old.pdf", legacy=True,
                                 issued_at=timezone.now() - datetime.timedelta(days=30))
    url = f"/cart/invoice/{order.number}/"
    assert client.get(url).status_code == 200

    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, S.CONFIRMED)
    invoice = Invoice.objects.get(pk=old.pk)
    assert not invoice.legacy and invoice.issued_at > old.issued_at and invoice.pdf_path != "invoices/old.pdf"

    with django_capture_on_commit_callbacks(execute=True):
        change_status(order, S.CANCELLED)
    assert client.get(url).status_code == 404


def test_legacy_invoice_of_cancelled_order_is_hidden(client, order, settings):
    Invoice.objects.create(order=order, number="INV-old", pdf_path="invoices/old.pdf", legacy=True)
    Order.objects.filter(pk=order.pk).update(status=S.CANCELLED)
    assert client.get(f"/cart/invoice/{order.number}/").status_code == 404


# --- валюта при подтверждении и правка состава


def tomorrow():
    return timezone.now() + datetime.timedelta(days=1)


def test_currency_recalculated_at_confirmation(order, django_capture_on_commit_callbacks):
    CurrencyRate.objects.create(currency="USD", date=timezone.localdate(tomorrow()), rate=Decimal("100"))
    microscope = order.items.get(source_currency="USD")
    assert microscope.unit_price == Decimal("95500") and microscope.exchange_rate == Decimal("95.5")

    with mock.patch("apps.orders.services.timezone.now", return_value=tomorrow()), \
            django_capture_on_commit_callbacks(execute=True):
        change_status(order, S.CONFIRMED)

    order.refresh_from_db()
    microscope.refresh_from_db()
    assert microscope.unit_price == Decimal("100000") and microscope.exchange_rate == Decimal("100")
    assert order.items.get(source_currency="RUB").unit_price == Decimal("48500")
    assert order.total_amount == Decimal("197000") and order.total_at_creation == Decimal("192500")
    assert invoice_notes(order) == ["Цены товаров в иностранной валюте пересчитаны в рубли по курсу ЦБ РФ на дату счёта."]


def test_manager_edits_before_confirmation(order, admin_user, django_capture_on_commit_callbacks):
    """Количество, цена (скидка), удаление, доставка — в истории; итог пересчитан."""
    loupe = order.items.get(source_currency="RUB")
    microscope = order.items.get(source_currency="USD")
    edit_order_items(order, [
        {"action": "quantity", "item_id": loupe.pk, "value": 3},
        {"action": "price", "item_id": microscope.pk, "value": "90000"},
        {"action": "delivery", "value": "1500"},
    ], admin_user)
    order.refresh_from_db()
    assert order.total_amount == Decimal("48500") * 3 + Decimal("90000") + Decimal("1500")
    assert order.total_at_creation == Decimal("192500")

    changes = list(order.changes.order_by("pk"))
    assert [c.action for c in changes] == ["quantity_changed", "price_changed", "delivery_added"]
    assert all(c.actor_user == admin_user for c in changes)
    price = changes[1].details
    assert (price["before"], price["after"], price["diff"], price["percent"]) == ("95500.00", "90000.00", "-5500.00", "-5.76")
    assert price["total_before"] == "241000.00" and price["total_after"] == "235500.00"

    # Цена менеджера при подтверждении по курсу не пересчитывается; доставка — отдельной строкой последней
    CurrencyRate.objects.create(currency="USD", date=timezone.localdate(tomorrow()), rate=Decimal("100"))
    with mock.patch("apps.orders.services.timezone.now", return_value=tomorrow()), \
            django_capture_on_commit_callbacks(execute=True):
        change_status(order, S.CONFIRMED)
    order.refresh_from_db()
    microscope.refresh_from_db()
    assert microscope.unit_price == Decimal("90000") and microscope.price_overridden
    lines = invoice_items(order)
    assert [(i.product_name, i.line_total) for i in lines][-1] == ("Доставка", Decimal("1500"))
    assert [i.kind for i in lines] == ["product", "product", "delivery"]
    assert order.total_amount == Decimal("237000")

    with pytest.raises(OrderEditError, match="только до подтверждения"):
        edit_order_items(order, [{"action": "quantity", "item_id": loupe.pk, "value": 1}], admin_user)


def test_edit_limits(order, admin_user):
    loupe = order.items.get(source_currency="RUB")
    microscope = order.items.get(source_currency="USD")
    edit_order_items(order, [{"action": "remove", "item_id": microscope.pk}], admin_user)
    with pytest.raises(OrderEditError, match="последнюю товарную позицию"):
        edit_order_items(order, [{"action": "remove", "item_id": loupe.pk}], admin_user)
    for bad in ({"action": "quantity", "item_id": loupe.pk, "value": 1000},
                {"action": "price", "item_id": loupe.pk, "value": "0"},
                {"action": "delivery", "value": "-1"}):
        with pytest.raises(OrderEditError):
            edit_order_items(order, [bad], admin_user)
    edit_order_items(order, [{"action": "delivery", "value": "700"}], admin_user)
    edit_order_items(order, [{"action": "delivery", "value": "900"}], admin_user)
    edit_order_items(order, [{"action": "delivery", "value": None}], admin_user)
    assert list(order.changes.order_by("pk").values_list("action", flat=True)) == [
        "item_removed", "delivery_added", "delivery_changed", "delivery_removed"]
    order.refresh_from_db()
    assert order.total_amount == Decimal("97000") and not order.items.filter(kind="delivery").exists()


# --- админка


class FormFields(HTMLParser):
    """Поля формы со страницы админки — чтобы отправить её так, как отправил бы браузер."""

    def __init__(self):
        super().__init__()
        self.data, self._select, self._textarea = {}, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get("name")
        if tag == "input" and name and a.get("type") not in ("submit", "button", "checkbox", "file"):
            self.data[name] = a.get("value", "")
        elif tag == "input" and name and a.get("type") == "checkbox" and "checked" in a:
            self.data[name] = "on"
        elif tag == "select" and name:
            self._select = name
            self.data.setdefault(name, "")
        elif tag == "option" and self._select and "selected" in a:
            self.data[self._select] = a.get("value", "")
        elif tag == "textarea" and name:
            self._textarea = name
            self.data[name] = ""

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea:
            self.data[self._textarea] += data


def form_data(admin_client, url):
    parser = FormFields()
    parser.feed(admin_client.get(url).content.decode())
    return parser.data


def test_admin_edits_and_confirms_from_order_page(admin_client, order, django_capture_on_commit_callbacks):
    url = f"/admin/orders/order/{order.pk}/change/"
    page = admin_client.get(url).content.decode()
    assert 'name="_status_confirmed"' in page and 'name="_status_cancelled"' in page and 'name="_status_paid"' not in page
    assert 'name="status"' not in page  # статус — только для чтения

    data = form_data(admin_client, url)
    items = {data[f"items-{i}-id"]: i for i in range(2)}
    loupe = order.items.get(source_currency="RUB")
    data[f"items-{items[str(loupe.pk)]}-unit_price"] = "45000.00"
    data["delivery_price"] = "1200"
    data["_status_confirmed"] = "Подтвердить"
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_client.post(url, data)
    assert response.status_code == 302, response.content.decode()[:2000]

    order.refresh_from_db()
    assert order.status == S.CONFIRMED and order.invoice
    assert list(order.changes.values_list("action", flat=True).order_by("pk")) == ["price_changed", "delivery_added"]
    assert order.total_amount == Decimal("45000") * 2 + Decimal("95500") + Decimal("1200")
    page = admin_client.get(url).content.decode()
    assert 'name="_status_paid"' in page and "Отправить счёт повторно" in page
    assert "цена за единицу 48 500 ₽ → 45 000 ₽ (−3 500 ₽, −7,22 %)" in page


def test_admin_bulk_confirm_reports_each_order(admin_client, order, client, products,
                                               django_capture_on_commit_callbacks):
    second = make_order(client, products, django_capture_on_commit_callbacks)
    Order.objects.filter(pk=second.pk).update(status=S.CANCELLED)
    with django_capture_on_commit_callbacks(execute=True):
        response = admin_client.post("/admin/orders/order/", {
            "action": "confirm_orders", "_selected_action": [order.pk, second.pk]}, follow=True)
    text = response.content.decode()
    assert f"Подтверждены: {order.number}" in text and f"Заказ № {second.number}: переход" in text
    assert Order.objects.get(pk=order.pk).status == S.CONFIRMED
