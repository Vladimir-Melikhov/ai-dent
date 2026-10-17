"""Наполнение каталога тестовыми данными: бренды, категории, ~40 товаров
с плейсхолдер-картинками. Идемпотентно — повторный запуск не плодит дубли."""
import random
import textwrap

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from PIL import Image, ImageDraw

from apps.catalog.models import Brand, Category, Product, ProductImage

CATEGORY_SPECS = {
    "Установки": {
        "price": (350_000, 2_100_000),
        "brands": ["KaVo", "Sirona", "A-dec", "Planmeca", "Anthos"],
        "models": ["Primus", "Estetica", "Performer", "Compact", "Intego", "Sirius", "Prime", "Comfort"],
        "chars": lambda: {"Тип": "Стоматологическая установка", "Питание": "220 В", "Гарантия": "24 мес."},
    },
    "Наконечники": {
        "price": (9_000, 65_000),
        "brands": ["NSK", "KaVo", "Bien-Air", "W&H", "Sirona"],
        "models": ["Pana-Max", "Ti-Max", "S-Max", "Synea", "Alegra", "PowerLine", "Contra-Angle", "Airmotor"],
        "chars": lambda: {"Тип": random.choice(["Турбинный", "Микромоторный", "Угловой"]), "Стерилизация": "Автоклав 135°C"},
    },
    "Оптика": {
        "price": (18_000, 2_200_000),
        "brands": ["Zeiss", "Leica", "Heine", "Orascoptic", "Univet"],
        "models": ["OPMI Pico", "M320", "HR 2.5x", "EyeZoom", "Discovery", "ProVantage", "Signature", "Loupe Pro"],
        "chars": lambda: {"Увеличение": random.choice(["2.5x", "3.5x", "5x"]), "Тип": random.choice(["Бинокуляр", "Микроскоп"])},
    },
    "Рентген": {
        "price": (160_000, 850_000),
        "brands": ["Planmeca", "Sirona", "Vatech", "Carestream", "Owandy"],
        "models": ["ProX", "Heliodent", "Green X", "CS 2200", "I-Max", "RVG 6200", "EzSensor", "ScanX"],
        "chars": lambda: {"Тип": random.choice(["Визиограф", "Радиовизиограф", "Дентальный рентген"]), "Разрешение": "20 lp/mm"},
    },
    "Стерилизация": {
        "price": (45_000, 380_000),
        "brands": ["Melag", "W&H", "Euronda", "Tuttnauer", "SciCan"],
        "models": ["Vacuklav", "Lisa", "E9", "Elara", "Statim", "Cliniclave", "Tiny", "Pro"],
        "chars": lambda: {"Класс": random.choice(["B", "S"]), "Объём": random.choice(["18 л", "22 л", "45 л"])},
    },
}


def make_placeholder(text: str) -> bytes:
    size = (640, 640)
    img = Image.new("RGB", size, color=(228, 231, 235))
    draw = ImageDraw.Draw(img)
    lines = textwrap.wrap(text, width=16) or [text]
    y = (size[1] - 28 * len(lines)) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line)
        x = (size[0] - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, fill=(120, 130, 145))
        y += 28
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Command(BaseCommand):
    help = "Наполнить каталог тестовыми данными"

    @transaction.atomic
    def handle(self, *args, **options):
        brand_objs = {}
        cat_sort = 0
        created_products = 0

        for cat_name, spec in CATEGORY_SPECS.items():
            cat_sort += 1
            category, _ = Category.objects.get_or_create(name=cat_name, defaults={"sort_order": cat_sort})

            for brand_name in spec["brands"]:
                if brand_name not in brand_objs:
                    brand_objs[brand_name], _ = Brand.objects.get_or_create(
                        name=brand_name, defaults={"country": random.choice(["Германия", "Япония", "США", "Корея"])}
                    )

            for i in range(8):
                brand = brand_objs[random.choice(spec["brands"])]
                model = random.choice(spec["models"])
                name = f"{brand.name} {model} {random.randint(100, 999)}"

                if Product.objects.filter(name=name).exists():
                    continue

                price = round(random.uniform(*spec["price"]), -2)
                product = Product.objects.create(
                    name=name,
                    brand=brand,
                    category=category,
                    price=price,
                    stock_qty=random.choice([0, 1, 2, 3, 5, 10]),
                    description=f"{cat_name}: {brand.name} {model}. Профессиональное стоматологическое оборудование.",
                    characteristics=spec["chars"](),
                    priority=random.choice([0, 0, 0, 5, 10]),
                    is_active=True,
                )
                img_bytes = make_placeholder(product.slug)
                ProductImage.objects.create(
                    product=product,
                    image=ContentFile(img_bytes, name=f"{product.slug}.png"),
                    is_primary=True,
                    sort_order=0,
                )
                created_products += 1

        self.stdout.write(self.style.SUCCESS(f"Создано товаров: {created_products}"))
