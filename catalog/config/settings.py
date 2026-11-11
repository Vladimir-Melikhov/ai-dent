from pathlib import Path
import os
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
from django.urls import reverse_lazy
from django.templatetags.static import static


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(*names, default=None):
    """Первое непустое значение из переменных окружения (корневой .env — DJANGO_*, catalog/.env — без префикса)."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def env_list(*names):
    return [item.strip() for item in env(*names, default="").split(",") if item.strip()]


SECRET_KEY = env("DJANGO_SECRET_KEY", "SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("Не задан DJANGO_SECRET_KEY (или SECRET_KEY) в окружении")

DEBUG = env("DEBUG", default="False") == "True"

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "ALLOWED_HOSTS")

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# Панель управления бизнесом живёт на своём хосте с неугадываемым именем (как секретный путь админки).
# Пусто — панель выключена. Хост попадает в ALLOWED_HOSTS и CSRF_TRUSTED_ORIGINS сам.
PANEL_HOST = env("PANEL_HOST", default="").lower()
if PANEL_HOST:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, PANEL_HOST]
    CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, f"{'http' if DEBUG else 'https'}://{PANEL_HOST}"]

# Секретный путь к админке. На проде обязателен, локально (DEBUG=True) по умолчанию admin/.
ADMIN_URL = env("DJANGO_ADMIN_URL", "ADMIN_URL", default="admin/" if DEBUG else None)
if not ADMIN_URL:
    raise ImproperlyConfigured("Не задан DJANGO_ADMIN_URL в окружении")
ADMIN_URL = ADMIN_URL.strip("/") + "/"

AUTH_USER_MODEL = "accounts.User"

INSTALLED_APPS = [
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",

    "apps.accounts",
    "apps.catalog",
    "apps.orders",
    "apps.chat",
    "apps.leads",
    "apps.panel",
]

UNFOLD = {
    "STYLES": [
            lambda request: static("css/admin.css"),
        ],
    "SITE_TITLE": "Ai-Dent",
    "SITE_HEADER": "Ai-Dent",
    "SITE_SUBHEADER": "Панель управления",
    "SITE_SYMBOL": "medical_services",

    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "SHOW_BACK_BUTTON": True,
    "SHOW_UI_WARNINGS": False,

    "THEME": "light",
    "BORDER_RADIUS": "10px",

    "COLORS": {
        "base": {
            "50": "#FAFAFA",
            "100": "#F5F5F5",
            "200": "#E5E5E5",
            "300": "#D4D4D4",
            "400": "#A3A3A3",
            "500": "#737373",
            "600": "#525252",
            "700": "#404040",
            "800": "#262626",
            "900": "#171717",
            "950": "#0A0A0A",
        },
        "primary": {
            "50": "#F5F5F5",
            "100": "#E5E5E5",
            "200": "#D4D4D4",
            "300": "#A3A3A3",
            "400": "#737373",
            "500": "#525252",
            "600": "#404040",
            "700": "#333333",
            "800": "#262626",
            "900": "#171717",
            "950": "#0A0A0A",
        },
    },

    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Магазин",
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": "Товары",
                        "icon": "inventory_2",
                        "link": reverse_lazy("admin:catalog_product_changelist"),
                    },
                    {
                        "title": "Категории",
                        "icon": "category",
                        "link": reverse_lazy("admin:catalog_category_changelist"),
                    },
                    {
                        "title": "Бренды",
                        "icon": "branding_watermark",
                        "link": reverse_lazy("admin:catalog_brand_changelist"),
                    },
                    {
                        "title": "Курсы валют",
                        "icon": "currency_exchange",
                        "link": reverse_lazy("admin:catalog_currencyrate_changelist"),
                    },
                ],
            },
            {
                "title": "Заказы",
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": "Все заказы",
                        "icon": "shopping_bag",
                        "link": reverse_lazy("admin:orders_order_changelist"),
                    },
                    {
                        "title": "Счета",
                        "icon": "receipt_long",
                        "link": reverse_lazy("admin:orders_invoice_changelist"),
                    },
                    {
                        "title": "Платежи",
                        "icon": "payments",
                        "link": reverse_lazy("admin:orders_payment_changelist"),
                    },
                ],
            },
            {
                "title": "ИИ-консультант",
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": "Диалоги",
                        "icon": "forum",
                        "link": reverse_lazy("admin:chat_conversation_changelist"),
                    },
                    {
                        "title": "База знаний",
                        "icon": "library_books",
                        "link": reverse_lazy("admin:chat_knowledgebaseversion_changelist"),
                    },
                    {
                        "title": "Коммерческие предложения",
                        "icon": "description",
                        "link": reverse_lazy("admin:chat_quote_changelist"),
                    },
                    {
                        "title": "Расходы ИИ",
                        "icon": "monitoring",
                        "link": reverse_lazy("admin:chat_llmusage_changelist"),
                    },
                ],
            },
            {
                "title": "Пользователи",
                "separator": True,
                "collapsible": False,
                "items": [
                    {
                        "title": "Пользователи",
                        "icon": "group",
                        "link": reverse_lazy("admin:accounts_user_changelist"),
                    },
                    {
                        "title": "Гостевые сессии",
                        "icon": "person_outline",
                        "link": reverse_lazy("admin:accounts_anonsession_changelist"),
                    },
                ],
            },
        ],
    },
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.panel.middleware.PanelHostMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.accounts.middleware.AnonymousSessionMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.chat.context_processors.chat",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "dentai"),
        "USER": os.environ.get("POSTGRES_USER", "dentai"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "dentai"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"

TIME_ZONE = "Europe/Moscow"

USE_I18N = True

USE_TZ = True


STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

SITE_ORIGIN = env("SITE_ORIGIN", default="http://localhost").rstrip("/")

CHAT_WIDGET_ENABLED = env("CHAT_WIDGET_ENABLED", default="False") == "True"
CHAT_TICKET_TTL = 60

API_DB_USER = env("API_DB_USER", default="")
API_DB_PASSWORD = env("API_DB_PASSWORD", default="")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    # HTTPS заканчивается на Caddy; порт catalog наружу закрыт, заголовок ставит только Caddy
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")

EMAIL_HOST = env("EMAIL_HOST", default="smtp.yandex.ru")
EMAIL_PORT = int(env("EMAIL_PORT", default="465"))

EMAIL_USE_TLS = env("EMAIL_USE_TLS", default="False") == "True"
EMAIL_USE_SSL = env("EMAIL_USE_SSL", default="True") == "True"

EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER)

MANAGER_EMAIL = env("MANAGER_EMAIL", default="")
# Рассылка для писем со счётом и о смене статуса, через запятую
NOTIFY_EMAILS = env_list("NOTIFY_EMAILS")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# Ссылки в админку в уведомлениях Telegram: путь админки становится известен Telegram
TELEGRAM_ADMIN_LINKS = env("TELEGRAM_ADMIN_LINKS", default="True") == "True"
# SMS с кодом входа: notificore — 2FA API Notificore; console — код в лог (только локальный стенд).
# На проде без провайдера вход по SMS отвечает «Не удалось отправить SMS»
SMS_PROVIDER = env("SMS_PROVIDER", default="console" if DEBUG else "")
SMS_API_KEY = env("SMS_API_KEY", default="")
SMS_SENDER = env("SMS_SENDER", default="")
SMS_TEMPLATE_ID = env("SMS_TEMPLATE_ID", default="")

# Ссылка на оплату в событии order_confirmed — после подключения провайдера
PAYMENT_LINK_ENABLED = env("PAYMENT_LINK_ENABLED", default="False") == "True"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "default",
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
