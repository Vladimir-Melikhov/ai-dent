"""HTML-письма в стиле КП и счёта (шаблоны в templates/emails/). Текстовая версия письма — прежняя:
её видят почтовые программы без HTML, и по ней проверяют тесты."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

# Бледный зуб справа внизу, как на странице счёта. PNG, а не WebP: WebP почтовые программы не показывают
TOOTH_PATH = "/static/img/email-tooth.png"


def letter(subject: str, text: str, to: list[str], template: str, context: dict) -> EmailMultiAlternatives:
    html = render_to_string(template, {
        "subject": subject, "site_origin": settings.SITE_ORIGIN, "tooth_url": settings.SITE_ORIGIN + TOOTH_PATH,
        **context,
    })
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, to)
    message.attach_alternative(html, "text/html")
    return message


def rows(lines: list[str]) -> list[tuple[str, str]]:
    """Строки «Телефон: +7…» → пары для карточки письма; строка без двоеточия (имя, компания) — без подписи."""
    result = []
    for line in lines:
        label, sep, value = line.partition(": ")
        result.append((label, value) if sep else ("", line))
    return result
