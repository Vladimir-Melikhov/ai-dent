"""Маскирование ПДн не задевает каталог и базу знаний (T18).

Клиенты цитируют названия, артикулы, описания и фразы из базы знаний — модель должна получить
их без изменений. Проверяются: артикулы, названия и бренды — ни одной замены; описания товаров
и активная база знаний — ни одного «имени», «номера» и т. п. (кроме строк с адресом и почтой
компании: они и есть адрес и почта). Без модели и бесплатно. Запуск:
    docker compose run --rm api python scripts/check_masking.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.ai.masking import mask_pii, normalize, refresh_vocabulary  # noqa: E402
from app.db.engine import async_session_factory, engine  # noqa: E402
from app.db.models import Brand, KnowledgeBaseVersion, Product, ProductContentBlock  # noqa: E402

COMPANY_CONTACTS = ("@", "ул.", "пр.", "д. ")


def changed(text: str) -> str | None:
    masked = mask_pii(text)
    return masked if masked != normalize(text) else None


async def main() -> int:
    await refresh_vocabulary(force=True)
    async with async_session_factory() as session:
        products = (await session.execute(select(Product.id, Product.sku, Product.name, Product.description))).all()
        brands = list(await session.scalars(select(Brand.name)))
        blocks = (await session.execute(
            select(ProductContentBlock.product_id, ProductContentBlock.title, ProductContentBlock.subtitle,
                   ProductContentBlock.body)
        )).all()
        kb = await session.scalar(select(KnowledgeBaseVersion.content).where(KnowledgeBaseVersion.is_active.is_(True)))
    await engine.dispose()

    hits = []
    for name in brands:
        if changed(name):
            hits.append(("бренд", name, changed(name)))
    for product_id, sku, name, description in products:
        for value in (sku, name):
            if value and changed(value):
                hits.append((f"товар {product_id}", value, changed(value)))
        texts = [description] + [t for b in blocks if b.product_id == product_id for t in (b.title, b.subtitle, b.body)]
        for line in (line for t in texts if t for line in t.splitlines()):
            if line.strip() and changed(line) and not any(c in line for c in COMPANY_CONTACTS):
                hits.append((f"описание товара {product_id}", line, changed(line)))
    kb_lines = [line for line in (kb or "").splitlines() if line.strip()]
    for line in kb_lines:
        if changed(line) and not any(c in line for c in COMPANY_CONTACTS):
            hits.append(("база знаний", line, changed(line)))

    print(f"Проверено: брендов {len(brands)}, товаров {len(products)}, блоков описаний {len(blocks)}, "
          f"строк базы знаний {len(kb_lines)}")
    for where, value, masked in hits:
        print(f"  {where}: {value!r}\n    → {masked!r}")
    print("Маскирование каталог и базу знаний не задевает" if not hits else f"Задето значений: {len(hits)}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
