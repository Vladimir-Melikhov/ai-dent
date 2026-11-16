import json
import re

from .models import AnonSession


def get_owner_filter(request, create=False):
    """Возвращает dict для фильтра/создания: привязка к юзеру или гостевой сессии.

    Гостевая сессия создаётся только при create=True (первое добавление в корзину/избранное).
    Если сессии нет и create=False — возвращает None: у гостя ещё ничего нет.
    """
    if request.user.is_authenticated:
        return {"user": request.user}
    session = getattr(request, "anon_session", None)
    if session is None:
        if not create:
            return None
        session = AnonSession.objects.create()
        request.anon_session = session
        request.anon_session_created = True
    return {"session": session}


def parse_json_body(request):
    """Тело запроса как dict; None — если это не JSON-объект."""
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


# Реализация вынесена в phone.py: модели пользователя тоже нормализуют номер, а utils импортирует модели
from .phone import normalize_phone  # noqa: E402,F401


def get_client_ip(request) -> str:
    # За Caddy реальный IP клиента — последний в X-Forwarded-For (его дописывает сам прокси)
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")
