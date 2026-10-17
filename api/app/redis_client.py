from redis.asyncio import Redis

from .config import get_settings


def create_redis() -> Redis:
    """Клиент создаётся в lifespan приложения: соединения привязаны к его циклу событий."""
    return Redis.from_url(get_settings().redis_url, decode_responses=True)
