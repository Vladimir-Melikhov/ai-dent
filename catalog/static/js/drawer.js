function getCookie(name) {
    const m = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
    return m ? m[2] : "";
  }
  
  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken"),
      },
      body: JSON.stringify(body),
    });
    return res.json();
  }
  
  function fmt(v) { return v == null ? "цена по запросу" : Number(v).toLocaleString("ru-RU") + " ₽"; }
  function escapeHTML(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
  function img(path) { return path ? (path.startsWith("/") ? path : `/media/${path}`) : ""; }
  
  const drawer = document.getElementById("drawer");
  const paneCart = document.getElementById("pane-cart");
  const paneFav = document.getElementById("pane-fav");
  const paneOrders = document.getElementById("pane-orders");
  const foot = document.getElementById("drawer-foot");
  const tabOrders = document.getElementById("tab-orders");
  const accountDelete = document.getElementById("account-delete");

  function open() { drawer.hidden = false; loadCart(); }
  function close() { drawer.hidden = true; }
  
  document.getElementById("cart-open").addEventListener("click", open);
  document.getElementById("drawer-x").addEventListener("click", close);
  document.getElementById("drawer-close").addEventListener("click", close);
  
  document.getElementById("tab-cart").addEventListener("click", () => switchTab("cart"));
  document.getElementById("tab-fav").addEventListener("click", () => switchTab("fav"));
  if (tabOrders) tabOrders.addEventListener("click", () => switchTab("orders"));
  
  function switchTab(tab) {
    document.querySelectorAll(".drawer__tab").forEach(t => t.classList.toggle("is-active", t.dataset.tab === tab));
    paneCart.hidden = tab !== "cart";
    paneFav.hidden = tab !== "fav";
    if (paneOrders) paneOrders.hidden = tab !== "orders";
    foot.hidden = tab !== "cart";
    if (tab === "cart") loadCart();
    else if (tab === "orders") loadOrders();
    else loadFav();
  }
  
  async function loadCart() {
    const res = await fetch("/cart/");
    const data = await res.json();
    updateBadge(data.count);
    if (!data.items.length) {
      paneCart.innerHTML = '<p class="drawer__empty">Корзина пуста</p>';
      foot.hidden = true;
      return;
    }
    foot.hidden = false;
    paneCart.innerHTML = data.items.map(it => `
      <div class="drawer-item" data-id="${it.id}">
        <div class="drawer-item__img">${it.image ? `<img src="${img(it.image)}" alt="">` : ""}</div>
        <div class="drawer-item__info">
          <div class="drawer-item__brand">${escapeHTML(it.brand)}</div>
          <div class="drawer-item__name">${escapeHTML(it.name)}</div>
          <div class="drawer-item__price">${fmt(it.price)}</div>
        </div>
        <div class="drawer-item__controls">
          <button class="qty-btn" data-act="dec" type="button">−</button>
          <span class="qty">${it.quantity}</span>
          <button class="qty-btn" data-act="inc" type="button">+</button>
          <button class="drawer-item__del" data-act="del" type="button">✕</button>
        </div>
      </div>`).join("");
    document.getElementById("cart-total").textContent = fmt(data.total);
    bindCartControls();
  }
  
  function bindCartControls() {
    paneCart.querySelectorAll(".drawer-item").forEach(row => {
      const id = Number(row.dataset.id);
      const qty = () => Number(row.querySelector(".qty").textContent);
      row.querySelector('[data-act="inc"]').onclick = async () => {
        await post("/cart/update/", { item_id: id, quantity: qty() + 1 }); loadCart();
      };
      row.querySelector('[data-act="dec"]').onclick = async () => {
        await post("/cart/update/", { item_id: id, quantity: qty() - 1 }); loadCart();
      };
      row.querySelector('[data-act="del"]').onclick = async () => {
        await post("/cart/remove/", { item_id: id }); loadCart();
      };
    });
  }
  
  async function loadFav() {
    const res = await fetch("/accounts/shortlist/");
    const data = await res.json();
    if (!data.items.length) {
      paneFav.innerHTML = '<p class="drawer__empty">Избранное пусто</p>';
      return;
    }
    paneFav.innerHTML = data.items.map(it => `
      <div class="drawer-item drawer-item--fav" data-product-id="${it.product_id}">
        <div class="drawer-item__img">${it.image ? `<img src="${img(it.image)}" alt="">` : ""}</div>
        <div class="drawer-item__info">
          <div class="drawer-item__brand">${escapeHTML(it.brand)}</div>
          <a class="drawer-item__name" href="/product/${encodeURIComponent(it.slug)}/">${escapeHTML(it.name)}</a>
          <div class="drawer-item__price">${fmt(it.price)}</div>
        </div>
        <div class="drawer-item__actions">
          <button class="drawer-item__remove-fav" type="button" data-pid="${it.product_id}">Удалить</button>
          <button class="drawer-item__tocart" type="button" data-pid="${it.product_id}">В корзину</button>
        </div>
      </div>`).join("");
  
    paneFav.querySelectorAll(".drawer-item__remove-fav").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.preventDefault(); e.stopPropagation();
        await post("/accounts/shortlist/toggle/", { product_id: Number(btn.dataset.pid) });
        const item = btn.closest(".drawer-item");
        if (item) item.remove();
        if (!paneFav.querySelector(".drawer-item")) {
          paneFav.innerHTML = '<p class="drawer__empty">Избранное пусто</p>';
        }
      });
    });
  
    paneFav.querySelectorAll(".drawer-item__tocart").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.preventDefault(); e.stopPropagation();
        await post("/cart/add/", { product_id: Number(btn.dataset.pid), quantity: 1 });
        switchTab("cart");
      });
    });
  }
  
  async function loadOrders() {
    if (!paneOrders) return;
    const res = await fetch("/cart/orders/");
    const data = await res.json();
    if (!data.items.length) {
      paneOrders.innerHTML = '<p class="drawer__empty">Заказов пока нет</p>';
      return;
    }
    paneOrders.innerHTML = data.items.map(o => `
      <div class="drawer-order">
        <div class="drawer-order__row">
          <span class="drawer-order__number">${escapeHTML(o.number)}</span>
          <span class="drawer-order__date">${o.created_at}</span>
        </div>
        <div class="drawer-order__row">
          <span class="drawer-order__status">${escapeHTML(o.status)}</span>
          <span class="drawer-order__total">${fmt(o.total)}</span>
        </div>
        <div class="drawer-order__row drawer-order__meta">
          <span>${o.items_count} товар(ов)</span>
          ${o.invoice_url ? `<a class="drawer-order__invoice" href="${o.invoice_url}" target="_blank" rel="noopener">Скачать счёт</a>` : ""}
        </div>
      </div>`).join("");
  }

  function updateBadge(count) {
    const badge = document.getElementById("cart-badge");
    if (count > 0) { badge.textContent = count; badge.hidden = false; }
    else badge.hidden = true;
  }

  window.updateCartBadge = updateBadge;
  
  window.addToCart = async function (productId, quantity = 1) {
    const data = await post("/cart/add/", { product_id: productId, quantity });
    updateBadge(data.count);
  };
  
  (async () => {
    const res = await fetch("/cart/count/");
    const data = await res.json();
    updateBadge(data.count);
  })();

  // Корзину изменил консультант в чате
  document.addEventListener("cart:changed", () => {
    if (!drawer.hidden && !paneCart.hidden) loadCart();
  });

  document.getElementById("cart-checkout").addEventListener("click", () => {
    window.location.href = "/checkout/";
  });

  if (accountDelete) {
    accountDelete.addEventListener("click", async () => {
      if (!confirm("Удалить аккаунт? Это действие необратимо.")) return;
      const data = await post("/accounts/delete/", {});
      if (data.ok) window.location.href = data.redirect || "/";
    });
  }