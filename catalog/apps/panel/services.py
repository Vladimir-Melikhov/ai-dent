"""Цифры панели. Продажи сайта берутся из оплаченных заказов, всё остальное — из операций панели."""
import datetime
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.chat.models import LLMUsage
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
        for r in Deal.objects.filter(stage__in=Deal.OPEN_STAGES).values("stage").annotate(n=Count("id"), total=Sum("amount"))
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
    open_qs = Deal.objects.filter(stage__in=Deal.OPEN_STAGES)
    won = Deal.objects.filter(stage=Deal.Stage.WON, closed_at__date__gte=start, closed_at__date__lte=end)
    lost = Deal.objects.filter(stage=Deal.Stage.LOST, closed_at__date__gte=start, closed_at__date__lte=end)
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
    if deal.stage != Deal.Stage.WON or deal.amount <= 0:
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
