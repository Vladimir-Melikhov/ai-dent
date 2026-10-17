"""Корзина, КП и заявка глазами агента (ТЗ §8, D16, D17, T7)."""

import datetime
import json
import uuid
from decimal import Decimal

import pytest

from app.ai.tools import ToolContext, ToolError, hydrate_widget, registry, validate
from app.chat.owner import Owner
from app.db.engine import async_session_factory
from devtools.fake_anthropic import Reply, thinking, tool_use

from .conftest import add_event, admin_sql, connect, make_ticket, receive_until, run_async, user_message


def owner_of(conversation):
    return Owner(conversation["id"], None, uuid.UUID(conversation["session_id"]))


def run_tool(owner, name, payload):
    async def go():
        async with async_session_factory() as session, session.begin():
            tool = registry()[name]
            return await tool.run(ToolContext(owner, session), validate(tool, payload))

    return run_async(go())


def cart_rows(conversation):
    return admin_sql("SELECT product_id, quantity, selected_options::text AS options FROM orders_cartitem"
                     " WHERE session_id = $1 ORDER BY id", uuid.UUID(conversation["session_id"]), fetch=True)


@pytest.fixture
def owner(conversation):
    yield owner_of(conversation)
    admin_sql("DELETE FROM orders_cartitem WHERE session_id = $1", uuid.UUID(conversation["session_id"]))
    admin_sql("DELETE FROM chat_quote WHERE session_id = $1", uuid.UUID(conversation["session_id"]))


def test_set_item_is_idempotent_and_zero_removes(owner, conversation, catalog):
    run_tool(owner, "cart_set_item", {"product_id": catalog["extaro"], "quantity": 2})
    output = run_tool(owner, "cart_set_item", {"product_id": catalog["extaro"], "quantity": 2})
    assert output.cart_changed
    assert [(r["product_id"], r["quantity"]) for r in cart_rows(conversation)] == [(catalog["extaro"], 2)]
    assert cart_rows(conversation)[0]["options"] == "{}"
    assert output.result["total_rub"] == 5848658.0

    run_tool(owner, "cart_set_item", {"product_id": catalog["extaro"], "quantity": 0})
    assert cart_rows(conversation) == []


def test_out_of_stock_allowed_with_delivery_note(owner, catalog):
    """«Под заказ» можно положить (D16) — агент предупреждает о сроке поставки."""
    result = run_tool(owner, "cart_set_item", {"product_id": catalog["pico"], "quantity": 1}).result
    assert result["items"][0]["availability"] == "под заказ"
    assert "срок" in result["note"]


def test_rules_match_storefront(owner, catalog):
    with pytest.raises(ToolError, match="нет в каталоге"):
        run_tool(owner, "cart_set_item", {"product_id": catalog["old"], "quantity": 1})
    admin_sql("UPDATE catalog_product SET price_rub = NULL WHERE id = $1", catalog["usd"])
    with pytest.raises(ToolError, match="цена по запросу"):
        run_tool(owner, "cart_set_item", {"product_id": catalog["usd"], "quantity": 1})
    with pytest.raises(ToolError):
        validate(registry()["cart_set_item"], {"product_id": catalog["loupe"], "quantity": 1000})


def test_cart_belongs_to_owner(owner, conversation, catalog):
    stranger = Owner(conversation["id"], None, uuid.uuid4())
    admin_sql("INSERT INTO accounts_anonsession(session_id, created_at, last_seen_at) VALUES ($1, now(), now())",
              stranger.session_id)
    try:
        run_tool(stranger, "cart_set_item", {"product_id": catalog["loupe"], "quantity": 3})
        assert run_tool(owner, "get_cart", {}).result["items"] == []
    finally:
        admin_sql("DELETE FROM orders_cartitem WHERE session_id = $1", stranger.session_id)
        admin_sql("DELETE FROM accounts_anonsession WHERE session_id = $1", stranger.session_id)


def test_price_formula_with_options_and_currency(owner, conversation, catalog):
    """T7, сторона api: те же числа, что даёт формула в Django."""
    today = datetime.date.today()
    admin_sql("INSERT INTO catalog_currencyrate(currency, date, rate, fetched_at) VALUES ('USD', $1, 95.5, now())"
              " ON CONFLICT (currency, date) DO UPDATE SET rate = 95.5", today)
    option = admin_sql("INSERT INTO catalog_productoption(product_id, name, value, price_delta)"
                       " VALUES ($1, 'Вариоскоп', 'да', 10) RETURNING id", catalog["usd"], fetch=True)[0]["id"]
    try:
        run_tool(owner, "cart_set_item", {"product_id": catalog["usd"], "quantity": 1})
        admin_sql("UPDATE orders_cartitem SET selected_options = $1::jsonb WHERE product_id = $2",
                  json.dumps({"option_ids": [option]}), catalog["usd"])
        item = run_tool(owner, "get_cart", {}).result["items"][0]
        assert item["unit_price_rub"] == 95000.0 + 955.0  # price_rub + 10 × 95,5
    finally:
        admin_sql("DELETE FROM catalog_productoption WHERE id = $1", option)


def test_quote_from_cart_and_from_items(owner, conversation, catalog):
    run_tool(owner, "cart_set_item", {"product_id": catalog["loupe"], "quantity": 2})
    output = run_tool(owner, "create_quote", {"source": "cart"})
    assert output.result["number"].startswith("KP-") and output.result["total_rub"] == 97000.0
    valid_until = datetime.date.fromisoformat(output.result["valid_until"])
    assert (valid_until - datetime.date.today()).days in (13, 14, 15)

    quote = admin_sql("SELECT * FROM chat_quote WHERE id = $1", uuid.UUID(output.widget["quote_id"]), fetch=True)[0]
    assert quote["conversation_id"] == conversation["id"] and quote["pdf_path"] == ""
    items = json.loads(quote["items"])
    assert items[0]["unit_price_rub"] == "48500.00" and items[0]["source_currency"] == "RUB"

    usd = run_tool(owner, "create_quote", {"source": "items", "items": [{"product_id": catalog["usd"], "quantity": 1}]})
    assert usd.result["total_rub"] == 95000.0

    async def card():
        async with async_session_factory() as session:
            return await hydrate_widget(session, output.widget)

    assert run_async(card())["url"] == f"/chat/quote/{output.widget['quote_id']}/"


def test_quote_needs_items(owner):
    with pytest.raises(ToolError, match="корзина пуста"):
        run_tool(owner, "create_quote", {"source": "cart"})
    with pytest.raises(ToolError):
        run_tool(owner, "create_quote", {"source": "items"})


def test_lead_form_keeps_masked_topic(owner, conversation):
    output = run_tool(owner, "open_lead_form", {"topic": "Лизинг, перезвонить на +7 999 123-45-67"})
    assert output.widget == {"widget": "lead_form"}
    topic = admin_sql("SELECT pending_lead_topic FROM chat_conversation WHERE id = $1", conversation["id"],
                      fetch=True)[0]["pending_lead_topic"]
    assert topic == "Лизинг, перезвонить на [телефон]"


def test_agent_changes_cart_and_storefront_is_told(client, redis_sync, conversation, owner, catalog, fake_model):
    fake_model.add(Reply(blocks=[thinking(), tool_use("cart_set_item", {"product_id": catalog["loupe"], "quantity": 1})],
                         stop_reason="tool_use"))
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message("Положите лупу в корзину"))
        received = receive_until(ws, "assistant_end")
    types = [m["type"] for m in received]
    assert "cart_updated" in types
    assert [m["status"] for m in received if m["type"] == "status"][1] == "cart"
    assert [(r["product_id"], r["quantity"]) for r in cart_rows(conversation)] == [(catalog["loupe"], 1)]


# --- форма заказа (ТЗ §8, §10.2, D6)


def test_order_form_only_with_items(owner, catalog):
    with pytest.raises(ToolError, match="корзина пуста"):
        run_tool(owner, "open_order_form", {})
    run_tool(owner, "cart_set_item", {"product_id": catalog["loupe"], "quantity": 2})
    output = run_tool(owner, "open_order_form", {})
    # Модель в форму ничего не передаёт: состав — текущая корзина, данные покупателя вводит клиент
    assert output.widget == {"widget": "order_form"}
    assert output.result["items_count"] == 1 and output.result["total_rub"] == 97000.0


def test_status_events_show_card_without_turn(client, redis_sync, conversation, fake_model):
    """order_confirmed / order_paid / order_cancelled — только карточка, хода агента нет (ТЗ §6.3)."""
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        add_event(redis_sync, conversation["id"], "order_confirmed",
                  {"number": "AD-7", "total": 97000.0, "invoice_url": "/cart/invoice/AD-7/"})
        event = receive_until(ws, "event")[-1]["message"]
        assert event["event_type"] == "order_confirmed" and event["event_payload"]["invoice_url"] == "/cart/invoice/AD-7/"
        add_event(redis_sync, conversation["id"], "order_paid", {"number": "AD-7", "total": 97000.0})
        assert receive_until(ws, "event")[-1]["message"]["event_type"] == "order_paid"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    assert fake_model.requests == []
    # В модель события попадут системным сообщением при следующей реплике — номер и сумма, без ПДн
    with connect(client, make_ticket(redis_sync, conversation)) as ws:
        ws.receive_json()
        ws.send_json(user_message("Спасибо"))
        receive_until(ws, "assistant_end")
    system = [m for m in fake_model.requests[-1]["messages"] if m["role"] == "system"]
    assert "Менеджер подтвердил заказ № AD-7 на сумму 97 000 ₽" in system[-1]["content"]
    assert "Заказ № AD-7 оплачен" in system[-1]["content"]
