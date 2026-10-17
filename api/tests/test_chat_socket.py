"""Протокол чата (ТЗ §6) на заглушке ответа: T4, T5, T14 и «Стоп»."""

import json
import time
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from .conftest import add_event, connect, make_ticket, messages_in_db, receive_until, user_message


def assert_closed_with(ws, code):
    with pytest.raises(WebSocketDisconnect) as exc:
        ws.receive_json()
    assert exc.value.code == code


# --- T4: тикет и Origin


def test_history_on_connect(client, redis_sync, conversation):
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        history = ws.receive_json()
    assert history == {"type": "history", "conversation_id": conversation["id"], "messages": []}


def test_ticket_is_single_use(client, redis_sync, conversation):
    ticket = make_ticket(redis_sync, conversation)
    with connect(client, ticket) as ws:
        assert ws.receive_json()["type"] == "history"
    with connect(client, ticket) as ws:
        assert_closed_with(ws, 4401)


def test_ticket_expires(client, redis_sync, conversation):
    ticket = make_ticket(redis_sync, conversation, ttl=1)
    time.sleep(1.2)
    with connect(client, ticket) as ws:
        assert_closed_with(ws, 4401)


def test_foreign_origin_rejected(client, redis_sync, conversation):
    with pytest.raises(WebSocketDisconnect) as exc:
        with connect(client, make_ticket(redis_sync, conversation), origin="https://evil.example") as ws:
            ws.receive_json()
    assert exc.value.code == 4403


def test_ticket_of_another_owner_rejected(client, redis_sync, conversation):
    ticket = make_ticket(redis_sync, conversation, session_id=str(uuid.uuid4()))
    with connect(client, ticket) as ws:
        assert_closed_with(ws, 4403)


# --- ход-заглушка


def test_reply_streams_and_only_full_answer_is_saved(client, redis_sync, conversation):
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message("Нужен микроскоп"))
        received = receive_until(ws, "assistant_end")

    types = [m["type"] for m in received]
    assert types[0] == "user_message"
    assert received[0]["message"]["text"] == "Нужен микроскоп"
    assert "assistant_start" in types and "assistant_delta" in types
    streamed = "".join(m["text"] for m in received if m["type"] == "assistant_delta")
    assert received[-1]["message"]["text"] == streamed
    assert [m["role"] for m in messages_in_db(conversation["id"])] == ["user", "assistant"]


def test_repeated_client_message_id_is_ignored(client, redis_sync, conversation):
    message = user_message()
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(message)
        receive_until(ws, "assistant_end")
        ws.send_json(message)
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert [m["role"] for m in messages_in_db(conversation["id"])] == ["user", "assistant"]


def test_too_long_message(client, redis_sync, conversation, chat_settings):
    chat_settings.chat_max_message_length = 10
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message("x" * 11))
        assert ws.receive_json()["code"] == "too_long"
    assert messages_in_db(conversation["id"]) == []


def test_rate_limit_per_minute(client, redis_sync, conversation, chat_settings):
    chat_settings.chat_messages_per_minute = 1
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message())
        receive_until(ws, "assistant_end")
        ws.send_json(user_message())
        assert ws.receive_json()["code"] == "rate_limited"


def test_daily_limit_shows_lead_button(client, redis_sync, conversation, chat_settings):
    chat_settings.chat_messages_per_day = 0
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message())
        answer = ws.receive_json()
    assert answer["code"] == "limit_reached" and answer["show_lead_button"] is True


# --- T5: один ход на диалог


def test_second_message_during_turn_is_busy(client, redis_sync, conversation, fake_model):
    fake_model.delay = 0.05
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message("первая"))
        receive_until(ws, "assistant_start")
        ws.send_json(user_message("вторая"))
        received = receive_until(ws, "error")
        assert received[-1]["code"] == "busy"
        ws.send_json({"type": "stop"})
        receive_until(ws, "assistant_discard")
    assert [m["content"] for m in messages_in_db(conversation["id"])] == ["первая"]


# --- «Стоп» (ТЗ §7.11)


def test_stop_discards_current_answer(client, redis_sync, conversation, fake_model):
    fake_model.delay = 0.05
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message())
        receive_until(ws, "assistant_start")
        ws.send_json({"type": "stop"})
        receive_until(ws, "assistant_discard")
        # Блокировка снята — можно писать дальше
        fake_model.delay = 0
        ws.send_json(user_message("снова"))
        receive_until(ws, "assistant_end")
    assert [m["role"] for m in messages_in_db(conversation["id"])] == ["user", "user", "assistant"]


# --- T14: несколько вкладок


def test_both_tabs_see_the_turn(client, redis_sync, conversation):
    with connect(client, make_ticket(redis_sync, conversation)) as a, \
            connect(client, make_ticket(redis_sync, conversation)) as b:
        a.receive_json()
        b.receive_json()
        a.send_json(user_message("из первой вкладки"))
        from_b = receive_until(b, "assistant_end")
        receive_until(a, "assistant_end")
    assert from_b[0]["type"] == "user_message"
    assert from_b[0]["message"]["text"] == "из первой вкладки"


def test_one_event_one_turn_with_two_tabs(client, redis_sync, conversation):
    with connect(client, make_ticket(redis_sync, conversation)) as a, \
            connect(client, make_ticket(redis_sync, conversation)) as b:
        a.receive_json()
        b.receive_json()
        message_id = add_event(redis_sync, conversation["id"], "order_created", {"number": "AD-1", "total": 100})
        for ws in (a, b):
            received = receive_until(ws, "assistant_end")
            event = next(m for m in received if m["type"] == "event")
            assert event["message"]["event_payload"]["number"] == "AD-1"
        # Повторная публикация того же события второго хода не запускает
        redis_sync.publish(f"chat:conv:{conversation['id']}", json.dumps({
            "kind": "event", "message_id": message_id, "event_type": "order_created", "payload": {"number": "AD-1"},
        }))
        receive_until(a, "event")
        a.send_json({"type": "ping"})
        assert receive_until(a, "pong")[-1] == {"type": "pong"}
    roles = [m["role"] for m in messages_in_db(conversation["id"])]
    assert roles.count("assistant") == 1


def test_event_during_turn_waits_for_it(client, redis_sync, conversation, fake_model):
    fake_model.delay = 0.02
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message())
        receive_until(ws, "assistant_start")
        add_event(redis_sync, conversation["id"], "order_created", {"number": "AD-2"})
        receive_until(ws, "assistant_end")
        second = receive_until(ws, "assistant_end")
    assert "AD-2" in second[-1]["message"]["text"]
    # Перед ходом по событию — служебная реплика «клиент отправил форму» (клиенту не показывается)
    assert [m["role"] for m in messages_in_db(conversation["id"])] == ["user", "event", "assistant", "user", "assistant"]


def test_fallback_lead_does_not_start_turn(client, redis_sync, conversation):
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        add_event(redis_sync, conversation["id"], "lead_created", {"number": "L-1", "source": "fallback"})
        assert receive_until(ws, "event")[-1]["message"]["event_type"] == "lead_created"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert [m["role"] for m in messages_in_db(conversation["id"])] == ["event"]


def test_conversation_closed_reaches_all_tabs(client, redis_sync, conversation):
    with connect(client, make_ticket(redis_sync, conversation)) as a, \
            connect(client, make_ticket(redis_sync, conversation)) as b:
        a.receive_json()
        b.receive_json()
        redis_sync.publish(f"chat:conv:{conversation['id']}", json.dumps({"kind": "conversation_closed"}))
        for ws in (a, b):
            assert ws.receive_json() == {"type": "conversation_closed"}
            assert_closed_with(ws, 4409)


def test_history_hides_tool_results(client, redis_sync, conversation):
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message("вопрос"))
        receive_until(ws, "assistant_end")
    from .conftest import admin_sql
    admin_sql(
        "INSERT INTO chat_message(conversation_id, role, content, event_type, epoch, stop_reason, request_id, created_at)"
        " VALUES ($1, 'tool', 'служебное', '', '', '', '', now())",
        conversation["id"],
    )
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        history = ws.receive_json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
