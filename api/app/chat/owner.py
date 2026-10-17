import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Owner:
    """Владелец соединения — только из тикета, никогда из сообщений клиента (ТЗ §6.2)."""

    conversation_id: int
    user_id: int | None
    session_id: uuid.UUID | None

    @property
    def key(self) -> str:
        return f"u{self.user_id}" if self.user_id is not None else f"s{self.session_id}"
