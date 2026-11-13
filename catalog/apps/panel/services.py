"""Цифры панели. Продажи сайта берутся из оплаченных заказов, всё остальное — из операций панели."""
import datetime
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.catalog.currency import rate_on
from apps.catalog.models import Product
from apps.chat.models import Conversation, LLMUsage, Message, Quote
from apps.leads.models import Lead
from apps.orders.models import MAX_ITEM_QUANTITY, Order, OrderItem, OrderStatusHistory
from apps.orders.services import InvoiceError, TransitionError, change_status, valid_inn

from .models import Category, Deal, DealQuote, Entry, Server

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


def order_cost(order) -> Decimal:
    """Закупка по заказу — то, что владелец указал при оплате. Не указал — 0, прибыль равна выручке."""
    deal = getattr(order, "panel_deal", None)
    return deal.cost if deal is not None and deal.cost is not None else ZERO


def cogs_by_day(start, end) -> dict:
    """Закупка проданного по дням: оплаченные заказы и выигранные ручные сделки."""
    days = defaultdict(lambda: ZERO)
    for order in paid_orders(start, end).select_related("panel_deal"):
        days[order.day] += order_cost(order)
    won = Deal.objects.filter(
        origin=Deal.Origin.MANUAL, stage=Deal.Stage.WON, cost__isnull=False,
        closed_at__date__gte=start, closed_at__date__lte=end,
    )
    for deal in won:
        days[timezone.localtime(deal.closed_at).date()] += deal.cost
    return days


def totals(start, end) -> dict:
    shop = shop_by_day(start, end)
    shop_revenue = sum((v[0] for v in shop.values()), ZERO)
    orders = sum(v[1] for v in shop.values())
    other_income = sum(entries_by_day(start, end, Category.Kind.INCOME).values(), ZERO)
    expenses = sum(entries_by_day(start, end, Category.Kind.EXPENSE).values(), ZERO)
    revenue = shop_revenue + other_income
    cogs = sum(cogs_by_day(start, end).values(), ZERO)
    costs = expenses + cogs
    profit = revenue - costs
    return {
        "cogs": cogs,
        "costs": costs,
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
    cogs = cogs_by_day(period.start, period.end)
    buckets = defaultdict(lambda: {"income": ZERO, "expenses": ZERO})
    day = period.start
    while day <= period.end:
        key = bucket_start(day, step)
        buckets[key]["income"] += shop.get(day, (ZERO, 0))[0] + other.get(day, ZERO)
        buckets[key]["expenses"] += spent.get(day, ZERO) + cogs.get(day, ZERO)
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
    cogs = sum(cogs_by_day(start, end).values(), ZERO)
    all_total = sum((r["total"] for r in rows), ZERO) + cogs
    result = ([{
        "name": "Закупка проданного", "color": 1, "amount": float(cogs), "share": float(cogs / all_total * 100),
    }] if cogs else []) + [
        {
            "name": r["category__name"],
            "color": r["category__color"],
            "amount": float(r["total"]),
            "share": float(r["total"] / all_total * 100) if all_total else 0,
        }
        for r in rows[:limit]
    ]
    result.sort(key=lambda r: -r["amount"])
    rest = sum((r["total"] for r in rows[limit:]), ZERO)
    if rest:
        result.append({"name": "Остальные", "color": 0, "amount": float(rest), "share": float(rest / all_total * 100)})
    return result


PURCHASE_CATEGORY = "Закупка вне заказов"
SOLD_GOODS = "Закупка проданного"


def expense_structure(period: Period) -> dict:
    """Куда ушли деньги: закупка товара отдельно от операционки и ресурсов, по категориям и по времени.

    Закупка — себестоимость проданного (указывается при оплате) и категория «Закупка вне заказов».
    Операционка и ресурсы — все остальные категории расходов."""
    step = bucket_step(period.days)
    cogs = cogs_by_day(period.start, period.end)
    entries = list(
        Entry.objects.filter(kind=Category.Kind.EXPENSE, date__gte=period.start, date__lte=period.end)
        .select_related("category")
    )
    # (группа, название, цвет) → сумма всего и по корзинам времени
    totals = defaultdict(lambda: ZERO)
    buckets = defaultdict(lambda: defaultdict(lambda: ZERO))
    colors = {}

    def add(group, name, color, day, amount):
        key = (group, name)
        totals[key] += amount
        buckets[bucket_start(day, step)][key] += amount
        colors[key] = color

    for day, amount in cogs.items():
        if amount:
            add("purchase", SOLD_GOODS, 1, day, amount)
    for entry in entries:
        group = "purchase" if entry.category.name == PURCHASE_CATEGORY else "operating"
        add(group, entry.category.name, entry.category.color, entry.date, entry.amount)

    grand = sum(totals.values(), ZERO)

    def group_block(key, title, hint):
        items = sorted(((k, v) for k, v in totals.items() if k[0] == key), key=lambda kv: -kv[1])
        total = sum((v for _, v in items), ZERO)
        return {
            "key": key, "title": title, "hint": hint, "total": float(total),
            "share": float(total / grand * 100) if grand else 0,
            "items": [{
                "name": k[1], "color": colors[k], "amount": float(v),
                "share": float(v / total * 100) if total else 0,
            } for k, v in items],
        }

    groups = [
        group_block("purchase", "Закупка товара", "себестоимость проданного и закупка вне заказов"),
        group_block("operating", "Операционка и ресурсы", "реклама, серверы, ИИ, доставка, налоги, зарплата и прочее"),
    ]
    # серии для столбцов: сначала закупка, затем операционка, внутри — от крупных к мелким
    keys = [(k, v) for g in ("purchase", "operating") for k, v in sorted(
        ((k, v) for k, v in totals.items() if k[0] == g), key=lambda kv: -kv[1])]
    series_meta = [{"group": k[0], "name": k[1], "color": colors[k], "amount": float(v)} for k, v in keys]
    rows, day = [], period.start
    seen = set()
    while day <= period.end:
        start = bucket_start(day, step)
        if start not in seen:
            seen.add(start)
            values = buckets.get(start, {})
            rows.append({
                "date": start.isoformat(), "step": step,
                "values": [float(values.get(k, ZERO)) for k, _ in keys],
            })
        day += datetime.timedelta(days=1)
    return {
        "total": float(grand), "groups": groups, "series": series_meta, "rows": rows,
        "servers_monthly": float(Server.objects.aggregate(t=Sum("monthly_cost"))["t"] or ZERO),
    }


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
    if quote.client_phone:
        return quote.client_name or "", quote.client_phone
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
    quotes = Quote.objects.filter(Q(created_at__gte=since) | Q(panel_deal__isnull=False)).select_related("user").order_by("created_at")
    for quote in quotes:
        _sync_quote(quote)
    _drop_orphans()
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
                deal.archived_at = None  # заказ удалён — КП снова живо; истёкшее вернёт в архив _archive_expired_quotes
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
    for quote in Quote.objects.filter(created_at__gte=since, panel_deal__isnull=True, superseded_in__isnull=True).select_related("user"):
        # КП, собранные в панели, владелец отправляет сам — это не потерянные контакты из чата
        if is_panel_quote(quote) or quote.conversation_id in taken or phone_key(quote_contact(quote)[1]):
            continue
        result.append(quote)
    return result


def ensure_order_deal(order) -> Deal:
    """Карточка заказа: нужна, чтобы записать закупку, даже если заказ старше окна синхронизации."""
    _sync_order(Order.objects.select_related("panel_deal").prefetch_related("items").get(pk=order.pk))
    return Deal.objects.get(order_id=order.pk)


def order_from_panel(order) -> bool:
    """Счёт выставлен владельцем в панели (не клиентом на сайте)."""
    return order.status_history.filter(
        from_status=Order.Status.DRAFT, actor=OrderStatusHistory.Actor.DIRECTOR
    ).exists()


def _note_is_auto(deal) -> bool:
    """Заметка пуста или это авто-текст по КП сделки (не правка владельца) — её можно заменить составом заказа."""
    note = deal.note.strip()
    if not note:
        return True
    quotes = [deal.quote] if deal.quote_id else []
    quotes += [d.quote for d in deal.old_quotes.select_related("quote")]
    return any(note == quote_summary(q).strip() for q in quotes)


def _sync_order(order):
    stage = ORDER_STAGE.get(order.status)
    if stage is None:
        return
    deal = getattr(order, "panel_deal", None)
    if deal is None and order.conversation_id:
        # Клиент сначала просил КП в чате, потом оформил заказ — та же карточка, а не вторая
        deal = Deal.objects.filter(conversation_id=order.conversation_id, order__isnull=True).order_by("id").first()
    if deal is None:
        # КП в одном чате, заказ из другого или без чата: тот же клиент по телефону
        deal = find_open_deal(order.contact_phone, None, without_order=True)
    fields = {
        "origin": Deal.Origin.ORDER, "order": order,
        "conversation_id": order.conversation_id or (deal.conversation_id if deal else None),
        "title": f"Заказ №{order.number}", "amount": order.total_amount or ZERO, "stage": stage,
        "client": order.company_name or order.buyer_name or order.contact_name or "",
        "contact": order.contact_phone or "",
        "source": Deal.PANEL_SOURCE if order_from_panel(order) else "Сайт",
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
        if _note_is_auto(deal):
            summary = order_summary(order)
            if summary != deal.note:
                changed["note"] = summary
        if deal.archived_at and stage == Deal.Stage.WON:
            changed["archived_at"] = None  # деньги пришли — карточка возвращается из архива
        if changed:
            for key, value in changed.items():
                setattr(deal, key, value)
            deal.save()
    sync_deal(deal)


def find_open_deal(phone, conversation_id, without_order=False):
    """Открытая сделка этого клиента: по диалогу, иначе по телефону. Закрытые и архивные не считаются."""
    live = Deal.objects.filter(stage__in=Deal.OPEN_STAGES, archived_at__isnull=True)
    if without_order:
        live = live.filter(order__isnull=True)
    if conversation_id:
        deal = live.filter(conversation_id=conversation_id).order_by("id").first()
        if deal:
            return deal
    key = phone_key(phone)
    if key:
        for deal in live.order_by("-id"):
            if phone_key(deal.contact) == key:
                return deal
    return None


def quote_in_deal(quote) -> bool:
    return Deal.objects.filter(quote=quote).exists() or DealQuote.objects.filter(quote=quote).exists()


def _follow_quote_client(deal, quote) -> None:
    """Имя и телефон в сделке следуют за актуальным КП: поправили имя в КП — оно и в карточке."""
    name, phone = quote_contact(quote)
    if name:
        deal.client = name
    if phone and phone_key(phone):
        deal.contact = phone


def attach_quote(deal, quote) -> None:
    """КП клиента ложится в его открытую сделку: новое становится актуальным, прежнее остаётся версией."""
    current = deal.quote
    if current is not None and current.pk == quote.pk:
        return
    if current is not None and current.created_at > quote.created_at:
        DealQuote.objects.get_or_create(quote=quote, defaults={"deal": deal})  # пришло старое КП — это версия
        return
    if current is not None:
        DealQuote.objects.get_or_create(quote=current, defaults={"deal": deal})
    deal.quote = quote
    if deal.conversation_id is None and quote.conversation_id:
        deal.conversation_id = quote.conversation_id
    if deal.order_id is None:
        # счёта ещё нет: сделка следует за актуальным КП; со счётом сумму задаёт он, а КП даёт предупреждение
        deal.title, deal.amount, deal.expected_date = f"КП №{quote.number}", quote.total, quote.valid_until
        deal.note = quote_summary(quote)
        _follow_quote_client(deal, quote)
        if deal.stage == Deal.Stage.NEW:
            deal.stage = Deal.Stage.OFFER
        if deal.origin == Deal.Origin.MANUAL:
            deal.origin = Deal.Origin.QUOTE
    deal.save()


def _sync_quote(quote):
    if Deal.objects.filter(quote=quote).exists() or DealQuote.objects.filter(quote=quote).exists():
        return
    name, phone = quote_contact(quote)
    deal = find_open_deal(phone, quote.conversation_id)
    if deal is not None:
        attach_quote(deal, quote)
        return
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


# --- КП и счета из панели

PANEL_QUOTE_MARK = "-P"
MAX_DISCOUNT = Decimal("99")
CENT = Decimal("0.01")


class DocumentError(Exception):
    """Не хватает данных для КП или счёта — текст для владельца."""


def is_panel_quote(quote) -> bool:
    # у КП из чата номер заканчивается шестью hex-цифрами; у панельных после дефиса стоит «P»
    return PANEL_QUOTE_MARK in quote.number


def parse_lines(raw) -> list[dict]:
    """Позиции из формы: [{"id", "qty", "discount"}] → товар, количество, цена до и после скидки."""
    if not isinstance(raw, list) or not raw:
        raise DocumentError("Добавьте хотя бы один товар")
    products = Product.objects.in_bulk([r.get("id") for r in raw if isinstance(r, dict) and str(r.get("id", "")).isdigit()])
    lines = []
    for row in raw:
        product = products.get(int(row["id"])) if isinstance(row, dict) and str(row.get("id", "")).isdigit() else None
        if product is None:
            raise DocumentError("Один из товаров не найден в каталоге — выберите его из списка")
        if not product.is_active or product.price_rub is None:
            raise DocumentError(f"{product.name}: у товара нет цены в рублях или он выключен")
        try:
            quantity = int(row.get("qty"))
            discount = Decimal(str(row.get("discount") or "0").replace(",", "."))
        except (TypeError, ValueError, ArithmeticError):
            raise DocumentError(f"{product.name}: проверьте количество и скидку")
        if not 1 <= quantity <= MAX_ITEM_QUANTITY:
            raise DocumentError(f"{product.name}: количество — от 1 до {MAX_ITEM_QUANTITY}")
        if not ZERO <= discount <= MAX_DISCOUNT:
            raise DocumentError(f"{product.name}: скидка — от 0 до {MAX_DISCOUNT}%")
        listed = product.price_rub
        unit = (listed * (100 - discount) / 100).quantize(CENT)
        lines.append({
            "product": product, "quantity": quantity, "list": listed, "unit": unit,
            "discount": discount if unit < listed else ZERO,
        })
    return lines


def _flatten(characteristics):
    """«Главное о товаре» так, как его показывает сайт: группы «название → список пунктов».

    Значение-строка — один пункт; вложенные словари и списки пар разворачиваются в группы."""
    pairs = []
    if isinstance(characteristics, dict):
        for key, value in characteristics.items():
            if isinstance(value, dict):
                pairs += [(k, v) for k, v in value.items()]
            else:
                pairs.append((key, value))
    elif isinstance(characteristics, list):
        for item in characteristics:
            if isinstance(item, dict):
                label = item.get("label") or item.get("title") or item.get("name") or item.get("key") or ""
                pairs.append((label, item.get("value", item.get("values", item.get("items")))))
            elif item:
                pairs.append(("", item))
    result = []
    for key, value in pairs:
        items = value if isinstance(value, (list, tuple)) else [value]
        items = [" ".join(str(v).split()) for v in items if v is not None and str(v).strip()]
        if items:
            result.append([str(key).strip(), items])
    return result


def product_details(product) -> dict:
    """Блок «Главное о товаре» для КП целиком, как на сайте: группы с названием и списком пунктов.

    Нет характеристик — берутся строки блоков контента «Характеристики» и «Ряд фич». Пусто — блока у товара не будет."""
    groups = _flatten(product.characteristics)
    if not groups:
        for block in product.content_blocks.all():
            if block.block_type not in ("specs", "features"):
                continue
            for item in block.data if isinstance(block.data, list) else []:
                if isinstance(item, dict):
                    label = str(item.get("label") or item.get("title") or item.get("name") or "").strip()
                    value = str(item.get("value") or "").strip()
                    if label or value:
                        groups.append([label, [value]] if label and value else [label or value, []])
    return {"groups": groups} if groups else {}


def quote_items(lines, with_details=True) -> list[dict]:
    """Снапшот позиций КП: цена до и после скидки, курс, комплектация."""
    items = []
    for line in lines:
        product = line["product"]
        items.append({
            "product_id": product.id, "name": product.name, "quantity": line["quantity"],
            "unit_price_rub": str(line["unit"]), "line_total_rub": str(line["unit"] * line["quantity"]),
            "list_price_rub": str(line["list"]) if line["discount"] else None,
            "discount_pct": str(line["discount"]) if line["discount"] else None,
            "source_price": str(product.price), "source_currency": product.currency,
            "exchange_rate": str(rate_on(product.currency)) if product.currency != "RUB" else None,
            **(product_details(product) if with_details else {}),
        })
    return items


def client_channel(phone, deal=None):
    """Как достучаться до клиента на сайте: (аккаунт, диалог). Аккаунт — по телефону, диалог — его открытый чат,
    а без аккаунта — чат из карточки сделки. Ничего не нашли — (None, None): документ просто остаётся у владельца."""
    from apps.accounts.models import User
    from apps.accounts.utils import normalize_phone as site_phone

    user = None
    number = site_phone(phone or "")
    if number:
        user = User.objects.filter(phone=number).first()
    conversation = None
    if user is not None:
        conversation = Conversation.objects.filter(
            user=user, status=Conversation.Status.ACTIVE).order_by("-last_message_at").first()
    if conversation is None and deal is not None and deal.conversation_id:
        conversation = deal.conversation
        if user is None:
            user = conversation.user
    return user, conversation


def notify_quote(quote, conversation) -> None:
    """В чат клиента: владелец подготовил КП (или обновил его)."""
    from apps.chat.services import add_event

    if conversation is None:
        return
    updated = quote.revision > 1
    add_event(conversation, Message.EventType.QUOTE_ISSUED, {
        "number": quote.number, "total": float(quote.total), "url": f"/chat/quote/{quote.id}/",
        "valid_until": quote.valid_until.isoformat(), "updated": updated,
    }, content=f"{'Обновлено' if updated else 'Подготовлено'} КП № {quote.number}")


def create_quote(user, *, client_name, client_phone, valid_days, lines, with_details=True) -> Quote:
    """КП по выбранным товарам со скидкой; PDF строится по снапшоту. С телефоном клиента заводится и карточка сделки."""
    from apps.chat.quote_pdf import generate_quote_pdf

    quote_id = uuid.uuid4()
    today = timezone.localdate()
    items = quote_items(lines, with_details)
    quote = Quote.objects.create(
        id=quote_id, number=f"KP-{today:%Y%m%d}{PANEL_QUOTE_MARK}{quote_id.hex[:5].upper()}",
        user=None, client_name=client_name or None, client_phone=client_phone or None, items=items,
        total=sum((Decimal(i["line_total_rub"]) for i in items), ZERO),
        valid_until=today + datetime.timedelta(days=valid_days),
    )
    quote.pdf_path = generate_quote_pdf(quote)
    Quote.objects.filter(pk=quote.pk).update(pdf_path=quote.pdf_path)
    owner, conversation = client_channel(client_phone, find_open_deal(client_phone, None) if phone_key(client_phone) else None)
    if owner is not None or conversation is not None:
        # КП видно клиенту на сайте: скачивание проверяет владельца по аккаунту или гостевой сессии
        quote.user = owner
        quote.session = conversation.session if owner is None and conversation is not None else None
        Quote.objects.filter(pk=quote.pk).update(user=quote.user, session=quote.session)
        transaction.on_commit(lambda: notify_quote(quote, conversation))
    if phone_key(client_phone):
        deal = find_open_deal(client_phone, None)
        if deal is not None:
            attach_quote(deal, quote)  # второе КП тому же клиенту — версия в его сделке, а не новая карточка
        else:
            deal = Deal.objects.create(
                origin=Deal.Origin.QUOTE, quote=quote, title=f"КП №{quote.number}", client=client_name or "Клиент",
                contact=client_phone, source=Deal.PANEL_SOURCE, amount=quote.total, stage=Deal.Stage.OFFER,
                expected_date=quote.valid_until, note=quote_summary(quote),
            )
            sync_deal(deal)
    return quote


def quote_editable(quote):
    """Правка возможна, пока КП актуально и по нему не получены деньги. Иначе — текст причины."""
    if DealQuote.objects.filter(quote=quote).exists():
        return "Это КП заменено новым — правьте актуальное"
    deal = Deal.objects.filter(quote=quote).select_related("order").first()
    if deal and (deal.stage == Deal.Stage.WON or (deal.order_id and deal.order.status == Order.Status.PAID)):
        return "По КП уже получена оплата — правка невозможна, сделайте новое КП"
    return ""


def revise_quote(quote, *, client_name, client_phone, valid_days, lines, with_details=True) -> Quote:
    """Правка готового КП: тот же номер, новая ревизия. Прежняя версия уходит в историю, PDF пересобирается."""
    from apps.chat.quote_pdf import generate_quote_pdf

    reason = quote_editable(quote)
    if reason:
        raise DocumentError(reason)
    now = timezone.now()
    today = timezone.localdate()
    quote.history = [*quote.history, {
        "revision": quote.revision, "at": (quote.revised_at or quote.created_at).isoformat(),
        "total": str(quote.total), "valid_until": quote.valid_until.isoformat(), "items": quote.items,
    }]
    quote.items = quote_items(lines, with_details)
    quote.total = sum((Decimal(i["line_total_rub"]) for i in quote.items), ZERO)
    quote.valid_until = today + datetime.timedelta(days=valid_days)
    quote.revision += 1
    quote.revised_at = now
    if is_panel_quote(quote):
        quote.client_name, quote.client_phone = client_name or None, client_phone or None
    quote.pdf_path = generate_quote_pdf(quote)
    deal = Deal.objects.filter(quote=quote).first()
    if is_panel_quote(quote):
        owner, conversation = client_channel(quote.client_phone, deal)
        if owner is not None or conversation is not None:
            quote.user = owner
            quote.session = conversation.session if owner is None and conversation is not None else None
            transaction.on_commit(lambda: notify_quote(quote, conversation))
    quote.save()
    if deal is not None and deal.order_id is None:
        deal.amount, deal.expected_date, deal.note = quote.total, quote.valid_until, quote_summary(quote)
        _follow_quote_client(deal, quote)
        deal.archived_at = None if deal.stage == Deal.Stage.OFFER else deal.archived_at  # срок продлён — КП снова живо
        deal.save()
    return quote


def quote_form(quote) -> dict:
    """Форма КП, заполненная по готовому КП — для правки."""
    name, phone = quote_contact(quote)
    items = [
        {"id": i["product_id"], "qty": i["quantity"], "discount": i.get("discount_pct") or ""}
        for i in quote.items if i.get("product_id")
    ]
    days = max((quote.valid_until - timezone.localdate()).days, 1)
    form = {"items": json.dumps(items), "name": name or "", "phone": phone or "", "valid_days": str(days)}
    if any(i.get("groups") or i.get("specs") or i.get("kit") for i in quote.items):
        form["with_details"] = "1"
    return form


BUYER_TYPES = {"individual": Order.BuyerType.INDIVIDUAL, "ip": Order.BuyerType.IP, "legal": Order.BuyerType.LEGAL}


def create_invoice(user, *, buyer_type, name, inn, contact_name, phone, email, delivery, comment, lines, deal=None) -> Order:
    """Заказ от имени владельца и счёт по нему: цены со скидкой, в счёте видна цена по прайсу.

    Заказ идёт обычным путём: подтверждён, счёт выставлен, дальше «Оплата пришла» в продажах."""
    if buyer_type not in BUYER_TYPES:
        raise DocumentError("Выберите, кому выставляется счёт: физлицо, ИП или юрлицо")
    if not name.strip():
        raise DocumentError("Укажите название организации или ФИО покупателя")
    length = {"ip": 12, "legal": 10}.get(buyer_type)
    if length and not valid_inn(inn, length):
        raise DocumentError(f"ИНН {'ИП' if length == 12 else 'организации'} — {length} цифр, проверьте номер")
    fields = {"buyer_type": BUYER_TYPES[buyer_type], "contact_phone": phone or None, "contact_email": email or None,
              "comment": comment}
    if buyer_type == "legal":
        fields.update(company_name=name.strip(), inn=inn, contact_name=contact_name.strip() or None)
    elif buyer_type == "ip":
        fields.update(buyer_name=name.strip(), inn=inn, contact_name=name.strip())
    else:
        fields.update(buyer_name=name.strip(), contact_name=name.strip())
    today = timezone.localdate()
    # Клиент есть на сайте — заказ попадёт в его «Мои заказы», а в чат придёт счёт
    owner, conversation = client_channel(phone, deal)
    if owner is not None or conversation is not None:
        fields.update(user=owner, conversation=conversation,
                      session=conversation.session if owner is None and conversation is not None else None)
    with transaction.atomic():
        order = Order.objects.create(
            status=Order.Status.PENDING_CONFIRMATION, payment_status=Order.PaymentStatus.NONE, currency="RUB",
            created_by=Order.CreatedBy.CATALOG, number=f"tmp-{uuid.uuid4().hex}", **fields,
        )
        order.number = f"AD-{timezone.now():%Y%m%d}-{order.id:04d}"
        total = ZERO
        for line in lines:
            product = line["product"]
            foreign = product.currency != Product.Currency.RUB
            total += line["unit"] * line["quantity"]
            OrderItem.objects.create(
                order=order, product=product, product_name=product.name, unit_price=line["unit"],
                quantity=line["quantity"], line_total=line["unit"] * line["quantity"], selected_options=[],
                list_unit_price=line["list"] if line["discount"] else None,
                source_price=product.price, source_currency=product.currency,
                exchange_rate=rate_on(product.currency, today) if foreign else None,
                # цену задали мы: при подтверждении по курсу не пересчитывается
                price_overridden=True,
            )
        if delivery:
            OrderItem.objects.create(
                order=order, kind=OrderItem.Kind.DELIVERY, product_name="Доставка", quantity=1,
                unit_price=delivery, line_total=delivery,
            )
            total += delivery
        order.total_amount = order.total_at_creation = total
        order.save()
        OrderStatusHistory.objects.create(
            order=order, from_status=Order.Status.DRAFT, to_status=Order.Status.PENDING_CONFIRMATION,
            actor=OrderStatusHistory.Actor.DIRECTOR, actor_user=user,
        )
    # счёт строится при подтверждении; сбой PDF откатывает подтверждение, заказ остаётся ждать
    try:
        return change_status(order, Order.Status.CONFIRMED, actor=OrderStatusHistory.Actor.DIRECTOR, actor_user=user)
    except (InvoiceError, TransitionError):
        order.delete()
        raise


def invoice_form_from_quote(deal) -> dict:
    """Форма счёта, заполненная по КП: те же товары, количество и скидки, клиент из карточки."""
    items = [
        {"id": i["product_id"], "qty": i["quantity"], "discount": i.get("discount_pct") or ""}
        for i in deal.quote.items if i.get("product_id")
    ]
    return {"items": json.dumps(items), "deal_id": str(deal.pk), "name": deal.client, "phone": deal.contact}


def link_invoice_to_quote_deal(deal_id, order) -> None:
    """Счёт выставлен по КП сделки: карточка становится карточкой заказа. Перевыставление заменяет неоплаченный счёт."""
    deal = Deal.objects.filter(pk=deal_id, quote__isnull=False).select_related("order").first() if str(deal_id).isdigit() else None
    if deal is None or Deal.objects.filter(order=order).exists():
        return
    old = deal.order
    if old is not None:
        if old.status == Order.Status.PAID:
            return
        deal.order = None
        deal.save(update_fields=["order", "updated_at"])
        old.delete()
    deal.order = order
    deal.save(update_fields=["order", "updated_at"])


def recent_documents(limit=15):
    """Последние КП и счета, собранные в панели."""
    quotes = [q for q in Quote.objects.filter(number__contains=PANEL_QUOTE_MARK).order_by("-created_at")[:limit]]
    invoices = list(
        Order.objects.filter(status_history__from_status=Order.Status.DRAFT, status_history__actor=OrderStatusHistory.Actor.DIRECTOR)
        .select_related("invoice").prefetch_related("items").distinct().order_by("-created_at")[:limit]
    )
    return quotes, invoices
