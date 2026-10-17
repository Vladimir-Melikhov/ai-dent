"""База знаний: активная версия и подсчёт токенов (ТЗ §9)."""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select, update

from ..db.engine import async_session_factory
from ..db.models import KnowledgeBaseVersion

logger = logging.getLogger(__name__)

COUNT_INTERVAL = 300
PROBE = [{"role": "user", "content": "."}]


@dataclass(frozen=True)
class KnowledgeBase:
    version_id: int | None
    content: str


class KnowledgeCache:
    """Каждый ход — один лёгкий запрос id активной версии; текст перечитывается только при смене."""

    def __init__(self) -> None:
        self._current = KnowledgeBase(None, "")

    async def active(self) -> KnowledgeBase:
        async with async_session_factory() as session:
            version_id = await session.scalar(
                select(KnowledgeBaseVersion.id).where(KnowledgeBaseVersion.is_active.is_(True))
            )
            if version_id is None:
                self._current = KnowledgeBase(None, "")
            elif version_id != self._current.version_id:
                content = await session.scalar(
                    select(KnowledgeBaseVersion.content).where(KnowledgeBaseVersion.id == version_id)
                )
                self._current = KnowledgeBase(version_id, content or "")
        return self._current


async def count_pending_tokens(client, model: str) -> int:
    """Размер версий без token_count: база как системный блок минус тот же запрос без неё."""
    async with async_session_factory() as session:
        pending = (await session.execute(
            select(KnowledgeBaseVersion.id, KnowledgeBaseVersion.content)
            .where(KnowledgeBaseVersion.token_count.is_(None))
        )).all()
    if not pending:
        return 0
    baseline = (await client.messages.count_tokens(model=model, messages=PROBE)).input_tokens
    for version_id, content in pending:
        total = (await client.messages.count_tokens(
            model=model, system=[{"type": "text", "text": content}], messages=PROBE,
        )).input_tokens
        async with async_session_factory() as session, session.begin():
            await session.execute(
                update(KnowledgeBaseVersion).where(KnowledgeBaseVersion.id == version_id)
                .values(token_count=max(total - baseline, 0))
            )
    return len(pending)


async def count_tokens_forever(client, model: str) -> None:
    """Фоновая задача api: при старте и каждые 5 минут."""
    while True:
        try:
            await count_pending_tokens(client, model)
        except Exception:
            logger.warning("Не удалось посчитать токены базы знаний", exc_info=True)
        await asyncio.sleep(COUNT_INTERVAL)
