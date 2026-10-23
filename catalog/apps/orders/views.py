import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import JsonResponse, FileResponse, Http404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.utils import get_owner_filter, normalize_phone, parse_json_body
from apps.catalog.models import Product
from .models import MAX_ITEM_QUANTITY, CartItem, Order, OrderStatusHistory
from .services import (
    CartChanged, CartInvalid, cart_item_allowed, cart_lines, cart_total, cart_version, create_order_from_cart,
    has_foreign_currency, invoice_available, invoice_path, valid_inn,
)
from .emails import send_order_email
from .telegram import send_order_telegram

logger = logging.getLogger(__name__)

MAX_REQUISITES_LENGTH = 1000
# Онлайн-оплата пока выключена: включить здесь и в checkout.html после интеграции провайдера (+ касса 54-ФЗ)
ENABLED_PAYMENT_METHODS = {Order.PaymentMethod.INVOICE}


def _bad_request(message):
    return JsonResponse({"error": message}, status=400)


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@require_POST
def cart_add(request):
    data = parse_json_body(request)
    if data is None:
        return _bad_request("Некорректный запрос")
    product_id = _parse_int(data.get("product_id"))
    quantity = _parse_int(data.get("quantity", 1))
    if product_id is None:
        return _bad_request("product_id обязателен")
    if quantity is None or quantity <= 0 or quantity > MAX_ITEM_QUANTITY:
        return _bad_request("Некорректное количество")
    product = Product.objects.filter(id=product_id, is_active=True).first()
    if product is None:
        return JsonResponse({"error": "Товар не найден"}, status=404)
    # Правило допуска одно для витрины и агента; остаток не проверяется — «под заказ» можно
    reason = cart_item_allowed(product)
    if reason:
        return _bad_request(f"Нельзя добавить в корзину: {reason}")

    owner = get_owner_filter(request, create=True)
    item, created = CartItem.objects.get_or_create(
        **owner, product_id=product_id, defaults={"quantity": quantity}
    )
    if not created:
        if item.quantity + quantity > MAX_ITEM_QUANTITY:
            return _bad_request(f"Максимум {MAX_ITEM_QUANTITY} шт. одного товара")
        item.quantity += quantity
        item.save()
    count = CartItem.objects.filter(**owner).count()
    return JsonResponse({"ok": True, "quantity": item.quantity, "count": count})


def cart_list(request):
    lines = cart_lines(get_owner_filter(request))
    result = []
    for line in lines:
        p = line.item.product
        img = p.images.first()
        result.append({
            "id": line.item.id,
            "product_id": p.id,
            "name": p.name,
            "brand": p.brand.name,
            # Цена за единицу в рублях — по единой формуле, та же, что попадёт в заказ
            "price": float(line.unit_price) if line.unit_price is not None else None,
            "currency": p.currency,
            "quantity": line.item.quantity,
            "line_total": float(line.line_total) if line.line_total is not None else None,
            "under_order": p.stock_qty <= 0,
            "image": img.image.url if img else None,
            "slug": p.slug,
        })
    return JsonResponse({
        "items": result,
        "total": float(cart_total(lines)),
        "count": len(result),
        "version": cart_version(lines),
        "has_foreign_currency": has_foreign_currency(lines),
    })


@require_POST
def cart_update(request):
    data = parse_json_body(request)
    if data is None:
        return _bad_request("Некорректный запрос")
    item_id = _parse_int(data.get("item_id"))
    quantity = _parse_int(data.get("quantity", 1))
    # 0 — удалить позицию (так работает «−» в корзине), отрицательное — ошибка
    if item_id is None or quantity is None or quantity < 0 or quantity > MAX_ITEM_QUANTITY:
        return _bad_request("Некорректное количество")
    owner = get_owner_filter(request)
    item = CartItem.objects.filter(**owner, id=item_id).first() if owner else None
    if not item:
        return JsonResponse({"error": "Позиция не найдена"}, status=404)
    if quantity == 0:
        item.delete()
        return JsonResponse({"ok": True, "removed": True})
    item.quantity = quantity
    item.save()
    return JsonResponse({"ok": True, "quantity": item.quantity})


@require_POST
def cart_remove(request):
    data = parse_json_body(request)
    if data is None:
        return _bad_request("Некорректный запрос")
    item_id = _parse_int(data.get("item_id"))
    if item_id is None:
        return _bad_request("item_id обязателен")
    owner = get_owner_filter(request)
    item = CartItem.objects.filter(**owner, id=item_id).first() if owner else None
    if item:
        item.delete()
    return JsonResponse({"ok": True})


def cart_count(request):
    owner = get_owner_filter(request)
    if owner is None:
        return JsonResponse({"count": 0})
    count = CartItem.objects.filter(**owner).count()
    return JsonResponse({"count": count})


def _send_order_notifications(order_id):
    """Письмо и Telegram — после коммита заказа; ошибки не ломают оформление."""
    order = Order.objects.get(pk=order_id)
    try:
        send_order_email(order)
    except Exception:
        logger.exception("Заказ %s: ошибка отправки письма", order.number)
    try:
        send_order_telegram(order)
    except Exception:
        logger.exception("Заказ %s: ошибка отправки в Telegram", order.number)


@require_POST
@transaction.atomic
def checkout(request):
    data = parse_json_body(request)
    if data is None:
        return _bad_request("Некорректный запрос")

    def text(field, max_length):
        value = data.get(field)
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValidationError(field)
        value = value.strip()
        if len(value) > max_length:
            raise ValidationError(field)
        return value

    try:
        contact_name = text("contact_name", 255)
        contact_email = text("contact_email", 254)
        requisites = text("requisites", MAX_REQUISITES_LENGTH) or None
    except ValidationError:
        return _bad_request("Проверьте заполнение полей (реквизиты — до 1000 символов)")

    contact_phone = normalize_phone(data.get("contact_phone"))
    if not contact_phone:
        return _bad_request("Телефон обязателен")
    # Согласие на обработку ПДн — как у заказа из чата: без него заказ не создаётся, время сохраняется
    if data.get("consent") is not True:
        return _bad_request("Подтвердите согласие с политикой конфиденциальности и соглашением")

    if contact_email:
        try:
            validate_email(contact_email)
        except ValidationError:
            return _bad_request("Некорректный email")

    payment_method = data.get("payment_method", Order.PaymentMethod.INVOICE)
    if payment_method not in Order.PaymentMethod.values:
        return _bad_request("Неизвестный способ оплаты")
    if payment_method not in ENABLED_PAYMENT_METHODS:
        return _bad_request("Этот способ оплаты временно недоступен")

    owner = get_owner_filter(request)
    if owner is None:
        return _bad_request("Корзина пуста")
    version = data.get("cart_version")
    if not isinstance(version, str) or not version:
        return _bad_request("Обновите страницу и оформите заказ ещё раз")
    try:
        order = create_order_from_cart(
            owner,
            {"contact_name": contact_name, "contact_phone": contact_phone, "contact_email": contact_email,
             "requisites": requisites, "payment_method": payment_method, "pd_consent_at": timezone.now()},
            Order.CreatedBy.CATALOG, version,
            actor_user=request.user if request.user.is_authenticated else None,
        )
    except CartChanged:
        return JsonResponse({"error": "cart_changed", "message": "Состав или цены изменились — проверьте заказ"},
                            status=409)
    except CartInvalid as error:
        return _bad_request(str(error))

    # Счёт выставляется при подтверждении менеджером, а не здесь
    # письмо и telegram — только после успешного коммита заказа
    transaction.on_commit(lambda: _send_order_notifications(order.pk))

    result = {"ok": True, "number": order.number, "total": float(order.total_amount)}

    # Онлайн-оплата — выключена до интеграции провайдера (ЮKassa / Т-Банк + чеки 54-ФЗ).
    # Чтобы включить: добавить ONLINE в ENABLED_PAYMENT_METHODS, вернуть вариант в checkout.html
    # и заменить заглушку в payment.py реальным созданием платежа.
    # if payment_method == Order.PaymentMethod.ONLINE:
    #     from .payment import create_payment_stub
    #     pay = create_payment_stub(order)
    #     result["pay_url"] = pay["pay_url"]

    return JsonResponse(result)


def orders_list(request):
    if not request.user.is_authenticated:
        return JsonResponse({"items": []})

    orders = (
        Order.objects.filter(user=request.user)
        .select_related("invoice")
        .order_by("-created_at")
    )
    result = []
    for o in orders:
        has_invoice = invoice_available(o)
        result.append({
            "number": o.number,
            "created_at": o.created_at.strftime("%d.%m.%Y"),
            "status": o.get_status_display(),
            "total": float(o.total_amount) if o.total_amount is not None else 0.0,
            "items_count": o.items.count(),
            "invoice_url": reverse("orders:invoice_download", args=[o.number]) if has_invoice else None,
        })
    return JsonResponse({"items": result})


def _is_order_owner(request, order):
    if request.user.is_authenticated and order.user_id == request.user.id:
        return True
    session = getattr(request, "anon_session", None)
    return session is not None and order.session_id == session.pk


def invoice_download(request, number):
    order = Order.objects.filter(number=number).first()
    if not order or not _is_order_owner(request, order):
        raise Http404("Счёт не найден")
    # До подтверждения и у отменённого заказа счёта нет
    if not invoice_available(order):
        raise Http404("Счёт не найден")
    return FileResponse(
        open(invoice_path(order.invoice), "rb"),
        content_type="application/pdf",
        as_attachment=True,
        filename=f"schet-{number}.pdf",
    )


# --- заказ из чата: данные покупателя идут в Django и в модель не попадают

CHAT_ORDER_LIMITS = {
    "company_name": 255, "buyer_name": 255, "contact_name": 255, "contact_email": 254,
    "delivery_city": 255, "delivery_address": 1000, "comment": 2000, "inn": 12,
}


class FormError(Exception):
    pass


def _inn_error(inn: str, length: int, whose: str) -> str:
    # Длина верная, но не сходится контрольная цифра — «10 цифр» клиента только запутает
    if len(inn) == length and inn.isdigit():
        return f"ИНН {whose} не проходит проверку — проверьте цифры"
    return f"ИНН {whose} — {length} цифр"


def _chat_order_data(data: dict) -> dict:
    """Поля покупателя по типу; ошибка — текст для клиента."""
    values = {}
    for field, limit in CHAT_ORDER_LIMITS.items():
        value = data.get(field)
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise FormError("Проверьте заполнение полей")
        value = " ".join(value.split()) if field not in ("delivery_address", "comment") else value.strip()
        if len(value) > limit:
            raise FormError("Слишком длинное значение — сократите текст")
        values[field] = value

    buyer_type = data.get("buyer_type")
    if buyer_type not in Order.BuyerType.values:
        raise FormError("Выберите тип покупателя")
    inn = values["inn"]
    if buyer_type == Order.BuyerType.LEGAL:
        if not values["company_name"]:
            raise FormError("Укажите название организации")
        if not valid_inn(inn, 10):
            raise FormError(_inn_error(inn, 10, "организации"))
        if not values["contact_name"]:
            raise FormError("Укажите контактное лицо")
        values["buyer_name"] = ""
    else:
        if not values["buyer_name"]:
            raise FormError("Укажите ФИО")
        if buyer_type == Order.BuyerType.IP:
            if not valid_inn(inn, 12):
                raise FormError(_inn_error(inn, 12, "ИП"))
        else:
            inn = ""
        values["company_name"] = ""
        # Для ИП и физлица контактное лицо — сам покупатель
        values["contact_name"] = values["buyer_name"]
    values["inn"] = inn

    phone = normalize_phone(data.get("contact_phone"))
    if not phone:
        raise FormError("Введите телефон в формате +7 999 123 45 67")
    if values["contact_email"]:
        try:
            validate_email(values["contact_email"])
        except ValidationError:
            raise FormError("Некорректный email") from None
    if not values["delivery_city"]:
        raise FormError("Укажите город доставки")
    if data.get("consent") is not True:
        raise FormError("Подтвердите согласие на обработку персональных данных")
    return {
        **values, "buyer_type": buyer_type, "contact_phone": phone, "pd_consent_at": timezone.now(),
        "payment_method": Order.PaymentMethod.INVOICE,
    }


@require_POST
@transaction.atomic
def chat_order(request):
    """Заказ из формы в чате: статус pending_confirmation, created_by = ai, связь с диалогом."""
    from apps.chat.models import Message
    from apps.chat.services import add_event, current_conversation

    data = parse_json_body(request)
    if data is None:
        return _bad_request("Некорректный запрос")
    try:
        fields = _chat_order_data(data)
    except FormError as error:
        return _bad_request(str(error))
    version = data.get("cart_version")
    owner = get_owner_filter(request)
    if owner is None:
        return _bad_request("Корзина пуста")
    if not isinstance(version, str) or not version:
        return _bad_request("Обновите форму и оформите заказ ещё раз")

    conversation = current_conversation(owner)
    try:
        order = create_order_from_cart(
            owner, fields, Order.CreatedBy.AI, version, conversation=conversation,
            actor=OrderStatusHistory.Actor.AI, actor_user=request.user if request.user.is_authenticated else None,
        )
    except CartChanged:
        return JsonResponse({"error": "cart_changed", "message": "Состав или цены изменились — проверьте заказ"},
                            status=409)
    except CartInvalid as error:
        return _bad_request(str(error))

    if conversation:
        # В диалог — только номер и сумма: данные покупателя модели не нужны
        add_event(conversation, Message.EventType.ORDER_CREATED,
                  {"number": order.number, "total": float(order.total_amount)}, content=f"Заказ № {order.number}")
    transaction.on_commit(lambda: _send_order_notifications(order.pk))
    return JsonResponse({"ok": True, "number": order.number, "total": float(order.total_amount)})
