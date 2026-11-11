from django.conf import settings

PANEL_URLCONF = "apps.panel.host_urls"

# Скрипты и стили — только свои: панель не грузит ничего со сторонних адресов
PANEL_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
)


class PanelHostMiddleware:
    """Панель живёт на своём хосте PANEL_HOST и больше нигде.

    На этом хосте работают только адреса панели, на основном — только сайт
    (панели в его urlconf нет). PANEL_HOST пуст — панель выключена."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0].lower()
        is_panel = bool(settings.PANEL_HOST) and host == settings.PANEL_HOST
        if is_panel:
            request.urlconf = PANEL_URLCONF
        request.is_panel = is_panel

        response = self.get_response(request)

        if is_panel:
            response["Cache-Control"] = "no-store"
            response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            response["Content-Security-Policy"] = PANEL_CSP
            response["Referrer-Policy"] = "same-origin"
        return response
