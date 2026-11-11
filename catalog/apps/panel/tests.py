import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.orders.models import Order

from . import services
from .access import LOGIN_ATTEMPTS
from .models import Category, Deal, Entry, Server

pytestmark = pytest.mark.django_db

PANEL = "panel.test"


@pytest.fixture(autouse=True)
def panel_host(settings, redis_client):
    settings.PANEL_HOST = PANEL
    settings.ALLOWED_HOSTS = ["testserver", "localhost", PANEL]


@pytest.fixture
def owner():
    return User.objects.create_superuser("+79990000001", "pass-12345")


@pytest.fixture
def client(owner):
    c = Client(HTTP_HOST=PANEL)
    c.force_login(owner)
    return c


def category(kind, name="Тест"):
    return Category.objects.get_or_create(kind=kind, name=name, defaults={"color": 1})[0]


# --- доступ

def test_panel_exists_only_on_its_host():
    assert Client(HTTP_HOST="localhost").get("/deals/").status_code == 404
    assert Client(HTTP_HOST=PANEL).get("/deals/").status_code == 302


def test_panel_host_does_not_serve_the_site():
    response = Client(HTTP_HOST=PANEL).get("/catalog/")
    assert response.status_code == 404


def test_disabled_without_host(settings, owner):
    settings.PANEL_HOST = ""
    assert Client(HTTP_HOST=PANEL).get("/login/").status_code == 404


def test_anonymous_redirected_to_login():
    response = Client(HTTP_HOST=PANEL).get("/finance/")
    assert response.status_code == 302 and response["Location"].startswith("/login/")


def test_customer_cannot_enter():
    User.objects.create_user("+79990000002", "pass-12345")
    response = Client(HTTP_HOST=PANEL).post("/login/", {"phone": "+79990000002", "password": "pass-12345"})
    assert response.status_code == 400


def test_director_can_enter():
    User.objects.create_user("+79990000003", "pass-12345", role=User.Role.DIRECTOR)
    c = Client(HTTP_HOST=PANEL)
    response = c.post("/login/", {"phone": "8 (999) 000-00-03", "password": "pass-12345"})
    assert response.status_code == 302
    assert c.get("/").status_code == 200


def test_login_rate_limited(owner):
    c = Client(HTTP_HOST=PANEL)
    for _ in range(LOGIN_ATTEMPTS):
        assert c.post("/login/", {"phone": owner.phone, "password": "wrong"}).status_code == 400
    # после лимита не пускает и с верным паролем
    response = c.post("/login/", {"phone": owner.phone, "password": "pass-12345"})
    assert response.status_code == 400 and "Слишком много" in response.content.decode()


def test_login_next_cannot_leave_host(owner):
    response = Client(HTTP_HOST=PANEL).post(
        "/login/", {"phone": owner.phone, "password": "pass-12345", "next": "https://evil.example/"}
    )
    assert response["Location"] == "/"


def test_panel_headers(client):
    response = client.get("/")
    assert "noindex" in response["X-Robots-Tag"]
    assert response["Cache-Control"] == "no-store"
    assert "script-src 'self'" in response["Content-Security-Policy"]


# --- страницы

@pytest.mark.parametrize("path", ["/", "/deals/", "/finance/", "/categories/", "/sales/", "/servers/", "/?p=365", "/?p=7"])
def test_pages_render(client, path):
    assert client.get(path).status_code == 200


# --- цифры

def test_totals_combine_shop_and_entries(client):
    today = timezone.now()
    Order.objects.create(number="A1", status=Order.Status.PAID, total_amount=1000, paid_at=today)
    Order.objects.create(number="A2", status=Order.Status.CONFIRMED, total_amount=5000)  # не оплачен
    Entry.objects.create(kind="income", category=category("income"), amount=500)
    Entry.objects.create(kind="expense", category=category("expense"), amount=300)
    period = services.resolve_period("30")
    totals = services.totals(period.start, period.end)
    assert totals["shop_revenue"] == 1000 and totals["revenue"] == 1500
    assert totals["expenses"] == 300 and totals["profit"] == 1200
    assert totals["orders"] == 1 and totals["avg_check"] == 1000


def test_series_buckets_follow_period_length():
    assert services.bucket_step(30) == "day" and services.bucket_step(90) == "week" and services.bucket_step(365) == "month"
    Entry.objects.create(kind="expense", category=category("expense"), amount=100)
    rows = services.series(services.resolve_period("365"))
    assert sum(r["expenses"] for r in rows) == 100


def test_delta_has_no_base():
    assert services.delta(Decimal(10), Decimal(0)) is None
    assert services.delta(Decimal(150), Decimal(100)) == 50


# --- операции

def test_entry_category_must_match_kind(client):
    response = client.post("/finance/save/", {
        "kind": "income", "category": category("expense").pk, "amount": "100", "date": "2026-11-01",
    })
    assert response.status_code == 302 and not Entry.objects.exists()


def test_entry_money_formats(client):
    client.post("/finance/save/", {
        "kind": "expense", "category": category("expense").pk, "amount": "1 500,50 ₽", "date": "2026-11-01",
    })
    assert Entry.objects.get().amount == Decimal("1500.50")


def test_entry_edit_and_delete(client):
    entry = Entry.objects.create(kind="expense", category=category("expense"), amount=10)
    client.post("/finance/save/", {
        "id": entry.pk, "kind": "expense", "category": entry.category_id, "amount": "20", "date": "2026-11-01",
    })
    entry.refresh_from_db()
    assert entry.amount == 20
    client.post(f"/finance/{entry.pk}/delete/")
    assert not Entry.objects.exists()


def test_csv_export_escapes_formulas(client):
    Entry.objects.create(kind="expense", category=category("expense"), amount=10, title="=1+1")
    body = client.get("/finance/export.csv").content.decode("utf-8-sig")
    assert "'=1+1" in body


# --- сделки

def make_deal(**kwargs):
    return Deal.objects.create(title="Клиника", amount=Decimal("50000"), **kwargs)


def test_winning_deal_creates_income_and_reopening_removes_it(client):
    deal = make_deal()
    assert client.post(f"/deals/{deal.pk}/stage/", {"stage": "won"}).status_code == 200
    entry = Entry.objects.get(deal=deal)
    assert entry.kind == "income" and entry.amount == 50000 and entry.category.name == "Сделки"
    deal.refresh_from_db()
    assert deal.closed_at is not None
    client.post(f"/deals/{deal.pk}/stage/", {"stage": "negotiation"})
    assert not Entry.objects.filter(deal=deal).exists()
    deal.refresh_from_db()
    assert deal.closed_at is None


def test_won_deal_amount_edit_updates_income(client):
    deal = make_deal()
    client.post(f"/deals/{deal.pk}/stage/", {"stage": "won"})
    client.post("/deals/save/", {"id": deal.pk, "title": "Клиника", "amount": "70 000", "stage": "won"})
    assert Entry.objects.get(deal=deal).amount == 70000


def test_lost_deal_gives_no_income(client):
    deal = make_deal()
    client.post(f"/deals/{deal.pk}/stage/", {"stage": "lost"})
    assert not Entry.objects.exists()


def test_unknown_stage_rejected(client):
    deal = make_deal()
    assert client.post(f"/deals/{deal.pk}/stage/", {"stage": "boom"}).status_code == 400


def test_deal_income_not_editable_from_finance(client):
    deal = make_deal()
    client.post(f"/deals/{deal.pk}/stage/", {"stage": "won"})
    entry = Entry.objects.get(deal=deal)
    client.post(f"/finance/{entry.pk}/delete/")
    assert Entry.objects.filter(pk=entry.pk).exists()


def test_stage_requires_login():
    deal = make_deal()
    response = Client(HTTP_HOST=PANEL).post(f"/deals/{deal.pk}/stage/", {"stage": "won"})
    assert response.status_code == 302


def test_deals_summary_conversion():
    won = make_deal(stage="won", closed_at=timezone.now())
    make_deal(stage="lost", closed_at=timezone.now())
    period = services.resolve_period("30")
    summary = services.deals_summary(period.start, period.end)
    assert summary["won_count"] == 1 and summary["conversion"] == 50
    assert won.pk


# --- категории

def test_category_in_use_not_deleted(client):
    cat = category("expense", "Реклама!")
    Entry.objects.create(kind="expense", category=cat, amount=5)
    client.post(f"/categories/{cat.pk}/delete/")
    assert Category.objects.filter(pk=cat.pk).exists()


def test_category_name_unique_ignoring_case(client):
    category("expense", "Аренда")
    client.post("/categories/save/", {"kind": "expense", "name": "аренда", "color": 2})
    assert Category.objects.filter(kind="expense", name__iexact="аренда").count() == 1


def test_default_categories_seeded():
    assert Category.objects.filter(kind="expense", name="Реклама").exists()


# --- серверы

def test_server_crud(client):
    client.post("/servers/save/", {"name": "VPS", "kind": "vps", "url": "https://example.com", "monthly_cost": "990"})
    server = Server.objects.get()
    assert server.monthly_cost == 990
    assert "VPS" in client.get("/servers/").content.decode()
    client.post(f"/servers/{server.pk}/delete/")
    assert not Server.objects.exists()


def test_server_rejects_non_http_url(client):
    client.post("/servers/save/", {"name": "X", "kind": "vps", "url": "javascript:alert(1)"})
    assert not Server.objects.exists()


def test_server_check_needs_http_url(client):
    server = Server.objects.create(name="X", address="10.0.0.1")
    assert client.post(f"/servers/{server.pk}/check/").status_code == 400


def test_server_check_reports_answer(client, monkeypatch):
    import urllib.request

    class Reply:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Reply())
    server = Server.objects.create(name="X", url="https://example.com")
    data = client.post(f"/servers/{server.pk}/check/").json()
    assert data["ok"] is True and data["status"] == 200


def test_referrer_policy_keeps_origin_for_csrf(client):
    # no-referrer заставил бы браузер слать «Origin: null» — CSRF отклонил бы каждую форму
    assert client.get("/")["Referrer-Policy"] == "same-origin"


def test_login_page_prefills_phone_prefix():
    body = Client(HTTP_HOST=PANEL).get("/login/").content.decode()
    assert 'data-phone-mask' in body and 'value="+7 "' in body and "Панель" in body


def test_masked_phone_logs_in(owner):
    # то, что отправляет маска: «+7 999 000 00 01»
    response = Client(HTTP_HOST=PANEL).post("/login/", {"phone": "+7 999 000 00 01", "password": "pass-12345"})
    assert response.status_code == 302


@pytest.mark.parametrize("stored", ["81703097097", "+71703097097", "71703097097"])
@pytest.mark.parametrize("typed", ["+7 170 309 70 97", "8 (170) 309-70-97", "81703097097"])
def test_login_finds_phone_however_it_is_stored(stored, typed):
    # в базе номер мог быть записан с 8, с 7 или с +7 — вход должен находить его при любом наборе
    User.objects.create_superuser(stored, "pass-12345")
    response = Client(HTTP_HOST=PANEL).post("/login/", {"phone": typed, "password": "pass-12345"})
    assert response.status_code == 302


def test_staff_without_rights_is_told_why():
    User.objects.create_user("+79990000004", "pass-12345", is_staff=True, role=User.Role.STAFF)
    response = Client(HTTP_HOST=PANEL).post("/login/", {"phone": "+79990000004", "password": "pass-12345"})
    assert response.status_code == 400 and "нет доступа к панели" in response.content.decode()


def test_wrong_password_stays_generic():
    User.objects.create_superuser("+79990000005", "pass-12345")
    response = Client(HTTP_HOST=PANEL).post("/login/", {"phone": "+79990000005", "password": "nope"})
    assert "Неверный телефон или пароль" in response.content.decode()


# --- карточки из заказов и КП

from apps.accounts.models import AnonSession  # noqa: E402
from apps.chat.models import Conversation, Quote  # noqa: E402
from apps.leads.models import Lead  # noqa: E402


def make_order(number="S1", status=Order.Status.PENDING_CONFIRMATION, **kwargs):
    defaults = dict(total_amount=Decimal("12000"), contact_name="Иван", contact_phone="+79001112233")
    return Order.objects.create(number=number, status=status, **{**defaults, **kwargs})


def make_quote(conversation=None, number="КП-1"):
    return Quote.objects.create(
        number=number, conversation=conversation, total=Decimal("50000"), valid_until=timezone.localdate(),
        items=[{"name": "Лупа", "quantity": 1, "unit_price_rub": 50000, "line_total_rub": 50000}],
    )


def test_order_becomes_card_in_payment_stage(client):
    order = make_order()
    client.get("/deals/")
    deal = Deal.objects.get(order=order)
    assert deal.stage == "payment" and deal.origin == "order" and deal.client == "Иван" and deal.contact == "+79001112233"
    assert deal.amount == 12000


def test_card_follows_order_status_and_creates_no_income(client):
    order = make_order()
    client.get("/deals/")
    order.status, order.paid_at = Order.Status.PAID, timezone.now()
    order.save()
    client.get("/deals/")
    deal = Deal.objects.get(order=order)
    assert deal.stage == "won" and deal.closed_at is not None
    assert not Entry.objects.exists()  # деньги уже в выручке через заказ
    order.status = Order.Status.CANCELLED
    order.save()
    client.get("/deals/")
    deal.refresh_from_db()
    assert deal.stage == "lost"


def test_draft_orders_are_ignored(client):
    make_order(status=Order.Status.DRAFT)
    client.get("/deals/")
    assert not Deal.objects.exists()


def test_old_orders_are_not_dumped_into_the_board(client):
    order = make_order()
    Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - datetime.timedelta(days=60))
    client.get("/deals/")
    assert not Deal.objects.exists()


def test_sync_is_idempotent(client):
    make_order()
    for _ in range(3):
        client.get("/deals/")
    assert Deal.objects.count() == 1


def test_quote_becomes_offer_card_with_lead_contact(client):
    conversation = Conversation.objects.create(session=AnonSession.objects.create())
    Lead.objects.create(number="L1", phone="+79005556677", name="Анна", conversation=conversation, pd_consent_at=timezone.now())
    quote = make_quote(conversation)
    client.get("/deals/")
    deal = Deal.objects.get(quote=quote)
    assert deal.stage == "offer" and deal.origin == "quote" and deal.client == "Анна" and deal.contact == "+79005556677"


def test_anonymous_quote_makes_no_card_but_is_listed(client):
    make_quote()
    body = client.get("/deals/").content.decode()
    assert not Deal.objects.exists()
    assert "КП без контактов · 1" in body and "КП-1" in body


def test_quote_gets_card_once_client_leaves_phone(client):
    conversation = Conversation.objects.create(session=AnonSession.objects.create())
    make_quote(conversation)
    client.get("/deals/")
    assert not Deal.objects.exists()
    Lead.objects.create(number="L9", phone="+79001110000", name="Ира", conversation=conversation, pd_consent_at=timezone.now())
    client.get("/deals/")
    assert Deal.objects.get().contact == "+79001110000"


def test_legacy_phoneless_quote_card_is_removed(client):
    quote = make_quote()
    Deal.objects.create(title="КП", origin="quote", quote=quote, stage="offer", amount=1)
    client.get("/deals/")
    assert not Deal.objects.exists()


def test_expired_quote_card_goes_to_archive(client):
    conversation = Conversation.objects.create(session=AnonSession.objects.create())
    Lead.objects.create(number="L8", phone="+79001110001", name="Ира", conversation=conversation, pd_consent_at=timezone.now())
    quote = make_quote(conversation)
    Quote.objects.filter(pk=quote.pk).update(valid_until=timezone.localdate() - datetime.timedelta(days=1))
    client.get("/deals/")
    assert Deal.objects.get().archived_at is not None


def test_order_after_quote_reuses_the_same_card(client):
    conversation = Conversation.objects.create(session=AnonSession.objects.create())
    Lead.objects.create(number="L3", phone="+79001110002", name="Ира", conversation=conversation, pd_consent_at=timezone.now())
    make_quote(conversation)
    client.get("/deals/")
    make_order(conversation=conversation)
    client.get("/deals/")
    deal = Deal.objects.get()  # одна карточка, а не две
    assert deal.stage == "payment" and deal.origin == "order" and deal.quote_id and deal.order_id


def test_system_card_cannot_be_moved_deleted_or_retitled(client):
    make_order()
    client.get("/deals/")
    deal = Deal.objects.get()
    assert client.post(f"/deals/{deal.pk}/stage/", {"stage": "won"}).status_code == 400
    client.post(f"/deals/{deal.pk}/delete/")
    assert Deal.objects.filter(pk=deal.pk).exists()
    client.post("/deals/save/", {"id": deal.pk, "title": "Взлом", "amount": "1", "stage": "won", "note": "заметка"})
    deal.refresh_from_db()
    assert deal.title.startswith("Заказ") and deal.amount == 12000 and deal.stage == "payment" and deal.note == "заметка"


def test_origin_filter(client):
    make_order()
    Deal.objects.create(title="Ручная", amount=1)
    client.get("/deals/")
    body = client.get("/deals/?src=manual").content.decode()
    assert "Ручная" in body and "Заказ №S1" not in body


def test_dashboard_syncs_cards(client):
    make_order()
    assert client.get("/").status_code == 200
    assert Deal.objects.count() == 1


# --- архив, удаление заказа, один клиент — одна карточка

def age(deal, days):
    Deal.objects.filter(pk=deal.pk).update(created_at=timezone.now() - datetime.timedelta(days=days))


def test_archive_hides_card_and_numbers_and_restores(client):
    make_order()
    client.get("/deals/")
    deal = Deal.objects.get()
    client.post(f"/deals/{deal.pk}/archive/")
    assert "Заказ №S1" not in client.get("/deals/").content.decode()
    assert "Заказ №S1" in client.get("/deals/?archive=1").content.decode()
    assert all(s["count"] == 0 for s in services.pipeline())
    assert services.deals_summary(*[services.resolve_period("30").start, services.resolve_period("30").end])["open_count"] == 0
    client.post(f"/deals/{deal.pk}/archive/")
    assert any(s["count"] == 1 for s in services.pipeline())


def test_archived_card_returns_when_order_is_paid(client):
    order = make_order()
    client.get("/deals/")
    deal = Deal.objects.get()
    client.post(f"/deals/{deal.pk}/archive/")
    order.status, order.paid_at = Order.Status.PAID, timezone.now()
    order.save()
    client.get("/deals/")
    deal.refresh_from_db()
    assert deal.archived_at is None and deal.stage == "won"


def test_deleting_order_in_admin_removes_card(client):
    order = make_order()
    client.get("/deals/")
    order.delete()
    client.get("/deals/")
    assert not Deal.objects.exists()


def test_deleting_order_keeps_quote_card(client):
    conversation = Conversation.objects.create(session=AnonSession.objects.create())
    Lead.objects.create(number="L4", phone="+79001110003", name="Ира", conversation=conversation, pd_consent_at=timezone.now())
    make_quote(conversation)
    order = make_order(conversation=conversation)
    client.get("/deals/")
    order.delete()
    client.get("/deals/")
    deal = Deal.objects.get()
    assert deal.stage == "offer" and deal.origin == "quote"


def test_unpaid_order_can_be_deleted_from_panel(client):
    order = make_order()
    client.get("/deals/")
    deal = Deal.objects.get()
    client.post(f"/deals/{deal.pk}/delete-order/")
    assert not Order.objects.filter(pk=order.pk).exists() and not Deal.objects.exists()
    # клиент оформил новый заказ — новая карточка
    make_order(number="S2")
    client.get("/deals/")
    assert Deal.objects.get().title == "Заказ №S2"


def test_paid_order_cannot_be_deleted_from_panel(client):
    order = make_order(status=Order.Status.PAID, paid_at=timezone.now())
    client.get("/deals/")
    deal = Deal.objects.get()
    client.post(f"/deals/{deal.pk}/delete-order/")
    assert Order.objects.filter(pk=order.pk).exists() and Deal.objects.exists()


def test_quote_card_merges_into_order_by_phone(client):
    conversation = Conversation.objects.create(session=AnonSession.objects.create())
    Lead.objects.create(number="L2", phone="8 900 111-22-33", name="Анна", conversation=conversation, pd_consent_at=timezone.now())
    make_quote(conversation)
    client.get("/deals/")
    # заказ из другого устройства, без чата, телефон записан иначе
    make_order(contact_phone="+79001112233")
    client.get("/deals/")
    deal = Deal.objects.get()
    assert deal.origin == "order" and deal.stage == "payment" and deal.quote_id and deal.order_id


def test_stale_unpaid_card_archived_when_client_orders_again(client):
    old = make_order(number="OLD")
    client.get("/deals/")
    age(Deal.objects.get(order=old), 10)
    make_order(number="NEW")
    client.get("/deals/")
    assert Deal.objects.get(order=old).archived_at is not None
    assert Deal.objects.get(title="Заказ №NEW").archived_at is None


def test_fresh_parallel_orders_both_stay(client):
    make_order(number="A")
    make_order(number="B")
    client.get("/deals/")
    assert Deal.objects.filter(archived_at__isnull=True).count() == 2


def test_waiting_days_shown_and_flagged_stale(client):
    order = make_order()
    Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - datetime.timedelta(days=9))
    assert 'class="wait stale">ждёт 9 дн.' in client.get("/deals/").content.decode()


# --- продажи: плитки и статусы

def test_sales_tiles_count_orders_by_status(client):
    make_order(number="P1")
    make_order(number="C1", status=Order.Status.CONFIRMED, total_amount=Decimal("500"))
    make_order(number="C2", status=Order.Status.CONFIRMED, total_amount=Decimal("700"))
    make_order(number="D1", status=Order.Status.PAID, paid_at=timezone.now())
    response = client.get("/sales/")
    tiles = {t["value"]: t for t in response.context["tiles"]}
    assert tiles["pending_confirmation"]["count"] == 1
    assert tiles["confirmed"]["count"] == 2 and tiles["confirmed"]["sum"] == 1200
    assert tiles["paid"]["count"] == 1 and tiles["cancelled"]["count"] == 0


def test_payment_by_invoice_marked_from_panel(client, owner):
    order = make_order(status=Order.Status.CONFIRMED)
    client.get("/deals/")
    response = client.post(f"/orders/{order.pk}/to/paid/", {"back": "/deals/"})
    assert response["Location"] == "/deals/"
    order.refresh_from_db()
    assert order.status == "paid" and order.paid_at and order.payment_status == "paid"
    assert order.status_history.get().actor == "director" and order.status_history.get().actor_user == owner
    assert Deal.objects.get(order=order).stage == "won"
    assert not Entry.objects.exists()  # деньги считаются через заказ, дубля дохода нет


def test_forbidden_transition_is_reported_not_applied(client):
    order = make_order(status=Order.Status.PAID, paid_at=timezone.now())
    client.post(f"/orders/{order.pk}/to/cancelled/")
    order.refresh_from_db()
    assert order.status == "paid"


def test_unknown_status_rejected(client):
    order = make_order()
    client.post(f"/orders/{order.pk}/to/draft/")
    order.refresh_from_db()
    assert order.status == "pending_confirmation"


def test_order_status_requires_login():
    order = make_order(status=Order.Status.CONFIRMED)
    response = Client(HTTP_HOST=PANEL).post(f"/orders/{order.pk}/to/paid/")
    order.refresh_from_db()
    assert response.status_code == 302 and order.status == "confirmed"


def test_deal_dialog_offers_order_actions(client):
    make_order(status=Order.Status.CONFIRMED)
    response = client.get("/deals/")
    labels = [a["label"] for a in list(response.context["objects"].values())[0]["actions"]]
    assert "Оплата пришла" in labels and "Отменить заказ" in labels and "Подтвердить заказ" not in labels


# --- ИИ и статистика

from apps.chat.models import LLMUsage  # noqa: E402


def test_ai_page_counts_cost_and_payback(client):
    LLMUsage.objects.create(configured_model="m", model="claude-x", cost_usd=Decimal("2.50"), input_tokens=100, output_tokens=50, cache_read_tokens=300)
    LLMUsage.objects.create(configured_model="m", model="claude-y", cost_usd=Decimal("0.50"), attempt_type="fallback_message")
    conversation = Conversation.objects.create(session=AnonSession.objects.create())
    make_order(created_by="ai", status=Order.Status.PAID, paid_at=timezone.now(), total_amount=Decimal("90000"), conversation=conversation)
    response = client.get("/ai/")
    stats = response.context["stats"]
    assert response.status_code == 200
    assert stats["cost"] == Decimal("3.00") and stats["requests"] == 2
    assert stats["conversations"] == 1 and stats["chat_orders"] == 1 and stats["chat_paid"] == 90000
    assert stats["fallback_share"] == 50
    assert stats["per_order"] == Decimal("3.00")
    assert [m["model"] for m in stats["models"]] == ["claude-x", "claude-y"]


def test_ai_page_empty_period_renders(client):
    response = client.get("/ai/?p=365")
    assert response.status_code == 200 and response.context["stats"]["payback"] is None


def test_card_age_is_order_age_not_sync_time(client):
    order = make_order()
    Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - datetime.timedelta(days=10))
    body = client.get("/deals/").content.decode()
    assert "ждёт 10 дн." in body
    # старый неоплаченный + новый заказ того же клиента: старый уходит в архив сразу
    make_order(number="NEW")
    client.get("/deals/")
    assert Deal.objects.get(order=order).archived_at is not None
