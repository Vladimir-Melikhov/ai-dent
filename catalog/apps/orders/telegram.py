import logging
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def send_telegram(text: str) -> bool:
    """Сообщение в чат менеджеров."""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5) as resp:
            resp.read()
        logger.info("Сообщение в Telegram отправлено")
        return True
    except Exception as e:
        logger.error("Ошибка отправки в Telegram: %s", e)
        return False


def send_order_telegram(order):
    """Отправить уведомление о заказе в Telegram-чат менеджера."""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return

    # Без персональных данных: имя, телефон и почта покупателя — только в письме
    count = order.items.count()
    lines = [
        f"🛒 Новый заказ №{order.number}",
        f"Позиций: {count} · сумма {order.total_amount} {order.currency}",
        f"Оплата: {order.get_payment_method_display() if order.payment_method else '—'}",
        "",
        "Состав и данные покупателя — в письме на почте.",
    ]
    text = "\n".join(lines)

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data)

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        logger.info("Заказ %s: сообщение в Telegram отправлено", order.number)
    except Exception as e:
        logger.error("Заказ %s: ошибка отправки в Telegram: %s", order.number, e)
