import json

import psycopg2
import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import Client

from apps.accounts.models import AnonSession
from conftest import next_published
from .models import Conversation, Message
from .services import add_event

pytestmark = pytest.mark.django_db


def post_ticket(client, body=None):
    return client.post("/chat/ticket/", data=json.dumps(body or {}), content_type="application/json")


# --- тикет


def test_ticket_creates_guest_session_and_dialog(client, redis_client):
    response = post_ticket(client)
    assert response.status_code == 200
    data = response.json()

    conversation = Conversation.objects.get(pk=data["conversation_id"])
    session = AnonSession.objects.get()
    assert conversation.session == session and conversation.status == Conversation.Status.ACTIVE
    assert response.cookies["session_id"].value == str(session.pk)

    key = f"chat:ticket:{data['ticket']}"
    assert 0 < redis_client.ttl(key) <= settings.CHAT_TICKET_TTL
    assert json.loads(redis_client.get(key)) == {
        "conversation_id": conversation.pk, "user_id": None, "session_id": str(session.pk),
    }


def test_ticket_returns_current_dialog(client, redis_client):
    first = post_ticket(client).json()
    second = post_ticket(client).json()
    assert first["conversation_id"] == second["conversation_id"]
    assert first["ticket"] != second["ticket"]


def test_new_dialog_closes_current_and_notifies_tabs(client, redis_client, channel, django_capture_on_commit_callbacks):
    first = post_ticket(client).json()["conversation_id"]
    listener = channel(first)
    with django_capture_on_commit_callbacks(execute=True):
        second = post_ticket(client, {"new": True}).json()["conversation_id"]

    assert second != first
    assert Conversation.objects.get(pk=first).status == Conversation.Status.CLOSED
    assert Conversation.objects.get(pk=second).status == Conversation.Status.ACTIVE
    assert next_published(listener) == {"kind": "conversation_closed"}


def test_ticket_requires_csrf(redis_client):
    assert post_ticket(Client(enforce_csrf_checks=True)).status_code == 403


def test_ticket_only_post(client):
    assert client.get("/chat/ticket/").status_code == 405


# --- события


def test_event_is_stored_and_published_after_commit(client, redis_client, channel, django_capture_on_commit_callbacks):
    conversation = Conversation.objects.get(pk=post_ticket(client).json()["conversation_id"])
    listener = channel(conversation.pk)
    with django_capture_on_commit_callbacks(execute=True):
        message = add_event(conversation, Message.EventType.ORDER_CREATED, {"number": "AD-1", "total": 1000})

    assert message.role == Message.Role.EVENT
    assert next_published(listener) == {
        "kind": "event", "message_id": message.pk, "event_type": "order_created",
        "payload": {"number": "AD-1", "total": 1000},
    }


# --- виджет


def test_widget_hidden_until_enabled(client, settings):
    settings.CHAT_WIDGET_ENABLED = False
    assert 'id="chat-widget"' not in client.get("/catalog/").content.decode()
    settings.CHAT_WIDGET_ENABLED = True
    assert 'id="chat-widget"' in client.get("/catalog/").content.decode()


# --- админка диалогов


def test_admin_shows_dialog_read_only(admin_client):
    conversation = Conversation.objects.create(session=AnonSession.objects.create())
    Message.objects.create(conversation=conversation, role=Message.Role.USER, content="Нужен микроскоп, мой тел. 89990001122")
    Message.objects.create(conversation=conversation, role=Message.Role.TOOL, content="служебный результат")

    listing = admin_client.get("/admin/chat/conversation/")
    page = admin_client.get(f"/admin/chat/conversation/{conversation.pk}/change/").content.decode()

    assert listing.status_code == 200
    # Исходный текст клиента виден менеджеру; служебное и кнопки сохранения — нет
    assert "Нужен микроскоп, мой тел. 89990001122" in page
    assert "служебный результат" not in page
    assert 'name="_save"' not in page


# --- роль БД для api


@pytest.mark.django_db(transaction=True)
def test_api_role_has_no_access_to_personal_data():
    call_command("grant_api_role")
    db = connection.settings_dict
    conn = psycopg2.connect(
        dbname=db["NAME"], user=settings.API_DB_USER, password=settings.API_DB_PASSWORD,
        host=db["HOST"], port=db["PORT"],
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for table in ["accounts_user", "accounts_anonsession", "orders_order", "orders_invoice",
                          "leads_lead", "django_session"]:
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
            # Статус и владельца диалога меняет только Django
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute("UPDATE chat_conversation SET status = 'closed'")

            cur.execute("SELECT count(*) FROM catalog_product")
            cur.execute("SELECT count(*) FROM orders_cartitem")
            cur.execute("UPDATE chat_conversation SET last_message_at = now()")
            cur.execute("SELECT count(*) FROM chat_message")
    finally:
        conn.close()


# --- база знаний


def test_import_kb_creates_active_version_once(tmp_path, capsys):
    from .models import KnowledgeBaseVersion

    path = tmp_path / "kb.md"
    path.write_text("# База\nЛупа Zumax SLF — 48 500 ₽", encoding="utf-8")
    call_command("import_kb", str(path), "--if-empty")
    output = capsys.readouterr().out
    version = KnowledgeBaseVersion.objects.get()
    assert version.is_active and version.source == "import" and version.char_count == len(path.read_text().strip())
    assert "48 500 ₽" in output  # предупреждение о ценах, импорт не блокируется

    call_command("import_kb", str(path), "--if-empty")
    assert KnowledgeBaseVersion.objects.count() == 1


def test_real_knowledge_base_file_imports():
    from pathlib import Path

    from .models import KnowledgeBaseVersion

    call_command("import_kb", str(Path(settings.BASE_DIR) / "knowledge_base" / "baza_znaniy.md"))
    content = KnowledgeBaseVersion.objects.get(is_active=True).content
    assert "зачёркн" not in content and "ЗАГЛУШКА" not in content


def test_admin_save_creates_new_active_version(admin_client):
    from .models import KnowledgeBaseVersion

    admin_client.post("/admin/chat/knowledgebaseversion/add/", {"content": "Версия 1", "comment": "первая"})
    first = KnowledgeBaseVersion.objects.get()
    response = admin_client.post(f"/admin/chat/knowledgebaseversion/{first.pk}/change/",
                                 {"content": "Версия 2 — цена 1 000 ₽", "comment": "вторая"}, follow=True)

    first.refresh_from_db()
    second = KnowledgeBaseVersion.objects.exclude(pk=first.pk).get()
    assert first.content == "Версия 1" and not first.is_active  # прежняя версия не меняется
    assert second.is_active and second.content == "Версия 2 — цена 1 000 ₽"
    page = response.content.decode()
    assert "Сохранено как версия" in page and "суммы в рублях" in page


def test_admin_upload_and_rollback(admin_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from .models import KnowledgeBaseVersion

    admin_client.post("/admin/chat/knowledgebaseversion/add/", {"content": "Старая"})
    old = KnowledgeBaseVersion.objects.get()
    upload = SimpleUploadedFile("kb.md", "Из файла".encode(), content_type="text/markdown")
    admin_client.post("/admin/chat/knowledgebaseversion/add/", {"content": "", "upload": upload})
    new = KnowledgeBaseVersion.objects.get(is_active=True)
    assert new.content == "Из файла" and new.source == "upload"

    admin_client.post("/admin/chat/knowledgebaseversion/",
                      {"action": "make_active", "_selected_action": [old.pk]})
    assert KnowledgeBaseVersion.objects.get(is_active=True) == old


def test_admin_rejects_empty_and_wrong_file(admin_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from .models import KnowledgeBaseVersion

    admin_client.post("/admin/chat/knowledgebaseversion/add/", {"content": "  "})
    upload = SimpleUploadedFile("kb.pdf", b"%PDF", content_type="application/pdf")
    admin_client.post("/admin/chat/knowledgebaseversion/add/", {"content": "", "upload": upload})
    assert not KnowledgeBaseVersion.objects.exists()


# --- главная: чат на первом экране


def test_landing_has_inline_chat_without_floating_button(client, settings, redis_client):
    settings.CHAT_WIDGET_ENABLED = True
    page = client.get("/").content.decode()
    assert 'data-mode="inline"' in page and 'id="chat-input"' in page
    assert 'id="chat-open"' not in page and 'id="chat-panel"' not in page
    assert 'id="agent-down" hidden' in page
    # Новый посетитель: чат подключится с первым вопросом, гостевая сессия не создаётся
    assert 'data-has-dialog="0"' in page and not AnonSession.objects.exists()

    # На остальных страницах — плавающая кнопка и панель
    other = client.get("/catalog/").content.decode()
    assert 'id="chat-open"' in other and 'data-mode="inline"' not in other


def test_landing_shows_started_dialog(client, settings, redis_client):
    settings.CHAT_WIDGET_ENABLED = True
    conversation = Conversation.objects.get(pk=post_ticket(client).json()["conversation_id"])
    assert 'data-has-dialog="0"' in client.get("/").content.decode()  # диалог пустой
    Message.objects.create(conversation=conversation, role=Message.Role.USER, content="Нужен микроскоп")
    assert 'data-has-dialog="1"' in client.get("/").content.decode()


def test_landing_falls_back_to_catalog_search_when_agent_unavailable(client, settings, redis_client):
    settings.CHAT_WIDGET_ENABLED = True
    redis_client.set("chat:agent_unavailable", "model", ex=60)
    page = client.get("/").content.decode()
    assert 'id="hero-search"' in page and 'id="chat-widget"' not in page and 'id="chat-open"' not in page
    assert 'id="agent-down" >' in page or 'id="agent-down"  >' in page
    assert "Агент временно не работает" in page and "Поиск по каталогу" in page


def test_landing_without_widget_is_plain_search(client, settings, redis_client):
    settings.CHAT_WIDGET_ENABLED = False
    page = client.get("/").content.decode()
    assert 'id="hero-search"' in page and 'id="chat-widget"' not in page and 'id="agent-down"' not in page


# --- КП


@pytest.fixture
def quote(client, redis_client):
    import datetime
    from decimal import Decimal

    from .models import Quote

    client.post("/chat/ticket/", data="{}", content_type="application/json")
    session = AnonSession.objects.get()
    return Quote.objects.create(
        number="KP-20261007-ABC123", session=session, total=Decimal("1078500"),
        valid_until=datetime.date(2026, 10, 21),
        items=[
            {"product_id": 1, "name": "Микроскоп EXTARO", "quantity": 1, "unit_price_rub": "1000000.00",
             "line_total_rub": "1000000.00", "source_price": "1000000.00", "source_currency": "RUB",
             "exchange_rate": None},
            {"product_id": 2, "name": "Лупа Lercher", "quantity": 1, "unit_price_rub": "78500.00",
             "line_total_rub": "78500.00", "source_price": "750.00", "source_currency": "EUR",
             "exchange_rate": "104.666667"},
        ],
    )


def test_quote_pdf_for_owner_only(client, quote, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    response = client.get(f"/chat/quote/{quote.pk}/")
    assert response.status_code == 200 and response["Content-Type"] == "application/pdf"
    assert b"".join(response.streaming_content).startswith(b"%PDF")
    quote.refresh_from_db()
    assert quote.pdf_path == f"quotes/kp-{quote.pk}.pdf" and (tmp_path / quote.pdf_path).exists()

    assert Client().get(f"/chat/quote/{quote.pk}/").status_code == 404


def test_quote_document_has_no_requisites_or_client_data(quote):
    from apps.orders.pdf import SELLER
    from .quote_pdf import CURRENCY_NOTE, quote_document

    doc = quote_document(quote)
    text = json.dumps(doc, ensure_ascii=False)
    assert SELLER["inn"] not in text and SELLER["account"] not in text and "ИНН" not in text
    assert "1 000 000 ₽" in text and "78 500 ₽" in text and doc["total"] == "1 078 500 ₽"
    assert CURRENCY_NOTE in doc["notes"] and "не является счётом" in text


def test_quote_admin(admin_client, quote):
    assert admin_client.get("/admin/chat/quote/").status_code == 200


# --- расход модели по дням


def test_usage_summary_by_day(admin_client):
    from decimal import Decimal

    from django.utils import timezone
    from .models import LLMUsage

    conversation = Conversation.objects.create(session=AnonSession.objects.create())
    common = {"configured_model": "claude-opus-5-5", "model": "claude-opus-5-5"}
    LLMUsage.objects.create(conversation=conversation, request_id="r1", input_tokens=1000, cache_read_tokens=3000,
                            output_tokens=500, cost_usd=Decimal("0.02"), **common)
    LLMUsage.objects.create(conversation=conversation, request_id="r2", input_tokens=1000, cache_read_tokens=3000,
                            output_tokens=500, cost_usd=Decimal("0.03"), attempt_type="fallback_message", **common)
    LLMUsage.objects.create(conversation=None, request_id="r3", input_tokens=10, output_tokens=5,
                            cost_usd=Decimal("1.00"), created_at=timezone.now() - timezone.timedelta(days=2), **common)

    page = admin_client.get("/admin/chat/llmusage/").content.decode()
    today = timezone.localtime(timezone.now(), timezone.get_fixed_timezone(180)).strftime("%d.%m.%Y")
    assert "Сводка по дням" in page and today in page
    assert "<strong>0,05</strong>" in page and "<strong>1,00</strong>" in page  # и попытка без диалога
    assert "75 %" in page and "50 %" in page  # доля чтения из кэша, доля резервной модели
