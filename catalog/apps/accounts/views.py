from django.contrib.auth import login
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from apps.catalog.models import Product
from .models import User
from .otp import (
    OTP_LOCKED,
    OTP_OK,
    generate_and_store_otp,
    ip_limit_exceeded,
    resend_wait_seconds,
    verify_otp,
)
from .sms import send_otp_sms
from django.contrib.auth import logout as auth_logout
from django.shortcuts import redirect
from .models import ShortlistItem
from .utils import get_client_ip, get_owner_filter, normalize_phone, parse_json_body
from .services import merge_guest_data


def _bad_request(message):
    return JsonResponse({"error": message}, status=400)


@require_POST
def logout(request):
    auth_logout(request)
    return redirect("storefront:landing")


@require_POST
def account_delete(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Не авторизован"}, status=403)
    user = request.user
    user.delete()
    auth_logout(request)
    return JsonResponse({"ok": True, "redirect": "/"})


@require_POST
def request_code(request):
    data = parse_json_body(request)
    if data is None:
        return _bad_request("Некорректный запрос")
    phone = normalize_phone(data.get("phone"))
    if not phone:
        return _bad_request("Введите номер в формате +7 999 123 45 67")

    wait = resend_wait_seconds(phone)
    if wait:
        return JsonResponse({"error": f"Повторно запросить код можно через {wait} сек"}, status=429)
    if ip_limit_exceeded(get_client_ip(request)):
        return JsonResponse({"error": "Слишком много запросов. Попробуйте позже"}, status=429)

    code = generate_and_store_otp(phone)
    send_otp_sms(phone, code)
    return JsonResponse({"ok": True})


@require_POST
def verify_code(request):
    data = parse_json_body(request)
    if data is None:
        return _bad_request("Некорректный запрос")
    phone = normalize_phone(data.get("phone"))
    code = data.get("code")
    if not phone or not isinstance(code, str) or not code.strip():
        return _bad_request("Неверный или просроченный код")

    result = verify_otp(phone, code.strip())
    if result == OTP_LOCKED:
        return _bad_request("Слишком много попыток. Запросите новый код")
    if result != OTP_OK:
        return _bad_request("Неверный или просроченный код")

    user, created = User.objects.get_or_create(phone=phone)
    login(request, user)
    merge_guest_data(request, user)
    return JsonResponse({"ok": True, "created": created})

@require_POST
def toggle_shortlist(request):
    data = parse_json_body(request)
    if data is None:
        return _bad_request("Некорректный запрос")
    try:
        product_id = int(data.get("product_id"))
    except (TypeError, ValueError):
        return _bad_request("product_id обязателен")
    if not Product.objects.filter(id=product_id).exists():
        return JsonResponse({"error": "Товар не найден"}, status=404)

    owner = get_owner_filter(request, create=True)
    existing = ShortlistItem.objects.filter(**owner, product_id=product_id).first()

    if existing:
        existing.delete()
        return JsonResponse({"active": False})

    ShortlistItem.objects.create(**owner, product_id=product_id)
    return JsonResponse({"active": True})


def shortlist_list(request):
    owner = get_owner_filter(request)
    if owner is None:
        return JsonResponse({"items": []})
    items = (
        ShortlistItem.objects.filter(**owner)
        .select_related("product", "product__brand")
        .order_by("-created_at")
    )
    result = []
    for it in items:
        p = it.product
        img = p.images.first()
        result.append({
            "product_id": p.id,
            "name": p.name,
            "brand": p.brand.name,
            "price": float(p.price_rub) if p.price_rub is not None else None,
            "image": img.image.url if img else None,
            "slug": p.slug,
        })
    return JsonResponse({"items": result})


def shortlist_ids(request):
    owner = get_owner_filter(request)
    if owner is None:
        return JsonResponse({"product_ids": []})
    ids = list(ShortlistItem.objects.filter(**owner).values_list("product_id", flat=True))
    return JsonResponse({"product_ids": ids})
