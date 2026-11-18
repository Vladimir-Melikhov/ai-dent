// Таблица сравнения в чате: все варианты, которые присылает сервер. Запуск: node --test catalog/tests_js/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

globalThis.window = {};
const { comparisonHTML, rowsHTML, formatValue } = await import("../static/js/chat-compare.js");

const product = (over = {}) => ({
  id: 1, slug: "slf-zumax", name: "SLF Zumax", full_name: "Бинокулярные лупы SLF Zumax", brand: "Zumax", country: "Китай",
  price_rub: 48500, availability: "в наличии", badge: "", image: "products/slf.webp", ...over,
});

const loupes = (over = {}) => ({
  title: "Бинокулярные лупы",
  products: [
    product(),
    product({ id: 2, slug: "slt-zumax", name: "SLT Zumax", price_rub: 57700, badge: "Хит" }),
    product({ id: 3, slug: "ttl", name: "TTL XC-View 2.8", brand: "I.C. Lercher", country: "Германия", price_rub: 160000 }),
  ],
  rows: [
    { name: "Увеличение", values: ["2.5× / 3.0× / 3.5×", "3.0×", "2.8×"] },
    { name: "Поле обзора", values: ["95–130 мм", "—", "120–130 мм"] },
    { name: "Глубина фокуса", values: ["150 мм", "140 мм", "200 мм"] },
    { name: "Вес", values: ["60 г", "50 г", "44 г"] },
  ],
  more: [{ name: "Угол наклона", values: ["0–40°", "—", "фикс"] }],
  same: [{ name: "Оптическая система", value: "галилеевская" }],
  ...over,
});

const count = (html, re) => (html.match(re) || []).length;

test("три лупы: заголовок, колонки товаров, первая строка — цена, единицы как прислал сервер", () => {
  const html = comparisonHTML(loupes());
  assert.match(html, /chat-compare__title">Бинокулярные лупы</);
  assert.match(html, /--n:3/);
  assert.equal(count(html, /class="chat-compare__head"/g), 3);
  const labels = [...html.matchAll(/chat-compare__label[^>]*>([^<]+)</g)].map(m => m[1]);
  assert.deepEqual(labels, ["Цена", "Увеличение", "Поле обзора", "Глубина фокуса", "Вес"]);
  assert.match(html, /48\s500\s₽/); // разряды — неразрывный пробел, как в ценах на сайте
  for (const value of ["2.5× / 3.0× / 3.5×", "95–130 мм", "200 мм", "44 г"]) assert.ok(html.includes(value), value);
  assert.match(html, /href="\/product\/slf-zumax\/"/);
  assert.match(html, /<img src="\/media\/products\/slf.webp"/);
});

test("«Хит» — плашка и серая колонка у своего товара; без метки — серой колонки нет", () => {
  const html = comparisonHTML(loupes());
  assert.match(html, /chat-compare__badge">Хит</);
  assert.match(html, /chat-compare__hl js-row" style="grid-column:3;grid-row:1 \/ span 7"/); // до строки «В корзину»
  const plain = comparisonHTML(loupes({ products: loupes().products.map(p => ({ ...p, badge: "" })) }));
  assert.doesNotMatch(plain, /chat-compare__hl|chat-compare__badge/);
});

test("пустое значение — прочерк, нет цены — «по запросу», не в наличии — «под заказ»", () => {
  const table = loupes();
  table.products[0] = product({ price_rub: null });
  table.products[1] = product({ id: 2, availability: "под заказ" });
  const html = comparisonHTML(table);
  assert.match(html, /<b>по запросу<\/b>/);
  assert.match(html, /<small>под заказ<\/small>/);
  assert.match(html, /class="is-none">—</);
  assert.equal(formatValue(""), "—");
  assert.equal(formatValue(null), "—");
});

test("«Все характеристики»: свёрнуто — только главные строки, раскрыто — отличия и общее у всех", () => {
  const closed = comparisonHTML(loupes());
  assert.match(closed, /chat-compare__more">Все характеристики \(2\)</);
  assert.doesNotMatch(closed, /Угол наклона|Оптическая система/);
  const open = comparisonHTML(loupes(), true);
  assert.match(open, /chat-compare__more">Свернуть</);
  assert.match(open, /Угол наклона/);
  assert.equal(count(open, /галилеевская/g), 3); // общее у всех — в каждой колонке
  const { back } = rowsHTML(loupes(), true);
  assert.match(back, /grid-row:2 \/ span 7/); // рамка растёт вместе со строками
});

test("нет отличий и общего — кнопки нет; старый ответ сервера с «Название: значение» тоже разбирается", () => {
  assert.doesNotMatch(comparisonHTML(loupes({ more: [], same: [] })), /chat-compare__more/);
  const legacy = comparisonHTML(loupes({ more: [], same: ["Вес: 44 г"] }), true);
  assert.match(legacy, /chat-compare__label[^>]*>Вес</);
});

test("два товара, без фото и без ссылки, без заголовка — «Сравнение»", () => {
  const html = comparisonHTML({
    title: "", products: [product({ slug: "", image: null }), product({ id: 2, name: "Pico", image: null })],
    rows: [{ name: "Увеличение", values: ["4–25×"] }], more: [], same: [],
  });
  assert.match(html, /--n:2/);
  assert.match(html, /chat-compare__title">Сравнение</);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /<span class="chat-compare__name">SLF Zumax<\/span>/);
  assert.equal(count(html, /class="chat-compare__val[^"]*"[^>]*><span class="is-none">—/g), 1); // нехватка значения — прочерк
});

test("теги в названиях и значениях не становятся разметкой", () => {
  const html = comparisonHTML(loupes({
    title: "<img src=x onerror=alert(1)>",
    products: [product({ name: "<script>alert(1)</script>", brand: '"><b>', badge: "<i>Хит</i>" }), product({ id: 2 })],
    rows: [{ name: "<u>Вес</u>", values: ["<b>44</b>", "50 г"] }],
  }));
  assert.doesNotMatch(html, /<script|<img src=x|<u>|<i>Хит/);
  assert.match(html, /&lt;script&gt;/);
});

test("под таблицей — «В корзину» у каждого товара с ценой; без цены кнопки нет", () => {
  const table = loupes();
  table.products[2] = product({ id: 3, price_rub: null });
  const html = comparisonHTML(table);
  const buttons = [...html.matchAll(/class="product-card__cart chat-compare__cart" data-product-id="(\d+)"/g)].map(m => m[1]);
  assert.deepEqual(buttons, ["1", "2"]);
  assert.match(html, /chat-compare__buy js-row" style="grid-column:4;grid-row:7"><\/div>/);
});
