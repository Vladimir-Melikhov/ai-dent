"""Подменная модель Claude для тестов и ручных проверок без ключа API.

Отвечает в настоящем потоковом формате Messages API (SSE), поэтому через неё проходит
тот же код SDK и агента, что и с живой моделью. В прод-образ не попадает (.dockerignore).
"""

import asyncio
import itertools
import json
from dataclasses import dataclass, field
from typing import Callable

import httpx2

MODEL = "claude-opus-5-5"
_ids = itertools.count(1)


@dataclass
class Reply:
    """Один ответ модели: блоки, причина остановки, расход и сбои для проверки обработки ошибок."""

    blocks: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    model: str = MODEL
    usage: dict = field(default_factory=lambda: {"input_tokens": 100, "output_tokens": 20})
    iterations: list | None = None
    stop_details: dict | None = None
    http_status: int | None = None  # ошибка до начала потока
    break_after: int | None = None  # обрыв потока после N событий (overloaded_error)
    input_transformations: list | None = None
    delay: float = 0.0  # пауза между событиями потока, секунды


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def thinking(signature: str | None = None) -> dict:
    # Отображение рассуждений по умолчанию omitted: текст пустой, подпись есть
    return {"type": "thinking", "thinking": "", "signature": signature or f"sig-{next(_ids)}"}


def tool_use(name: str, payload: dict, tool_id: str | None = None) -> dict:
    return {"type": "tool_use", "id": tool_id or f"toolu_{next(_ids):04d}", "name": name, "input": payload}


def fallback(from_model: str = MODEL, to_model: str = "claude-opus-5") -> dict:
    return {"type": "fallback", "from": {"model": from_model}, "to": {"model": to_model},
            "trigger": {"type": "refusal", "category": "general_harms"}}


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_events(reply: Reply) -> list[str]:
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": 0, **reply.usage}
    start_usage = {**usage, "output_tokens": 1}
    message = {"id": f"msg_{next(_ids):06d}", "type": "message", "role": "assistant", "model": reply.model,
               "content": [], "stop_reason": None, "stop_sequence": None, "usage": start_usage}
    events = [_event("message_start", {"type": "message_start", "message": message})]
    for index, block in enumerate(reply.blocks):
        kind = block["type"]
        if kind == "text":
            events.append(_event("content_block_start", {"type": "content_block_start", "index": index,
                                                         "content_block": {"type": "text", "text": ""}}))
            value = block["text"]
            for i in range(0, len(value), 12) or [0]:
                events.append(_event("content_block_delta", {"type": "content_block_delta", "index": index,
                                                             "delta": {"type": "text_delta", "text": value[i:i + 12]}}))
        elif kind == "thinking":
            events.append(_event("content_block_start", {"type": "content_block_start", "index": index,
                                                         "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
            events.append(_event("content_block_delta", {"type": "content_block_delta", "index": index,
                                                         "delta": {"type": "signature_delta", "signature": block["signature"]}}))
        elif kind == "tool_use":
            events.append(_event("content_block_start", {"type": "content_block_start", "index": index,
                                                         "content_block": {**block, "input": {}}}))
            events.append(_event("content_block_delta", {"type": "content_block_delta", "index": index,
                                                         "delta": {"type": "input_json_delta",
                                                                   "partial_json": json.dumps(block["input"], ensure_ascii=False)}}))
        else:
            events.append(_event("content_block_start", {"type": "content_block_start", "index": index,
                                                         "content_block": block}))
        events.append(_event("content_block_stop", {"type": "content_block_stop", "index": index}))
    delta_usage = dict(usage)
    if reply.iterations is not None:
        delta_usage["iterations"] = reply.iterations
    delta = {"type": "message_delta",
             "delta": {"stop_reason": reply.stop_reason, "stop_sequence": None, "stop_details": reply.stop_details},
             "usage": delta_usage}
    if reply.input_transformations is not None:
        delta["input_transformations"] = reply.input_transformations
    events.append(_event("message_delta", delta))
    events.append(_event("message_stop", {"type": "message_stop"}))
    if reply.break_after is not None:
        events = events[:reply.break_after] + [_event("error", {
            "type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})]
    return events


def respond(reply: Reply) -> httpx2.Response:
    headers = {"request-id": f"req_fake_{next(_ids):06d}"}
    if reply.http_status:
        body = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
        return httpx2.Response(reply.http_status, json=body, headers=headers)
    events = sse_events(reply)
    headers["content-type"] = "text/event-stream"
    if not reply.delay:
        return httpx2.Response(200, headers=headers, content="".join(events).encode())

    async def slow():
        for event in events:
            await asyncio.sleep(reply.delay)
            yield event.encode()

    return httpx2.Response(200, headers=headers, content=slow())


def last_text(body: dict) -> str:
    """Текст последнего сообщения запроса: реплика клиента или событие сайта."""
    message = body["messages"][-1]
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


def echo(body: dict) -> Reply:
    return Reply(blocks=[thinking(), text(f"Это тестовый ответ консультанта. Вы написали: «{last_text(body)}»")])


def count_tokens(body: dict) -> dict:
    """Грубая оценка: около трёх символов на токен."""
    return {"input_tokens": max(1, len(json.dumps(body, ensure_ascii=False)) // 3)}


class ScriptedModel:
    """Модель по сценарию: ответы берутся по очереди; все запросы сохраняются для проверок."""

    def __init__(self, default: Callable[[dict], Reply] | None = None) -> None:
        self.queue: list[Reply | Callable[[dict], Reply]] = []
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.default = default
        self.delay = 0.0  # пауза между событиями для ответов по умолчанию

    def add(self, *replies) -> "ScriptedModel":
        self.queue.extend(replies)
        return self

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json=count_tokens(body))
        self.requests.append(body)
        self.headers.append(dict(request.headers))
        if self.queue:
            reply = self.queue.pop(0)
        elif self.default:
            reply = self.default
        else:
            raise AssertionError("подменная модель: сценарий закончился, а запрос пришёл")
        if callable(reply):
            reply = reply(body)
            if self.delay and not reply.delay:
                reply.delay = self.delay
        return respond(reply)

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handler)


def tool_results(body: dict) -> dict:
    """Результаты инструментов из последнего сообщения запроса: id вызова → разобранный JSON."""
    results = {}
    for block in body["messages"][-1]["content"]:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            content = block.get("content")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content)
            try:
                results[block["tool_use_id"]] = json.loads(content)
            except (TypeError, ValueError):
                results[block["tool_use_id"]] = content
    return results
