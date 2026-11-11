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
