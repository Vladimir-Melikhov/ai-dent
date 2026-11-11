import logging
from functools import wraps

import redis
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

from apps.accounts.models import User
from apps.chat.services import redis_client

logger = logging.getLogger(__name__)

LOGIN_ATTEMPTS = 5
LOGIN_WINDOW = 15 * 60
SESSION_AGE = 12 * 60 * 60


def has_panel_access(user) -> bool:
    return bool(
        user.is_authenticated and user.is_active and (user.is_superuser or user.role == User.Role.DIRECTOR)
    )


def panel_required(view):
    """Только владелец. Не вошёл — на страницу входа (для fetch — 401)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if has_panel_access(request.user):
            return view(request, *args, **kwargs)
        if request.headers.get("x-requested-with") == "fetch":
            return JsonResponse({"error": "Нужен вход"}, status=401)
        query = urlencode({"next": request.get_full_path()}) if request.method == "GET" else ""
        return redirect(f"{reverse('panel:login')}?{query}" if query else reverse("panel:login"))

    return wrapper


def client_ip(request) -> str:
    # Заголовок ставит Caddy; порт catalog наружу закрыт
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")) or "?"


def _keys(request, phone):
    return [f"panel:login:ip:{client_ip(request)}", f"panel:login:phone:{phone}"]


def login_blocked(request, phone) -> bool:
    try:
        return any(int(redis_client.get(key) or 0) >= LOGIN_ATTEMPTS for key in _keys(request, phone))
    except redis.RedisError:
        logger.exception("Redis недоступен: лимит попыток входа в панель не проверен")
        return False


def login_failed(request, phone) -> None:
    try:
        for key in _keys(request, phone):
            if redis_client.incr(key) == 1:
                redis_client.expire(key, LOGIN_WINDOW)
    except redis.RedisError:
        logger.exception("Redis недоступен: неудачная попытка входа в панель не учтена")


def login_succeeded(request, phone) -> None:
    try:
        redis_client.delete(*_keys(request, phone))
    except redis.RedisError:
        pass
