from .models import Payment, Order


def create_payment_stub(order, provider="tinkoff"):
    """Заглушка онлайн-оплаты: создаёт запись Payment в статусе pending,
    возвращает фейковую ссылку на оплату. Реальный провайдер подключается здесь."""
    payment = Payment.objects.create(
        order=order,
        provider=provider,
        amount=order.total_amount or 0,
        status=Payment.Status.PENDING,
        external_id=f"stub-{order.number}",
    )
    # реальный провайдер вернул бы URL платёжной страницы
    fake_url = f"/payment/mock/{payment.id}/"
    return {"payment_id": payment.id, "pay_url": fake_url}


def mark_payment_succeeded(payment_id):
    """Заглушка коллбэка успешной оплаты."""
    payment = Payment.objects.filter(id=payment_id).first()
    if not payment:
        return False
    payment.status = Payment.Status.SUCCEEDED
    payment.save()
    order = payment.order
    order.payment_status = Order.PaymentStatus.PAID
    order.status = Order.Status.PAID
    order.save()
    return True
