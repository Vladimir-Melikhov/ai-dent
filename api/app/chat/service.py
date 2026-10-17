import asyncio
import datetime
import json
import logging
import secrets
import uuid
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.websockets import WebSocket

from ..ai.agent import Agent
from ..ai.masking import mask_pii
from ..config import Settings
from ..db.models import Conversation, Message
from . import store
from .hub import Hub
from .owner import Owner
from .protocol import error

logger = logging.getLogger(__name__)

MOSCOW = ZoneInfo("Europe/Moscow")

TICKET_KEY = "chat:ticket:{token}"
LOCK_KEY = "chat:lock:{id}"
EVENT_KEY = "chat:event:{id}"
PENDING_KEY = "chat:conv:{id}:pending"
MINUTE_KEY = "chat:limit:minute:{id}"
DAY_KEY = "chat:limit:day:{owner}:{date}"
CHANNEL_PATTERN = "chat:conv:*"

EVENT_TTL = 24 * 60 * 60

# Снять блокировку, только если она наша: иначе можно снять чужую после истечения срока
RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

# Коды закрытия WebSocket: 4401 — нужен новый тикет, 4403 — доступ запрещён, 4409 — диалог закрыт
CLOSE_BAD_TICKET = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_CONVERSATION_CLOSED = 4409

def triggers_turn(event_type: str, payload: dict | None) -> bool:
    """Ход агента запускают оформленный заказ и заявка из чата; заявка из запасной формы — нет (ТЗ §6.3)."""
    if event_type == "order_created":
        return True
    return event_type == "lead_created" and (payload or {}).get("source") == "ai"


class ChatService:
    def __init__(self, redis: Redis, hub: Hub, settings: Settings, agent: Agent) -> None:
        self.redis = redis
        self.hub = hub
        self.settings = settings
        self.agent = agent
        # Ссылки на фоновые задачи, чтобы их не собрал сборщик мусора
        self._background: set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # --- подключение

    async def redeem_ticket(self, token: str) -> Owner | None:
        """Тикет одноразовый: забирается из Redis с удалением (ТЗ §6.2)."""
        if not token:
            return None
        raw = await self.redis.getdel(TICKET_KEY.format(token=token))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            session_id = uuid.UUID(data["session_id"]) if data.get("session_id") else None
            user_id = int(data["user_id"]) if data.get("user_id") is not None else None
            owner = Owner(conversation_id=int(data["conversation_id"]), user_id=user_id, session_id=session_id)
        except (ValueError, KeyError, TypeError):
            logger.warning("Повреждённый тикет")
            return None
        if owner.user_id is None and owner.session_id is None:
            return None
        return owner

    async def conversation_for(self, owner: Owner) -> Conversation | None:
        return await store.get_conversation(owner)

    async def history(self, conversation_id: int) -> list[dict]:
        return await store.history(conversation_id)

    # --- блокировка и лимиты

    async def acquire_lock(self, conversation_id: int) -> str | None:
        token = secrets.token_hex(8)
        locked = await self.redis.set(
            LOCK_KEY.format(id=conversation_id), token, nx=True, ex=self.settings.chat_turn_lock_ttl
        )
        return token if locked else None

    async def release_lock(self, conversation_id: int, token: str) -> None:
        await self.redis.eval(RELEASE_LOCK, 1, LOCK_KEY.format(id=conversation_id), token)

    async def _count(self, key: str, ttl: int) -> int:
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, ttl)
        return count

    async def check_limits(self, owner: Owner) -> str | None:
        """Счётчики по образцу otp.py (ТЗ §7.8). Возвращает код ошибки или None."""
        if await store.count_user_messages(owner.conversation_id) >= self.settings.chat_messages_per_conversation:
            return "conversation_full"
        minute = await self._count(MINUTE_KEY.format(id=owner.conversation_id), 60)
        if minute > self.settings.chat_messages_per_minute:
            return "rate_limited"
        today = datetime.datetime.now(MOSCOW).strftime("%Y%m%d")
        day = await self._count(DAY_KEY.format(owner=owner.key, date=today), EVENT_TTL)
        if day > self.settings.chat_messages_per_day:
            return "limit_reached"
        return None

    # --- сообщения клиента

    async def on_user_message(self, ws: WebSocket, owner: Owner, data: dict) -> None:
        text = data.get("text")
        try:
            client_id = uuid.UUID(str(data.get("id")))
        except ValueError:
            await ws.send_json(error("bad_request"))
            return
        if not isinstance(text, str) or not text.strip():
            await ws.send_json(error("bad_request"))
            return
        text = text.strip()
        if len(text) > self.settings.chat_max_message_length:
            await ws.send_json(error("too_long"))
            return

        cid = owner.conversation_id
        lock = await self.acquire_lock(cid)
        if lock is None:
            await ws.send_json(error("busy"))
            return
        try:
            # Повтор той же реплики после переподключения — молча игнорируем
            if await store.client_message_exists(cid, client_id):
                await self.release_lock(cid, lock)
                return
            code = await self.check_limits(owner)
            if code:
                await self.release_lock(cid, lock)
                await ws.send_json(error(code))
                return
            # Висящий вызов инструмента закрывается до новой реплики — иначе история не соберётся (ТЗ §7.11)
            await self.agent.close_dangling(cid)
            # В БД — исходный текст для человека; модели — замаскированная копия, раз и навсегда (ТЗ §7.4, §7.6)
            message = await store.save_message(
                cid, Message.USER, text, client_message_id=client_id,
                api_content={"role": "user", "content": [{"type": "text", "text": mask_pii(text)}]},
            )
        except BaseException:
            await self.release_lock(cid, lock)
            raise

        await self.hub.broadcast(cid, {"type": "user_message", "id": str(client_id), "message": store.display(message)})
        self.hub.start_turn(cid, self._turn(owner, lock, self.agent.run(owner)))

    def on_stop(self, owner: Owner) -> None:
        self.hub.cancel_turn(owner.conversation_id)

    # --- ход агента

    async def _turn(self, owner: Owner, lock: str, work) -> None:
        """Обёртка хода: блокировка снимается всегда, затем — события, пришедшие во время хода."""
        cid = owner.conversation_id
        try:
            await work
        finally:
            await self.release_lock(cid, lock)
            self._spawn(self._drain_pending(cid))

    # --- события из Django (ТЗ §6.3)

    async def _drain_pending(self, cid: int) -> None:
        """События, пришедшие во время хода, обрабатываются сразу после него."""
        try:
            raw = await self.redis.lpop(PENDING_KEY.format(id=cid))
            if raw is None:
                return
            event = await store.get_event(cid, int(raw))
            owner = await store.owner_of(cid)
            if event is None or owner is None:
                return
            lock = await self.acquire_lock(cid)
            if lock is None:
                # Ход уже идёт — событие вернётся в очередь и дождётся его окончания
                await self.redis.lpush(PENDING_KEY.format(id=cid), raw)
                return
            self.hub.start_turn(cid, self._turn(owner, lock, self.agent.run_after_event(owner)))
        except Exception:
            logger.exception("Диалог %s: не удалось обработать очередь событий", cid)

    async def on_channel_message(self, cid: int, data: dict) -> None:
        if not self.hub.has(cid):
            return
        kind = data.get("kind")
        if kind == "conversation_closed":
            self.hub.cancel_turn(cid)
            await self.hub.broadcast(cid, {"type": "conversation_closed"})
            await self.hub.close_all(cid, CLOSE_CONVERSATION_CLOSED)
            return
        if kind != "event":
            return

        message_id = int(data["message_id"])
        event_type = data.get("event_type", "")
        payload = data.get("payload") or {}
        event = await store.get_event(cid, message_id)
        if event is not None:
            await self.hub.broadcast(cid, {"type": "event", "message": store.display(event)})
        if not triggers_turn(event_type, payload):
            return
        # Один ход на событие, сколько бы соединений и процессов ни было
        claimed = await self.redis.set(EVENT_KEY.format(id=message_id), 1, nx=True, ex=EVENT_TTL)
        if not claimed:
            return
        owner = await store.owner_of(cid)
        if owner is None:
            return
        lock = await self.acquire_lock(cid)
        if lock is None:
            key = PENDING_KEY.format(id=cid)
            await self.redis.rpush(key, message_id)
            await self.redis.expire(key, EVENT_TTL)
            return
        self.hub.start_turn(cid, self._turn(owner, lock, self.agent.run_after_event(owner)))

    async def listen(self) -> None:
        """Подписка на события всех диалогов; переподключается при обрыве Redis."""
        while True:
            pubsub = self.redis.pubsub()
            try:
                await pubsub.psubscribe(CHANNEL_PATTERN)
                async for message in pubsub.listen():
                    if message["type"] != "pmessage":
                        continue
                    try:
                        cid = int(message["channel"].rsplit(":", 1)[1])
                        data = json.loads(message["data"])
                        await self.on_channel_message(cid, data)
                    except Exception:
                        logger.exception("Не удалось обработать событие из канала %s", message.get("channel"))
            except RedisConnectionError:
                logger.warning("Redis недоступен, подписка на события будет восстановлена")
                await asyncio.sleep(1)
            finally:
                await pubsub.aclose()
