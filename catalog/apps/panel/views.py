import csv
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.chat.models import Quote
from apps.chat.quote_pdf import generate_quote_pdf
from apps.orders.models import Order, OrderStatusHistory
from apps.orders.services import (
    InvoiceError, TransitionError, allowed_transitions, change_status, invoice_available, invoice_path,
)

from . import services
from .access import (
    SESSION_AGE, has_panel_access, login_blocked, login_failed, login_succeeded, panel_required,
)
from .forms import CategoryForm, CostForm, DealForm, EntryForm, ServerForm
from apps.catalog.models import Product
from .models import Category, Deal, Entry, Server

ZERO = services.ZERO


# --- вход

def normalize_phone(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    return f"+{digits}" if digits else ""


def phone_variants(raw: str) -> list[str]:
    """Как один и тот же номер мог быть записан в базе: +7…, 7…, 8…, как набран."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    variants = [normalize_phone(raw), digits, f"+{digits}"]
    if len(digits) == 11 and digits[0] in "78":
        variants += [f"8{digits[1:]}", f"7{digits[1:]}", f"+7{digits[1:]}"]
    return list(dict.fromkeys(v for v in variants if v))


def find_user(raw: str):
    return User.objects.filter(phone__in=phone_variants(raw)).first()


def login_view(request):
    if has_panel_access(request.user):
        return redirect("panel:dashboard")
    error = ""
    phone_value = ""
    if request.method == "POST":
        phone_value = request.POST.get("phone", "")
        phone = normalize_phone(phone_value)
        if login_blocked(request, phone):
            error = "Слишком много попыток. Подождите 15 минут."
        else:
            account = find_user(phone_value)
            user = authenticate(
                request, username=account.phone if account else phone, password=request.POST.get("password", "")
            )
            if user is not None and not has_panel_access(user):
                # Пароль верный, но роль не та: говорим прямо, чтобы не гадать (ответ получает только знающий пароль)
                login_failed(request, phone)
                error = "Пароль верный, но у аккаунта нет доступа к панели: нужен суперпользователь или роль «Директор»"
            elif user is not None:
                login_succeeded(request, phone)
                login(request, user)
                request.session.set_expiry(SESSION_AGE)
                target = request.POST.get("next", "")
                if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
                    target = ""
                return redirect(target or "panel:dashboard")
            else:
                login_failed(request, phone)
                error = "Неверный телефон или пароль"
    return render(request, "panel/login.html", {
        "error": error, "phone": phone_value, "next": request.GET.get("next", request.POST.get("next", "")),
    }, status=400 if error else 200)


@require_POST
def logout_view(request):
    logout(request)
    return redirect("panel:login")


@require_GET
def robots(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


# --- общее

def page_context(request, section, **extra):
    period = services.resolve_period(request.GET.get("p"))
    return {
        "section": section, "period": period, "periods": services.PERIODS,
        "site_origin": settings.SITE_ORIGIN, **extra,
    }


def as_float(value):
    return float(value) if value is not None else None


def save_object(request, form_class, model, redirect_to, done_text, guard=None):
    """Создание и правка одной формой: поле id в POST — правка."""
    instance = None
    if request.POST.get("id"):
        instance = get_object_or_404(model, pk=request.POST["id"])
        if guard and (reason := guard(instance)):
            messages.error(request, reason)
            return redirect(redirect_to)
    form = form_class(request.POST, instance=instance)
    if form.is_valid():
        with transaction.atomic():
            obj = form.save()
            if model is Deal:
                services.sync_deal(obj)
        messages.success(request, done_text)
    else:
        problems = "; ".join(f"{form.fields[name].label or name}: {' '.join(errs)}" for name, errs in form.errors.items())
        messages.error(request, f"Не сохранено. {problems}")
    return redirect(request.POST.get("back") or redirect_to)


def safe_back(request, fallback):
    target = request.POST.get("back", "")
    return target if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}) else fallback


# --- обзор

@panel_required
def dashboard(request):
    ctx = page_context(request, "dashboard")
    services.sync_auto_deals()
    period, prev = ctx["period"], ctx["period"].previous
    now, before = services.totals(period.start, period.end), services.totals(prev.start, prev.end)
    rows = services.series(period)
    deals = services.deals_summary(period.start, period.end)
    ai_usd = services.ai_spend_usd(period.start, period.end)

    cards = [
        {"key": "revenue", "title": "Выручка", "value": as_float(now["revenue"]), "money": True,
         "delta": services.delta(now["revenue"], before["revenue"]), "spark": [r["income"] for r in rows],
         "hint": f"Сайт {now['shop_revenue']:,.0f} ₽".replace(",", " ")},
        {"key": "expenses", "title": "Расходы", "value": as_float(now["costs"]), "money": True,
         "delta": services.delta(now["costs"], before["costs"]), "invert": True,
         "spark": [r["expenses"] for r in rows],
         "hint": f"в т.ч. закупка {now['cogs']:,.0f} ₽".replace(",", " ") if now["cogs"] else ""},
        {"key": "profit", "title": "Прибыль", "value": as_float(now["profit"]), "money": True,
         "delta": services.delta(now["profit"], before["profit"]), "spark": [r["profit"] for r in rows],
         "hint": f"Маржа {now['margin']:.0f}%" if now["margin"] is not None else ""},
        {"key": "orders", "title": "Заказы сайта", "value": now["orders"], "money": False,
         "delta": services.delta(now["orders"], before["orders"]), "spark": [], "hint": (
             f"Средний чек {now['avg_check']:,.0f} ₽".replace(",", " ") if now["avg_check"] else "")},
    ]
    data = {
        "series": rows,
        "expenses": services.expenses_by_category(period.start, period.end),
        "pipeline": services.pipeline(),
        "cards": cards,
    }
    ctx.update(
        cards=cards, deals=deals, ai_usd=ai_usd, totals=now, data=data,
        recent=services.recent_activity(), servers=Server.objects.all()[:6],
        servers_cost=Server.objects.aggregate(t=Sum("monthly_cost"))["t"] or ZERO,
        greeting=greeting(),
    )
    return render(request, "panel/dashboard.html", ctx)


def greeting():
    hour = timezone.localtime().hour
    return "Доброй ночи" if hour < 5 else "Доброе утро" if hour < 12 else "Добрый день" if hour < 18 else "Добрый вечер"


# --- сделки

@panel_required
def deals(request):
    ctx = page_context(request, "deals")
    services.sync_auto_deals()
    origin = request.GET.get("src", "")
    archive = request.GET.get("archive") == "1"
    qs = Deal.objects.select_related("order", "conversation", "quote").filter(archived_at__isnull=not archive)
    # КП и счета, собранные в панели, — «Вручную»; «Из чата» и «С сайта» — только то, что пришло от клиентов
    made_here = Q(origin=Deal.Origin.MANUAL) | Q(source=Deal.PANEL_SOURCE)
    if origin == Deal.Origin.MANUAL:
        qs = qs.filter(made_here)
    elif origin in Deal.Origin.values:
        qs = qs.filter(origin=origin).exclude(made_here)
    stages = []
    all_deals = list(qs)
    now = timezone.now()
    for d in all_deals:
        # сколько карточка уже ждёт; с недели — тревожный цвет
        since = (d.order.confirmed_at or d.order.created_at) if d.order_id else d.created_at
        d.wait_days = (now - since).days if d.is_system and d.stage in Deal.OPEN_STAGES else None
        d.stale = d.wait_days is not None and d.wait_days >= services.STALE_DAYS
    for stage in Deal.Stage:
        items = [d for d in all_deals if d.stage == stage.value]
        if stage in (Deal.Stage.WON, Deal.Stage.LOST):
            cutoff = timezone.now() - datetime.timedelta(days=90)
            items = [d for d in items if d.closed_at is None or d.closed_at >= cutoff]
        stages.append({
            "value": stage.value, "label": stage.label, "items": items,
            "amount": sum((d.amount for d in items), ZERO),
        })
    ctx.update(
        stages=stages, form=DealForm(), origin=origin, origins=Deal.Origin, archive=archive,
        archived_count=Deal.objects.filter(archived_at__isnull=False).count(),
        anon_quotes=[] if archive else [
            {"quote": q, "link": admin_link("chat/conversation", q.conversation_id) if q.conversation_id else ""}
            for q in services.anonymous_quotes()
        ],
        summary=services.deals_summary(ctx["period"].start, ctx["period"].end),
        objects={d.pk: deal_json(d) for d in all_deals},
    )
    return render(request, "panel/deals.html", ctx)


def admin_link(app_model, pk):
    return f"{settings.SITE_ORIGIN}/{settings.ADMIN_URL}{app_model}/{pk}/change/"


ORDER_ACTIONS = [
    (Order.Status.CONFIRMED, "Подтвердить заказ",
     "Подтвердить заказ №{number}? Клиенту уйдёт счёт на почту."),
    (Order.Status.PAID, "Оплата пришла",
     "Отметить заказ №{number} оплаченным? Деньги уже на счёте — клиент увидит это в чате."),
    (Order.Status.CANCELLED, "Отменить заказ", "Отменить заказ №{number}?"),
]


def deal_json(deal):
    links = []
    if deal.order_id:
        links.append(["Заказ в админке", admin_link("orders/order", deal.order_id)])
    if deal.quote_id:
        links.append([f"КП №{deal.quote.number}", admin_link("chat/quote", deal.quote_id)])
    if deal.conversation_id:
        links.append(["Диалог в чате", admin_link("chat/conversation", deal.conversation_id)])
    actions = [{
        "label": "Вернуть из архива" if deal.archived_at else "В архив",
        "url": f"/deals/{deal.pk}/archive/", "confirm": "",
    }]
    if deal.quote_id and not deal.order_id and not deal.archived_at:
        actions.insert(0, {"label": "Выставить счёт по КП", "href": f"/docs/?from_quote={deal.pk}"})
    if deal.order_id:
        for target, label, text in ORDER_ACTIONS:
            if target in allowed_transitions(deal.order):
                actions.append({
                    "label": label, "danger": target == Order.Status.CANCELLED, "url": f"/orders/{deal.order_id}/to/{target}/",
                    "confirm": text.format(number=deal.order.number),
                    # оплачен — сначала спрашиваем закупку
                    "ask_cost": target == Order.Status.PAID,
                    "cost": "" if deal.cost is None else str(deal.cost),
                })
    if deal.order_id and deal.order.status != Order.Status.PAID:
        actions.append({
            "label": "Удалить заказ", "danger": True, "url": f"/deals/{deal.pk}/delete-order/",
            "confirm": f"Удалить заказ №{deal.order.number} вместе со счётом? Клиент сможет оформить новый. Это нельзя отменить.",
        })
    return {
        "locked": list(DealForm.SYSTEM_LOCKED) if deal.is_system else [], "links": links, "actions": actions,
        "origin": deal.origin_label,
        "title": deal.title, "client": deal.client, "contact": deal.contact, "amount": str(deal.amount),
        "cost": "" if deal.cost is None else str(deal.cost),
        "stage": deal.stage, "source": deal.source, "note": deal.note,
        "expected_date": deal.expected_date.isoformat() if deal.expected_date else "",
    }


@panel_required
@require_POST
def deal_save(request):
    return save_object(request, DealForm, Deal, "panel:deals", "Сделка сохранена")


@panel_required
@require_POST
def deal_stage(request, pk):
    """Перенос карточки по воронке (перетаскивание)."""
    deal = get_object_or_404(Deal, pk=pk)
    stage = request.POST.get("stage")
    if stage not in Deal.Stage.values:
        return JsonResponse({"error": "Неизвестный этап"}, status=400)
    if deal.is_system:
        return system_deal_stage(request, deal, stage)
    if stage == Deal.Stage.WON:
        cost = CostForm(request.POST)
        if not cost.is_valid():
            return JsonResponse({"error": "Укажите закупку (0, если её не было)", "need": "cost"}, status=400)
        deal.cost = cost.cleaned_data["cost"]
    with transaction.atomic():
        deal.stage = stage
        deal.save(update_fields=["stage", "cost", "updated_at"])
        services.sync_deal(deal)
    return JsonResponse({"ok": True})


def system_deal_stage(request, deal, stage):
    """Перетаскивание карточки заказа или КП: двигаются только настоящие действия — оплата и отмена."""
    if stage == deal.stage:
        return JsonResponse({"ok": True})
    order = deal.order
    if order is None:
        if stage == Deal.Stage.LOST:
            deal.archived_at = timezone.now()
            deal.save(update_fields=["archived_at"])
            return JsonResponse({"ok": True})
        return JsonResponse({
            "error": "КП само перейдёт в «Ждём оплату», когда по нему выставят счёт. Отказ клиента — перетащите в «Проиграна»",
        }, status=400)
    target = {Deal.Stage.WON: Order.Status.PAID, Deal.Stage.LOST: Order.Status.CANCELLED}.get(stage)
    if target is None:
        return JsonResponse({"error": "Этап карточки следует за заказом: перетащите в «Выиграна» (оплата) или «Проиграна» (отмена)"}, status=400)
    if target not in allowed_transitions(order):
        return JsonResponse({"error": f"Заказ №{order.number} сейчас нельзя перевести в этот этап"}, status=400)
    cost = None
    if target == Order.Status.PAID:
        form = CostForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"error": "Укажите закупку (0, если её не было)", "need": "cost"}, status=400)
        cost = form.cleaned_data["cost"]
    try:
        change_status(order, target, actor=OrderStatusHistory.Actor.DIRECTOR, actor_user=request.user)
    except (TransitionError, InvoiceError) as error:
        return JsonResponse({"error": str(error)}, status=400)
    if cost is not None:
        paid = services.ensure_order_deal(order)
        paid.cost = cost
        paid.save(update_fields=["cost", "updated_at"])
    services.sync_auto_deals()
    return JsonResponse({"ok": True})


@panel_required
@require_POST
def deal_archive(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    deal.archived_at = None if deal.archived_at else timezone.now()
    deal.save(update_fields=["archived_at"])
    messages.success(request, "Карточка в архиве" if deal.archived_at else "Карточка возвращена на доску")
    return redirect("panel:deals")


@panel_required
@require_POST
def deal_order_delete(request, pk):
    """Удаление неоплаченного заказа вместе с карточкой. Оплаченный — деньги в учёте — только в архив."""
    deal = get_object_or_404(Deal, pk=pk)
    order = deal.order
    if order is None:
        messages.error(request, "У карточки нет заказа")
    elif order.status == Order.Status.PAID:
        messages.error(request, "Оплаченный заказ удалить нельзя: он в учёте. Уберите карточку в архив")
    else:
        number = order.number
        with transaction.atomic():
            order.delete()
            services.sync_auto_deals()
        messages.success(request, f"Заказ №{number} удалён")
    return redirect("panel:deals")


@panel_required
@require_POST
def deal_delete(request, pk):
    deal = get_object_or_404(Deal, pk=pk)
    if deal.is_system:
        messages.error(request, "Карточка из заказа или чата удаляется вместе с ним")
        return redirect("panel:deals")
    deal.delete()
    messages.success(request, "Сделка удалена")
    return redirect("panel:deals")


# --- финансы

def ledger(request, period):
    qs = Entry.objects.select_related("category").filter(date__gte=period.start, date__lte=period.end)
    kind = request.GET.get("kind", "")
    if kind in Category.Kind.values:
        qs = qs.filter(kind=kind)
    category = request.GET.get("category", "")
    if category.isdigit():
        qs = qs.filter(category_id=int(category))
    query = request.GET.get("q", "").strip()
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(note__icontains=query) | Q(category__name__icontains=query))
    return qs, kind, category, query


@panel_required
def finance(request):
    ctx = page_context(request, "finance")
    period = ctx["period"]
    qs, kind, category, query = ledger(request, period)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    sums = {row["kind"]: row["t"] for row in qs.values("kind").annotate(t=Sum("amount"))}
    now = services.totals(period.start, period.end)
    params = request.GET.copy()
    params.pop("page", None)
    ctx.update(
        page=page, kind=kind, category_id=category, q=query, form=EntryForm(),
        categories=Category.objects.all(), filtered_income=sums.get("income", ZERO),
        filtered_expenses=sums.get("expense", ZERO), totals=now, querystring=params.urlencode(),
        objects={e.pk: entry_json(e) for e in page},
        category_kinds={c.pk: c.kind for c in Category.objects.all()},
        structure=services.expense_structure(period),
    )
    return render(request, "panel/finance.html", ctx)


def entry_json(entry):
    return {
        "kind": entry.kind, "category": entry.category_id, "amount": str(entry.amount),
        "date": entry.date.isoformat(), "title": entry.title, "note": entry.note,
    }


def entry_guard(entry):
    if entry.deal_id:
        return "Доход от сделки меняется в самой сделке"
    return None


@panel_required
@require_POST
def entry_save(request):
    return save_object(request, EntryForm, Entry, "panel:finance", "Операция сохранена", guard=entry_guard)


@panel_required
@require_POST
def entry_delete(request, pk):
    entry = get_object_or_404(Entry, pk=pk)
    if reason := entry_guard(entry):
        messages.error(request, reason)
    else:
        entry.delete()
        messages.success(request, "Операция удалена")
    return redirect(safe_back(request, "/finance/"))


def csv_safe(value: str) -> str:
    # Excel выполнил бы ячейку, начинающуюся с = + - @
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@panel_required
@require_GET
def finance_export(request):
    period = services.resolve_period(request.GET.get("p"))
    qs, *_ = ledger(request, period)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="operations-{period.start}-{period.end}.csv"'
    response.write("﻿")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(["Дата", "Вид", "Категория", "Описание", "Сумма, ₽", "Заметка"])
    for e in qs:
        writer.writerow([
            e.date.isoformat(), e.get_kind_display(), csv_safe(e.category.name), csv_safe(e.title),
            str(e.amount).replace(".", ","), csv_safe(e.note),
        ])
    return response


# --- категории

@panel_required
def categories(request):
    ctx = page_context(request, "categories")
    period = ctx["period"]
    sums = {
        row["category_id"]: row["t"]
        for row in Entry.objects.filter(date__gte=period.start, date__lte=period.end)
        .values("category_id").annotate(t=Sum("amount"))
    }
    counts = {row["category_id"]: row["n"] for row in Entry.objects.values("category_id").annotate(n=Count("id"))}
    groups = []
    for kind in Category.Kind:
        items = [
            {"obj": c, "sum": sums.get(c.pk, ZERO), "count": counts.get(c.pk, 0)}
            for c in Category.objects.filter(kind=kind.value)
        ]
        groups.append({"kind": kind.value, "label": "Доходы" if kind.value == "income" else "Расходы", "items": items})
    ctx.update(
        groups=groups, form=CategoryForm(), colors=range(1, 9),
        objects={c.pk: {"kind": c.kind, "name": c.name, "color": c.color} for c in Category.objects.all()},
    )
    return render(request, "panel/categories.html", ctx)


@panel_required
@require_POST
def category_save(request):
    return save_object(request, CategoryForm, Category, "panel:categories", "Категория сохранена")


@panel_required
@require_POST
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if category.entries.exists():
        messages.error(request, "В категории есть операции — сначала перенесите или удалите их")
    else:
        category.delete()
        messages.success(request, "Категория удалена")
    return redirect("panel:categories")


# --- продажи сайта

@panel_required
def sales(request):
    ctx = page_context(request, "sales")
    period = ctx["period"]
    base = Order.objects.exclude(status=Order.Status.DRAFT).filter(
        created_at__date__gte=period.start, created_at__date__lte=period.end
    )
    by_status = {r["status"]: r for r in base.values("status").annotate(n=Count("id"), t=Sum("total_amount"))}
    status = request.GET.get("status", "")
    qs = base.select_related("invoice", "panel_deal")
    if status in Order.Status.values:
        qs = qs.filter(status=status)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    now = timezone.now()
    for order in page:
        waiting = order.status in (Order.Status.PENDING_CONFIRMATION, Order.Status.CONFIRMED)
        since = order.confirmed_at or order.created_at
        order.wait_days = (now - since).days if waiting else None
        if order.status == Order.Status.PAID:
            cost = services.order_cost(order)
            order.cost, order.profit = (cost, (order.total_amount or ZERO) - cost) if cost else (None, None)
        order.can_confirm = Order.Status.CONFIRMED in allowed_transitions(order)
        order.can_pay = Order.Status.PAID in allowed_transitions(order)
    tiles = []
    for value, title, hint in [
        (Order.Status.PENDING_CONFIRMATION, "Ждут подтверждения", "нужно подтвердить и выставить счёт"),
        (Order.Status.CONFIRMED, "Подтверждены", "счёт выставлен, ждём оплату"),
        (Order.Status.PAID, "Оплачены", "деньги получены"),
        (Order.Status.CANCELLED, "Отменены", ""),
    ]:
        row = by_status.get(value, {})
        tiles.append({"value": value, "title": title, "hint": hint, "count": row.get("n", 0), "sum": row.get("t") or ZERO})
    total_n = sum(r["n"] for r in by_status.values())
    total_sum = sum((r["t"] or ZERO for r in by_status.values()), ZERO)
    params = request.GET.copy()
    params.pop("page", None)
    ctx.update(
        page=page, status=status, statuses=Order.Status, tiles=tiles, total_n=total_n, total_sum=total_sum,
        totals=services.totals(period.start, period.end), querystring=params.urlencode(),
    )
    return render(request, "panel/sales.html", ctx)


@panel_required
@require_POST
def order_status(request, pk, to):
    """Подтвердить, отметить оплаченным (оплата по счёту сайту не видна) или отменить заказ — как в админке."""
    order = get_object_or_404(Order, pk=pk)
    if to not in {Order.Status.CONFIRMED, Order.Status.PAID, Order.Status.CANCELLED}:
        messages.error(request, "Неизвестный статус")
    else:
        cost = None
        if to == Order.Status.PAID:
            form = CostForm(request.POST)
            if not form.is_valid():
                messages.error(request, "Укажите закупку — во сколько обошёлся товар (0, если закупки не было)")
                return redirect(safe_back(request, "/sales/"))
            cost = form.cleaned_data["cost"]
        try:
            change_status(order, to, actor=OrderStatusHistory.Actor.DIRECTOR, actor_user=request.user)
            if cost is not None:
                deal = services.ensure_order_deal(order)
                deal.cost = cost
                deal.save(update_fields=["cost", "updated_at"])
            word = {"confirmed": "подтверждён", "paid": "отмечен оплаченным", "cancelled": "отменён"}[to]
            messages.success(request, f"Заказ №{order.number} {word}")
        except (TransitionError, InvoiceError) as error:
            messages.error(request, str(error))
    services.sync_auto_deals()
    return redirect(safe_back(request, "/sales/"))


# --- ИИ и статистика

@panel_required
def ai(request):
    ctx = page_context(request, "ai")
    stats = services.ai_stats(ctx["period"])
    chart = {"series": stats["series"]}
    ctx.update(stats=stats, chart=chart)
    return render(request, "panel/ai.html", ctx)


# --- калькулятор маржи

@panel_required
def margin(request):
    ctx = page_context(request, "margin")
    products = [
        {"name": p.name, "sku": p.sku or "", "price": float(p.price_rub)}
        for p in Product.objects.filter(is_active=True, price_rub__isnull=False).order_by("name")[:3000]
    ]
    ctx.update(products=products)
    return render(request, "panel/margin.html", ctx)


# --- КП и счета

def details_count(product):
    groups = services.product_details(product).get("groups", [])
    return {"groups": len(groups), "items": sum(len(items) or 1 for _, items in groups)}


@panel_required
def documents(request):
    ctx = page_context(request, "documents")
    quotes, invoices = services.recent_documents()
    products = [
        {"id": p.pk, "name": p.name, "sku": p.sku or "", "price": float(p.price_rub),
         "details": details_count(p)}
        for p in Product.objects.filter(is_active=True, price_rub__isnull=False)
        .prefetch_related("content_blocks").order_by("name")[:3000]
    ]
    form = request.session.pop("doc_form", {})
    quote_deal = None
    source = request.GET.get("from_quote", "")
    if source.isdigit():
        quote_deal = Deal.objects.filter(pk=source, quote__isnull=False, order__isnull=True).select_related("quote").first()
        if quote_deal and not form:
            form = services.invoice_form_from_quote(quote_deal)
    made = request.GET.get("made", "")
    result = None
    if made.startswith("quote:"):
        found = next((q for q in quotes if str(q.pk) == made[6:]), None)
        if found:
            result = {"kind": "КП", "done": "КП готово", "number": found.number, "total": found.total, "url": f"/docs/quote/{found.pk}.pdf",
                      "discount": any(i.get("list_price_rub") for i in found.items)}
    elif made.startswith("invoice:"):
        found = next((o for o in invoices if o.number == made[8:]), None)
        if found:
            result = {"kind": "Счёт", "done": "Счёт выставлен", "number": found.number, "total": found.total_amount,
                      "url": f"/docs/invoice/{found.number}.pdf",
                      "discount": any(i.list_unit_price for i in found.items.all()),
                      "order": f"/sales/?status={found.status}"}
    for quote in quotes:
        quote.discounted = any(i.get("list_price_rub") for i in quote.items)
    for order in invoices:
        order.discounted = any(i.list_unit_price for i in order.items.all())
        order.has_pdf = invoice_available(order)
    ctx.update(
        products=products, quotes=quotes, invoices=invoices, result=result, valid_days=14,
        form=form, from_quote=quote_deal,
    )
    return render(request, "panel/documents.html", ctx)


def document_lines(request):
    try:
        raw = json.loads(request.POST.get("items", "[]"))
    except ValueError:
        raw = []
    return services.parse_lines(raw)


def remember_form(request):
    """Введённое не пропадает, если документ не получился."""
    request.session["doc_form"] = {k: v for k, v in request.POST.items() if k in {
        "phone", "buyer_type", "name", "inn", "contact_name", "email", "delivery", "comment",
        "valid_days", "items", "with_details", "deal_id",
    }}


@panel_required
@require_POST
def quote_create(request):
    try:
        lines = document_lines(request)
        try:
            days = int(request.POST.get("valid_days") or 14)
        except ValueError:
            days = 0
        if not 1 <= days <= 365:
            raise services.DocumentError("Срок действия — от 1 до 365 дней")
        quote = services.create_quote(
            request.user, client_name=request.POST.get("name", "").strip()[:160],
            client_phone=request.POST.get("phone", "").strip()[:30], valid_days=days, lines=lines,
            with_details=request.POST.get("with_details") == "1",
        )
    except services.DocumentError as error:
        remember_form(request)
        messages.error(request, str(error))
        return redirect("panel:documents")
    return redirect(f"/docs/?made=quote:{quote.pk}")


@panel_required
@require_POST
def invoice_create(request):
    try:
        lines = document_lines(request)
        delivery = parse_money(request.POST.get("delivery", ""))
        order = services.create_invoice(
            request.user, buyer_type=request.POST.get("buyer_type", ""), name=request.POST.get("name", "")[:255],
            inn="".join(ch for ch in request.POST.get("inn", "") if ch.isdigit()),
            contact_name=request.POST.get("contact_name", "")[:255], phone=request.POST.get("phone", "").strip()[:30],
            email=request.POST.get("email", "").strip()[:254], delivery=delivery,
            comment=request.POST.get("comment", "")[:1000], lines=lines,
        )
    except (services.DocumentError, InvoiceError, TransitionError) as error:
        remember_form(request)
        messages.error(request, str(error))
        return redirect("panel:documents")
    services.link_invoice_to_quote_deal(request.POST.get("deal_id", ""), order)
    services.sync_auto_deals()
    return redirect(f"/docs/?made=invoice:{order.number}")


def parse_money(raw):
    raw = re.sub(r"[\s\u00a0₽]", "", raw).replace(",", ".")
    if not raw:
        return None
    try:
        value = Decimal(raw).quantize(Decimal("0.01"))
    except ArithmeticError:
        raise services.DocumentError("Доставка: введите сумму цифрами")
    if value < 0:
        raise services.DocumentError("Доставка не может быть отрицательной")
    return value or None


@panel_required
@require_GET
def quote_file(request, pk):
    quote = get_object_or_404(Quote, pk=pk)
    if not services.is_panel_quote(quote):
        raise Http404
    path = os.path.join(settings.MEDIA_ROOT, quote.pdf_path) if quote.pdf_path else ""
    if not path or not os.path.exists(path):
        quote.pdf_path = generate_quote_pdf(quote)
        Quote.objects.filter(pk=quote.pk).update(pdf_path=quote.pdf_path)
        path = os.path.join(settings.MEDIA_ROOT, quote.pdf_path)
    return FileResponse(open(path, "rb"), content_type="application/pdf", filename=f"{quote.number}.pdf")


@panel_required
@require_GET
def invoice_file(request, number):
    order = get_object_or_404(Order.objects.select_related("invoice"), number=number)
    if not invoice_available(order):
        raise Http404
    return FileResponse(open(invoice_path(order.invoice), "rb"), content_type="application/pdf", filename=f"{order.number}.pdf")


# --- серверы

@panel_required
def servers(request):
    ctx = page_context(request, "servers")
    items = list(Server.objects.all())
    ctx.update(
        servers=items, form=ServerForm(), total_cost=sum((s.monthly_cost for s in items), ZERO),
        objects={s.pk: {
            "name": s.name, "kind": s.kind, "url": s.url, "purpose": s.purpose, "provider": s.provider,
            "address": s.address, "login_url": s.login_url, "login": s.login,
            "monthly_cost": str(s.monthly_cost), "note": s.note,
        } for s in items},
    )
    return render(request, "panel/servers.html", ctx)


@panel_required
@require_POST
def server_save(request):
    return save_object(request, ServerForm, Server, "panel:servers", "Сервер сохранён")


@panel_required
@require_POST
def server_delete(request, pk):
    get_object_or_404(Server, pk=pk).delete()
    messages.success(request, "Сервер удалён")
    return redirect("panel:servers")


@panel_required
@require_POST
def server_check(request, pk):
    """Живой ли адрес: один запрос с сервера панели, до 6 секунд."""
    server = get_object_or_404(Server, pk=pk)
    if not server.url.startswith(("http://", "https://")):
        return JsonResponse({"ok": False, "error": "У сервера нет ссылки для проверки"}, status=400)
    started = time.monotonic()
    http_request = urllib.request.Request(server.url, method="GET", headers={"User-Agent": "AiDentPanel/1.0"})
    try:
        with urllib.request.urlopen(http_request, timeout=6) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return JsonResponse({"ok": False, "error": str(getattr(error, "reason", error))[:120]})
    # 4xx — сервер отвечает (закрытая страница), 5xx — болен
    return JsonResponse({"ok": status < 500, "status": status, "ms": round((time.monotonic() - started) * 1000)})
