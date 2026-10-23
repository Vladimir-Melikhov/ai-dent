import { fetchProduct, formatPrice, imageUrl } from "./api.js";

function escapeHTML(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function getImage(path) {
  if (!path) return "";
  return imageUrl(path);
}

function collectImages(product) {
  const list = [];

  const primary = product.primary_image_path || product.image || product.primary_image;
  if (primary) list.push(primary);

  const sources = [
    product.images,
    product.gallery,
    product.media,
    product.photos,
    product.image_list,
  ];

  sources.forEach((src) => {
    if (!src) return;
    if (Array.isArray(src)) {
      src.forEach((item) => {
        const path =
          typeof item === "string"
            ? item
            : item?.path || item?.url || item?.image || item?.src || "";
        if (path && !list.includes(path)) list.push(path);
      });
    }
  });

  return list.filter(Boolean);
}

function splitValue(v) {
  const s = String(v ?? "");
  if (s.includes(",") && s.length > 40) {
    return `<ul class="gb-list">${s
      .split(",")
      .map((x) => `<li>${escapeHTML(x.trim())}</li>`)
      .join("")}</ul>`;
  }
  return escapeHTML(s);
}

function normalizeList(data) {
  if (!data) return [];
  if (typeof data === "string") {
    try {
      data = JSON.parse(data);
    } catch {
      return data.trim() ? [{ label: data }] : [];
    }
  }
  if (Array.isArray(data)) {
    return data
      .map((item) => {
        if (item == null) return null;
        if (typeof item === "string") return { label: item, value: "" };
        return {
          label: item.label ?? item.key ?? item.name ?? item.title ?? item.text ?? "",
          value: item.value ?? item.val ?? item.description ?? "",
        };
      })
      .filter((x) => x && (x.label || x.value));
  }
  if (typeof data === "object") {
    return Object.entries(data).map(([label, value]) => {
      if (Array.isArray(value)) {
        return { label, value: value.join(", ") };
      }
      return { label, value: value == null ? "" : String(value) };
    });
  }
  return [];
}

function blockTypeKey(type) {
  const t = String(type ?? "")
    .toLowerCase()
    .trim();
  if (
    [
      "specs",
      "spec",
      "characteristics",
      "characteristic",
      "характеристики",
      "характеристика",
    ].includes(t)
  ) {
    return "specs";
  }
  if (["features", "feature", "фичи", "фича", "преимущества"].includes(t)) {
    return "features";
  }
  if (["tech", "technology", "технологии", "технология"].includes(t)) {
    return "tech";
  }
  if (["brand", "бренд", "о бренде"].includes(t)) {
    return "brand";
  }
  if (["text", "текст", "описание"].includes(t)) {
    return "text";
  }
  return t || "text";
}

function headHTML(block) {
  if (!block.title && !block.subtitle) return "";
  return `
    <div class="gb-head">
      ${block.subtitle ? `<span class="gb-eyebrow">${escapeHTML(block.subtitle)}</span>` : ""}
      ${block.title ? `<h2 class="gb-title" style="font-style: italic; font-weight: 300; font-size: 2.4rem; letter-spacing: -0.04em; line-height: 1.1;">${escapeHTML(block.title)}</h2>` : ""}
    </div>
  `;
}

function bodyHTML(block) {
  if (!block.body) return "";
  return `<p class="gb-text">${escapeHTML(block.body)}</p>`;
}

function videoHTML(block) {
  if (!block.video_url) return "";
  return `
    <a class="gb-video" href="${escapeHTML(block.video_url)}" target="_blank" rel="noopener">
      <span class="gb-video__ic">▶</span>
      <span>Смотреть видео</span>
    </a>
  `;
}

function imageHTML(block, alt) {
  const img = getImage(block.image);
  if (!img) return "";
  return `
    <div class="gb-two__img">
      <img src="${img}" alt="${escapeHTML(alt || block.title || "")}" loading="lazy">
    </div>
  `;
}

function featsHTML(items) {
  if (!items.length) return "";
  return `
    <div class="gb-feats">
      ${items
        .map(
          (item) => `
        <div class="gb-feat">
          <span class="gb-feat__dot"></span>
          <span class="gb-feat__label">${escapeHTML(item.label || item.value)}</span>
        </div>`
        )
        .join("")}
    </div>
  `;
}

function rowsHTML(items) {
  if (!items.length) return "";
  const hasValues = items.some((r) => r.value);
  if (!hasValues) return featsHTML(items);

  return `
    <div class="gb-rows">
      ${items
        .map(
          (row) => `
        <div class="gb-row">
          <div class="gb-row__key">${escapeHTML(row.label)}</div>
          <div class="gb-row__val">${splitValue(row.value)}</div>
        </div>`
        )
        .join("")}
    </div>
  `;
}

function wrapGlass(inner, extraClass = "") {
  return `
    <div class="gb ${extraClass}">
      <div class="gb__inner">${inner}</div>
    </div>
  `;
}

function renderBlock(block) {
  if (!block) return "";

  const type = blockTypeKey(block.block_type);
  const items = normalizeList(block.data);
  const hasImage = Boolean(getImage(block.image));
  const head = headHTML(block);
  const body = bodyHTML(block);
  const video = videoHTML(block);
  const img = imageHTML(block);

  if (type === "features") {
    const content = `${head}${body}${featsHTML(items)}${video}`;
    if (hasImage) {
      return wrapGlass(`
        <div class="gb-two">
          <div class="gb-two__text">${content}</div>
          ${img}
        </div>
      `);
    }
    return wrapGlass(content);
  }

  if (type === "specs") {
    const content = `${head}${body}${rowsHTML(items)}${video}`;
    if (hasImage) {
      return wrapGlass(`
        <div class="gb-two">
          <div class="gb-two__text">${content}</div>
          ${img}
        </div>
      `);
    }
    return wrapGlass(content);
  }

  if (type === "tech") {
    return wrapGlass(`
      <div class="gb-two">
        <div class="gb-two__text">
          ${head}${body}${video}
        </div>
        ${img}
      </div>
    `);
  }

  if (type === "brand") {
    return wrapGlass(
      `
      <div class="gb-two">
        <div class="gb-two__text">
          ${head}${body}
        </div>
        ${img}
      </div>
    `,
      "gb--brand"
    );
  }

  const content = `${head}${body}${items.length ? rowsHTML(items) : ""}${video}`;
  if (hasImage) {
    return wrapGlass(`
      <div class="gb-two">
        <div class="gb-two__text">${content}</div>
        ${img}
      </div>
    `);
  }
  return wrapGlass(content);
}

function renderContentBlocks(blocks) {
  if (!Array.isArray(blocks) || !blocks.length) return "";
  return blocks.map(renderBlock).filter(Boolean).join("");
}

function renderProductCharacteristics(characteristics) {
  if (!characteristics || typeof characteristics !== "object") return "";

  const groups = Object.entries(characteristics).filter(
    ([, val]) =>
      val != null &&
      (Array.isArray(val) ? val.length > 0 : String(val).trim() !== "")
  );

  if (!groups.length) return "";

  const body = groups
    .map(([title, val]) => {
      const items = Array.isArray(val)
        ? val.map((x) => String(x).trim()).filter(Boolean)
        : [String(val).trim()].filter(Boolean);

      if (!items.length) return "";

      const listItems = items
        .map((item) => `<li>${escapeHTML(item)}</li>`)
        .join("");

      return `
        <div class="gb-char-group">
          <h3 class="gb-char-group__title">${escapeHTML(title)}</h3>
          <ul class="gb-list">${listItems}</ul>
        </div>
      `;
    })
    .filter(Boolean)
    .join("");

  if (!body) return "";

  return wrapGlass(
    `
    <div class="gb-head">
      <span class="gb-eyebrow">Характеристики</span>
      <h2 class="gb-title" style="font-style: italic; font-weight: 500; font-size: 2.1rem; letter-spacing: -0.02em; line-height: 1.15;">Главное о товаре</h2>
    </div>
    <div class="gb-char-groups">
      ${body}
    </div>
  `,
    "gb--chars"
  );
}

function renderProduct(product) {
  const root = document.getElementById("product-root");
  if (!root) return;

  const images = collectImages(product);
  const mainImage = images[0] ? getImage(images[0]) : "";
  const inStock = Number(product.stock_qty) > 0;

  const stock = inStock
    ? `<span class="product-stock">В наличии</span>`
    : `<span class="product-stock product-stock--out">Под заказ</span>`;

  const parts = [];
  const fromChars = renderProductCharacteristics(product.characteristics);
  if (fromChars) parts.push(fromChars);

  const fromBlocks = renderContentBlocks(product.content_blocks);
  if (fromBlocks) parts.push(fromBlocks);

  const blocksHtml = parts.length
    ? `<div class="gb-wrap">${parts.join("")}</div>`
    : "";

  const thumbsHtml =
    images.length > 1
      ? `
      <div class="product-thumbs">
        ${images
          .map(
            (path, i) => `
          <div class="product-thumb ${i === 0 ? "is-active" : ""}" data-src="${getImage(path)}">
            <img src="${getImage(path)}" alt="" loading="lazy">
          </div>`
          )
          .join("")}
      </div>`
      : "";

  const tooth = root.querySelector(".product-page__tooth");

  root.innerHTML = `
    ${
      tooth
        ? tooth.outerHTML
        : `<div class="product-page__tooth"><img src="/static/img/teeth.png" alt=""></div>`
    }

    <section class="product-hero">
      <div class="product-hero__content">
        <div class="product-hero__eyebrow">
          ${escapeHTML(product.brand?.name || "")}
          ·
          ${escapeHTML(product.category?.name || "")}
        </div>

        <h1 class="product-hero__title">
          ${escapeHTML(product.name)}
        </h1>

        ${
          product.description
            ? `<p class="product-hero__description">${escapeHTML(product.description)}</p>`
            : ""
        }

        <div class="product-meta">
          ${product.sku ? `<span>Артикул ${escapeHTML(product.sku)}</span>` : ""}
          <span>${escapeHTML(product.brand?.name || "")}</span>
        </div>

        <div class="product-hero__bottom">
          <div class="product-price">${product.price_rub != null ? formatPrice(product.price_rub) : "Цена по запросу"}</div>
          ${product.currency && product.currency !== "RUB" ? `<div class="product-price-source">${product.currency} ${Number(product.price).toLocaleString("ru-RU")} · по курсу ЦБ</div>` : ""}
          ${stock}
        </div>

        <button
          type="button"
          class="product-buy"
          ${!inStock ? "disabled" : ""}
          data-product-id="${product.id}"
        >
          <span class="product-buy__label">${inStock ? "В корзину" : "Под заказ"}</span>
          <span class="product-buy__arrow">→</span>
        </button>
      </div>

      <div class="product-hero__visual-wrap">
        <div class="product-hero__visual">
          ${
            mainImage
              ? `<img class="product-hero__image" id="product-main-image" src="${mainImage}" alt="${escapeHTML(product.name)}">`
              : ""
          }
        </div>
        ${thumbsHtml}
      </div>
    </section>

    ${blocksHtml}
  `;

  const mainImg = document.getElementById("product-main-image");
  const thumbs = root.querySelectorAll(".product-thumb");

  thumbs.forEach((thumb) => {
    thumb.addEventListener("click", () => {
      const src = thumb.dataset.src;
      if (mainImg && src) {
        mainImg.src = src;
        thumbs.forEach((t) => t.classList.remove("is-active"));
        thumb.classList.add("is-active");
      }
    });
  });

  /* ---- Кнопка "В корзину" ---- */
  const buyBtn = root.querySelector(".product-buy");
  if (buyBtn && inStock) {
    const label = buyBtn.querySelector(".product-buy__label");
    buyBtn.addEventListener("click", async () => {
      if (buyBtn.disabled) return;
      buyBtn.disabled = true;
      try {
        if (window.addToCart) {
          await window.addToCart(Number(buyBtn.dataset.productId), 1);
        }
        if (label) label.textContent = "Добавлено ✓";
        setTimeout(() => {
          if (label) label.textContent = "В корзину";
          buyBtn.disabled = false;
        }, 1400);
      } catch (error) {
        console.error("Ошибка добавления в корзину:", error);
        if (label) label.textContent = "Ошибка, попробуйте ещё";
        setTimeout(() => {
          if (label) label.textContent = "В корзину";
          buyBtn.disabled = false;
        }, 1400);
      }
    });
  }
}

function renderError() {
  const root = document.getElementById("product-root");
  if (!root) return;

  const tooth = root.querySelector(".product-page__tooth");

  root.innerHTML = `
    ${tooth ? tooth.outerHTML : ""}
    <div class="product-empty">
      <h1>Товар не найден</h1>
      <p>Возможно, товар был удалён или больше недоступен.</p>
    </div>
  `;
}

async function loadProduct() {
  const root = document.getElementById("product-root");
  if (!root) return;

  const slug = root.dataset.slug;
  if (!slug) {
    renderError();
    return;
  }

  try {
    const product = await fetchProduct(slug);
    if (!product) {
      renderError();
      return;
    }
    renderProduct(product);
  } catch (error) {
    console.error("Product loading error:", error);
    renderError();
  }
}

loadProduct();