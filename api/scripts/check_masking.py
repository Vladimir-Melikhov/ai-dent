"""Маскирование ПДн не задевает ни один артикул и ни одно название каталога.

Без модели и бесплатно. Запуск:
    docker compose run --rm api python scripts/check_masking.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.ai.masking import mask_pii  # noqa: E402
from app.db.engine import async_session_factory, engine  # noqa: E402
from app.db.models import Product  # noqa: E402


async def main() -> int:
    async with async_session_factory() as session:
        rows = (await session.execute(select(Product.id, Product.sku, Product.name))).all()
    await engine.dispose()
    hits = []
    for product_id, sku, name in rows:
        for value in (sku, name):
            if value and mask_pii(value) != value:
                hits.append((product_id, value, mask_pii(value)))
    print(f"Проверено товаров: {len(rows)}")
    for product_id, value, masked in hits:
        print(f"  товар {product_id}: {value!r} → {masked!r}")
    print("Маскирование каталог не задевает" if not hits else f"Задето значений: {len(hits)}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
