"""Клиент Claude API: напрямую или через прокси."""

from urllib.parse import urlsplit

import anthropic

from ..config import Settings, get_settings

DEFAULT_BASE_URL = "https://api.anthropic.com"
# Фолбэк при отказе классификатора и контроль привязки блоков мышления
BETAS = ["server-side-fallback-2026-07-01", "thinking-binding-controls-2026-08-01"]


def _client_kwargs(settings: Settings) -> dict:
    # Адрес передаётся явно: иначе SDK возьмёт ANTHROPIC_BASE_URL из окружения, и пустая строка его сломает
    return {
        "api_key": settings.anthropic_api_key,
        "base_url": settings.anthropic_base_url or DEFAULT_BASE_URL,
    }


def create_client(settings: Settings | None = None) -> anthropic.Anthropic:
    settings = settings or get_settings()
    kwargs = _client_kwargs(settings)
    if settings.anthropic_proxy_url:
        kwargs["http_client"] = anthropic.DefaultHttpxClient(proxy=settings.anthropic_proxy_url)
    return anthropic.Anthropic(**kwargs)


def create_async_client(settings: Settings | None = None) -> anthropic.AsyncAnthropic:
    settings = settings or get_settings()
    kwargs = _client_kwargs(settings)
    if settings.anthropic_proxy_url:
        kwargs["http_client"] = anthropic.DefaultAsyncHttpxClient(proxy=settings.anthropic_proxy_url)
    return anthropic.AsyncAnthropic(**kwargs)


def describe_route(settings: Settings | None = None) -> str:
    """Как идут запросы — без логинов и паролей из адреса прокси."""
    settings = settings or get_settings()
    if settings.anthropic_proxy_url:
        return f"через HTTP-прокси {urlsplit(settings.anthropic_proxy_url).hostname}"
    if settings.anthropic_base_url:
        return f"через обратный прокси {urlsplit(settings.anthropic_base_url).hostname}"
    return "напрямую (api.anthropic.com)"
