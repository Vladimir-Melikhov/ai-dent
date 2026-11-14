"""Оптимизация загружаемых фото: WebP и разумный размер вместо оригиналов в несколько мегабайт."""

import io
import os

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

MAX_SIDE = 1600
QUALITY = 82


def optimize(image_file) -> ContentFile | None:
    """Файл → WebP не больше MAX_SIDE по длинной стороне. None — картинку трогать не нужно или не удалось."""
    try:
        image_file.seek(0)
        img = ImageOps.exif_transpose(Image.open(image_file))
        img.load()
    except Exception:
        return None
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
    buffer = io.BytesIO()
    img.save(buffer, "WEBP", quality=QUALITY, method=6)
    name = os.path.splitext(os.path.basename(image_file.name))[0] + ".webp"
    return ContentFile(buffer.getvalue(), name=name)


def optimize_field(field_file) -> bool:
    """Новая загрузка в ImageField → WebP. True, если файл заменён."""
    if not field_file or getattr(field_file, "_committed", True):
        return False
    result = optimize(field_file.file)
    if result is None:
        return False
    field_file.save(result.name, result, save=False)
    return True
