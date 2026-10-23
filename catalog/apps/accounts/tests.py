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
