"""Коды входа по SMS.

Провайдер выбирается SMS_PROVIDER:
- notificore — 2FA API Notificore: код генерирует, отправляет и проверяет Notificore, у нас хранится только id сеанса;
- console — код генерируется здесь и пишется в лог (локальный стенд, тесты).
"""
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request

from django.conf import settings

from .constans import OTP_CODE_DIGITS, OTP_MAX_ATTEMPTS, OTP_TTL

logger = logging.getLogger(__name__)

NOTIFICORE_URL = "https://one-api.notificore.ru/api"
NOTIFICORE_TIMEOUT = 10


class SmsError(Exception):
    """Провайдер не настроен, недоступен или не принял запрос."""


def send_otp(phone: str) -> str:
    """Отправляет код на +7XXXXXXXXXX. Возвращает ссылку на код для check_otp: id сеанса или сам код."""
    provider = settings.SMS_PROVIDER
    if provider == "notificore":
        return _notificore_send(phone)
    if provider == "console":
        code = f"{secrets.randbelow(10 ** OTP_CODE_DIGITS):0{OTP_CODE_DIGITS}d}"
        logger.info("[SMS] код для входа на %s: %s", phone, code)
        return code
    raise SmsError(f"SMS_PROVIDER не задан или неизвестен: {provider!r}")


def check_otp(reference: str, code: str) -> bool:
    """True — код верный. False — неверный, просрочен или попытки у провайдера кончились."""
    if settings.SMS_PROVIDER == "notificore":
        return _notificore_verify(reference, code)
    return secrets.compare_digest(reference, code)


# --- Notificore

_token_lock = threading.Lock()
_token = {"value": "", "expires_at": 0.0}


def _notificore_send(phone: str) -> str:
    status, body = _notificore_call("/2fa/authentications/otp", {
        "channel": "sms",
        "recipient": phone.lstrip("+"),
        "sender": settings.SMS_SENDER,
        "template_id": int(settings.SMS_TEMPLATE_ID or 0),
        "code_digits": OTP_CODE_DIGITS,
        "code_max_tries": OTP_MAX_ATTEMPTS,
        "code_lifetime": OTP_TTL,
    })
    auth_id = (body.get("data") or {}).get("id") if status == 200 else None
    if not auth_id:
        logger.error("Notificore: код не отправлен, HTTP %s: %s", status, _errors(body))
        raise SmsError("код не отправлен")
    return auth_id


def _notificore_verify(auth_id: str, code: str) -> bool:
    if not code.isdigit():
        return False
    # Строкой: числом код с ведущим нулём («0123») не пройдёт
    status, body = _notificore_call(f"/2fa/authentications/otp/{auth_id}/verify", {"access_code": code})
    if status == 200:
        return True
    # 404 — код не совпал или сеанс не найден; 422 — код просрочен, попытки исчерпаны, сеанс закрыт
    if status in (404, 422):
        logger.info("Notificore: код не принят, HTTP %s: %s", status, _errors(body))
        return False
    logger.error("Notificore: проверка кода не прошла, HTTP %s: %s", status, _errors(body))
    raise SmsError("код не проверен")


def _notificore_call(path: str, payload: dict) -> tuple[int, dict]:
    """POST с токеном; токен просрочен (401) — получаем новый и повторяем один раз."""
    for attempt in range(2):
        status, body = _post(path, payload, token=_notificore_token(refresh=attempt > 0))
        if status != 401:
            return status, body
    return status, body


def _notificore_token(refresh: bool = False) -> str:
    """JWT по ключу API; живёт час, обновляем за минуту до срока."""
    with _token_lock:
        if refresh or time.time() >= _token["expires_at"]:
            if not settings.SMS_API_KEY:
                raise SmsError("SMS_API_KEY не задан")
            status, body = _post("/auth/login", {"api_key": settings.SMS_API_KEY})
            if status != 200 or not body.get("bearer"):
                logger.error("Notificore: токен не получен, HTTP %s: %s", status, _errors(body))
                raise SmsError("токен не получен")
            _token["value"] = body["bearer"]
            _token["expires_at"] = time.time() + 59 * 60
        return _token["value"]


def _post(path: str, payload: dict, token: str = "") -> tuple[int, dict]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        NOTIFICORE_URL + path, data=json.dumps(payload).encode(), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=NOTIFICORE_TIMEOUT) as response:
            return response.status, _json(response.read())
    except urllib.error.HTTPError as e:
        return e.code, _json(e.read())
    except OSError as e:
        logger.error("Notificore недоступен: %s", e)
        raise SmsError("провайдер недоступен") from e


def _json(raw: bytes) -> dict:
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _errors(body: dict) -> str:
    """Текст ошибки Notificore для лога — без телефона и токена."""
    errors = body.get("errors")
    if isinstance(errors, list):
        return "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors)
    if isinstance(errors, dict):
        return "; ".join(f"{field}: {', '.join(map(str, msgs))}" for field, msgs in errors.items())
    return str(body.get("message") or body)[:300]
