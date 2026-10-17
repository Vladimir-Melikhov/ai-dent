"""Подменная модель для ручной проверки чата без ключа API (docker-compose.dev.yml).

Ведёт диалог по простым правилам: ищет в каталоге, показывает карточки, сравнивает,
кладёт в корзину, открывает формы. Ответы идут настоящим потоком Messages API, поэтому
проверяется весь путь: SDK, агент, история, виджеты, фронтенд. Только для разработки.
"""

import asyncio
import json
import re
import uuid

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .fake_anthropic import Reply, count_tokens, sse_events, text, thinking, tool_use

STREAM_DELAY = 0.02
PRODUCT_WORDS = ("микроскоп", "луп", "осветит", "наконечник", "установк", "рентген", "сканер", "оборудован",
                 "zeiss", "zumax", "alltion", "labomed", "lercher", "extaro", "pico")


def rub(value) -> str:
    return f"{value:,.0f} ₽".replace(",", " ") if isinstance(value, (int, float)) else "цена по запросу"


def tools_available(body: dict) -> set[str]:
    return {t["name"] for t in body.get("tools", [])}


def last_shown(body: dict) -> list[int]:
    """id товаров из последнего показа карточек — чтобы «сравни» и «в корзину» работали без уточнений."""
    calls = {}
    shown: list[int] = []
    for message in body["messages"]:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                calls[block["id"]] = block["name"]
            if block.get("type") == "tool_result" and calls.get(block["tool_use_id"]) in ("show_products", "search_products"):
                try:
                    data = json.loads(block["content"])
                except (TypeError, ValueError):
                    continue
                items = data.get("shown") or data.get("products") or []
                if items:
                    shown = [p["id"] for p in items]
    return shown


def answer_after_tool(name: str, result) -> Reply:
    if isinstance(result, str):
        return Reply(blocks=[thinking(), text(f"Не получилось: {result}")])
    if name == "search_products":
        products = result.get("products", [])
        if not products:
            return Reply(blocks=[thinking(), text("В каталоге такого не нашёл. Расскажите подробнее, что нужно, — подберу альтернативу.")])
        return Reply(blocks=[thinking(), tool_use("show_products", {"product_ids": [p["id"] for p in products[:3]]})],
                     stop_reason="tool_use")
    if name == "show_products":
        lines = [f"- **{p['name']}** — {rub(p['price_rub'])}, {p['availability']}" for p in result.get("shown", [])]
        return Reply(blocks=[thinking(), text(
            "Подобрал варианты:\n" + "\n".join(lines) + "\n\nМогу сравнить их или положить нужный в корзину.")])
    if name == "show_comparison":
        return Reply(blocks=[thinking(), text("Сравнение — в таблице выше. Подскажите, что важнее: камера, увеличение или бюджет?")])
    if name == "catalog_overview":
        names = ", ".join(c["name"] for c in result.get("categories", []) if c["products"])
        return Reply(blocks=[thinking(), text(f"В каталоге есть: {names}. Что подбираем?")])
    if name in ("get_cart", "cart_set_item"):
        items = result.get("items", [])
        lines = [f"- {i['name']} × {i['quantity']} — {rub(i['line_total_rub'])}" for i in items]
        intro = "Корзина обновлена:" if name == "cart_set_item" else "В корзине:"
        warn = ""
        if any(i.get("availability") == "под заказ" for i in items):
            warn = "\n\nЧасть товаров под заказ — срок поставки уточнит менеджер, ориентировочно по базе знаний."
        return Reply(blocks=[thinking(), text(
            f"{intro}\n" + ("\n".join(lines) or "- пусто") + f"\n\nИтого: {rub(result.get('total_rub'))}.{warn}")])
    if name == "create_quote":
        return Reply(blocks=[thinking(), text(
            f"КП № {result.get('number')} готово: итог {rub(result.get('total_rub'))}, действует до {result.get('valid_until')}. "
            "Скачать можно кнопкой в карточке выше.")])
    if name == "open_order_form":
        return Reply(blocks=[thinking(), text("Открыл форму оформления — заполните данные покупателя, состав заказа уже в ней.")])
    if name == "open_lead_form":
        return Reply(blocks=[thinking(), text("Открыл форму заявки — оставьте телефон, менеджер перезвонит в рабочее время.")])
    return Reply(blocks=[thinking(), text("Готово.")])


def decide(body: dict) -> Reply:
    messages = body["messages"]
    last = messages[-1]
    available = tools_available(body)

    if last["role"] == "system" and len(messages) > 1:
        # События, накопившиеся до реплики клиента, идут системным сообщением после неё — отвечаем на реплику
        previous = messages[-2]
        content = previous.get("content")
        said = "".join(b.get("text", "") for b in content if isinstance(b, dict)) if isinstance(content, list) else content
        if previous["role"] == "user" and "[Клиент отправил форму" not in (said or "") and not any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in (content if isinstance(content, list) else [])):
            last = previous

    if last["role"] == "system":
        if "оформил заказ" in last["content"]:
            number = re.search(r"№ (\S+)", last["content"])
            return Reply(blocks=[thinking(), text(
                f"Спасибо! Заказ № {number.group(1) if number else ''} оформлен. "
                "Менеджер свяжется с вами для подтверждения в рабочее время: пн–чт 9:30–18:00, пт 9:30–17:00.")])
        return Reply(blocks=[thinking(), text("Спасибо, заявка принята — менеджер перезвонит в рабочее время.")])

    content = last["content"]
    results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] if isinstance(content, list) else []
    if results:
        calls = {b["id"]: b["name"] for m in messages if isinstance(m.get("content"), list)
                 for b in m["content"] if b.get("type") == "tool_use"}
        block = results[-1]
        try:
            result = json.loads(block["content"])
        except (TypeError, ValueError):
            result = block["content"]
        if block.get("is_error"):
            result = str(result)
        return answer_after_tool(calls.get(block["tool_use_id"], ""), result)

    said = "".join(b.get("text", "") for b in content if isinstance(b, dict)) if isinstance(content, list) else content
    low = said.lower()
    shown = last_shown(body)

    def call(name, payload):
        return Reply(blocks=[thinking(), text("Секунду, посмотрю."), tool_use(name, payload)], stop_reason="tool_use")

    if "сравн" in low and "show_comparison" in available and len(shown) >= 2:
        return call("show_comparison", {"product_ids": shown[:3]})
    if any(w in low for w in ("скидк", "лизинг", "ремонт", "менеджер")) and "open_lead_form" in available:
        return call("open_lead_form", {"topic": said[:200]})
    if ("кп" in low.split() or "коммерческ" in low) and "create_quote" in available:
        return call("create_quote", {"source": "cart"})
    if "оформ" in low and "open_order_form" in available:
        return call("open_order_form", {})
    if any(w in low for w in ("в корзину", "положи", "добавь", "беру")) and "cart_set_item" in available and shown:
        count = re.search(r"(\d+)\s*шт", low)
        return call("cart_set_item", {"product_id": shown[0], "quantity": int(count.group(1)) if count else 1})
    if "корзин" in low and "get_cart" in available:
        return call("get_cart", {})
    if any(w in low for w in PRODUCT_WORDS) or "подбер" in low:
        return call("search_products", {"query": said[:200], "limit": 4})
    if "каталог" in low:
        return call("catalog_overview", {})
    return Reply(blocks=[thinking(), text(
        "Здравствуйте! Я консультант Ai-Dent. Подскажите, что подбираете — бинокулярные лупы или микроскоп, "
        "и для каких задач?")])


async def messages(request: Request):
    body = await request.json()
    reply = decide(body)
    reply.usage = {"input_tokens": len(json.dumps(body, ensure_ascii=False)) // 3, "output_tokens": 60}

    async def stream():
        for event in sse_events(reply):
            await asyncio.sleep(STREAM_DELAY)
            yield event

    # Свой id у каждого запроса, как у настоящего API: по ним «Расход модели» считает запросы
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"request-id": f"req_fake_{uuid.uuid4().hex[:16]}"})


async def tokens(request: Request):
    return JSONResponse(count_tokens(await request.json()))


app = Starlette(routes=[
    Route("/v1/messages", messages, methods=["POST"]),
    Route("/v1/messages/count_tokens", tokens, methods=["POST"]),
])
