"""Инструменты каталога — напрямую, без модели."""

import pytest

from app.ai.tools import ToolContext, ToolError, registry, validate
from app.db.engine import async_session_factory

from .conftest import run_async


def run_tool(name, payload):
    async def go():
        async with async_session_factory() as session:
            tool = registry()[name]
            return await tool.run(ToolContext(None, session), validate(tool, payload))

    return run_async(go())


def names(result):
    return [p["name"].rsplit(" ", 1)[0] for p in result["products"]]


def test_search_finds_word_forms_and_skips_inactive(catalog):
    found = run_tool("search_products", {"query": f"микроскопы {catalog['tag']}"}).result
    assert set(names(found)) == {"Микроскоп EXTARO 300", "Микроскоп OPMI Pico", "Микроскоп импортный"}


def test_search_filters_by_price_in_rubles_and_stock(catalog):
    tag = catalog["tag"]
    cheap = run_tool("search_products", {"query": tag, "price_max": 100000}).result
    assert set(names(cheap)) == {"Лупа Zumax SLF", "Микроскоп импортный"}
    in_stock = run_tool("search_products", {"query": f"микроскоп {tag}", "in_stock_only": True,
                                            "sort": "price_desc"}).result
    assert names(in_stock) == ["Микроскоп EXTARO 300", "Микроскоп импортный"]


def test_search_by_brand_and_category_slug(catalog):
    tag = catalog["tag"]
    result = run_tool("search_products", {"brand": f"zeiss-{tag}", "category": f"Микроскопы {tag}", "limit": 10}).result
    assert len(result["products"]) == 3


def test_search_result_has_rub_price_and_availability(catalog):
    result = run_tool("search_products", {"query": f"импортный {catalog['tag']}"}).result
    product = result["products"][0]
    assert product["price_rub"] == 95000.0
    assert product["catalog_price"] == {"amount": 1000.0, "currency": "USD"}
    pico = run_tool("search_products", {"query": f"pico {catalog['tag']}"}).result["products"][0]
    assert pico["availability"] == "под заказ"


def test_loose_search_when_no_product_has_all_words(catalog):
    result = run_tool("search_products", {"query": f"экстаро {catalog['tag']}"}).result
    assert result["note"].startswith("точных совпадений нет")


def test_limits_are_checked_by_pydantic_not_schema():
    tool = registry()["search_products"]
    assert "maximum" not in str(tool.schema) and "maxItems" not in str(registry()["show_products"].schema)
    with pytest.raises(ToolError, match="limit"):
        validate(tool, {"limit": 11})
    with pytest.raises(ToolError):
        validate(registry()["show_products"], {"product_ids": [1, 2, 3, 4, 5]})
    with pytest.raises(ToolError):
        validate(tool, {"unexpected": 1})


def test_get_product_and_unknown_id(catalog):
    product = run_tool("get_product", {"product_id": catalog["extaro"]}).result
    assert product["characteristics"]["Камера"] == "Full HD" and product["price_rub"] == 2924329.0
    with pytest.raises(ToolError):
        run_tool("get_product", {"product_id": catalog["old"]})


def test_show_products_widget_keeps_only_ids(catalog):
    output = run_tool("show_products", {"product_ids": [catalog["extaro"], catalog["old"], catalog["loupe"]]})
    assert output.widget == {"widget": "product_cards", "product_ids": [catalog["extaro"], catalog["loupe"]]}
    assert output.result["not_found"] == [catalog["old"]]


def test_comparison_table_from_characteristics(catalog):
    output = run_tool("show_comparison", {"product_ids": [catalog["extaro"], catalog["pico"]]})
    rows = {row["name"]: row["values"] for row in output.result["rows"]}
    assert rows == {"Увеличение": ["4–25×", "4–25×"], "Камера": ["Full HD", "—"], "Вариоскоп": ["—", "опция"]}
    assert all(p["slug"] for p in output.result["products"])  # название в таблице ведёт на страницу товара
    with pytest.raises(ToolError):
        run_tool("show_comparison", {"product_ids": [catalog["extaro"], catalog["old"]]})


def test_catalog_overview(catalog):
    overview = run_tool("catalog_overview", {}).result
    category = next(c for c in overview["categories"] if c["slug"] == f"mikroskopy-{catalog['tag']}")
    assert category["products"] == 3 and category["price_rub_from"] == 95000.0
    brand = next(b for b in overview["brands"] if b["slug"] == f"zeiss-{catalog['tag']}")
    assert brand["country"] == "Германия"
