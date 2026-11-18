// Сравнение товаров в чате — таблица: слева подписи параметров, в колонках товары (название, бренд · страна, фото).
// Товар с меткой из админки («Хит») — серой колонкой с плашкой сверху; пометку ИИ («Для терапевта») не выводим —
// свою рекомендацию он объясняет текстом. Первая строка — цена; остальные отличия и общее у всех — под кнопкой
// «Все характеристики». Последняя строка — кнопки «В корзину»: отдельные карточки к таблице не нужны.
// Разметка собирается строками (их проверяют тесты в catalog/tests_js), DOM — в renderComparison.
import { bindCartButtons, escapeHTML, formatPrice, imageUrl, trimImageMargins } from "./api.js";

// Характеристики в каталоге бывают строками, списками и словарями
export function formatValue(value) {
  if (Array.isArray(value)) return value.map(formatValue).join("; ");
  if (value && typeof value === "object") return Object.entries(value).map(([k, v]) => `${k}: ${formatValue(v)}`).join("; ");
  return value == null || value === "" ? "—" : String(value);
}

function countryLine(brand, country) {
  return `${escapeHTML(brand || "")}${country ? ` · <b>${escapeHTML(country)}</b>` : ""}`;
}

// Общее у всех — строка таблицы с одинаковым значением в каждой колонке
// (старый ответ сервера — строка «Название: значение»)
function sameRows(table) {
  return (table.same || []).map(s => {
    let name = "";
    let value = s;
    if (typeof s === "string") {
      const i = s.indexOf(": ");
      if (i > 0) [name, value] = [s.slice(0, i), s.slice(i + 2)];
    } else ({ name, value } = s);
    return { name, values: table.products.map(() => value) };
  });
}

export function extraRows(table) {
  return [...(table.more || []), ...sameRows(table)];
}

function compareHead(p, i) {
  const href = p.slug ? `/product/${encodeURIComponent(p.slug)}/` : "";
  const link = (cls, inner) => href
    ? `<a class="${cls}" href="${href}" title="${escapeHTML(p.full_name || p.name)}">${inner}</a>`
    : `<span class="${cls}">${inner}</span>`;
  return `<div class="chat-compare__head" style="grid-column:${i + 2};grid-row:1">
    ${p.badge ? `<span class="chat-compare__badge">${escapeHTML(p.badge)}</span>` : ""}
    <div class="chat-compare__info">
      ${link("chat-compare__name", escapeHTML(p.name))}
      <div class="chat-compare__brand">${countryLine(p.brand, p.country)}</div>
    </div>
    ${link("chat-compare__photo", p.image ? `<img src="${escapeHTML(imageUrl(p.image))}" alt="" loading="lazy" decoding="async">` : "")}
  </div>`;
}

export function headsHTML(table) {
  return table.products.map(compareHead).join("");
}

// Строки таблицы: рамка и серая колонка «Хит» (под ячейками) и сами ячейки. expanded — с «Все характеристики»
export function rowsHTML(table, expanded = false) {
  const products = table.products;
  const hit = products.findIndex(p => p.badge);
  const priceRow = {
    name: "Цена",
    cells: products.map(p => `<b>${p.price_rub != null ? formatPrice(p.price_rub) : "по запросу"}</b>`
      + (p.availability && p.availability !== "в наличии" ? `<small>${escapeHTML(p.availability)}</small>` : "")),
  };
  const asRow = r => ({
    name: r.name,
    cells: products.map((_, i) => {
      const text = formatValue(r.values?.[i]);
      return text === "—" ? '<span class="is-none">—</span>' : escapeHTML(text);
    }),
  });
  const rows = [priceRow, ...table.rows.map(asRow), ...(expanded ? extraRows(table).map(asRow) : [])];
  const back = `<div class="chat-compare__box js-row" style="grid-row:2 / span ${rows.length}"></div>`
    + (hit >= 0 ? `<div class="chat-compare__hl js-row" style="grid-column:${hit + 2};grid-row:1 / span ${rows.length + 2}"></div>` : "");
  // Под таблицей — «В корзину» у каждого товара с ценой (кнопку обслуживает api.js, как в карточках)
  const buyRow = rows.length + 2;
  const buy = products.map((p, i) => `<div class="chat-compare__buy js-row" style="grid-column:${i + 2};grid-row:${buyRow}">${
    p.price_rub != null && p.id != null
      ? `<button type="button" class="product-card__cart chat-compare__cart" data-product-id="${Number(p.id)}">В корзину</button>`
      : ""}</div>`).join("");
  const cells = rows.map((r, ri) => {
    const at = `grid-row:${ri + 2}`;
    const top = ri === 0 ? " is-top" : "";
    return `<div class="chat-compare__label js-row${top}" style="${at}">${escapeHTML(r.name)}</div>`
      + r.cells.map((c, ci) => `<div class="chat-compare__val js-row${top}" style="grid-column:${ci + 2};${at}">${c}</div>`).join("");
  }).join("") + buy;
  return { back, cells };
}

export function moreLabel(table, expanded) {
  return expanded ? "Свернуть" : `Все характеристики (${extraRows(table).length})`;
}

// Весь блок строкой — для тестов и для первой отрисовки
export function comparisonHTML(table, expanded = false) {
  const { back, cells } = rowsHTML(table, expanded);
  const extra = extraRows(table).length;
  return `
    <div class="chat-compare__title">${escapeHTML(table.title || "Сравнение")}</div>
    <div class="chat-compare__sub">Наглядное сравнение ключевых характеристик</div>
    <div class="chat-compare__scroll">
      <div class="chat-compare__grid" style="--n:${table.products.length}">${back}${headsHTML(table)}${cells}</div>
    </div>
    ${extra ? `<button type="button" class="chat-compare__more">${moreLabel(table, expanded)}</button>` : ""}`;
}

export function renderComparison(table) {
  const el = document.createElement("div");
  el.className = "chat-widget chat-compare";
  el.innerHTML = comparisonHTML(table);
  const grid = el.querySelector(".chat-compare__grid");
  const button = el.querySelector(".chat-compare__more");
  let expanded = false;

  // Шапки с фото остаются, по кнопке перестраиваются только строки — фото не мигают
  button?.addEventListener("click", () => {
    expanded = !expanded;
    grid.querySelectorAll(".js-row").forEach(node => node.remove());
    const { back, cells } = rowsHTML(table, expanded);
    grid.insertAdjacentHTML("afterbegin", back);
    grid.insertAdjacentHTML("beforeend", cells);
    button.textContent = moreLabel(table, expanded);
    bindCartButtons(); // кнопки «В корзину» перерисованы вместе со строками
  });
  trimImageMargins(el.querySelectorAll(".chat-compare__photo img"));
  return el;
}
