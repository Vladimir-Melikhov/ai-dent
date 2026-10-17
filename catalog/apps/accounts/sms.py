import logging

logger = logging.getLogger(__name__)


def send_sms(phone: str, text: str) -> None:
    # Заглушка: печатаем в консоль. Реальный провайдер (SMS.ru) — позже за этим интерфейсом.
    logger.info("[SMS] на %s: %s", phone, text)
    print(f"[SMS] на {phone}: {text}")


def send_otp_sms(phone: str, code: str) -> None:
    send_sms(phone, f"Ваш код для входа в Ai-Dent: {code}")
