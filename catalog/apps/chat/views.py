import os

from django.conf import settings
from django.utils import timezone
from django.http import FileResponse, Http404, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.utils import get_owner_filter, parse_json_body
from . import services
from .models import Quote
from .quote_pdf import generate_quote_pdf


@require_POST
def ticket(request):
    """Тикет для WebSocket чата. {"new": true} — начать новый диалог."""
    data = parse_json_body(request) if request.body else {}
    if data is None:
        return JsonResponse({"error": "Некорректный запрос"}, status=400)
    # Гостевая сессия создаётся при первом открытии чата, а не при загрузке страницы
    owner = get_owner_filter(request, create=True)
    conversation = services.get_or_create_current(owner, new=data.get("new") is True)
    return JsonResponse({
        "ticket": services.issue_ticket(conversation),
        "conversation_id": conversation.pk,
    })


def _is_quote_owner(request, quote) -> bool:
    if request.user.is_authenticated and quote.user_id == request.user.id:
        return True
    session = getattr(request, "anon_session", None)
    return session is not None and quote.session_id == session.pk


@require_GET
def quotes_list(request):
    """История КП клиента — для вкладки «КП» в профиле: и подготовленные в чате, и выставленные менеджером."""
    if not request.user.is_authenticated:
        return JsonResponse({"items": []})
    today = timezone.localdate()
    quotes = Quote.objects.filter(user=request.user).order_by("-created_at")
    items = []
    for q in quotes:
        status = "Срок истёк" if q.valid_until < today else f"Действует до {q.valid_until:%d.%m.%Y}"
        items.append({
            "number": q.number, "created_at": q.created_at.astimezone().strftime("%d.%m.%Y"),
            "status": status, "total": float(q.total), "items_count": len(q.items),
            "url": f"/chat/quote/{q.id}/",
        })
    return JsonResponse({"items": items})


@require_GET
def quote_pdf(request, quote_id):
    """PDF коммерческого предложения: только владельцу; создаётся при первом обращении."""
    quote = Quote.objects.filter(pk=quote_id).first()
    if quote is None or not _is_quote_owner(request, quote):
        raise Http404("КП не найдено")
    path = os.path.join(settings.MEDIA_ROOT, quote.pdf_path) if quote.pdf_path else ""
    if not path or not os.path.exists(path):
        quote.pdf_path = generate_quote_pdf(quote)
        Quote.objects.filter(pk=quote.pk).update(pdf_path=quote.pdf_path)
        path = os.path.join(settings.MEDIA_ROOT, quote.pdf_path)
    return FileResponse(open(path, "rb"), content_type="application/pdf", as_attachment=True,
                        filename=f"{quote.number}.pdf")
