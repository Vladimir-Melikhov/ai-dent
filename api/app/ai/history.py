"""Сборка истории для модели из сохранённых сообщений.

Результат должен быть одинаковым при каждой сборке: история только дописывается,
иначе ломаются кэш и проверка блоков мышления.
"""

import copy

from ..db.models import Message

THINKING_TYPES = {"thinking", "redacted_thinking"}


def event_line(event_type: str, payload: dict | None) -> str:
    """События без ПДн — номер и сумма."""
    p = payload or {}
    number = p.get("number", "")
    total = p.get("total")
    amount = f" на сумму {total:,.0f} ₽".replace(",", " ") if isinstance(total, (int, float)) else ""
    lines = {
        "order_created": f"Клиент оформил заказ № {number}{amount}; заказ ждёт подтверждения менеджером.",
        "order_confirmed": f"Менеджер подтвердил заказ № {number}{amount}; счёт доступен клиенту.",
        "order_paid": f"Заказ № {number} оплачен.",
        "order_cancelled": f"Заказ № {number} отменён.",
        "lead_created": f"Клиент оставил заявку № {number}; менеджер перезвонит.",
    }
    return lines.get(event_type, f"Событие {event_type}.")


def events_message(events: list[Message]) -> dict:
    text = "[Событие сайта]\n" + "\n".join(f"- {event_line(e.event_type, e.event_payload)}" for e in events)
    return {"role": "system", "content": text}


def _strip_thinking(message: dict) -> dict:
    content = message.get("content")
    if not isinstance(content, list):
        return message
    return {**message, "content": [b for b in content if b.get("type") not in THINKING_TYPES]}


def build(rows: list[Message], epoch: str) -> list[dict]:
    """Сообщения для API.

    События ставятся одним системным сообщением сразу после реплики клиента, за которой
    идёт ответ модели (или которая последняя). Если за репликой сразу следует другая
    реплика (прошлый ход не удался), события переходят к более поздней — меняется только
    хвост, где блоков мышления нет.

    Блоки мышления из сообщений прежней эпохи не отправляются: вырезается сплошной
    начальный участок, что API допускает.
    """
    visible = [r for r in rows if r.role != Message.EVENT and r.api_content is not None]
    next_visible = {visible[i].id: visible[i + 1] if i + 1 < len(visible) else None for i in range(len(visible))}

    # Последнее сообщение агента не текущей эпохи: всё до него включительно — без мышления
    stale_until = max(
        (i for i, r in enumerate(visible) if r.role == Message.ASSISTANT and r.epoch != epoch), default=-1
    )
    stale_ids = {r.id for r in visible[:stale_until + 1]}

    messages: list[dict] = []
    pending: list[Message] = []
    for row in rows:
        if row.role == Message.EVENT:
            pending.append(row)
            continue
        if row.api_content is None:
            continue
        message = copy.deepcopy(row.api_content)
        if row.id in stale_ids and row.role == Message.ASSISTANT:
            message = _strip_thinking(message)
        messages.append(message)
        if row.role == Message.USER and pending:
            following = next_visible[row.id]
            if following is None or following.role == Message.ASSISTANT:
                messages.append(events_message(pending))
                pending = []
    return messages


def dangling_tool_uses(rows: list[Message]) -> list[str]:
    """id вызовов последнего ответа агента, на которые нет результатов."""
    visible = [r for r in rows if r.role != Message.EVENT and r.api_content is not None]
    if not visible or visible[-1].role != Message.ASSISTANT:
        return []
    content = visible[-1].api_content.get("content") or []
    return [b["id"] for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
