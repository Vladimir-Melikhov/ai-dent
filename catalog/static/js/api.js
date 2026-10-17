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

/* Цена в рублях — та, что попадёт в корзину и счёт; у валютных товаров ниже — исходная (ТЗ §10.7) */
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

/*
 * productId -> {
 *   quantity,
 *   itemId
 * }
 */

const cartState = new Map();


/*
 * Загружаем реальное состояние корзины.
 */

/* Обновить состояние кнопок из корзины на сервере — например, после изменения корзины в чате */
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


/*
 * Получить состояние конкретного товара.
 */

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

/*
 * Когда товара нет:
 *
 *       В корзину
 *
 * Когда товар есть:
 *
 *       −      1      +
 *
 */

function renderCartControl(btn) {
  if (!btn) return;

  const productId = Number(btn.dataset.productId);
  const item = getCartItem(productId);

  const quantity = item ? item.quantity : 0;


  /*
   * Товар ещё не добавлен.
   */

  if (quantity <= 0) {

    btn.classList.remove("is-in-cart");

    btn.innerHTML = "В корзину";

    btn.setAttribute(
      "aria-label",
      "Добавить в корзину"
    );

    return;
  }


  /*
   * Товар уже находится в корзине.
   */

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


/*
 * Обновляем все карточки.
 */

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


  /*
   * Смотрим, есть ли товар уже в корзине
   * на момент построения карточки.
   */

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


  /*
   * Если количество стало 0 —
   * удаляем товар из корзины.
   */

  if (newQuantity <= 0) {

    await postJSON("/cart/remove/", {
      item_id: item.itemId,
    });

    cartState.delete(productId);

    renderCartControl(btn);

    return;
  }


  /*
   * Обычное изменение количества.
   */

  const data = await postJSON(
    "/cart/update/",
    {
      item_id: item.itemId,
      quantity: newQuantity,
    }
  );


  /*
   * Сохраняем новое количество.
   */

  cartState.set(productId, {
    quantity: newQuantity,
    itemId: item.itemId,
  });


  /*
   * Обновляем именно эту кнопку.
   */

  renderCartControl(btn);


  /*
   * Если backend вернул count —
   * обновляем глобальный бейдж.
   */

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

  /*
   * Сначала получаем реальное состояние корзины.
   */

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


          /*
           * Проверяем, по какой части кнопки
           * нажали.
           */

          const actionElement =
            event.target.closest(
              "[data-cart-action]"
            );


          try {

            /*
             * Нажатие на "-".
             */

            if (
              actionElement &&
              actionElement.dataset.cartAction === "minus"
            ) {

              const item =
                getCartItem(productId);


              if (!item) {
                return;
              }


              /*
               * Временно блокируем кнопку,
               * чтобы быстрые клики не отправили
               * несколько конфликтующих запросов.
               */

              btn.disabled = true;


              await changeCartQuantity(
                productId,
                item.quantity - 1,
                btn
              );


              btn.disabled = false;

              return;
            }


            /*
             * Нажатие на "+".
             */

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


            /*
             * Если это обычное "В корзину",
             * добавляем первую единицу.
             */

            const current =
              getCartItem(productId);


            /*
             * Защита от повторного
             * срабатывания во время запроса.
             */

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


              /*
               * После добавления снова читаем корзину,
               * чтобы получить реальный item_id.
               */

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

                /*
                 * Запасной вариант.
                 */

                cartState.set(productId, {
                  quantity: 1,
                  itemId: null,
                });
              }


              /*
               * Сразу превращаем кнопку
               * в счётчик.
               */

              renderCartControl(btn);


              /*
               * Обновляем бейдж корзины.
               */

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


            /*
             * Если что-то пошло не так —
             * синхронизируемся с сервером.
             */

            await loadCartState();
          }

        }
      );

    });
}