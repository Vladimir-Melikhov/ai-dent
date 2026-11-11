"""Цифры панели. Продажи сайта берутся из оплаченных заказов, всё остальное — из операций панели."""
import datetime
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.catalog.currency import rate_on
from apps.chat.models import Conversation, LLMUsage, Message, Quote
from apps.leads.models import Lead
from apps.orders.models import Order

from .models import Category, Deal, Entry

ZERO = Decimal("0")
PERIODS = {"7": ("Неделя", 7), "30": ("Месяц", 30), "90": ("Квартал", 90), "365": ("Год", 365)}
DEFAULT_PERIOD = "30"
DEALS_CATEGORY = "Сделки"


@dataclass(frozen=True)
class Period:
    code: str
    label: str
    start: datetime.date
    end: datetime.date

    @property
    def days(self):
        return (self.end - self.start).days + 1

    @property
    def previous(self):
        end = self.start - datetime.timedelta(days=1)
        return Period(self.code, self.label, end - datetime.timedelta(days=self.days - 1), end)


def resolve_period(code) -> Period:
    code = code if code in PERIODS else DEFAULT_PERIOD
    label, days = PERIODS[code]
    end = timezone.localdate()
    return Period(code, label, end - datetime.timedelta(days=days - 1), end)


# --- выручка

def paid_orders(start, end):
    """Оплаченные заказы за даты start..end; день — по моменту оплаты."""
    return (
        Order.objects.filter(status=Order.Status.PAID)
        .annotate(day=TruncDate(Coalesce("paid_at", "confirmed_at", "created_at")))
        .filter(day__gte=start, day__lte=end)
    )


def shop_by_day(start, end):
    rows = paid_orders(start, end).values("day").annotate(total=Sum("total_amount"), n=Count("id"))
    return {row["day"]: (row["total"] or ZERO, row["n"]) for row in rows}


def entries_by_day(start, end, kind):
    rows = Entry.objects.filter(kind=kind, date__gte=start, date__lte=end).values("date").annotate(total=Sum("amount"))
    return {row["date"]: row["total"] for row in rows}


def totals(start, end) -> dict:
    shop = shop_by_day(start, end)
    shop_revenue = sum((v[0] for v in shop.values()), ZERO)
    orders = sum(v[1] for v in shop.values())
    other_income = sum(entries_by_day(start, end, Category.Kind.INCOME).values(), ZERO)
    expenses = sum(entries_by_day(start, end, Category.Kind.EXPENSE).values(), ZERO)
    revenue = shop_revenue + other_income
    profit = revenue - expenses
    return {
        "shop_revenue": shop_revenue,
        "other_income": other_income,
        "revenue": revenue,
        "expenses": expenses,
        "profit": profit,
        "margin": (profit / revenue * 100) if revenue else None,
        "orders": orders,
        "avg_check": (shop_revenue / orders) if orders else None,
    }


def delta(current, previous):
    """Изменение в процентах к прошлому периоду; None — сравнивать не с чем."""
    if not previous:
        return None
    return float((current - previous) / abs(previous) * 100)


# --- ряды для графиков

def bucket_start(day, step):
    if step == "week":
        return day - datetime.timedelta(days=day.weekday())
    if step == "month":
        return day.replace(day=1)
    return day


def bucket_step(days):
    return "day" if days <= 31 else "week" if days <= 120 else "month"


def series(period: Period) -> list[dict]:
    """Доходы, расходы и прибыль по дням, неделям или месяцам — смотря на длину периода."""
    step = bucket_step(period.days)
    shop = shop_by_day(period.start, period.end)
    other = entries_by_day(period.start, period.end, Category.Kind.INCOME)
    spent = entries_by_day(period.start, period.end, Category.Kind.EXPENSE)
    buckets = defaultdict(lambda: {"income": ZERO, "expenses": ZERO})
    day = period.start
    while day <= period.end:
        key = bucket_start(day, step)
        buckets[key]["income"] += shop.get(day, (ZERO, 0))[0] + other.get(day, ZERO)
        buckets[key]["expenses"] += spent.get(day, ZERO)
        day += datetime.timedelta(days=1)
    result = []
    for key in sorted(buckets):
        income, expenses = buckets[key]["income"], buckets[key]["expenses"]
        result.append({
            "date": key.isoformat(),
            "step": step,
            "income": float(income),
            "expenses": float(expenses),
            "profit": float(income - expenses),
        })
    return result


def expenses_by_category(start, end, limit=7) -> list[dict]:
    rows = list(
        Entry.objects.filter(kind=Category.Kind.EXPENSE, date__gte=start, date__lte=end)
        .values("category__name", "category__color")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    all_total = sum((r["total"] for r in rows), ZERO)
    result = [
        {
            "name": r["category__name"],
            "color": r["category__color"],
            "amount": float(r["total"]),
            "share": float(r["total"] / all_total * 100) if all_total else 0,
        }
        for r in rows[:limit]
    ]
    rest = sum((r["total"] for r in rows[limit:]), ZERO)
    if rest:
        result.append({"name": "Остальные", "color": 0, "amount": float(rest), "share": float(rest / all_total * 100)})
    return result


def pipeline() -> list[dict]:
    """Воронка: открытые сделки по этапам (на сейчас)."""
    rows = {
        r["stage"]: r
        for r in Deal.objects.filter(stage__in=Deal.OPEN_STAGES, archived_at__isnull=True).values("stage").annotate(n=Count("id"), total=Sum("amount"))
    }
    return [
        {
            "stage": stage.value,
            "label": stage.label,
            "count": rows.get(stage.value, {}).get("n", 0),
            "amount": float(rows.get(stage.value, {}).get("total") or ZERO),
        }
        for stage in Deal.OPEN_STAGES
    ]


def deals_summary(start, end) -> dict:
    live = Deal.objects.filter(archived_at__isnull=True)
    open_qs = live.filter(stage__in=Deal.OPEN_STAGES)
    won = live.filter(stage=Deal.Stage.WON, closed_at__date__gte=start, closed_at__date__lte=end)
    lost = live.filter(stage=Deal.Stage.LOST, closed_at__date__gte=start, closed_at__date__lte=end)
    won_agg = won.aggregate(n=Count("id"), total=Sum("amount"))
    lost_n = lost.count()
    closed = (won_agg["n"] or 0) + lost_n
    return {
        "open_count": open_qs.count(),
        "open_amount": open_qs.aggregate(t=Sum("amount"))["t"] or ZERO,
        "won_count": won_agg["n"] or 0,
        "won_amount": won_agg["total"] or ZERO,
        "conversion": ((won_agg["n"] or 0) / closed * 100) if closed else None,
    }


def ai_spend_usd(start, end) -> Decimal:
    since = timezone.make_aware(datetime.datetime.combine(start, datetime.time.min))
    until = timezone.make_aware(datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time.min))
    return LLMUsage.objects.filter(created_at__gte=since, created_at__lt=until).aggregate(t=Sum("cost_usd"))["t"] or ZERO


def recent_activity(limit=8) -> list[dict]:
    """Последние поступления и траты в одной ленте."""
    items = []
    for order in Order.objects.filter(status=Order.Status.PAID).order_by("-paid_at", "-created_at")[:limit]:
        when = order.paid_at or order.confirmed_at or order.created_at
        items.append({
            "when": timezone.localtime(when), "sign": "+", "title": f"Заказ №{order.number}",
            "hint": "Продажа с сайта", "amount": order.total_amount or ZERO,
        })
    for entry in Entry.objects.select_related("category").order_by("-date", "-id")[:limit]:
        items.append({
            "when": timezone.make_aware(datetime.datetime.combine(entry.date, datetime.time(12))),
            "sign": "+" if entry.kind == Category.Kind.INCOME else "−",
            "title": entry.title or entry.category.name, "hint": entry.category.name, "amount": entry.amount,
        })
    items.sort(key=lambda item: item["when"], reverse=True)
    return items[:limit]


# --- сделки ↔ доходы

def sync_deal(deal: Deal) -> None:
    """Выигранная сделка = доход на её сумму; вернули в работу или проиграли — доход убирается."""
    now = timezone.now()
    if deal.stage in Deal.OPEN_STAGES:
        if deal.closed_at is not None:
            Deal.objects.filter(pk=deal.pk).update(closed_at=None)
            deal.closed_at = None
    elif deal.closed_at is None:
        Deal.objects.filter(pk=deal.pk).update(closed_at=now)
        deal.closed_at = now

    entry = Entry.objects.filter(deal=deal).first()
    # Системные карточки денег не создают: оплаченный заказ уже в выручке
    if deal.stage != Deal.Stage.WON or deal.amount <= 0 or deal.is_system:
        if entry:
            entry.delete()
        return
    if entry:
        entry.amount, entry.title = deal.amount, deal.title
        entry.save(update_fields=["amount", "title"])
        return
    category, _ = Category.objects.get_or_create(
        kind=Category.Kind.INCOME, name=DEALS_CATEGORY, defaults={"color": 3}
    )
    Entry.objects.create(
        kind=Category.Kind.INCOME, category=category, amount=deal.amount, title=deal.title,
        date=timezone.localtime(deal.closed_at).date(), deal=deal,
    )


# --- карточки из заказов сайта и КП чата

AUTO_WINDOW_DAYS = 30
ORDER_STAGE = {
    Order.Status.PENDING_CONFIRMATION: Deal.Stage.PAYMENT,
    Order.Status.CONFIRMED: Deal.Stage.PAYMENT,
    Order.Status.PAID: Deal.Stage.WON,
    Order.Status.CANCELLED: Deal.Stage.LOST,
}


def order_summary(order) -> str:
    lines = [f"{i.product_name} × {i.quantity} — {i.line_total} ₽" for i in order.items.all()]
    place = ", ".join(x for x in (order.delivery_city, order.delivery_address) if x)
    return "\n".join(lines + ([f"Доставка: {place}"] if place else []) + ([f"Email: {order.contact_email}"] if order.contact_email else []))


def quote_summary(quote) -> str:
    return "\n".join(f"{i['name']} × {i['quantity']} — {i['line_total_rub']} ₽" for i in quote.items)


def quote_contact(quote):
    """Имя и телефон клиента КП: из заявки агента по диалогу, иначе из аккаунта. Аноним — пусто."""
    lead = Lead.objects.filter(conversation_id=quote.conversation_id).order_by("-id").first() if quote.conversation_id else None
    if lead:
        return lead.name, lead.phone
    if quote.user_id:
        return quote.user.first_name, quote.user.phone
    return "", ""


def sync_auto_deals() -> None:
    """Заводит и двигает карточки по заказам сайта и КП чата. Идемпотентна: вызывается при открытии воронки.

    Берутся заказы и КП за последние 30 дней и всё, у чего карточка уже есть."""
    since = timezone.now() - datetime.timedelta(days=AUTO_WINDOW_DAYS)
    orders = (
        Order.objects.exclude(status=Order.Status.DRAFT)
        .filter(Q(created_at__gte=since) | Q(panel_deal__isnull=False))
        .select_related("panel_deal").prefetch_related("items")
    )
    for order in orders:
        _sync_order(order)
    quotes = Quote.objects.filter(Q(created_at__gte=since) | Q(panel_deal__isnull=False)).select_related("user")
    for quote in quotes:
        _sync_quote(quote)
    _drop_orphans()
    _archive_superseded()
    _archive_expired_quotes()


STALE_DAYS = 7


def phone_key(phone) -> str:
    """Последние 10 цифр: +7 900…, 8 900… и 900… — один и тот же клиент."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else ""


def _drop_orphans():
    """Заказ или КП удалили — карточка уходит вместе с ним; остался только КП — карточка снова «КП отправлено»."""
    for deal in Deal.objects.exclude(origin=Deal.Origin.MANUAL).filter(order__isnull=True):
        if deal.quote_id:
            if deal.origin != Deal.Origin.QUOTE or deal.stage != Deal.Stage.OFFER:
                deal.origin, deal.stage, deal.closed_at = Deal.Origin.QUOTE, Deal.Stage.OFFER, None
                deal.title, deal.amount = f"КП №{deal.quote.number}", deal.quote.total
                deal.save()
        else:
            deal.delete()
    Deal.objects.filter(origin=Deal.Origin.QUOTE, quote__isnull=True, order__isnull=True).delete()
    # карточки КП без телефона (заведены раньше) — в список «КП без контактов»
    for deal in Deal.objects.filter(origin=Deal.Origin.QUOTE, order__isnull=True):
        if not phone_key(deal.contact):
            deal.delete()


def _archive_expired_quotes():
    """КП истекло, заказа нет — карточка мертва: в архив."""
    Deal.objects.filter(
        origin=Deal.Origin.QUOTE, order__isnull=True, stage=Deal.Stage.OFFER, archived_at__isnull=True,
        expected_date__lt=timezone.localdate(),
    ).update(archived_at=timezone.now())


def anonymous_quotes():
    """КП последних 30 дней, по которым не с кем связаться и нет заказа."""
    since = timezone.now() - datetime.timedelta(days=AUTO_WINDOW_DAYS)
    taken = set(Deal.objects.exclude(conversation__isnull=True).values_list("conversation_id", flat=True))
    result = []
    for quote in Quote.objects.filter(created_at__gte=since, panel_deal__isnull=True).select_related("user"):
        if quote.conversation_id in taken or phone_key(quote_contact(quote)[1]):
            continue
        result.append(quote)
    return result


def _archive_superseded():
    """Клиент вернулся с новым заказом, а старый неоплаченный висит больше недели — старый уходит в архив."""
    now = timezone.now()
    groups = defaultdict(list)
    for deal in Deal.objects.filter(
        archived_at__isnull=True, stage__in=(Deal.Stage.PAYMENT, Deal.Stage.OFFER)
    ).exclude(origin=Deal.Origin.MANUAL):
        key = phone_key(deal.contact)
        if key:
            groups[key].append(deal)
    for deals in groups.values():
        if len(deals) < 2:
            continue
        newest = max(deals, key=lambda d: d.created_at)
        for deal in deals:
            if deal is not newest and now - deal.created_at >= datetime.timedelta(days=STALE_DAYS):
                Deal.objects.filter(pk=deal.pk).update(archived_at=now)


def _sync_order(order):
    stage = ORDER_STAGE.get(order.status)
    if stage is None:
        return
    deal = getattr(order, "panel_deal", None)
    if deal is None and order.conversation_id:
        # Клиент сначала просил КП в чате, потом оформил заказ — та же карточка, а не вторая
        deal = Deal.objects.filter(conversation_id=order.conversation_id, order__isnull=True).order_by("id").first()
    if deal is None and phone_key(order.contact_phone):
        # КП в одном чате, заказ из другого или без чата: тот же клиент по телефону
        key = phone_key(order.contact_phone)
        for candidate in Deal.objects.filter(origin=Deal.Origin.QUOTE, order__isnull=True).order_by("id"):
            if phone_key(candidate.contact) == key:
                deal = candidate
                break
    fields = {
        "origin": Deal.Origin.ORDER, "order": order,
        "conversation_id": order.conversation_id or (deal.conversation_id if deal else None),
        "title": f"Заказ №{order.number}", "amount": order.total_amount or ZERO, "stage": stage,
        "client": order.company_name or order.buyer_name or order.contact_name or "",
        "contact": order.contact_phone or "", "source": "Сайт",
    }
    if deal is None:
        try:
            with transaction.atomic():
                deal = Deal.objects.create(note=order_summary(order), **fields)
                # возраст карточки — возраст заказа, а не момент, когда мы её завели
                Deal.objects.filter(pk=deal.pk).update(created_at=order.created_at)
        except IntegrityError:
            return
    else:
        changed = {k: v for k, v in fields.items() if getattr(deal, k) != v}
        if not deal.note.strip():
            changed["note"] = order_summary(order)
        if deal.archived_at and stage == Deal.Stage.WON:
            changed["archived_at"] = None  # деньги пришли — карточка возвращается из архива
        if changed:
            for key, value in changed.items():
                setattr(deal, key, value)
            deal.save()
    sync_deal(deal)


def _sync_quote(quote):
    if Deal.objects.filter(quote=quote).exists():
        return
    # По диалогу уже есть карточка (заказ или другое КП) — новая не нужна, только привязка
    existing = Deal.objects.filter(conversation_id=quote.conversation_id).order_by("id").first() if quote.conversation_id else None
    if existing is not None:
        if existing.quote_id is None:
            existing.quote = quote
            existing.save(update_fields=["quote", "updated_at"])
        return
    name, phone = quote_contact(quote)
    if not phone_key(phone):
        return  # без телефона связаться нечем: карточку не заводим, КП видно в списке «КП без контактов»
    try:
        with transaction.atomic():
            deal = Deal.objects.create(
                origin=Deal.Origin.QUOTE, quote=quote, conversation_id=quote.conversation_id,
                title=f"КП №{quote.number}", client=name or "Клиент из чата", contact=phone, source="Чат",
                amount=quote.total, stage=Deal.Stage.OFFER, expected_date=quote.valid_until, note=quote_summary(quote),
            )
            Deal.objects.filter(pk=deal.pk).update(created_at=quote.created_at)
    except IntegrityError:
        return
    sync_deal(deal)


# --- ИИ-консультант: расходы и отдача

def _day_bounds(start, end):
    since = timezone.make_aware(datetime.datetime.combine(start, datetime.time.min))
    until = timezone.make_aware(datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time.min))
    return since, until


def ai_stats(period: Period) -> dict:
    since, until = _day_bounds(period.start, period.end)
    usage = LLMUsage.objects.filter(created_at__gte=since, created_at__lt=until)
    agg = usage.aggregate(
        cost=Sum("cost_usd"), requests=Count("id"), tokens_in=Sum("input_tokens"), tokens_out=Sum("output_tokens"),
        cache_read=Sum("cache_read_tokens"), cache_w5=Sum("cache_write_5m_tokens"), cache_w1=Sum("cache_write_1h_tokens"),
        latency=Avg("latency_ms"), fallback=Count("id", filter=Q(attempt_type=LLMUsage.AttemptType.FALLBACK)),
    )
    cost = agg["cost"] or ZERO
    rate = rate_on("USD")
    cost_rub = cost * rate if rate else None

    conversations = Conversation.objects.filter(created_at__gte=since, created_at__lt=until).count()
    client_messages = Message.objects.filter(
        role=Message.Role.USER, created_at__gte=since, created_at__lt=until
    ).count()
    leads = Lead.objects.filter(created_at__gte=since, created_at__lt=until).count()
    quotes = Quote.objects.filter(created_at__gte=since, created_at__lt=until).count()
    chat_orders = Order.objects.filter(created_by=Order.CreatedBy.AI, created_at__gte=since, created_at__lt=until).exclude(
        status=Order.Status.DRAFT
    )
    chat_orders_n = chat_orders.count()
    chat_paid = chat_orders.filter(status=Order.Status.PAID).aggregate(t=Sum("total_amount"))["t"] or ZERO

    reads = agg["cache_read"] or 0
    total_in = (agg["tokens_in"] or 0) + reads + (agg["cache_w5"] or 0) + (agg["cache_w1"] or 0)

    def per(count):
        return (cost / count) if count and cost else None

    # расход по дням, неделям или месяцам — как на обзоре
    step = bucket_step(period.days)
    daily = defaultdict(lambda: {"cost": ZERO, "requests": 0})
    for row in usage.annotate(day=TruncDate("created_at")).values("day").annotate(c=Sum("cost_usd"), n=Count("id")):
        bucket = daily[bucket_start(row["day"], step)]
        bucket["cost"] += row["c"] or ZERO
        bucket["requests"] += row["n"]
    series_rows = [
        {"date": key.isoformat(), "step": step, "cost": float(v["cost"]), "requests": v["requests"]}
        for key, v in sorted(daily.items())
    ]
    models = [
        {"model": row["model"], "requests": row["n"], "cost": row["c"] or ZERO}
        for row in usage.values("model").annotate(n=Count("id"), c=Sum("cost_usd")).order_by("-c")
    ]
    return {
        "cost": cost, "cost_rub": cost_rub, "rate": rate, "requests": agg["requests"],
        "tokens_in": total_in, "tokens_out": agg["tokens_out"] or 0,
        "cache_share": (reads / total_in * 100) if total_in else None,
        "fallback_share": (agg["fallback"] / agg["requests"] * 100) if agg["requests"] else None,
        "latency": agg["latency"],
        "conversations": conversations, "client_messages": client_messages, "leads": leads, "quotes": quotes,
        "chat_orders": chat_orders_n, "chat_paid": chat_paid,
        "per_conversation": per(conversations), "per_lead": per(leads), "per_order": per(chat_orders_n),
        "payback": (chat_paid / cost_rub) if cost_rub else None,
        "series": series_rows, "models": models,
    }
