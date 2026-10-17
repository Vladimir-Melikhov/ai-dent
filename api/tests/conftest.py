"""Тесты api работают с настоящими Postgres и Redis (scripts/run_tests.sh).

DATABASE_URL — под ролью api, как в проде; TEST_ADMIN_DATABASE_URL — под владельцем БД,
только для подготовки данных, которые в проде пишет Django.
"""

import asyncio
import json
import os
import secrets
import time
import uuid

import anthropic
import asyncpg
import pytest
import redis
from fastapi.testclient import TestClient

from devtools.fake_anthropic import ScriptedModel, echo

if os.environ.get("DENTAI_TESTS") != "1":
    # Тесты чистят Redis и пишут в БД — только на тестовых сервисах
    pytest.exit("Запускайте тесты через scripts/run_tests.sh — он поднимает окружение на тестовых Postgres и Redis")

ADMIN_DSN = os.environ["TEST_ADMIN_DATABASE_URL"]
ORIGIN = os.environ.get("SITE_ORIGIN", "http://testserver")


def admin_sql(query, *args, fetch=False):
    async def run():
        conn = await asyncpg.connect(ADMIN_DSN)
        try:
            if fetch:
                return [dict(r) for r in await conn.fetch(query, *args)]
            return await conn.execute(query, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


@pytest.fixture
def redis_sync():
    client = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    client.flushdb()
    yield client
    client.close()


@pytest.fixture
def conversation():
    """Гостевая сессия и её текущий диалог — в проде их создаёт Django."""
    session_id = uuid.uuid4()
    admin_sql(
        "INSERT INTO accounts_anonsession(session_id, created_at, last_seen_at) VALUES ($1, now(), now())",
        session_id,
    )
    rows = admin_sql(
        "INSERT INTO chat_conversation(session_id, status, prompt_epoch, pending_lead_topic, created_at, last_message_at)"
        " VALUES ($1, 'active', '', '', now(), now()) RETURNING id",
        session_id,
        fetch=True,
    )
    yield {"id": rows[0]["id"], "session_id": str(session_id)}
    admin_sql("DELETE FROM chat_llmusage WHERE conversation_id = $1", rows[0]["id"])
    admin_sql("DELETE FROM chat_message WHERE conversation_id = $1", rows[0]["id"])
    admin_sql("DELETE FROM chat_conversation WHERE id = $1", rows[0]["id"])
    admin_sql("DELETE FROM accounts_anonsession WHERE session_id = $1", session_id)


@pytest.fixture
def fake_model():
    """Подменная модель: по умолчанию отвечает эхом реплики или события."""
    return ScriptedModel(default=echo)


@pytest.fixture
def client(redis_sync, fake_model, monkeypatch):
    from app import main

    def fake_client(settings=None):
        return anthropic.AsyncAnthropic(
            api_key="test", base_url="https://api.anthropic.com", max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=fake_model.transport),
        )

    monkeypatch.setattr(main, "create_async_client", fake_client)
    app = main.app
    with TestClient(app) as test_client:
        # Подписка на события поднимается фоном — ждём её, иначе публикация уйдёт в пустоту
        deadline = time.monotonic() + 5
        while redis_sync.execute_command("PUBSUB", "NUMPAT") < 1:
            assert time.monotonic() < deadline, "api не подписался на события"
            time.sleep(0.02)
        yield test_client


@pytest.fixture
def chat_settings(client):
    """Настройки чата живого приложения; тест может их менять, после — откат."""
    settings = client.app.state.chat.settings
    saved = settings.model_dump()
    yield settings
    for key, value in saved.items():
        setattr(settings, key, value)


def make_ticket(redis_sync, conversation, ttl=60, **override):
    token = secrets.token_urlsafe(16)
    payload = {"conversation_id": conversation["id"], "user_id": None, "session_id": conversation["session_id"]}
    payload.update(override)
    redis_sync.set(f"chat:ticket:{token}", json.dumps(payload), ex=ttl)
    return token


def connect(client, ticket, origin=ORIGIN):
    return client.websocket_connect(f"/ws/chat?ticket={ticket}", headers={"origin": origin})


def receive_until(ws, message_type, limit=200):
    """Сообщения до первого нужного типа включительно."""
    received = []
    for _ in range(limit):
        message = ws.receive_json()
        received.append(message)
        if message["type"] == message_type:
            return received
    raise AssertionError(f"не дождались {message_type}: {received}")


def user_message(text="Здравствуйте"):
    return {"type": "user_message", "id": str(uuid.uuid4()), "text": text}


def messages_in_db(conversation_id):
    return admin_sql(
        "SELECT role, content, event_type FROM chat_message WHERE conversation_id = $1 ORDER BY id",
        conversation_id,
        fetch=True,
    )


def add_event(redis_sync, conversation_id, event_type, payload):
    """То, что делает Django: строка-событие и звонок в канал после коммита."""
    rows = admin_sql(
        "INSERT INTO chat_message(conversation_id, role, content, event_type, event_payload, epoch, stop_reason,"
        " request_id, created_at) VALUES ($1, 'event', '', $2, $3::jsonb, '', '', '', now()) RETURNING id",
        conversation_id, event_type, json.dumps(payload),
        fetch=True,
    )
    message_id = rows[0]["id"]
    redis_sync.publish(f"chat:conv:{conversation_id}", json.dumps({
        "kind": "event", "message_id": message_id, "event_type": event_type, "payload": payload,
    }))
    return message_id


@pytest.fixture
def catalog():
    """Небольшой каталог: в проде его ведёт Django, api только читает."""
    tag = uuid.uuid4().hex[:8]
    brand = admin_sql(
        "INSERT INTO catalog_brand(name, slug, country) VALUES ($1, $2, 'Германия') RETURNING id",
        f"Zeiss {tag}", f"zeiss-{tag}", fetch=True,
    )[0]["id"]
    category = admin_sql(
        "INSERT INTO catalog_category(name, slug, sort_order) VALUES ($1, $2, 0) RETURNING id",
        f"Микроскопы {tag}", f"mikroskopy-{tag}", fetch=True,
    )[0]["id"]

    loupes = admin_sql(
        "INSERT INTO catalog_category(name, slug, sort_order) VALUES ($1, $2, 0) RETURNING id",
        f"Лупы {tag}", f"lupy-{tag}", fetch=True,
    )[0]["id"]

    def product(name, price, *, stock=1, currency="RUB", price_rub=None, active=True, priority=0, chars=None,
                in_category=None):
        return admin_sql(
            "INSERT INTO catalog_product(name, slug, brand_id, category_id, price, currency, price_rub, stock_qty,"
            " description, characteristics, priority, is_active, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'Описание', $9::jsonb, $10, $11, now(), now()) RETURNING id",
            f"{name} {tag}", f"p-{uuid.uuid4().hex[:10]}", brand, in_category or category, price, currency,
            price if price_rub is None and currency == "RUB" else price_rub, stock,
            json.dumps(chars or {}, ensure_ascii=False), priority, active, fetch=True,
        )[0]["id"]

    from decimal import Decimal
    items = {
        "extaro": product("Микроскоп EXTARO 300", Decimal("2924329"), stock=2, priority=10,
                          chars={"Увеличение": "4–25×", "Камера": "Full HD"}),
        "pico": product("Микроскоп OPMI Pico", Decimal("1710534"), stock=0,
                        chars={"Увеличение": "4–25×", "Вариоскоп": "опция"}),
        "loupe": product("Лупа Zumax SLF", Decimal("48500"), stock=5, chars={"Увеличение": "3,0×"}, in_category=loupes),
        "old": product("Микроскоп снятый", Decimal("100"), active=False),
        "usd": product("Микроскоп импортный", Decimal("1000"), currency="USD", price_rub=Decimal("95000")),
    }
    yield {"tag": tag, "brand": brand, "category": category, **items}
    ids = list(items.values())
    admin_sql("DELETE FROM orders_cartitem WHERE product_id = ANY($1::bigint[])", ids)
    admin_sql("DELETE FROM catalog_product WHERE id = ANY($1::bigint[])", ids)
    admin_sql("DELETE FROM catalog_category WHERE id = ANY($1::bigint[])", [category, loupes])
    admin_sql("DELETE FROM catalog_brand WHERE id = $1", brand)


@pytest.fixture
def knowledge_base():
    """Активная версия базы знаний; после теста удаляется."""
    created = []

    def activate(content):
        admin_sql("UPDATE chat_knowledgebaseversion SET is_active = false WHERE is_active")
        row = admin_sql(
            "INSERT INTO chat_knowledgebaseversion(content, comment, source, is_active, created_at, char_count)"
            " VALUES ($1, '', 'import', true, now(), $2) RETURNING id", content, len(content), fetch=True,
        )[0]
        created.append(row["id"])
        return row["id"]

    yield activate
    admin_sql("DELETE FROM chat_knowledgebaseversion WHERE id = ANY($1::bigint[])", created)


def run_async(coro):
    return asyncio.run(coro)
