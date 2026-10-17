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

    // сайдбар из того же списка
    buildSidebarCategories(categories);

    // URL-параметры
    const url = new URLSearchParams(window.location.search);

    if (url.get("q") && getElement("f-q")) {
      getElement("f-q").value = url.get("q");
    }

    if (url.get("category") && cSel) {
      cSel.value = url.get("category");
    }

    updateCatalogTitle();
    syncSidebarFromSelect();

    // события
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