from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie

from apps.accounts.utils import get_owner_filter
from apps.catalog.models import Product
from apps.chat.services import agent_unavailable, current_conversation


def has_dialog(request) -> bool:
    """Есть ли у посетителя начатый диалог — тогда чат на главной подключается сразу и показывает его."""
    owner = get_owner_filter(request)  # гостевая сессия здесь не создаётся
    conversation = current_conversation(owner) if owner else None
    return conversation is not None and conversation.messages.exists()


def landing(request):
    """Главная: чат встроен в первый экран, плавающей кнопки нет (ТЗ §11, D1)."""
    unavailable = agent_unavailable()
    return render(request, "storefront/landing.html", {
        "agent_unavailable": unavailable,
        "chat_inline": True,
        "chat_has_dialog": settings.CHAT_WIDGET_ENABLED and not unavailable and has_dialog(request),
    })


def catalog(request):
    return render(request, "storefront/catalog.html")


def product(request, slug):
    if not Product.objects.filter(slug=slug, is_active=True).exists():
        raise Http404("Товар не найден")
    return render(request, "storefront/product.html", {"slug": slug})


def checkout(request):
    return render(request, "storefront/checkout.html")


def delivery(request):
    return render(request, "storefront/delivery.html")


def about(request):
    return render(request, "storefront/about.html")


@ensure_csrf_cookie
def privacy(request):
    return render(request, "storefront/privacy.html")


@ensure_csrf_cookie
def terms(request):
    return render(request, "storefront/terms.html")
