import csv
import datetime
import json
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import User
from apps.orders.models import Order

from . import services
from .access import (
    SESSION_AGE, has_panel_access, login_blocked, login_failed, login_succeeded, panel_required,
)
from .forms import CategoryForm, DealForm, EntryForm, ServerForm
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
        {"key": "expenses", "title": "Расходы", "value": as_float(now["expenses"]), "money": True,
         "delta": services.delta(now["expenses"], before["expenses"]), "invert": True,
         "spark": [r["expenses"] for r in rows], "hint": ""},
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
    qs = Deal.objects.select_related("order", "conversation")
    if origin in Deal.Origin.values:
        qs = qs.filter(origin=origin)
    stages = []
    all_deals = list(qs)
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
        stages=stages, form=DealForm(), origin=origin, origins=Deal.Origin, summary=services.deals_summary(ctx["period"].start, ctx["period"].end),
        objects={d.pk: deal_json(d) for d in all_deals},
    )
    return render(request, "panel/deals.html", ctx)


def admin_link(app_model, pk):
    return f"{settings.SITE_ORIGIN}/{settings.ADMIN_URL}{app_model}/{pk}/change/"


def deal_json(deal):
    links = []
    if deal.order_id:
        links.append(["Заказ в админке", admin_link("orders/order", deal.order_id)])
    if deal.conversation_id:
        links.append(["Диалог в чате", admin_link("chat/conversation", deal.conversation_id)])
    return {
        "locked": list(DealForm.SYSTEM_LOCKED) if deal.is_system else [], "links": links,
        "origin": deal.get_origin_display(),
        "title": deal.title, "client": deal.client, "contact": deal.contact, "amount": str(deal.amount),
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
    if deal.is_system:
        return JsonResponse({"error": "Этап этой карточки следует за заказом"}, status=400)
    if stage not in Deal.Stage.values:
        return JsonResponse({"error": "Неизвестный этап"}, status=400)
    with transaction.atomic():
        deal.stage = stage
        deal.save(update_fields=["stage", "updated_at"])
        services.sync_deal(deal)
    return JsonResponse({"ok": True})


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
    qs = (
        Order.objects.exclude(status=Order.Status.DRAFT)
        .select_related("invoice")
        .filter(created_at__date__gte=period.start, created_at__date__lte=period.end)
    )
    status = request.GET.get("status", "")
    if status in Order.Status.values:
        qs = qs.filter(status=status)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    now = services.totals(period.start, period.end)
    by_status = {r["status"]: r for r in qs.values("status").annotate(n=Count("id"), t=Sum("total_amount"))}
    params = request.GET.copy()
    params.pop("page", None)
    ctx.update(
        page=page, status=status, statuses=Order.Status, totals=now, by_status=by_status,
        pending=Order.objects.filter(status=Order.Status.PENDING_CONFIRMATION).count(),
        querystring=params.urlencode(),
    )
    return render(request, "panel/sales.html", ctx)


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
