"""Заявка менеджеру."""

import json
from unittest import mock

import pytest
from django.core import mail

from apps.chat.models import Conversation, Message
from conftest import next_published
from .models import Lead

pytestmark = pytest.mark.django_db


def post_lead(client, **body):
    payload = {"phone": "+7 999 123-45-67", "name": "Иван", "city": "Казань", "consent": True, **body}
    return client.post("/chat/lead/", data=json.dumps(payload), content_type="application/json")


@pytest.fixture
def dialog(client, redis_client):
    """Гость открыл чат, агент оставил пометку для заявки."""
    data = client.post("/chat/ticket/", data="{}", content_type="application/json").json()
    conversation = Conversation.objects.get(pk=data["conversation_id"])
    Conversation.objects.filter(pk=conversation.pk).update(pending_lead_topic="Лизинг микроскопа для клиники")
    return conversation


def test_lead_from_chat(client, dialog, channel, settings, django_capture_on_commit_callbacks):
    settings.MANAGER_EMAIL = "admin@example.com"
    settings.NOTIFY_EMAILS = ["sales@example.com"]
    listener = channel(dialog.pk)
    with mock.patch("apps.leads.notifications.send_telegram") as telegram, \
            django_capture_on_commit_callbacks(execute=True):
        response = post_lead(client, source="ai")

    assert response.status_code == 200
    lead = Lead.objects.get()
    assert response.json()["number"] == lead.number and lead.number.startswith("L-")
    assert lead.phone == "+79991234567" and lead.topic == "Лизинг микроскопа для клиники"
    assert lead.source == "ai" and lead.conversation == dialog and lead.pd_consent_at
    dialog.refresh_from_db()
    assert dialog.pending_lead_topic == ""  # пометка использована

    event = next_published(listener)
    assert event["event_type"] == "lead_created" and event["payload"] == {"number": lead.number, "source": "ai"}
    assert Message.objects.get(role="event").event_payload == {"number": lead.number, "source": "ai"}

    # Telegram и письмо — с данными заявки; ссылка в админку — последней строкой
    text = telegram.call_args.args[0]
    assert text.splitlines() == [
        f"Новая заявка № {lead.number}", "Имя: Иван", "Телефон: +79991234567", "Город: Казань",
        "Суть запроса: Лизинг микроскопа для клиники", "", f"http://testserver/admin/leads/lead/{lead.pk}/change/",
    ]
    letter = mail.outbox[0]
    assert letter.to == ["admin@example.com", "sales@example.com"]
    assert "+79991234567" in letter.body and "Лизинг микроскопа" in letter.body


def test_fallback_lead_without_dialog(client, redis_client):
    response = post_lead(client)
    assert response.status_code == 200
    lead = Lead.objects.get()
    assert lead.source == "fallback" and lead.conversation is None and lead.session is not None


@pytest.mark.parametrize("body, error", [
    ({"phone": "123"}, "телефон"),
    ({"consent": False}, "согласие"),
])
def test_lead_validation(client, redis_client, body, error):
    response = post_lead(client, **body)
    assert response.status_code == 400 and error in response.json()["error"].lower()
    assert not Lead.objects.exists()


def test_lead_rate_limit(client, redis_client):
    codes = [post_lead(client).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_lead_admin(admin_client, client, dialog):
    post_lead(client, source="ai")
    lead = Lead.objects.get()
    page = admin_client.get(f"/admin/leads/lead/{lead.pk}/change/").content.decode()
    assert "Лизинг микроскопа" in page and f"Диалог #{dialog.pk}" in page
    assert admin_client.get("/admin/leads/lead/").status_code == 200
