import secrets

import redis
from django.conf import settings

from .constans import (
    OTP_ATTEMPTS_KEY,
    OTP_IP_HOURLY_LIMIT,
    OTP_IP_KEY,
    OTP_KEY,
    OTP_MAX_ATTEMPTS,
    OTP_RESEND_COOLDOWN,
    OTP_RESEND_KEY,
    OTP_TTL,
)

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

# Результаты проверки кода
OTP_OK = "ok"
OTP_INVALID = "invalid"
OTP_LOCKED = "locked"


def resend_wait_seconds(phone: str) -> int:
    """Сколько секунд ждать до повторной отправки кода на номер (0 — можно отправлять)."""
    ttl = redis_client.ttl(OTP_RESEND_KEY.format(phone=phone))
    return ttl if ttl and ttl > 0 else 0


def ip_limit_exceeded(ip: str) -> bool:
    """Считает запрос кода с IP; True — часовой лимит исчерпан."""
    key = OTP_IP_KEY.format(ip=ip)
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, 3600)
    return count > OTP_IP_HOURLY_LIMIT


def generate_and_store_otp(phone: str) -> str:
    code = f"{secrets.randbelow(10000):04d}"
    pipe = redis_client.pipeline()
    pipe.set(OTP_KEY.format(phone=phone), code, ex=OTP_TTL)
    pipe.delete(OTP_ATTEMPTS_KEY.format(phone=phone))
    pipe.set(OTP_RESEND_KEY.format(phone=phone), 1, ex=OTP_RESEND_COOLDOWN)
    pipe.execute()
    return code


def verify_otp(phone: str, code: str) -> str:
    code_key = OTP_KEY.format(phone=phone)
    attempts_key = OTP_ATTEMPTS_KEY.format(phone=phone)

    stored = redis_client.get(code_key)
    if stored is None:
        return OTP_INVALID

    attempts = redis_client.incr(attempts_key)
    if attempts == 1:
        redis_client.expire(attempts_key, OTP_TTL)
    if attempts > OTP_MAX_ATTEMPTS:
        # код сгорает — нужно запросить новый
        redis_client.delete(code_key, attempts_key)
        return OTP_LOCKED

    if secrets.compare_digest(stored, code):
        redis_client.delete(code_key, attempts_key)
        return OTP_OK
    if attempts == OTP_MAX_ATTEMPTS:
        redis_client.delete(code_key, attempts_key)
        return OTP_LOCKED
    return OTP_INVALID
