"""Работа с таблицами диалога. Права роли api — ТЗ §4: сообщения пишем, диалог только обновляем."""

import datetime
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.tools import hydrate_widget
from ..db.engine import async_session_factory
from ..db.models import Conversation, LLMUsage, Message
from .owner import Owner


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


async def get_conversation(owner: Owner) -> Conversation | None:
    async with async_session_factory() as session:
        conversation = await session.get(Conversation, owner.conversation_id)
    if conversation is None:
        return None
    if owner.user_id is not None:
        belongs = conversation.user_id == owner.user_id
    else:
        belongs = conversation.user_id is None and conversation.session_id == owner.session_id
    return conversation if belongs else None


async def count_user_messages(conversation_id: int) -> int:
    async with async_session_factory() as session:
        return await session.scalar(
            select(func.count()).select_from(Message).where(
                Message.conversation_id == conversation_id, Message.role == Message.USER
            )
        )


async def client_message_exists(conversation_id: int, client_message_id: uuid.UUID) -> bool:
    async with async_session_factory() as session:
        found = await session.scalar(
            select(Message.id).where(
                Message.conversation_id == conversation_id,
                Message.client_message_id == client_message_id,
            )
        )
    return found is not None


async def owner_of(conversation_id: int) -> Owner | None:
    async with async_session_factory() as session:
        conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    return Owner(conversation.id, conversation.user_id, conversation.session_id)


async def add_messages(session: AsyncSession, conversation_id: int, *messages: Message) -> None:
    """Сообщения и время последнего сообщения диалога — в транзакции вызывающего."""
    moment = now()
    for message in messages:
        message.conversation_id = conversation_id
        message.created_at = moment
        session.add(message)
    await session.flush()
    await session.execute(
        update(Conversation).where(Conversation.id == conversation_id).values(last_message_at=moment)
    )


async def save_message(conversation_id: int, role: str, content: str, **fields) -> Message:
    async with async_session_factory() as session, session.begin():
        message = Message(role=role, content=content, **fields)
        await add_messages(session, conversation_id, message)
    return message


async def model_rows(conversation_id: int) -> list[Message]:
    """Всё, из чего собирается история для модели, по порядку id."""
    async with async_session_factory() as session:
        return list((await session.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        )).all())


async def set_prompt_epoch(conversation_id: int, epoch: str) -> None:
    async with async_session_factory() as session, session.begin():
        await session.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(prompt_epoch=epoch)
        )


async def add_usage(rows: list[LLMUsage]) -> None:
    async with async_session_factory() as session, session.begin():
        session.add_all(rows)


async def get_event(conversation_id: int, message_id: int) -> Message | None:
    async with async_session_factory() as session:
        message = await session.get(Message, message_id)
    if message is None or message.conversation_id != conversation_id or message.role != Message.EVENT:
        return None
    return message


def display(message: Message) -> dict:
    """Сообщение в формате для фронтенда (без виджетов — их собирает history())."""
    return {
        "id": message.id,
        "role": message.role,
        "text": message.content,
        "ui": message.ui,
        "event_type": message.event_type or None,
        "event_payload": message.event_payload,
        "created_at": message.created_at.isoformat(),
    }


async def widgets_for_display(session: AsyncSession, message: Message) -> list[dict]:
    items = []
    for index, widget in enumerate((message.ui or {}).get("widgets", [])):
        hydrated = await hydrate_widget(session, widget)
        if hydrated:
            items.append({"id": f"{message.id}-{index}", "role": "ui", "ui": hydrated,
                          "created_at": message.created_at.isoformat()})
    return items


async def history(conversation_id: int) -> list[dict]:
    """Что видит клиент: реплики, ответы, события и виджеты с актуальными данными каталога.

    Служебные результаты инструментов, реплика «клиент отправил форму» и ответы модели
    без текста (только вызов инструмента) не показываются.
    """
    items = []
    async with async_session_factory() as session:
        rows = await session.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        )
        for message in rows:
            if message.role == Message.TOOL:
                items.extend(await widgets_for_display(session, message))
            elif message.role in (Message.USER, Message.ASSISTANT) and not message.content:
                # Служебная реплика «клиент отправил форму» и ответ модели только с вызовом инструмента
                continue
            elif message.role in (Message.USER, Message.ASSISTANT, Message.EVENT):
                items.append(display(message))
    return items
