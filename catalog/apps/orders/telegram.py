import logging
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def _post(text: str) -> None:
    """Одно место, где уходит запрос к Telegram: через прокси, если он задан (напрямую api.telegram.org недоступен)."""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": settings.TELEGRAM_CHAT_ID, "text": text}).encode()
    proxy = settings.TELEGRAM_PROXY_URL
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy}) if proxy else urllib.request.ProxyHandler())
    with opener.open(urllib.request.Request(url, data=data), timeout=10) as resp:
        resp.read()


def send_telegram(text: str) -> bool:
    """Сообщение в чат менеджеров."""
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        return False
    try:
        _post(text)
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

    try:
        _post(text)
        logger.info("Заказ %s: сообщение в Telegram отправлено", order.number)
    except Exception as e:
        logger.error("Заказ %s: ошибка отправки в Telegram: %s", order.number, e)
