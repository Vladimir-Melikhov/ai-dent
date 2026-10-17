"""Ход агента: запрос к модели, поток клиенту, инструменты, запись истории (ТЗ §7)."""

import asyncio
import datetime
import hashlib
import json
import logging
import time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
from redis.asyncio import Redis

from ..chat import store
from ..chat.hub import Hub
from ..chat.owner import Owner
from ..chat.protocol import error
from ..config import Settings
from ..db.engine import async_session_factory
from ..db.models import LLMUsage, Message
from . import history, pricing
from .client import BETAS
from .knowledge import KnowledgeBase, KnowledgeCache
from .tools import Tool, ToolContext, ToolError, ToolOutput, definitions, hydrate_widget, registry, validate

logger = logging.getLogger(__name__)

MOSCOW = ZoneInfo("Europe/Moscow")
BASE_PROMPT = (Path(__file__).parent / "prompts" / "base.md").read_text(encoding="utf-8")
RETRY_MAX_TOKENS = 32000
BUDGET_KEY = "chat:budget:{date}"
UNAVAILABLE_KEY = "chat:agent_unavailable"
UNAVAILABLE_TTL = 300
FORM_SUBMITTED = "[Клиент отправил форму на сайте]"
ROUNDS_EXHAUSTED = "Не получилось довести ответ до конца — переформулируйте, пожалуйста, вопрос."


class TurnAborted(Exception):
    """Ход прерван: клиенту уходит error с кодом (ТЗ §6.2)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class StreamBroken(Exception):
    """Поток оборвался после первого байта — SDK такое не повторяет (ТЗ §7.10)."""


def clean_blocks(content) -> list[dict]:
    """Блоки ответа для истории.

    После фолбэка посреди ответа блоки мышления, вызовы инструментов и незнакомые
    служебные блоки до последнего блока fallback не сохраняются; текст сохраняется.
    Сам блок fallback — только отметка, в историю не идёт (ТЗ §7.10).
    """
    blocks = [b.model_dump(mode="json", by_alias=True, exclude_none=True) for b in content]
    boundary = max((i for i, b in enumerate(blocks) if b["type"] == "fallback"), default=-1)
    return [
        b for i, b in enumerate(blocks)
        if b["type"] != "fallback" and (i > boundary or b["type"] == "text")
    ]


def text_of(blocks: list[dict]) -> str:
    return "".join(b["text"] for b in blocks if b["type"] == "text").strip()


def usage_rows(message, conversation_id: int, configured_model: str, request_id: str | None,
               latency_ms: int) -> list[LLMUsage]:
    """Строка на каждую попытку: при фолбэке верхний usage — только последняя (ТЗ §7.9)."""
    usage = message.usage
    attempts = usage.iterations or [None]
    rows = []
    for attempt in attempts:
        source = attempt or usage
        model = getattr(attempt, "model", None) or message.model
        breakdown = source.cache_creation
        write_5m = breakdown.ephemeral_5m_input_tokens if breakdown else (source.cache_creation_input_tokens or 0)
        write_1h = breakdown.ephemeral_1h_input_tokens if breakdown else 0
        tokens = pricing.Tokens(
            input=source.input_tokens or 0, output=source.output_tokens or 0,
            cache_write_5m=write_5m, cache_write_1h=write_1h, cache_read=source.cache_read_input_tokens or 0,
        )
        cost = pricing.cost(model, tokens)
        if cost is None:
            logger.error("Нет цены модели %s — стоимость попытки не посчитана", model)
        rows.append(LLMUsage(
            conversation_id=conversation_id, request_id=request_id or "", configured_model=configured_model,
            model=model, attempt_type=getattr(attempt, "type", "message"),
            input_tokens=tokens.input, cache_write_5m_tokens=write_5m, cache_write_1h_tokens=write_1h,
            cache_read_tokens=tokens.cache_read, output_tokens=tokens.output, cost_usd=cost,
            latency_ms=latency_ms, stop_reason=message.stop_reason or "",
        ))
    return rows


def budget_cost(rows: list[LLMUsage]) -> Decimal:
    return sum(
        (pricing.budget_cost(r.model, pricing.Tokens(r.input_tokens, r.output_tokens, r.cache_write_5m_tokens,
                                                     r.cache_write_1h_tokens, r.cache_read_tokens))
         for r in rows),
        Decimal(0),
    )


def budget_key() -> str:
    return BUDGET_KEY.format(date=f"{datetime.datetime.now(MOSCOW):%Y%m%d}")


def seconds_to_midnight() -> int:
    now = datetime.datetime.now(MOSCOW)
    midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((midnight - now).total_seconds()), 60)


class Agent:
    def __init__(self, client: anthropic.AsyncAnthropic, settings: Settings, redis: Redis, hub: Hub,
                 knowledge: KnowledgeCache) -> None:
        self.client = client
        self.settings = settings
        self.redis = redis
        self.hub = hub
        self.knowledge = knowledge
        self.tools: dict[str, Tool] = registry()
        self.tool_definitions = definitions()

    # --- запрос

    def epoch(self, kb: KnowledgeBase) -> str:
        """Отпечаток всего, что задаёт смысл блоков мышления (ТЗ §7.4)."""
        source = json.dumps(
            [self.settings.anthropic_model, BASE_PROMPT, kb.version_id, self.tool_definitions],
            ensure_ascii=False, sort_keys=True,
        )
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    def system(self, kb: KnowledgeBase) -> list[dict]:
        """Одинаков для всех клиентов: ничего меняющегося сюда не попадает (ТЗ §7.2)."""
        cached = {"type": "ephemeral", "ttl": "1h"}
        if not kb.content:
            return [{"type": "text", "text": BASE_PROMPT, "cache_control": cached}]
        return [
            {"type": "text", "text": BASE_PROMPT},
            {"type": "text", "text": "# База знаний\n\n" + kb.content, "cache_control": cached},
        ]

    def params(self, kb: KnowledgeBase, messages: list[dict], max_tokens: int) -> dict:
        return {
            "model": self.settings.anthropic_model,
            "max_tokens": max_tokens,
            "betas": BETAS,
            "fallbacks": "default",
            "thinking": {"type": "adaptive",
                         "block_binding": {"prefix_mismatch_behavior": self.settings.agent_prefix_mismatch}},
            "output_config": {"effort": self.settings.agent_effort},
            "tool_choice": {"type": "auto"},
            "tools": self.tool_definitions,
            "system": self.system(kb),
            "messages": messages,
            # Хвост истории кэшируется автоматически на 5 минут, после маркера базы знаний на 1 час
            "cache_control": {"type": "ephemeral"},
        }

    async def _stream(self, cid: int, params: dict):
        """Поток клиенту. Возвращает (ответ, был ли показан текст, request-id)."""
        started_text = False
        got_event = False
        try:
            async with self.client.beta.messages.stream(**params) as stream:
                async for event in stream:
                    got_event = True
                    if event.type == "content_block_start" and event.content_block.type == "tool_use":
                        tool = self.tools.get(event.content_block.name)
                        await self.hub.broadcast(cid, {"type": "status", "status": tool.status if tool else "thinking"})
                    elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                        if not started_text:
                            started_text = True
                            await self.hub.broadcast(cid, {"type": "assistant_start"})
                        await self.hub.broadcast(cid, {"type": "assistant_delta", "text": event.delta.text})
                message = await stream.get_final_message()
                return message, started_text, stream.request_id
        except asyncio.CancelledError:
            # «Стоп» во время генерации: ответ не сохраняется (ТЗ §7.11)
            await self.hub.broadcast(cid, {"type": "assistant_discard"})
            raise
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as error:
            if got_event:
                await self.hub.broadcast(cid, {"type": "assistant_discard"})
                raise StreamBroken(str(error)) from error
            raise

    async def _check_budget(self) -> None:
        limit = self.settings.chat_daily_budget_usd
        if limit <= 0:
            return
        spent = await self.redis.get(budget_key())
        if spent is not None and Decimal(spent) >= Decimal(str(limit)):
            await self.redis.set(UNAVAILABLE_KEY, "budget", ex=seconds_to_midnight())
            raise TurnAborted("limit_reached")

    async def _record(self, cid: int, message, request_id: str | None, started: float) -> None:
        rows = usage_rows(message, cid, self.settings.anthropic_model, request_id,
                          int((time.monotonic() - started) * 1000))
        try:
            await store.add_usage(rows)
        except Exception:
            logger.exception("Диалог %s: не удалось записать расход", cid)
        key = budget_key()
        await self.redis.incrbyfloat(key, float(budget_cost(rows)))
        await self.redis.expire(key, 2 * 24 * 3600)

    async def _unavailable(self, error: Exception) -> TurnAborted:
        logger.error("Модель недоступна: %s", error)
        await self.redis.set(UNAVAILABLE_KEY, "model", ex=UNAVAILABLE_TTL)
        return TurnAborted("model_unavailable")

    async def request(self, cid: int, kb: KnowledgeBase, messages: list[dict]):
        """Один ответ модели с обработкой отказа, обрыва и лимита токенов (ТЗ §7.5, §7.10)."""
        await self._check_budget()
        max_tokens = self.settings.agent_max_tokens
        broken = 0
        pauses = 0
        while True:
            started = time.monotonic()
            try:
                message, streamed, request_id = await self._stream(cid, self.params(kb, messages, max_tokens))
            except StreamBroken as error:
                broken += 1
                if broken > 1:
                    raise await self._unavailable(error)
                continue
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as error:
                raise await self._unavailable(error)

            await self._record(cid, message, request_id, started)
            for change in message.input_transformations or []:
                # В норме бывает только model_binding_mismatch после фолбэка (ТЗ §7.4)
                logger.warning("Диалог %s: %s %s (%s)", cid, change.type, getattr(change, "path", ""),
                               getattr(change, "reason", ""))

            if message.stop_reason == "refusal":
                if streamed:
                    await self.hub.broadcast(cid, {"type": "assistant_discard"})
                category = message.stop_details.category if message.stop_details else None
                logger.warning("Диалог %s: отказ модели, категория %s", cid, category)
                raise TurnAborted("refused")
            if message.stop_reason == "max_tokens":
                if streamed:
                    await self.hub.broadcast(cid, {"type": "assistant_discard"})
                if max_tokens < RETRY_MAX_TOKENS:
                    max_tokens = RETRY_MAX_TOKENS
                    continue
                raise await self._unavailable(RuntimeError("ответ не уложился в лимит токенов"))
            if message.stop_reason == "pause_turn":
                if streamed:
                    await self.hub.broadcast(cid, {"type": "assistant_discard"})
                pauses += 1
                if pauses > 2:
                    raise await self._unavailable(RuntimeError("pause_turn повторяется"))
                continue
            return message, streamed, request_id

    # --- инструменты

    async def _run_tool(self, ctx: ToolContext, call: dict) -> tuple[dict, ToolOutput | None]:
        tool = self.tools.get(call["name"])
        try:
            if tool is None:
                raise ToolError(f"инструмента {call['name']} нет")
            output = await tool.run(ctx, validate(tool, call.get("input") or {}))
        except ToolError as error:
            return {"type": "tool_result", "tool_use_id": call["id"], "content": str(error), "is_error": True}, None
        except Exception:
            logger.exception("Инструмент %s упал", call["name"])
            return {"type": "tool_result", "tool_use_id": call["id"],
                    "content": "внутренняя ошибка инструмента, попробуйте иначе", "is_error": True}, None
        content = json.dumps(output.result, ensure_ascii=False)
        return {"type": "tool_result", "tool_use_id": call["id"], "content": content}, output

    async def _reading(self, owner: Owner, call: dict):
        async with async_session_factory() as session:
            return await self._run_tool(ToolContext(owner, session), call)

    async def _finish_round(self, owner: Owner, assistant: Message, calls: list[dict], streamed: bool) -> None:
        """Круг с инструментами пишется атомарно: ответ, изменения корзины, результаты (ТЗ §7.11)."""
        cid = owner.conversation_id
        outcomes: list = [None] * len(calls)
        reading = [i for i, c in enumerate(calls) if not getattr(self.tools.get(c["name"]), "mutating", False)]
        done = await asyncio.gather(*(self._reading(owner, calls[i]) for i in reading))
        for i, outcome in zip(reading, done):
            outcomes[i] = outcome

        async with async_session_factory() as session, session.begin():
            for i, call in enumerate(calls):
                if outcomes[i] is None:
                    async with session.begin_nested():
                        outcomes[i] = await self._run_tool(ToolContext(owner, session), call)
            widgets = [o.widget for _, o in outcomes if o and o.widget]
            results = Message(role=Message.TOOL, content="", epoch=assistant.epoch,
                              api_content={"role": "user", "content": [r for r, _ in outcomes]},
                              ui={"widgets": widgets} if widgets else None)
            await store.add_messages(session, cid, assistant, results)

        if streamed:
            await self.hub.broadcast(cid, {"type": "assistant_end", "message": store.display(assistant)})
        async with async_session_factory() as session:
            for widget in widgets:
                hydrated = await hydrate_widget(session, widget)
                if hydrated:
                    await self.hub.broadcast(cid, {"type": "ui", "ui": hydrated})
        if any(o and o.cart_changed for _, o in outcomes):
            await self.hub.broadcast(cid, {"type": "cart_updated"})

    async def close_dangling(self, cid: int) -> list[Message]:
        """Висящий вызов закрывается результатом с is_error — дописыванием, не правкой (ТЗ §7.11)."""
        rows = await store.model_rows(cid)
        ids = history.dangling_tool_uses(rows)
        if not ids:
            return rows
        logger.error("Диалог %s: висящие вызовы инструментов %s закрыты ошибкой", cid, ids)
        await store.save_message(cid, Message.TOOL, "", api_content={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": i, "content": "Выполнение прервано", "is_error": True}
            for i in ids
        ]})
        return await store.model_rows(cid)

    # --- ход

    async def run(self, owner: Owner) -> None:
        cid = owner.conversation_id
        await self.hub.broadcast(cid, {"type": "status", "status": "thinking"})
        try:
            kb = await self.knowledge.active()
            epoch = self.epoch(kb)
            await store.set_prompt_epoch(cid, epoch)
            rounds = 0
            while True:
                rows = await self.close_dangling(cid)
                message, streamed, request_id = await self.request(cid, kb, history.build(rows, epoch))
                blocks = clean_blocks(message.content)
                assistant = Message(
                    role=Message.ASSISTANT, content=text_of(blocks), epoch=epoch,
                    api_content={"role": "assistant", "content": blocks},
                    stop_reason=message.stop_reason or "", request_id=request_id or "",
                )
                calls = [b for b in blocks if b["type"] == "tool_use"]
                if not calls:
                    async with async_session_factory() as session, session.begin():
                        await store.add_messages(session, cid, assistant)
                    if streamed or assistant.content:
                        await self.hub.broadcast(cid, {"type": "assistant_end", "message": store.display(assistant)})
                    await self.redis.delete(UNAVAILABLE_KEY)
                    return

                # Круг доводится до конца даже при «Стоп»: иначе останется висящий вызов
                round_task = asyncio.ensure_future(self._finish_round(owner, assistant, calls, streamed))
                try:
                    await asyncio.shield(round_task)
                except asyncio.CancelledError:
                    await round_task
                    raise
                await self.redis.delete(UNAVAILABLE_KEY)
                rounds += 1
                if rounds >= self.settings.agent_max_tool_rounds:
                    final = await store.save_message(cid, Message.ASSISTANT, ROUNDS_EXHAUSTED)
                    await self.hub.broadcast(cid, {"type": "assistant_end", "message": store.display(final)})
                    return
                await self.hub.broadcast(cid, {"type": "status", "status": "thinking"})
        except TurnAborted as abort:
            await self.hub.broadcast(cid, error(abort.code))

    async def run_after_event(self, owner: Owner) -> None:
        """Ход по событию: перед ним служебная реплика «клиент отправил форму» (ТЗ §7.3)."""
        await store.save_message(
            owner.conversation_id, Message.USER, "",
            api_content={"role": "user", "content": [{"type": "text", "text": FORM_SUBMITTED}]},
        )
        await self.run(owner)
