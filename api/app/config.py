from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Пустая переменная окружения — то же, что отсутствующая: берётся значение по умолчанию
    model_config = SettingsConfigDict(env_ignore_empty=True)

    app_title: str = "Ai-Dent"
    debug: bool = False
    database_url: str = "postgresql+asyncpg://dentai:dentai@localhost:5432/dentai"
    # В тестах соединения не переиспользуются между циклами событий
    database_null_pool: bool = False
    redis_url: str = "redis://localhost:6379/0"
    site_origin: str = "http://localhost"
    default_page_size: int = 20
    max_page_size: int = 60

    # Чат: лимиты из ТЗ §7.8. Оценка — $0,02–0,03 за запрос к модели, 2–3 запроса на реплику:
    # покупателю хватает 20–30 реплик, а одна сессия не съедает больше нескольких долларов в сутки
    chat_max_message_length: int = 2000
    chat_messages_per_minute: int = 6
    chat_messages_per_day: int = 50
    # Длинная история дорожает с каждой репликой — дальше предлагаем новый диалог
    chat_messages_per_conversation: int = 60
    # Суточный бюджет на модель по сайту, $ (~25–35 диалогов); 0 — без ограничения
    chat_daily_budget_usd: float = 20.0
    # Блокировка «один ход на диалог», секунды (ТЗ §7.11)
    chat_turn_lock_ttl: int = 300
    # Claude API (ТЗ §7). Прокси необязателен: обе переменные пустые — прямое подключение
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_proxy_url: str = ""  # прямой HTTP-прокси
    anthropic_base_url: str = ""  # обратный прокси вместо api.anthropic.com
    agent_effort: str = "low"
    agent_max_tokens: int = 16000
    agent_max_tool_rounds: int = 8
    # drop_block в проде, error в тестах (ТЗ §7.4)
    agent_prefix_mismatch: str = "drop_block"


@lru_cache
def get_settings() -> Settings:
    return Settings()
