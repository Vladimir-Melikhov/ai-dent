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


def test_comparison_widget_is_compact(catalog):
    from app.ai.tools import comparison_view, active_products

    async def go():
        async with async_session_factory() as session:
            return comparison_view(await active_products(session, [catalog["extaro"], catalog["pico"]]))

    view = run_async(go())
    names_ = [row["name"] for row in view["rows"]]
    # Ключевой параметр (увеличение) виден всегда, даже если у моделей он одинаков; остальные отличия — следом
    assert names_ == ["Увеличение", "Камера", "Вариоскоп"] and view["more"] == [] and view["same"] == []


def test_facts_flatten_groups_and_drop_marketing_lines():
    from app.ai.tools import facts

    chars = {"Оптика": ["Увеличение: 2.8x", "Высокоразрешающая TTL-оптика", "Рабочее расстояние: 360 / 420 мм"],
             "Прочее": {"Вес": "44 г"}}
    assert facts(chars) == {"Увеличение": "2.8x", "Рабочее расстояние": "360 / 420 мм", "Вес": "44 г"}


def make_loupe(product_id, kind="individual", ipds=("54–60 мм", "60–66 мм", "66–74 мм"), distances=("340 мм", "420 мм"),
               zooms=(("2.5×", "130 мм"), ("3.5×", "95 мм"))):
    from .conftest import admin_sql

    admin_sql("UPDATE catalog_product SET is_loupe = true, loupe_kind = $1 WHERE id = $2", kind, product_id)
    for i, value in enumerate(ipds):
        admin_sql("INSERT INTO catalog_loupeipd(product_id, value, sort_order) VALUES ($1, $2, $3)", product_id, value, i)
    for i, d in enumerate(distances):
        admin_sql("INSERT INTO catalog_loupedistance(product_id, distance, sort_order) VALUES ($1, $2, $3)", product_id, d, i)
    for i, (zoom, field) in enumerate(zooms):
        admin_sql("INSERT INTO catalog_loupeoption(product_id, zoom, field_of_view, sort_order) VALUES ($1, $2, $3, $4)",
                  product_id, zoom, field, i)


def test_show_loupe_needs_the_checkbox_and_zooms(catalog):
    with pytest.raises(ToolError, match="нет данных"):
        run_tool("show_loupe", {"product_id": catalog["loupe"]})
    make_loupe(catalog["loupe"])
    output = run_tool("show_loupe", {"product_id": catalog["loupe"]})
    assert output.widget == {"widget": "loupe", "product_ids": [catalog["loupe"]]}
    assert output.result["options"][1] == {"zoom": "3.5×", "field": "95 мм"}


def test_loupe_distances_are_independent_of_zoom_and_ipd_only_for_individual(catalog):
    make_loupe(catalog["loupe"])
    result = run_tool("show_loupe", {"product_id": catalog["loupe"]}).result
    assert result["distances"] == ["340 мм", "420 мм"] and result["interpupillary_distances"] == ["54–60 мм", "60–66 мм", "66–74 мм"]
    assert result["kind"] == "individual"


def test_universal_loupe_has_no_interpupillary_distance(catalog):
    make_loupe(catalog["loupe"], kind="universal")  # даже если варианты остались в базе
    result = run_tool("show_loupe", {"product_id": catalog["loupe"]}).result
    assert result["interpupillary_distances"] == []


def test_comparison_widget_is_compact(catalog):
    from app.ai.tools import comparison_view, active_products

    async def go():
        async with async_session_factory() as session:
            return comparison_view(await active_products(session, [catalog["extaro"], catalog["pico"]]))

    view = run_async(go())
    names_ = [row["name"] for row in view["rows"]]
    # Ключевой параметр (увеличение) виден всегда, даже если у моделей он одинаков; остальные отличия — следом
    assert names_ == ["Увеличение", "Камера", "Вариоскоп"] and view["more"] == [] and view["same"] == []


def test_facts_flatten_groups_and_drop_marketing_lines():
    from app.ai.tools import facts

    chars = {"Оптика": ["Увеличение: 2.8x", "Высокоразрешающая TTL-оптика", "Рабочее расстояние: 360 / 420 мм"],
             "Прочее": {"Вес": "44 г"}}
    assert facts(chars) == {"Увеличение": "2.8x", "Рабочее расстояние": "360 / 420 мм", "Вес": "44 г"}


def test_comparison_marks_cheapest_and_country(catalog):
    from app.ai.tools import comparison_view, active_products

    async def go():
        async with async_session_factory() as session:
            return comparison_view(await active_products(session, [catalog["extaro"], catalog["pico"]]))

    view = run_async(go())
    by_id = {p["id"]: p for p in view["products"]}
    assert by_id[catalog["pico"]]["cheapest"] and not by_id[catalog["extaro"]]["cheapest"]
    assert by_id[catalog["extaro"]]["country"] == "Германия"


def test_comparison_hides_working_distance():
    from types import SimpleNamespace as N

    from app.ai.tools import comparison_view

    def product(i, chars):
        return N(id=i, slug=f"s{i}", name=f"Лупа {i}", brand=N(name="B", country="С"), price_rub=100 * i, stock_qty=1,
                 characteristics=chars)

    view = comparison_view([product(1, {"Оптика": ["Увеличение: 2.8x", "Рабочее расстояние: 360 мм"]}),
                            product(2, {"Оптика": ["Увеличение: 3x"]})])
    text = str(view)
    assert "Рабочее" not in text and "360" not in text


def test_comparison_shortens_names_and_marks_pick():
    from types import SimpleNamespace as N

    from app.ai.tools import comparison_view

    def product(i, name):
        return N(id=i, slug=f"s{i}", name=name, brand=N(name="B", country="Китай"), price_rub=100 * i, stock_qty=1,
                 characteristics={})

    view = comparison_view([product(1, "Бинокулярная лупа TTL XC-View 2.8"), product(2, "Бинокулярные лупы SLT Zumax"),
                            product(3, "Микроскоп")], {"product_id": 2, "label": "Для терапевта"})
    names_ = [p["name"] for p in view["products"]]
    assert names_ == ["TTL XC-View 2.8", "SLT Zumax", "Микроскоп"]
    assert view["title"] == ""  # не все товары — лупы: заголовка нет
    loupes = comparison_view([product(1, "Бинокулярная лупа TTL XC-View 2.8"), product(2, "Бинокулярные лупы SLT Zumax")])
    assert loupes["title"] == "Бинокулярные лупы"
    assert view["products"][0]["full_name"] == "Бинокулярная лупа TTL XC-View 2.8"
    assert [p["pick"] for p in view["products"]] == ["", "Для терапевта", ""]


def test_comparison_pick_must_be_among_shown(catalog):
    ids = [catalog["extaro"], catalog["pico"]]
    ok = run_tool("show_comparison", {"product_ids": ids, "pick": {"product_id": catalog["pico"], "label": "Для терапевта"}})
    assert ok.widget["pick"] == {"product_id": catalog["pico"], "label": "Для терапевта"}
    foreign = run_tool("show_comparison", {"product_ids": ids, "pick": {"product_id": 1, "label": "чужой"}})
    assert foreign.widget["pick"] is None


def test_comparison_shows_badge_from_admin(catalog):
    from app.ai.tools import comparison_view, active_products
    from .conftest import admin_sql

    admin_sql("UPDATE catalog_product SET badge = 'Хит' WHERE id = $1", catalog["extaro"])

    async def go():
        async with async_session_factory() as session:
            return comparison_view(await active_products(session, [catalog["extaro"], catalog["pico"]]))

    by_id = {p["id"]: p for p in run_async(go())["products"]}
    assert (by_id[catalog["extaro"]]["badge"], by_id[catalog["pico"]]["badge"]) == ("Хит", "")


def test_loupe_comparison_takes_fixed_rows_from_loupe_fields():
    from types import SimpleNamespace as N

    from app.ai.tools import comparison_view

    def opt(zoom, fov):
        return N(zoom=zoom, field_of_view=fov)

    def loupe(i, name, options, **fields):
        base = dict(loupe_depth="", loupe_weight="", loupe_tilt="фикс", loupe_vertical="фикс",
                    loupe_optics="галилеевская", loupe_mount="TTL")
        return N(id=i, slug=f"s{i}", name=name, brand=N(name="B", country="С"), price_rub=100 * i, stock_qty=1,
                 characteristics={"Поле обзора": "мусор"}, is_loupe=True, loupe_options=options, **{**base, **fields})

    view = comparison_view([
        loupe(1, "TTL XC-View 2.8", [opt("2.8x", "120–130 мм")], loupe_depth="200 мм", loupe_weight="44 г"),
        loupe(2, "SLF Zumax", [opt("2.5x", "95 мм"), opt("3.0x", "80 мм"), opt("3.5x", "70 мм")],
              loupe_tilt="0–40°", loupe_depth="150 мм", loupe_weight="50 г"),
    ])
    assert view["title"] == "Бинокулярные лупы"
    assert [r["name"] for r in view["rows"]] == ["Увеличение", "Поле обзора", "Глубина фокуса", "Вес"]
    assert view["rows"][1]["values"] == ["120–130 мм", "70–95 мм"]
    more = {r["name"]: r["values"] for r in view["more"]}
    assert more["Угол наклона"] == ["фикс", "0–40°"]
    assert "мусор" not in str(view)
