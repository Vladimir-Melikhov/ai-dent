import datetime
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.catalog.models import Brand, Category, Product
from apps.chat.models import Conversation, Quote
from apps.leads.models import Lead
from apps.orders.models import CartItem, Order
from conftest import next_published
from .models import AnonSession, User
from .services import merge_guest_data

pytestmark = pytest.mark.django_db


@pytest.fixture
def product():
    return Product.objects.create(
        name="Микроскоп", brand=Brand.objects.create(name="Бренд"),
        category=Category.objects.create(name="Микроскопы"), price=1000,
    )


def guest_request(session):
    return SimpleNamespace(anon_session=session)


# --- слияние при входе


def test_active_guest_dialog_becomes_current(redis_client, channel, django_capture_on_commit_callbacks):
    user = User.objects.create_user(phone="+79990000001")
    account_dialog = Conversation.objects.create(user=user)
    session = AnonSession.objects.create()
    guest_closed = Conversation.objects.create(session=session, status=Conversation.Status.CLOSED)
    guest_active = Conversation.objects.create(session=session)
    listener = channel(account_dialog.pk)

    with django_capture_on_commit_callbacks(execute=True):
        merge_guest_data(guest_request(session), user)

    for dialog in (account_dialog, guest_closed, guest_active):
        dialog.refresh_from_db()
    assert guest_active.user == user and guest_active.session is None
    assert guest_active.status == Conversation.Status.ACTIVE
    assert account_dialog.status == Conversation.Status.CLOSED
    assert guest_closed.user == user
    # Вкладки, где открыт старый диалог аккаунта, переподключатся к новому текущему
    assert next_published(listener) == {"kind": "conversation_closed"}


def test_without_guest_dialog_account_dialog_stays_current(redis_client):
    user = User.objects.create_user(phone="+79990000002")
    account_dialog = Conversation.objects.create(user=user)
    session = AnonSession.objects.create()
    guest_closed = Conversation.objects.create(session=session, status=Conversation.Status.CLOSED)

    merge_guest_data(guest_request(session), user)

    account_dialog.refresh_from_db()
    guest_closed.refresh_from_db()
    assert account_dialog.status == Conversation.Status.ACTIVE
    assert guest_closed.user == user and guest_closed.status == Conversation.Status.CLOSED


def test_cart_quantity_capped_and_quotes_moved(product, redis_client):
    user = User.objects.create_user(phone="+79990000003")
    session = AnonSession.objects.create()
    CartItem.objects.create(user=user, product=product, quantity=900)
    CartItem.objects.create(session=session, product=product, quantity=500)
    quote = Quote.objects.create(
        number="KP-1", session=session, total=1000, valid_until=datetime.date.today(),
    )

    merge_guest_data(guest_request(session), user)

    assert CartItem.objects.get(user=user, product=product).quantity == 999
    assert not CartItem.objects.filter(session=session).exists()
    quote.refresh_from_db()
    assert quote.user == user and quote.session is None


def test_sms_login_moves_guest_dialog(client, redis_client):
    session = AnonSession.objects.create()
    dialog = Conversation.objects.create(session=session)
    client.cookies["session_id"] = str(session.pk)
    redis_client.set("otp:+79990000004", "1234", ex=300)

    response = client.post(
        "/accounts/verify-code/", data={"phone": "+79990000004", "code": "1234"}, content_type="application/json",
    )

    assert response.status_code == 200
    dialog.refresh_from_db()
    assert dialog.user.phone == "+79990000004" and dialog.status == Conversation.Status.ACTIVE


# --- вход по SMS через Notificore


@pytest.fixture
def notificore(settings, monkeypatch):
    """Notificore без сети: ответы по пути запроса, запросы копятся в calls."""
    from . import sms

    settings.SMS_PROVIDER = "notificore"
    settings.SMS_API_KEY = "live_test"
    settings.SMS_SENDER = "Ai-Dent"
    settings.SMS_TEMPLATE_ID = "443"
    monkeypatch.setattr(sms, "_token", {"value": "", "expires_at": 0.0})
    fake = SimpleNamespace(calls=[], responses={
        "/auth/login": [(200, {"bearer": "jwt"})],
        "/2fa/authentications/otp": [(200, {"data": {"id": "auth-1", "status": "pending"}})],
        "/2fa/authentications/otp/auth-1/verify": [(200, {"data": {"id": "auth-1", "status": "verified"}})],
    })

    def post(path, payload, token=""):
        fake.calls.append((path, payload, token))
        answers = fake.responses[path]
        return answers.pop(0) if len(answers) > 1 else answers[0]

    monkeypatch.setattr(sms, "_post", post)
    return fake


def request_code(client, phone="+79060608801"):
    return client.post("/accounts/request-code/", data={"phone": phone}, content_type="application/json")


def verify_code(client, code, phone="+79060608801"):
    return client.post(
        "/accounts/verify-code/", data={"phone": phone, "code": code}, content_type="application/json",
    )


def test_notificore_login(client, redis_client, notificore):
    assert request_code(client, "8 906 060-88-01").status_code == 200
    assert redis_client.get("otp:+79060608801") == "auth-1"
    path, payload, token = notificore.calls[1]
    assert path == "/2fa/authentications/otp" and token == "jwt"
    assert payload == {
        "channel": "sms", "recipient": "79060608801", "sender": "Ai-Dent", "template_id": 443,
        "code_digits": 4, "code_max_tries": 2, "code_lifetime": 60,
    }

    response = verify_code(client, "0786")

    assert response.status_code == 200
    assert notificore.calls[-1][1] == {"access_code": "0786"}  # строкой — ведущий ноль на месте
    assert User.objects.filter(phone="+79060608801").exists()
    assert redis_client.get("otp:+79060608801") is None


def test_notificore_wrong_code_then_locked(client, redis_client, notificore):
    notificore.responses["/2fa/authentications/otp/auth-1/verify"] = [
        (404, {"message": "2FA Service error.", "errors": ["The code does not match the expected value"]}),
    ]
    request_code(client)

    first, second = verify_code(client, "1111"), verify_code(client, "2222")

    assert first.json()["error"] == "Неверный или просроченный код"
    assert second.json()["error"] == "Слишком много попыток. Запросите новый код"
    assert redis_client.get("otp:+79060608801") is None
    assert not User.objects.filter(phone="+79060608801").exists()


def test_notificore_expired_token_is_renewed(client, redis_client, notificore):
    notificore.responses["/auth/login"] = [(200, {"bearer": "old"}), (200, {"bearer": "new"})]
    notificore.responses["/2fa/authentications/otp"] = [
        (401, {"code": 401, "message": "Expired JWT Token"}),
        (200, {"data": {"id": "auth-1"}}),
    ]

    assert request_code(client).status_code == 200
    assert [token for path, _, token in notificore.calls if path != "/auth/login"] == ["old", "new"]


def test_notificore_send_failure_allows_retry(client, redis_client, notificore):
    notificore.responses["/2fa/authentications/otp"] = [
        (402, {"errors": [{"message": "Insufficient funds"}]}),
        (200, {"data": {"id": "auth-1"}}),
    ]

    failed = request_code(client)

    assert failed.status_code == 503
    assert failed.json()["error"] == "Не удалось отправить SMS. Попробуйте позже"
    assert redis_client.get("otp:+79060608801") is None
    assert request_code(client).status_code == 200  # без паузы на повторную отправку


def test_notificore_unavailable_on_verify(client, redis_client, notificore):
    request_code(client)
    notificore.responses["/2fa/authentications/otp/auth-1/verify"] = [(500, {})]

    response = verify_code(client, "0786")

    assert response.status_code == 503
    assert response.json()["error"] == "Не удалось проверить код. Попробуйте ещё раз"


def test_no_sms_provider(client, redis_client, settings):
    settings.SMS_PROVIDER = ""

    response = request_code(client)

    assert response.status_code == 503
    assert redis_client.get("otp:+79060608801") is None


# --- очистка гостевых сессий


def test_cleanup_keeps_sessions_linked_to_order_or_lead():
    old = timezone.now() - datetime.timedelta(days=200)
    with_order, with_lead, plain = (AnonSession.objects.create() for _ in range(3))
    Order.objects.create(number="AD-1", conversation=Conversation.objects.create(session=with_order))
    Lead.objects.create(
        number="L-1", phone="+79990000005", pd_consent_at=timezone.now(),
        conversation=Conversation.objects.create(session=with_lead),
    )
    Conversation.objects.create(session=plain)
    AnonSession.objects.update(last_seen_at=old)

    call_command("cleanup_anon_sessions")

    assert set(AnonSession.objects.values_list("pk", flat=True)) == {with_order.pk, with_lead.pk}
