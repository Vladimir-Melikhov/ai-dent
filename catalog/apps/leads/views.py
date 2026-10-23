import uuid

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.utils import get_client_ip, get_owner_filter, normalize_phone, parse_json_body
from apps.chat.models import Conversation, Message
from apps.chat.services import add_event, current_conversation, redis_client
from .models import Lead
from .notifications import notify_lead

IP_LIMIT = 5      # заявок в час с одного IP
OWNER_LIMIT = 3   # заявок в час от одной сессии или аккаунта


def _too_many(key: str, limit: int) -> bool:
    count = redis_client.incr(key)
    if count == 1:
        redis_client.expire(key, 3600)
    return count > limit


@require_POST
def create_lead(request):
    """Заявка менеджеру из чата: данные идут в Django и в модель не попадают."""
    data = parse_json_body(request)
    if data is None:
        return JsonResponse({"error": "Некорректный запрос"}, status=400)
    phone = normalize_phone(data.get("phone"))
    if not phone:
        return JsonResponse({"error": "Введите телефон в формате +7 999 123 45 67"}, status=400)
    if data.get("consent") is not True:
        return JsonResponse({"error": "Подтвердите согласие на обработку персональных данных"}, status=400)
    name, city = (str(data.get(f) or "").strip()[:255] for f in ("name", "city"))
    source = Lead.Source.AI if data.get("source") == Lead.Source.AI else Lead.Source.FALLBACK

    owner = get_owner_filter(request, create=True)
    owner_key = f"u{owner['user'].pk}" if "user" in owner else f"s{owner['session'].pk}"
    if _too_many(f"chat:lead:ip:{get_client_ip(request)}", IP_LIMIT) or \
            _too_many(f"chat:lead:owner:{owner_key}", OWNER_LIMIT):
        return JsonResponse({"error": "Заявка уже отправлена — менеджер скоро свяжется с вами"}, status=429)

    conversation = current_conversation(owner)
    with transaction.atomic():
        lead = Lead.objects.create(
            # Временный уникальный номер: два одновременных пустых номера нарушили бы уникальность
            number=f"tmp-{uuid.uuid4().hex}", phone=phone, name=name, city=city, source=source, conversation=conversation,
            topic=conversation.pending_lead_topic if conversation else "", pd_consent_at=timezone.now(),
            user=owner.get("user"), session=owner.get("session"),
        )
        lead.number = f"L-{timezone.now():%Y%m%d}-{lead.pk:04d}"
        lead.save(update_fields=["number"])
        if conversation:
            # Пометка использована — следующая заявка без агента её не унаследует
            Conversation.objects.filter(pk=conversation.pk).update(pending_lead_topic="")
            add_event(conversation, Message.EventType.LEAD_CREATED, {"number": lead.number, "source": source},
                      content=f"Заявка № {lead.number}")
        transaction.on_commit(lambda: notify_lead(lead))
    return JsonResponse({"ok": True, "number": lead.number})
