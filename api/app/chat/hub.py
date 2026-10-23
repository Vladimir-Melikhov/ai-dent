import asyncio
import logging
from collections import defaultdict
from collections.abc import Coroutine

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


class Hub:
    """Открытые соединения и идущие ходы по диалогам.

    api — один процесс, поэтому реестр живёт в памяти. Если процессов станет несколько,
    рассылка переводится на канал Redis.
    """

    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._turns: dict[int, asyncio.Task] = {}

    def add(self, conversation_id: int, ws: WebSocket) -> None:
        self._connections[conversation_id].add(ws)

    def remove(self, conversation_id: int, ws: WebSocket) -> None:
        connections = self._connections.get(conversation_id)
        if connections is None:
            return
        connections.discard(ws)
        if not connections:
            del self._connections[conversation_id]

    def has(self, conversation_id: int) -> bool:
        return bool(self._connections.get(conversation_id))

    async def broadcast(self, conversation_id: int, payload: dict) -> None:
        for ws in list(self._connections.get(conversation_id, ())):
            try:
                await ws.send_json(payload)
            except Exception:
                # Соединение уже закрыто — его обработчик сам уберёт себя из реестра
                self.remove(conversation_id, ws)

    async def close_all(self, conversation_id: int, code: int) -> None:
        for ws in list(self._connections.get(conversation_id, ())):
            try:
                await ws.close(code=code)
            except Exception:
                pass
            self.remove(conversation_id, ws)

    def start_turn(self, conversation_id: int, coro: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._turns[conversation_id] = task

        def forget(done: asyncio.Task) -> None:
            if self._turns.get(conversation_id) is done:
                del self._turns[conversation_id]
            if not done.cancelled() and done.exception():
                logger.error("Диалог %s: ход завершился ошибкой", conversation_id, exc_info=done.exception())

        task.add_done_callback(forget)
        return task

    def cancel_turn(self, conversation_id: int) -> bool:
        task = self._turns.get(conversation_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        tasks = list(self._turns.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
