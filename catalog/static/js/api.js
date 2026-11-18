const API_BASE = "/api/catalog";


/* ============================================================
   API
   ============================================================ */

async function getJSON(url) {
  const res = await fetch(url);

  if (!res.ok) {
    throw new Error("Ошибка загрузки");
  }

  return res.json();
}


async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCookie("csrftoken"),
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    throw new Error("Ошибка запроса");
  }

  return res.json();
}


function getCookie(name) {
  const match = document.cookie.match(
    new RegExp("(^| )" + name + "=([^;]+)")
  );

  return match ? match[2] : "";
}


function buildQuery(params) {
  const q = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (
      value !== undefined &&
      value !== null &&
      value !== ""
    ) {
      q.set(key, value);
    }
  });

  return q.toString();
}


/* ============================================================
   КАТАЛОГ
   ============================================================ */

export function fetchProducts(params = {}) {
  return getJSON(
    `${API_BASE}/products?${buildQuery(params)}`
  );
}


export function fetchProduct(slug) {
  return getJSON(
    `${API_BASE}/products/${encodeURIComponent(slug)}`
  );
}


export function fetchCategories() {
  return getJSON(`${API_BASE}/categories`);
}


export function fetchBrands() {
  return getJSON(`${API_BASE}/brands`);
}


/* ============================================================
   FORMAT
   ============================================================ */

export function formatPrice(value) {
  return Number(value).toLocaleString("ru-RU") + " ₽";
}

const CURRENCY_SIGN = { USD: "$", EUR: "€" };

/* Цена в рублях, у валютных товаров ниже — исходная */
export function priceHTML(p) {
  const rub = p.price_rub ?? (p.currency && p.currency !== "RUB" ? null : p.price);
  if (rub == null) return '<span class="product-card__price">Цена по запросу</span>';
  let html = `<span class="product-card__price">${formatPrice(rub)}</span>`;
  if (p.currency && p.currency !== "RUB") {
    const sign = CURRENCY_SIGN[p.currency] || p.currency + " ";
    html += `<span class="product-card__price-source">${sign}${Number(p.price).toLocaleString("ru-RU")} · по курсу ЦБ</span>`;
  }
  return html;
}


export function escapeHTML(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}


export function imageUrl(path) {
  return path ? `/media/${path}` : "";
}


/* ============================================================
   СОСТОЯНИЕ КОРЗИНЫ
   ============================================================ */

const cartState = new Map();


export function refreshCartState() {
  return loadCartState();
}

window.refreshCartState = refreshCartState;

async function loadCartState() {
  try {
    const res = await fetch("/cart/");

    if (!res.ok) {
      throw new Error("Не удалось получить корзину");
    }

    const data = await res.json();

    cartState.clear();

    (data.items || []).forEach(item => {
      const productId = Number(item.product_id);

      if (!Number.isNaN(productId)) {
        cartState.set(productId, {
          quantity: Number(item.quantity) || 0,
          itemId: Number(item.id),
        });
      }
    });

    updateAllCartControls();

    return data;

  } catch (error) {
    console.error(
      "Ошибка синхронизации корзины:",
      error
    );

    return null;
  }
}


function getCartItem(productId) {
  return cartState.get(Number(productId)) || null;
}


function getCartQuantity(productId) {
  const item = getCartItem(productId);

  return item ? item.quantity : 0;
}


/* ============================================================
   КНОПКА / СЧЁТЧИК КОРЗИНЫ
   ============================================================ */

function renderCartControl(btn) {
  if (!btn) return;

  const productId = Number(btn.dataset.productId);
  const item = getCartItem(productId);

  const quantity = item ? item.quantity : 0;


  if (quantity <= 0) {

    btn.classList.remove("is-in-cart");

    btn.innerHTML = "В корзину";

    btn.setAttribute(
      "aria-label",
      "Добавить в корзину"
    );

    return;
  }


  btn.classList.add("is-in-cart");

  btn.innerHTML = `
    <span
      class="product-card__cart-minus"
      data-cart-action="minus"
      aria-label="Уменьшить количество"
    >
      −
    </span>

    <span
      class="product-card__cart-quantity"
    >
      ${quantity}
    </span>

    <span
      class="product-card__cart-plus"
      data-cart-action="plus"
      aria-label="Увеличить количество"
    >
      +
    </span>
  `;

  btn.setAttribute(
    "aria-label",
    `Количество в корзине: ${quantity}`
  );
}


function updateAllCartControls() {
  document
    .querySelectorAll(".product-card__cart")
    .forEach(renderCartControl);
}


/* ============================================================
   КАРТОЧКА ТОВАРА
   ============================================================ */

export function productCard(p) {

  const badge =
    p.priority >= 10
      ? '<span class="product-card__badge">Топ продаж</span>'
      : p.priority >= 5
      ? '<span class="product-card__badge">Новинка</span>'
      : "";


  const img = p.primary_image_path
    ? `
      <img
        src="${imageUrl(p.primary_image_path)}"
        alt="${escapeHTML(p.name)}"
        loading="lazy"
        decoding="async"
      >
    `
    : "";


  const stockClass =
    p.stock_qty > 0
      ? "product-card__stock--yes"
      : "product-card__stock--no";


  const stockText =
    p.stock_qty > 0
      ? "В наличии"
      : "Под заказ";


  const cartItem = getCartItem(p.id);
  const quantity = cartItem ? cartItem.quantity : 0;


  let cartButton;


  if (quantity > 0) {

    cartButton = `
      <button
        class="product-card__cart is-in-cart"
        type="button"
        data-product-id="${p.id}"
        aria-label="Количество в корзине: ${quantity}"
      >

        <span
          class="product-card__cart-minus"
          data-cart-action="minus"
          aria-label="Уменьшить количество"
        >
          −
        </span>

        <span
          class="product-card__cart-quantity"
        >
          ${quantity}
        </span>

        <span
          class="product-card__cart-plus"
          data-cart-action="plus"
          aria-label="Увеличить количество"
        >
          +
        </span>

      </button>
    `;

  } else {

    cartButton = `
      <button
        class="product-card__cart"
        type="button"
        data-product-id="${p.id}"
        aria-label="Добавить в корзину"
      >
        В корзину
      </button>
    `;
  }


  return `
    <div
      class="product-card"
      data-product-id="${p.id}"
    >

      <a
        class="product-card__link"
        href="/product/${p.slug}/"
      >

        <div class="product-card__media">

          ${badge}

          <button
            class="product-card__fav"
            type="button"
            data-product-id="${p.id}"
            aria-label="В избранное"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/>
            </svg>
          </button>

          ${img}

        </div>


        <div class="product-card__body">

          <div class="product-card__brand">
            ${escapeHTML(p.brand.name)}
          </div>

          <div class="product-card__name">
            ${escapeHTML(p.name)}
          </div>

          <div class="product-card__footer">

            ${priceHTML(p)}

            <span class="product-card__stock ${stockClass}">
              ${stockText}
            </span>

          </div>

        </div>

      </a>


      ${cartButton}

    </div>
  `;
}


/* ============================================================
   ИЗМЕНЕНИЕ КОЛИЧЕСТВА
   ============================================================ */

async function changeCartQuantity(
  productId,
  newQuantity,
  btn
) {

  const item = getCartItem(productId);

  if (!item) {
    return;
  }


  if (newQuantity <= 0) {

    await postJSON("/cart/remove/", {
      item_id: item.itemId,
    });

    cartState.delete(productId);

    renderCartControl(btn);

    return;
  }


  const data = await postJSON(
    "/cart/update/",
    {
      item_id: item.itemId,
      quantity: newQuantity,
    }
  );


  cartState.set(productId, {
    quantity: newQuantity,
    itemId: item.itemId,
  });


  renderCartControl(btn);


  if (
    data &&
    typeof data.count !== "undefined" &&
    window.updateCartBadge
  ) {
    window.updateCartBadge(data.count);
  }
}


/* ============================================================
   ПРИВЯЗКА КНОПОК
   ============================================================ */

export function bindCartButtons() {

  loadCartState();


  document
    .querySelectorAll(".product-card__cart")
    .forEach(btn => {

      if (btn.dataset.bound) {
        return;
      }

      btn.dataset.bound = "1";


      btn.addEventListener(
        "click",
        async (event) => {

          event.preventDefault();
          event.stopPropagation();


          const productId =
            Number(btn.dataset.productId);


          if (!productId) {
            return;
          }


          const actionElement =
            event.target.closest(
              "[data-cart-action]"
            );


          try {

            if (
              actionElement &&
              actionElement.dataset.cartAction === "minus"
            ) {

              const item =
                getCartItem(productId);


              if (!item) {
                return;
              }


              btn.disabled = true;


              await changeCartQuantity(
                productId,
                item.quantity - 1,
                btn
              );


              btn.disabled = false;

              return;
            }


            if (
              actionElement &&
              actionElement.dataset.cartAction === "plus"
            ) {

              const item =
                getCartItem(productId);


              if (!item) {
                return;
              }


              btn.disabled = true;


              await changeCartQuantity(
                productId,
                item.quantity + 1,
                btn
              );


              btn.disabled = false;

              return;
            }


            const current =
              getCartItem(productId);


            btn.disabled = true;


            if (!current) {

              const data =
                await postJSON(
                  "/cart/add/",
                  {
                    product_id: productId,
                    quantity: 1,
                  }
                );


              // /cart/add/ не возвращает item_id
              const cartResponse =
                await fetch("/cart/");


              const cartData =
                await cartResponse.json();


              const cartItem =
                (cartData.items || []).find(
                  item =>
                    Number(item.product_id) === productId
                );


              if (cartItem) {

                cartState.set(productId, {
                  quantity:
                    Number(cartItem.quantity) || 1,

                  itemId:
                    Number(cartItem.id),
                });

              } else {

                cartState.set(productId, {
                  quantity: 1,
                  itemId: null,
                });
              }


              renderCartControl(btn);


              if (
                data &&
                typeof data.count !== "undefined" &&
                window.updateCartBadge
              ) {
                window.updateCartBadge(data.count);
              }

            }


            btn.disabled = false;

          } catch (error) {

            console.error(
              "Ошибка изменения корзины:",
              error
            );


            btn.disabled = false;


            await loadCartState();
          }

        }
      );

    });
}


/* ============================================================
   ФОТО ТОВАРА: обрезка пустых полей
   У загруженных фото вокруг товара большие прозрачные (или белые) поля —
   в карточке каталога и в сравнении товар выходит мелким. Обрезаем поля
   в браузере; результат кешируется по адресу фото. Пока фото не готово,
   у него нет класса is-ready — стили прячут его, чтобы не прыгало.
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

export function trimImageMargins(images) {
  images.forEach((img) => {
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
