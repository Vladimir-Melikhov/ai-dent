import json
import logging
import secrets

import redis
from django.conf import settings
from django.db import IntegrityError, transaction

from .models import Conversation, Message

logger = logging.getLogger(__name__)

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

TICKET_KEY = "chat:ticket:{token}"
CHANNEL = "chat:conv:{id}"
# Ставит api, когда модель недоступна или исчерпан суточный бюджет
AGENT_UNAVAILABLE_KEY = "chat:agent_unavailable"


def _publish(conversation_id, payload):
    """Звонок в api через Redis. Ошибка Redis не ломает запись в БД: событие уже лежит в истории."""
    try:
        redis_client.publish(CHANNEL.format(id=conversation_id), json.dumps(payload, ensure_ascii=False))
    except redis.RedisError:
        logger.exception("Диалог %s: не удалось опубликовать %s", conversation_id, payload.get("kind"))


def agent_unavailable() -> bool:
    try:
        return bool(redis_client.exists(AGENT_UNAVAILABLE_KEY))
    except redis.RedisError:
        logger.warning("Redis недоступен — статус агента неизвестен")
        return False


def current_conversation(owner):
    return (
        Conversation.objects.filter(**owner, status=Conversation.Status.ACTIVE)
        .order_by("-last_message_at", "-id")
        .first()
    )


def close_conversations(queryset):
    """Закрывает диалоги; после коммита их соединения получают conversation_closed."""
    ids = list(queryset.filter(status=Conversation.Status.ACTIVE).values_list("id", flat=True))
    if not ids:
        return
    Conversation.objects.filter(id__in=ids).update(status=Conversation.Status.CLOSED)
    for conversation_id in ids:
        transaction.on_commit(
            lambda cid=conversation_id: _publish(cid, {"kind": "conversation_closed"})
        )


def get_or_create_current(owner, new=False):
    """Текущий диалог владельца; new=True — закрыть текущий и начать новый."""
    for _ in range(2):
        try:
            with transaction.atomic():
                if new:
                    close_conversations(Conversation.objects.filter(**owner))
                else:
                    conversation = current_conversation(owner)
                    if conversation:
                        return conversation
                return Conversation.objects.create(**owner)
        except IntegrityError:
            # Две вкладки открыли чат одновременно — второй достаётся уже созданный диалог
            new = False
    return current_conversation(owner)


def issue_ticket(conversation):
    """Одноразовый тикет для подключения WebSocket."""
    token = secrets.token_urlsafe(32)
    payload = {
        "conversation_id": conversation.pk,
        "user_id": conversation.user_id,
        "session_id": str(conversation.session_id) if conversation.session_id else None,
    }
    redis_client.set(TICKET_KEY.format(token=token), json.dumps(payload), ex=settings.CHAT_TICKET_TTL)
    return token


def add_event(conversation, event_type, payload, content=""):
    """Строка-событие в диалоге (без ПДн) и звонок api после коммита."""
    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.EVENT,
        event_type=event_type,
        event_payload=payload,
        content=content,
    )
    transaction.on_commit(lambda: _publish(conversation.pk, {
        "kind": "event",
        "message_id": message.pk,
        "event_type": event_type,
        "payload": payload,
    }))
    return message


def merge_guest_conversations(session, user):
    """Диалоги гостя переходят на аккаунт при входе.

    Активный гостевой диалог становится текущим, активный диалог аккаунта закрывается.
    Гостевого нет — текущим остаётся диалог аккаунта.
    """
    guest = Conversation.objects.filter(session=session, user__isnull=True)
    if guest.filter(status=Conversation.Status.ACTIVE).exists():
        close_conversations(Conversation.objects.filter(user=user))
    guest.update(user=user, session=None)
