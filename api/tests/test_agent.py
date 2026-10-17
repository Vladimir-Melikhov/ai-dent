"""Ход агента на подменной модели: инструменты, история, фолбэк, отказы, расход (ТЗ §7, T3, T9, T10, T12)."""

import json
import time
from decimal import Decimal

import pytest

from app.ai import pricing
from app.ai.agent import RETRY_MAX_TOKENS, ROUNDS_EXHAUSTED
from devtools.fake_anthropic import Reply, fallback, text, thinking, tool_results, tool_use

from .conftest import (
    admin_sql, add_event, connect, make_ticket, messages_in_db, receive_until, run_async, user_message,
)


def chat(client, redis_sync, conversation):
    ws = connect(client, make_ticket(redis_sync, conversation))
    return ws


def api_rows(conversation_id):
    return admin_sql(
        "SELECT role, content, api_content::text AS api, ui::text AS ui, epoch, stop_reason, request_id"
        " FROM chat_message WHERE conversation_id = $1 ORDER BY id", conversation_id, fetch=True,
    )


def usage(conversation_id):
    return admin_sql("SELECT * FROM chat_llmusage WHERE conversation_id = $1 ORDER BY id", conversation_id, fetch=True)


def assert_append_only(requests):
    """T3: соседние запросы совпадают на общем участке — история только дописывается."""
    for previous, current in zip(requests, requests[1:]):
        assert json.dumps(current["system"]) == json.dumps(previous["system"])
        assert json.dumps(current["tools"]) == json.dumps(previous["tools"])
        shared = len(previous["messages"])
        assert json.dumps(current["messages"][:shared], ensure_ascii=False) == \
            json.dumps(previous["messages"], ensure_ascii=False)


def search_then_show(catalog):
    """Сценарий подбора: поиск → карточки → ответ с ценой из результата."""
    def show(body):
        found = next(iter(tool_results(body).values()))
        ids = [p["id"] for p in found["products"]][:2]
        return Reply(blocks=[thinking(), tool_use("show_products", {"product_ids": ids})], stop_reason="tool_use")

    def answer(body):
        shown = next(iter(tool_results(body).values()))["shown"]
        lines = [f"- {p['name']} — {p['price_rub']:,.0f} ₽".replace(",", " ") for p in shown]
        return Reply(blocks=[thinking(), text("Подобрал варианты:\n" + "\n".join(lines))])

    return [
        Reply(blocks=[thinking(), text("Посмотрю каталог."),
                      tool_use("search_products", {"query": f"микроскоп {catalog['tag']}", "in_stock_only": True})],
              stop_reason="tool_use"),
        show, answer,
    ]


def test_selection_dialog_with_cards(client, redis_sync, conversation, catalog, fake_model):
    fake_model.add(*search_then_show(catalog))
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Нужен микроскоп"))
        # Ответы с текстом заканчиваются assistant_end; ответ только с вызовом инструмента — нет
        received = receive_until(ws, "assistant_end")
        received += receive_until(ws, "assistant_end")

    statuses = [m["status"] for m in received if m["type"] == "status"]
    assert "searching" in statuses and "preparing" in statuses
    cards = next(m for m in received if m["type"] == "ui")["ui"]
    assert cards["widget"] == "product_cards"
    assert [p["id"] for p in cards["products"]] == [catalog["extaro"], catalog["usd"]]
    final = received[-1]["message"]["text"]
    assert "2 924 329 ₽" in final

    rows = api_rows(conversation["id"])
    assert [r["role"] for r in rows] == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    # В ui хранятся только id, данные подставляются при выдаче
    assert json.loads(rows[4]["ui"]) == {"widgets": [{"widget": "product_cards",
                                                      "product_ids": [catalog["extaro"], catalog["usd"]]}]}
    assert all(r["request_id"].startswith("req_fake") for r in rows if r["role"] == "assistant")
    assert_append_only(fake_model.requests)

    # История после перезагрузки: виджет с актуальной ценой
    admin_sql("UPDATE catalog_product SET price = 3000000, price_rub = 3000000 WHERE id = $1", catalog["extaro"])
    with chat(client, redis_sync, conversation) as ws:
        history = ws.receive_json()["messages"]
    widget = next(m for m in history if m["role"] == "ui")["ui"]
    assert widget["products"][0]["price"] == "3000000.00"
    # Ответ модели только с вызовом инструмента (без текста) в ленте не показывается
    assert [m["role"] for m in history] == ["user", "assistant", "ui", "assistant"]


def test_request_shape_and_cache(client, redis_sync, conversation, fake_model, knowledge_base):
    knowledge_base("База знаний: лупы и микроскопы.")
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Здравствуйте"))
        receive_until(ws, "assistant_end")
    request = fake_model.requests[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert request["thinking"] == {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}
    assert request["output_config"] == {"effort": "low"}
    assert request["tool_choice"] == {"type": "auto"}
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["system"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "лупы и микроскопы" in request["system"][1]["text"]
    assert [t["name"] for t in request["tools"]][:2] == ["catalog_overview", "search_products"]
    assert all(t["strict"] for t in request["tools"])
    beta = fake_model.headers[0]["anthropic-beta"]
    assert "server-side-fallback-2026-07-01" in beta and "thinking-binding-controls-2026-08-01" in beta


def test_client_text_reaches_model_masked(client, redis_sync, conversation, fake_model):
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Перезвоните на +7 999 123-45-67, ИНН 7701234567"))
        receive_until(ws, "assistant_end")
    sent = json.dumps(fake_model.requests, ensure_ascii=False)
    assert "123-45-67" not in sent and "7701234567" not in sent
    assert "[телефон]" in sent and "[реквизит]" in sent
    # В нашей БД — исходный текст
    assert messages_in_db(conversation["id"])[0]["content"].startswith("Перезвоните на +7 999 123-45-67")


def test_events_go_after_client_message_and_stay_there(client, redis_sync, conversation, fake_model):
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        add_event(redis_sync, conversation["id"], "order_confirmed", {"number": "AD-7", "total": 150000})
        receive_until(ws, "event")
        ws.send_json(user_message("Когда придёт счёт?"))
        receive_until(ws, "assistant_end")
        ws.send_json(user_message("Спасибо"))
        receive_until(ws, "assistant_end")
    first, second = fake_model.requests
    assert first["messages"][-1]["role"] == "system"
    assert "Менеджер подтвердил заказ № AD-7 на сумму 150 000 ₽" in first["messages"][-1]["content"]
    assert first["messages"][-2]["content"][0]["text"] == "Когда придёт счёт?"
    assert_append_only(fake_model.requests)


def test_new_knowledge_base_strips_old_thinking(client, redis_sync, conversation, fake_model, knowledge_base):
    knowledge_base("Версия 1")
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Первый вопрос"))
        receive_until(ws, "assistant_end")
        knowledge_base("Версия 2")
        ws.send_json(user_message("Второй вопрос"))
        receive_until(ws, "assistant_end")
        ws.send_json(user_message("Третий вопрос"))
        receive_until(ws, "assistant_end")
    first, second, third = fake_model.requests
    assert "Версия 2" in second["system"][1]["text"]

    def kinds(message):
        return [b["type"] for b in message["content"]]

    # Ответ прежней эпохи уходит без мышления; ответы новой эпохи — с ним
    assert kinds(second["messages"][1]) == ["text"]
    assert kinds(third["messages"][1]) == ["text"]
    assert kinds(third["messages"][3]) == ["thinking", "text"]
    assert third["messages"][:4] == second["messages"] + [third["messages"][3]]


def test_mid_stream_fallback_keeps_text_and_drops_blocks_before_boundary(
        client, redis_sync, conversation, fake_model, catalog):
    iterations = [
        {"type": "message", "input_tokens": 1000, "output_tokens": 50, "cache_creation_input_tokens": 0,
         "cache_read_input_tokens": 0, "model": "claude-opus-5-5"},
        {"type": "fallback_message", "input_tokens": 1000, "output_tokens": 100, "cache_creation_input_tokens": 0,
         "cache_read_input_tokens": 0, "model": "claude-opus-5"},
    ]
    fake_model.add(Reply(
        blocks=[thinking(), text("Начало ответа. "), tool_use("search_products", {"query": "x"}),
                fallback(), thinking(), text("Продолжение от резервной модели.")],
        model="claude-opus-5", iterations=iterations, input_transformations=[],
    ))
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Вопрос"))
        received = receive_until(ws, "assistant_end")
    assert "assistant_discard" not in [m["type"] for m in received]
    assert received[-1]["message"]["text"] == "Начало ответа. Продолжение от резервной модели."

    rows = api_rows(conversation["id"])
    assert [r["role"] for r in rows] == ["user", "assistant"]  # вызов до границы не выполнялся
    saved = json.loads(rows[1]["api"])["content"]
    assert [b["type"] for b in saved] == ["text", "thinking", "text"]

    # T10: расход по каждой попытке по цене своей модели
    attempts = usage(conversation["id"])
    assert [(a["model"], a["attempt_type"]) for a in attempts] == [
        ("claude-opus-5-5", "message"), ("claude-opus-5", "fallback_message")]
    assert attempts[0]["cost_usd"] == pricing.cost("claude-opus-5-5", pricing.Tokens(1000, 50))
    assert attempts[1]["cost_usd"] == pricing.cost("claude-opus-5", pricing.Tokens(1000, 100))


def test_refusal_of_whole_chain_is_discarded(client, redis_sync, conversation, fake_model):
    fake_model.add(Reply(blocks=[text("Частичный ответ, который нельзя показывать")], stop_reason="refusal",
                         stop_details={"type": "refusal", "category": "bio", "explanation": None}))
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Вопрос"))
        received = receive_until(ws, "error")
        types = [m["type"] for m in received]
    assert types.index("assistant_start") < types.index("assistant_discard") < types.index("error")
    assert received[-1]["code"] == "refused" and received[-1]["show_lead_button"] is True
    assert [r["role"] for r in api_rows(conversation["id"])] == ["user"]
    assert len(usage(conversation["id"])) == 1  # отказ тоже стоит денег


def test_stream_break_is_retried_once_then_model_unavailable(client, redis_sync, conversation, fake_model):
    fake_model.add(Reply(blocks=[text("abc")], break_after=4), Reply(blocks=[text("Готово")]))
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Вопрос"))
        received = receive_until(ws, "assistant_end")
    assert "assistant_discard" in [m["type"] for m in received]
    assert received[-1]["message"]["text"] == "Готово"

    fake_model.add(Reply(blocks=[text("abc")], break_after=4), Reply(blocks=[text("def")], break_after=4))
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Ещё вопрос"))
        assert receive_until(ws, "error")[-1]["code"] == "model_unavailable"
    assert redis_sync.get("chat:agent_unavailable") == "model"


def test_http_error_marks_agent_unavailable_and_success_clears_it(client, redis_sync, conversation, fake_model):
    fake_model.add(Reply(http_status=529))
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Вопрос"))
        answer = receive_until(ws, "error")[-1]
        assert answer["code"] == "model_unavailable" and answer["show_lead_button"]
        assert 0 < redis_sync.ttl("chat:agent_unavailable") <= 300
        ws.send_json(user_message("Ещё раз"))
        receive_until(ws, "assistant_end")
    assert redis_sync.get("chat:agent_unavailable") is None


def test_max_tokens_retried_with_bigger_limit(client, redis_sync, conversation, fake_model):
    fake_model.add(Reply(blocks=[text("обрезано")], stop_reason="max_tokens"), Reply(blocks=[text("Полный ответ")]))
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Вопрос"))
        assert receive_until(ws, "assistant_end")[-1]["message"]["text"] == "Полный ответ"
    assert fake_model.requests[1]["max_tokens"] == RETRY_MAX_TOKENS
    assert [r["role"] for r in api_rows(conversation["id"])] == ["user", "assistant"]


def test_dangling_tool_use_is_closed_by_appending(client, redis_sync, conversation, fake_model):
    """T12: висящий вызов (сбой, ручное вмешательство) закрывается результатом с is_error."""
    admin_sql(
        "INSERT INTO chat_message(conversation_id, role, content, api_content, event_type, epoch, stop_reason,"
        " request_id, created_at) VALUES ($1, 'user', 'старый вопрос', $2::jsonb, '', '', '', '', now()),"
        " ($1, 'assistant', '', $3::jsonb, '', '', 'tool_use', '', now())",
        conversation["id"],
        json.dumps({"role": "user", "content": [{"type": "text", "text": "старый вопрос"}]}),
        json.dumps({"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_lost", "name": "get_product", "input": {"product_id": 1}}]}),
    )
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Новый вопрос"))
        receive_until(ws, "assistant_end")
    messages = fake_model.requests[0]["messages"]
    closing = messages[2]["content"][0]
    assert closing == {"type": "tool_result", "tool_use_id": "toolu_lost", "content": "Выполнение прервано",
                       "is_error": True}
    assert messages[3]["content"][0]["text"] == "Новый вопрос"


def test_stop_during_tool_round_finishes_the_round(client, redis_sync, conversation, fake_model, catalog, monkeypatch):
    from app.ai import tools

    original = tools.run_search

    async def slow_search(ctx, args):
        import asyncio
        await asyncio.sleep(0.5)
        return await original(ctx, args)

    monkeypatch.setattr(tools.registry()["search_products"], "run", slow_search, raising=False)
    for tool in tools.TOOLS:
        if tool.name == "search_products":
            monkeypatch.setattr(tool, "run", slow_search)
    client.app.state.chat.agent.tools = tools.registry()

    fake_model.add(Reply(blocks=[thinking(), tool_use("search_products", {"query": catalog["tag"]})],
                         stop_reason="tool_use"))
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Вопрос"))
        receive_until(ws, "status")
        receive_until(ws, "status")  # «ищу в каталоге» — поток кончился, инструмент работает
        time.sleep(0.1)
        ws.send_json({"type": "stop"})
        time.sleep(0.8)
        ws.send_json({"type": "ping"})
        receive_until(ws, "pong")
    # Круг сохранён целиком, следующий запрос к модели не отправлялся
    assert [r["role"] for r in api_rows(conversation["id"])] == ["user", "assistant", "tool"]
    assert len(fake_model.requests) == 1
    assert redis_sync.get(f"chat:lock:{conversation['id']}") is None


def test_rounds_limit(client, redis_sync, conversation, fake_model, catalog, chat_settings):
    chat_settings.agent_max_tool_rounds = 2
    loop = Reply(blocks=[tool_use("catalog_overview", {})], stop_reason="tool_use")
    fake_model.add(loop, Reply(blocks=[tool_use("catalog_overview", {})], stop_reason="tool_use"))
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Вопрос"))
        assert receive_until(ws, "assistant_end")[-1]["message"]["text"] == ROUNDS_EXHAUSTED
    assert len(fake_model.requests) == 2


def test_daily_budget(client, redis_sync, conversation, fake_model, chat_settings):
    chat_settings.chat_daily_budget_usd = 0.0001
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        ws.send_json(user_message("Первый"))
        receive_until(ws, "assistant_end")
        ws.send_json(user_message("Второй"))
        answer = receive_until(ws, "error")[-1]
    assert answer["code"] == "limit_reached" and answer["show_lead_button"]
    assert redis_sync.get("chat:agent_unavailable") == "budget"
    row = usage(conversation["id"])[0]
    assert row["cost_usd"] == Decimal("0.000800")  # 100 входных по $4 + 20 выходных по $20 за млн


def test_token_count_of_knowledge_base(client, fake_model, knowledge_base):
    from app.ai.knowledge import count_pending_tokens

    version = knowledge_base("Текст базы знаний " * 50)
    counted = run_async(count_pending_tokens(client.app.state.chat.agent.client, "claude-opus-5-5"))
    assert counted >= 1
    tokens = admin_sql("SELECT token_count FROM chat_knowledgebaseversion WHERE id = $1", version, fetch=True)
    assert tokens[0]["token_count"] > 0


@pytest.mark.parametrize("event_type, payload, line", [
    ("order_created", {"number": "AD-1", "total": 1983000}, "Клиент оформил заказ № AD-1 на сумму 1 983 000 ₽"),
    ("lead_created", {"number": "L-5", "source": "ai"}, "Клиент оставил заявку № L-5"),
])
def test_event_turn_sees_form_and_event(client, redis_sync, conversation, fake_model, event_type, payload, line):
    with chat(client, redis_sync, conversation) as ws:
        ws.receive_json()
        add_event(redis_sync, conversation["id"], event_type, payload)
        receive_until(ws, "assistant_end")
        ws.send_json({"type": "ping"})
        receive_until(ws, "pong")
    with chat(client, redis_sync, conversation) as ws:
        history = ws.receive_json()["messages"]
    messages = fake_model.requests[0]["messages"]
    assert messages[0]["content"][0]["text"] == "[Клиент отправил форму на сайте]"
    assert messages[1]["role"] == "system" and line in messages[1]["content"]
    # Служебная реплика клиенту не показывается
    assert [m["role"] for m in history] == ["event", "assistant"]
