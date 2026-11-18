import {
  fetchProducts,
  fetchCategories,
  fetchBrands,
  productCard,
  bindCartButtons,
  escapeHTML
} from "./api.js";

import { initShortlist } from "./shortlist.js";

const state = { page: 1 };
const categoryNames = new Map();


/* ============================================================
   ФОТО В КАРТОЧКАХ: обрезка пустых полей
   У загруженных фото вокруг товара большие прозрачные (или белые) поля —
   товар в карточке выходит мелким. Обрезаем поля в браузере, чтобы товар
   был крупным и начинался от верхнего края карточки. Результат — по адресу фото.
   ============================================================ */

const trimmedSrc = new Map();

/* Границы товара на фото в пикселях оригинала; null — полей нет или не удалось */
function contentBox(img) {
  const nw = img.naturalWidth;
  const nh = img.naturalHeight;
  if (!nw || !nh) return null;

  const k = Math.min(1, 240 / Math.max(nw, nh));
  const w = Math.max(1, Math.round(nw * k));
  const h = Math.max(1, Math.round(nh * k));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, w, h);
  const d = ctx.getImageData(0, 0, w, h).data;

  // Фон — прозрачный, если прозрачен левый верхний угол, иначе цвет этого угла (белый фон у JPG)
  const clearBg = d[3] < 16;
  const [r0, g0, b0] = d;
  const isEmpty = (i) =>
    d[i + 3] < 16 ||
    (!clearBg && Math.abs(d[i] - r0) + Math.abs(d[i + 1] - g0) + Math.abs(d[i + 2] - b0) < 30);

  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (isEmpty((y * w + x) * 4)) continue;
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
    }
  }
  if (x1 < 0) return null;

  // запас в пиксель уменьшенной копии, чтобы не срезать край товара
  const sx = Math.max(0, (x0 - 1) / k);
  const sy = Math.max(0, (y0 - 1) / k);
  const sw = Math.min(nw, (x1 + 2) / k) - sx;
  const sh = Math.min(nh, (y1 + 2) / k) - sy;
  if (sw > nw * 0.96 && sh > nh * 0.96) return null;
  return { sx, sy, sw, sh };
}

async function trimmedUrl(img) {
  const box = contentBox(img);
  if (!box) return null;

  const k = Math.min(1, 600 / Math.max(box.sw, box.sh));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(box.sw * k));
  canvas.height = Math.max(1, Math.round(box.sh * k));
  canvas
    .getContext("2d")
    .drawImage(img, box.sx, box.sy, box.sw, box.sh, 0, 0, canvas.width, canvas.height);

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/webp", 0.9));
  return blob ? URL.createObjectURL(blob) : null;
}

function trimCardImages(root) {
  root.querySelectorAll(".product-card__media img").forEach((img) => {
    const show = () => img.classList.add("is-ready");

    const run = () => {
      const src = img.currentSrc || img.src;
      if (!trimmedSrc.has(src)) {
        trimmedSrc.set(src, trimmedUrl(img).catch(() => null));
      }
      trimmedSrc.get(src).then((url) => {
        if (!url) return show();
        img.addEventListener("load", show, { once: true });
        img.addEventListener("error", show, { once: true });
        img.src = url;
      });
    };

    if (img.complete && img.naturalWidth) run();
    else {
      img.addEventListener("load", run, { once: true });
      img.addEventListener("error", show, { once: true });
    }
  });
}

function getElement(id) {
  return document.getElementById(id);
}

/* Нормализация ответа API (массив или { results / items / categories }) */
function normalizeList(data) {
  if (Array.isArray(data)) return data;
  if (!data || typeof data !== "object") return [];
  return data.results || data.items || data.categories || data.brands || [];
}

function readFilters() {
  return {
    q: getElement("f-q")?.value.trim() || "",
    category: getElement("f-category")?.value || "",
    brand: getElement("f-brand")?.value || "",
    price_min: getElement("f-price-min")?.value || "",
    price_max: getElement("f-price-max")?.value || "",
    in_stock: getElement("f-instock")?.checked ? "true" : "",
    sort: getElement("f-sort")?.value || "priority",
    page: state.page,
  };
}

function updateCatalogTitle() {
  const sel = getElement("f-category");
  const title = getElement("c-title");
  if (!sel || !title) return;

  const slug = sel.value.trim();
  if (!slug) {
    title.textContent = "Оборудование";
    return;
  }

  const name = categoryNames.get(slug);
  if (name) {
    title.textContent = name;
    return;
  }

  const opt = sel.options[sel.selectedIndex];
  title.textContent = opt?.textContent?.trim() || "Оборудование";
}

function setActiveCategory(slug) {
  document.querySelectorAll(".catalog-category").forEach((link) => {
    const isActive = (link.dataset.category || "") === (slug || "");
    link.classList.toggle("catalog-category--active", isActive);
  });
}

function syncSidebarFromSelect() {
  const sel = getElement("f-category");
  if (!sel) return;
  setActiveCategory(sel.value);
}

async function load() {
  const grid = getElement("c-products");
  const count = getElement("c-count");
  const pager = getElement("c-pager");

  if (!grid) return;

  try {
    const data = await fetchProducts(readFilters());

    grid.innerHTML = data.items?.length
      ? data.items.map(productCard).join("")
      : '<p class="empty">Ничего не найдено</p>';

    trimCardImages(grid);

    if (count) count.textContent = data.total ?? 0;

    if (pager) {
      pager.innerHTML = "";

      if (data.total_pages > 1) {
        for (let i = 1; i <= data.total_pages; i++) {
          const b = document.createElement("button");
          b.className = i === data.page ? "btn--primary" : "btn--ghost";
          b.textContent = i;
          b.addEventListener("click", () => {
            state.page = i;
            load();
            window.scrollTo({ top: 0, behavior: "smooth" });
          });
          pager.appendChild(b);
        }
      }
    }

    initShortlist();
    bindCartButtons();
  } catch (error) {
    console.error("Ошибка загрузки каталога:", error);
    grid.innerHTML = '<p class="empty">Ошибка загрузки</p>';
  }
}

function resetAndLoad() {
  state.page = 1;
  load();
}

function buildSidebarCategories(categories) {
  const nav = getElement("catalog-categories");

  if (!nav) {
    console.warn("[catalog] #catalog-categories не найден в DOM");
    return;
  }

  const list = normalizeList(categories);

  nav.innerHTML = `
    <a href="#" class="catalog-category catalog-category--active" data-category="">
      <span>Все оборудование</span>
      <strong></strong>
    </a>
  `;

  list.forEach((c) => {
    const slug = c.slug ?? c.id ?? "";
    const name = c.name ?? c.title ?? "";

    if (!name && !slug) return;

    const a = document.createElement("a");
    a.href = "#";
    a.className = "catalog-category";
    a.dataset.category = String(slug);
    a.innerHTML = `
      <span>${escapeHTML(name || slug)}</span>
      <strong></strong>
    `;
    nav.appendChild(a);
  });

  nav.querySelectorAll(".catalog-category").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const slug = link.dataset.category || "";
      const sel = getElement("f-category");
      if (sel) {
        sel.value = slug;
        updateCatalogTitle();
        setActiveCategory(slug);
        resetAndLoad();
      }
    });
  });
}

async function initFilters() {
  try {
    const [categoriesRaw, brandsRaw] = await Promise.all([
      fetchCategories(),
      fetchBrands(),
    ]);

    const categories = normalizeList(categoriesRaw);
    const brands = normalizeList(brandsRaw);

    const cSel = getElement("f-category");
    const bSel = getElement("f-brand");

    if (cSel) {
      categories.forEach((c) => {
        const slug = c.slug ?? c.id ?? "";
        const name = c.name ?? c.title ?? String(slug);

        categoryNames.set(String(slug), String(name));

        const o = document.createElement("option");
        o.value = slug;
        o.textContent = name;
        cSel.appendChild(o);
      });
    }

    if (bSel) {
      brands.forEach((b) => {
        const slug = b.slug ?? b.id ?? "";
        const name = b.name ?? b.title ?? String(slug);

        const o = document.createElement("option");
        o.value = slug;
        o.textContent = name;
        bSel.appendChild(o);
      });
    }

    buildSidebarCategories(categories);

    const url = new URLSearchParams(window.location.search);

    if (url.get("q") && getElement("f-q")) {
      getElement("f-q").value = url.get("q");
    }

    if (url.get("category") && cSel) {
      cSel.value = url.get("category");
    }

    updateCatalogTitle();
    syncSidebarFromSelect();

    if (cSel) {
      cSel.addEventListener("change", () => {
        updateCatalogTitle();
        syncSidebarFromSelect();
        resetAndLoad();
      });
    }

    if (bSel) {
      bSel.addEventListener("change", resetAndLoad);
    }

    if (getElement("f-sort")) {
      getElement("f-sort").addEventListener("change", resetAndLoad);
    }

    ["f-q", "f-price-min", "f-price-max"].forEach((id) => {
      const el = getElement(id);
      if (!el) return;
      let t;
      el.addEventListener("input", () => {
        clearTimeout(t);
        t = setTimeout(resetAndLoad, 400);
      });
    });

    if (getElement("f-instock")) {
      getElement("f-instock").addEventListener("change", resetAndLoad);
    }

    const searchBtn = getElement("catalog-search-submit");
    if (searchBtn) {
      searchBtn.addEventListener("click", resetAndLoad);
    }

    const searchInput = getElement("f-q");
    if (searchInput) {
      searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          resetAndLoad();
        }
      });
    }

    const form = getElement("catalog-search-form");
    if (form) {
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        resetAndLoad();
      });
    }

    load();
  } catch (error) {
    console.error("Ошибка инициализации каталога:", error);
  }
}

initFilters();