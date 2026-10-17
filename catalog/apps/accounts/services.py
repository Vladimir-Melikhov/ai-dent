from django.db import transaction

from apps.chat.models import Quote
from apps.chat.services import merge_guest_conversations
from apps.orders.models import MAX_ITEM_QUANTITY, CartItem
from .models import ShortlistItem


def merge_guest_data(request, user):
    session = getattr(request, "anon_session", None)
    if session is None:
        return

    with transaction.atomic():
        guest_cart = CartItem.objects.filter(session=session, user__isnull=True)
        for item in guest_cart:
            existing = CartItem.objects.filter(user=user, product=item.product).first()
            if existing:
                existing.quantity = min(existing.quantity + item.quantity, MAX_ITEM_QUANTITY)
                existing.save()
                item.delete()
            else:
                item.user = user
                item.session = None
                item.save()

        guest_fav = ShortlistItem.objects.filter(session=session, user__isnull=True)
        for item in guest_fav:
            if ShortlistItem.objects.filter(user=user, product=item.product).exists():
                item.delete()
            else:
                item.user = user
                item.session = None
                item.save()

        merge_guest_conversations(session, user)
        Quote.objects.filter(session=session, user__isnull=True).update(user=user, session=None)

        session.merged_into = user
        session.save()
