"""Сервисный слой заказов: единая формула цены, правило допуска, версия корзины,
создание заказа, смена статуса, счёт при подтверждении, правка состава менеджером."""

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.catalog.currency import rate_on, to_rub
from apps.catalog.models import Product, ProductOption
from .models import MAX_ITEM_QUANTITY, CartItem, Invoice, Order, OrderChange, OrderItem, OrderStatusHistory

logger = logging.getLogger(__name__)


class CartChanged(Exception):
    """Состав или цены корзины изменились после показа формы — заказ не создаётся (409)."""


class CartInvalid(Exception):
    """В корзине позиция, которую нельзя заказать — текст для клиента."""


def cart_item_allowed(product: Product) -> str | None:
    """Правило допуска — одно для витрины и агента: товар активен и у него есть цена в рублях.

    Остаток не проверяется: товар «под заказ» купить можно. Количество 1–999 закреплено в БД.
    """
    if not product.is_active:
        return "товар больше недоступен"
    if product.price_rub is None:
        return "цена по запросу — оставьте заявку менеджеру"
    return None


def option_ids(selected_options) -> list[int]:
    ids = (selected_options or {}).get("option_ids") if isinstance(selected_options, dict) else None
    return [int(i) for i in ids or [] if str(i).isdigit()]


def unit_price_rub(item: CartItem, options: list[ProductOption] | None = None) -> Decimal | None:
    """Единая формула: price_rub товара + доплаты опций по тому же курсу, до копеек.

    Без опций равна price_rub — поэтому корзина в Django и ответы агента дают одни и те же числа.
    """
    product = item.product
    if product.price_rub is None:
        return None
    if options is None:
        ids = option_ids(item.selected_options)
        options = list(ProductOption.objects.filter(product=product, id__in=ids)) if ids else []
    if not options:
        return product.price_rub
    rate = rate_on(product.currency)
    return product.price_rub + sum((to_rub(o.price_delta, rate) for o in options), Decimal(0))


@dataclass
class CartLine:
    item: CartItem
    unit_price: Decimal | None
    options: list[ProductOption]

    @property
    def line_total(self) -> Decimal | None:
        return self.unit_price * self.item.quantity if self.unit_price is not None else None


def cart_lines(owner) -> list[CartLine]:
    if owner is None:
        return []
    items = (
        CartItem.objects.filter(**owner)
        .select_related("product", "product__brand")
        .order_by("-created_at", "-id")
    )
    lines = []
    for item in items:
        ids = option_ids(item.selected_options)
        options = list(ProductOption.objects.filter(product=item.product, id__in=ids)) if ids else []
        lines.append(CartLine(item, unit_price_rub(item, options), options))
    return lines


def cart_total(lines: list[CartLine]) -> Decimal:
    return sum((line.line_total or Decimal(0) for line in lines), Decimal(0))


def cart_version(lines: list[CartLine]) -> str:
    """Отпечаток состава и цен: форма отправляет его обратно, расхождение — 409."""
    parts = sorted(f"{l.item.id}:{l.item.product_id}:{l.item.quantity}:{l.unit_price}" for l in lines)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def has_foreign_currency(lines: list[CartLine]) -> bool:
    return any(line.item.product.currency != Product.Currency.RUB for line in lines)


@transaction.atomic
def create_order_from_cart(owner, data: dict, created_by: str, version: str, *, conversation=None,
                           actor=OrderStatusHistory.Actor.CUSTOMER, actor_user=None) -> Order:
    """Общая для /checkout/ и чата. Счёт здесь не создаётся."""
    lines = cart_lines(owner)
    if not lines:
        raise CartInvalid("Корзина пуста")
    if version != cart_version(lines):
        raise CartChanged()
    for line in lines:
        reason = cart_item_allowed(line.item.product)
        if reason:
            raise CartInvalid(f"{line.item.product.name}: {reason}")
        if not 1 <= line.item.quantity <= MAX_ITEM_QUANTITY:
            raise CartInvalid(f"{line.item.product.name}: некорректное количество")

    order = Order.objects.create(
        **owner, status=Order.Status.PENDING_CONFIRMATION, payment_status=Order.PaymentStatus.NONE,
        # Временный уникальный номер до получения id: пустые номера двух одновременных заказов конфликтуют
        currency="RUB", created_by=created_by, number=f"tmp-{uuid.uuid4().hex}", conversation=conversation, **data,
    )
    order.number = f"AD-{timezone.now():%Y%m%d}-{order.id:04d}"

    total = Decimal(0)
    today = timezone.localdate()
    for line in lines:
        product = line.item.product
        foreign = product.currency != Product.Currency.RUB
        total += line.line_total
        OrderItem.objects.create(
            order=order, product=product, product_name=product.name,
            unit_price=line.unit_price, quantity=line.item.quantity, line_total=line.line_total,
            selected_options=[
                {"id": o.id, "name": o.name, "value": o.value, "price_delta": str(o.price_delta)} for o in line.options
            ],
            source_price=product.price, source_currency=product.currency,
            exchange_rate=rate_on(product.currency, today) if foreign else None,
        )
    order.total_amount = total
    order.total_at_creation = total
    order.save()

    OrderStatusHistory.objects.create(
        order=order, from_status=Order.Status.DRAFT, to_status=Order.Status.PENDING_CONFIRMATION,
        actor=actor, actor_user=actor_user,
    )
    CartItem.objects.filter(**owner).delete()
    return order


# --- ИНН покупателя

INN10 = [2, 4, 10, 3, 5, 9, 4, 6, 8]
INN12_1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
INN12_2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]


def valid_inn(inn: str, length: int) -> bool:
    """Контрольная сумма ИНН: 10 цифр — юрлицо, 12 — ИП."""
    if len(inn) != length or not inn.isdigit():
        return False
    digits = [int(c) for c in inn]

    def check(coefficients):
        return sum(c * d for c, d in zip(coefficients, digits)) % 11 % 10

    if length == 10:
        return check(INN10) == digits[9]
    return check(INN12_1) == digits[10] and check(INN12_2) == digits[11]


# --- статусы

S = Order.Status
TRANSITIONS = {
    S.PENDING_CONFIRMATION: {S.CONFIRMED, S.CANCELLED},
    S.CONFIRMED: {S.PAID, S.CANCELLED},
}


class TransitionError(Exception):
    """Переход запрещён или невозможен — текст для менеджера."""


class InvoiceError(Exception):
    """PDF счёта не сформировался — подтверждение откатывается."""


def allowed_transitions(order) -> set[str]:
    return TRANSITIONS.get(order.status, set())


def invoice_path(invoice) -> str:
    return os.path.join(settings.MEDIA_ROOT, invoice.pdf_path) if invoice and invoice.pdf_path else ""


def invoice_available(order) -> bool:
    """Одно правило для скачивания, «Моих заказов» и чата: подтверждён или оплачен — счёт есть;
    старый счёт, выставленный при оформлении, продолжает выдаваться, пока заказ ждёт подтверждения; у отменённого — нет."""
    invoice = getattr(order, "invoice", None)
    if invoice is None or not os.path.exists(invoice_path(invoice) or "/nonexistent"):
        return False
    if order.status in (S.CONFIRMED, S.PAID):
        return True
    return invoice.legacy and order.status == S.PENDING_CONFIRMATION


def order_total(order) -> Decimal:
    return sum((item.line_total for item in order.items.all()), Decimal(0))


def recalc_foreign_items(order, date) -> None:
    """Пересчёт валютных товаров по курсу ЦБ на дату подтверждения.

    Доставка и позиции с ценой менеджера не пересчитываются; total_at_creation не меняется.
    """
    items = order.items.filter(kind=OrderItem.Kind.PRODUCT, price_overridden=False).exclude(source_currency__in=["", "RUB"])
    for item in items:
        rate = rate_on(item.source_currency, date)
        if rate is None or item.source_price is None:
            raise TransitionError(f"Нет курса {item.source_currency} — обновите курсы ЦБ и повторите")
        options = item.selected_options if isinstance(item.selected_options, list) else []
        unit = to_rub(item.source_price, rate) + sum(
            (to_rub(Decimal(str(o.get("price_delta", 0))), rate) for o in options), Decimal(0)
        )
        item.unit_price = unit
        item.line_total = unit * item.quantity
        item.exchange_rate = rate
        item.save(update_fields=["unit_price", "line_total", "exchange_rate"])
    order.total_amount = order_total(order)


def issue_invoice(order, issued_at) -> Invoice:
    """Счёт с датой подтверждения; старый счёт перевыпускается, отметка legacy снимается."""
    from .invoice import generate_invoice_pdf

    try:
        path = generate_invoice_pdf(order, issued_at)
    except Exception as error:
        raise InvoiceError(str(error) or error.__class__.__name__) from error
    invoice, _ = Invoice.objects.update_or_create(order=order, defaults={
        "number": f"INV-{order.number}", "pdf_path": path, "issued_at": issued_at,
        "legacy": False, "emailed_at": None,
    })
    return invoice


def invoice_url(order) -> str:
    return reverse("orders:invoice_download", args=[order.number])


EVENT_TEXT = {
    S.CONFIRMED: "Заказ № {} подтверждён",
    S.PAID: "Заказ № {} оплачен",
    S.CANCELLED: "Заказ № {} отменён",
}


def _status_event(order, to_status) -> None:
    """Системная карточка в диалоге: номер, сумма, ссылка на счёт — без ПДн; хода агента нет."""
    from apps.chat.models import Message
    from apps.chat.services import add_event

    event_type = {S.CONFIRMED: Message.EventType.ORDER_CONFIRMED, S.PAID: Message.EventType.ORDER_PAID,
                  S.CANCELLED: Message.EventType.ORDER_CANCELLED}[to_status]
    payload = {"number": order.number, "total": float(order.total_amount or 0)}
    if to_status == S.CONFIRMED:
        payload["invoice_url"] = invoice_url(order)
        if settings.PAYMENT_LINK_ENABLED:
            from .payment import create_payment_stub
            payload["pay_url"] = create_payment_stub(order)["pay_url"]
    add_event(order.conversation, event_type, payload, content=EVENT_TEXT[to_status].format(order.number))


@transaction.atomic
def _change_status(order_id, to_status, actor, actor_user) -> Order:
    order = Order.objects.select_for_update().get(pk=order_id)
    if to_status not in allowed_transitions(order):
        raise TransitionError(
            f"Заказ № {order.number}: переход «{order.get_status_display()}» → «{S(to_status).label}» не разрешён"
        )
    from_status = order.status
    now = timezone.now()
    if to_status == S.CONFIRMED:
        if not order.items.filter(kind=OrderItem.Kind.PRODUCT).exists():
            raise TransitionError(f"Заказ № {order.number}: в заказе нет товаров — его можно только отменить")
        recalc_foreign_items(order, timezone.localdate(now))
        order.confirmed_at = now
        order.payment_status = Order.PaymentStatus.AWAITING
    elif to_status == S.PAID:
        order.paid_at = now
        order.payment_status = Order.PaymentStatus.PAID
    order.status = to_status
    order.save()
    OrderStatusHistory.objects.create(
        order=order, from_status=from_status, to_status=to_status, actor=actor, actor_user=actor_user,
    )
    if to_status == S.CONFIRMED:
        # Правило для остального кода: у заказа в confirmed или paid счёт есть всегда
        issue_invoice(order, now)
    if order.conversation_id:
        _status_event(order, to_status)

    from .notifications import notify_status
    transaction.on_commit(lambda: notify_status(order.pk, to_status))
    return order


def change_status(order, to_status, actor=OrderStatusHistory.Actor.MANAGER, actor_user=None) -> Order:
    """Единственный путь смены статуса. Сбой PDF откатывает подтверждение целиком."""
    try:
        return _change_status(order.pk, to_status, actor, actor_user)
    except InvoiceError as error:
        logger.exception("Заказ %s: счёт не сформирован, заказ не подтверждён", order.number)
        from .telegram import send_telegram
        send_telegram(f"Не удалось сформировать счёт по заказу № {order.number}")
        raise InvoiceError(f"Счёт не сформирован, заказ не подтверждён: {error}") from error


# --- правка состава до подтверждения

class OrderEditError(Exception):
    """Правка не разрешена — текст для менеджера."""


def rub_text(value) -> str:
    text = f"{Decimal(value):,.2f}".replace(",", " ")
    return (text[:-3] if text.endswith(".00") else text.replace(".", ",")) + " ₽"


def _percent(before: Decimal, after: Decimal) -> str:
    if not before:
        return ""
    return str(((after - before) / before * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@transaction.atomic
def edit_order_items(order, changes: list[dict], actor_user=None) -> Order:
    """Правки менеджера в одной транзакции; каждая — запись в OrderChange, итог пересчитывается.

    changes: {"action": "quantity"|"price"|"remove", "item_id", "value"} и {"action": "delivery", "value": сумма|None}.
    """
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != S.PENDING_CONFIRMATION:
        raise OrderEditError("Состав заказа можно менять только до подтверждения")
    A = OrderChange.Action

    def log(action, details, total_before):
        order.total_amount = order_total(order)
        OrderChange.objects.create(order=order, actor_user=actor_user, action=action, details={
            **details, "total_before": str(total_before), "total_after": str(order.total_amount),
        })

    for change in changes:
        total_before = order_total(order)
        action = change["action"]
        if action == "delivery":
            delivery = order.items.filter(kind=OrderItem.Kind.DELIVERY).first()
            value = change.get("value")
            if value is None:
                if delivery:
                    before = delivery.line_total
                    delivery.delete()
                    log(A.DELIVERY_REMOVED, {"item": "Доставка", "before": str(before), "after": None,
                                             "diff": str(-before)}, total_before)
                continue
            value = Decimal(value).quantize(Decimal("0.01"))
            if value < 0:
                raise OrderEditError("Сумма доставки не может быть отрицательной")
            if delivery is None:
                OrderItem.objects.create(order=order, kind=OrderItem.Kind.DELIVERY, product_name="Доставка",
                                         quantity=1, unit_price=value, line_total=value)
                log(A.DELIVERY_ADDED, {"item": "Доставка", "before": None, "after": str(value), "diff": str(value)},
                    total_before)
            elif delivery.unit_price != value:
                before = delivery.unit_price
                delivery.unit_price = delivery.line_total = value
                delivery.save(update_fields=["unit_price", "line_total"])
                log(A.DELIVERY_CHANGED, {"item": "Доставка", "before": str(before), "after": str(value),
                                         "diff": str(value - before)}, total_before)
            continue

        item = order.items.filter(pk=change["item_id"], kind=OrderItem.Kind.PRODUCT).first()
        if item is None:
            raise OrderEditError("Позиция не найдена в заказе")
        if action == "remove":
            if order.items.filter(kind=OrderItem.Kind.PRODUCT).count() == 1:
                raise OrderEditError("Нельзя удалить последнюю товарную позицию — такой заказ отменяют")
            before = item.line_total
            item.delete()
            log(A.ITEM_REMOVED, {"item": item.product_name, "quantity": item.quantity, "before": str(before),
                                 "after": None, "diff": str(-before)}, total_before)
        elif action == "quantity":
            value = int(change["value"])
            if not 1 <= value <= MAX_ITEM_QUANTITY:
                raise OrderEditError(f"Количество — от 1 до {MAX_ITEM_QUANTITY}")
            if value == item.quantity:
                continue
            before = item.quantity
            item.quantity = value
            item.line_total = item.unit_price * value
            item.save(update_fields=["quantity", "line_total"])
            log(A.QUANTITY_CHANGED, {"item": item.product_name, "before": before, "after": value,
                                     "diff": value - before}, total_before)
        elif action == "price":
            value = Decimal(change["value"]).quantize(Decimal("0.01"))
            if value <= 0:
                raise OrderEditError("Цена за единицу должна быть больше нуля")
            if value == item.unit_price:
                continue
            before = item.unit_price
            item.unit_price = value
            item.line_total = value * item.quantity
            # Окончательная цена менеджера: при подтверждении по курсу не пересчитывается
            item.price_overridden = True
            item.save(update_fields=["unit_price", "line_total", "price_overridden"])
            log(A.PRICE_CHANGED, {"item": item.product_name, "before": str(before), "after": str(value),
                                  "diff": str(value - before), "percent": _percent(before, value)}, total_before)
        else:
            raise OrderEditError(f"Неизвестная правка: {action}")

    order.total_amount = order_total(order)
    order.save(update_fields=["total_amount", "updated_at"])
    return order


def describe_change(change: OrderChange) -> str:
    """Строка для истории правок в админке: что, было → стало, разница, итог."""
    d = change.details or {}
    total = f"итог {rub_text(d['total_before'])} → {rub_text(d['total_after'])}" if d.get("total_after") else ""
    A = OrderChange.Action
    if change.action == A.QUANTITY_CHANGED:
        what = f"{d['item']}: количество {d['before']} → {d['after']} ({int(d['diff']):+d})"
    elif change.action == A.PRICE_CHANGED:
        diff = Decimal(d["diff"])
        sign = "+" if diff > 0 else "−"
        percent = f", {sign}{str(abs(Decimal(d['percent']))).replace('.', ',')} %" if d.get("percent") else ""
        what = (f"{d['item']}: цена за единицу {rub_text(d['before'])} → {rub_text(d['after'])} "
                f"({sign}{rub_text(abs(diff))}{percent})")
    elif change.action == A.ITEM_REMOVED:
        what = f"{d['item']} × {d.get('quantity')}: удалена ({rub_text(d['before'])})"
    elif change.action == A.DELIVERY_ADDED:
        what = f"Доставка добавлена: {rub_text(d['after'])}"
    elif change.action == A.DELIVERY_CHANGED:
        what = f"Доставка: {rub_text(d['before'])} → {rub_text(d['after'])}"
    else:
        what = f"Доставка удалена ({rub_text(d['before'])})"
    return f"{what}; {total}" if total else what
