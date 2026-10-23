/* ============================================================
   CHECKOUT — страница оформления заказа (/checkout/)
   ============================================================ */

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
  
  function fmt(v) {
    return v == null ? "цена по запросу" : Number(v).toLocaleString("ru-RU") + " ₽";
  }
  
  function img(path) {
    return path ? (path.startsWith("/") ? path : `/media/${path}`) : "";
  }
  
  function el(id) {
    return document.getElementById(id);
  }

  function escapeHTML(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  /* ---- маска телефона: +7 подставляется сам, ввод только с девятки ---- */
  function formatPhoneDigits(digits) {
    if (!digits.length) return "+7 ";
    let out = "+7 " + digits.slice(0, 3);
    if (digits.length > 3) out += " " + digits.slice(3, 6);
    if (digits.length > 6) out += " " + digits.slice(6, 8);
    if (digits.length > 8) out += " " + digits.slice(8, 10);
    return out;
  }
  function extractPhoneDigits(raw) {
    let d = String(raw || "").replace(/\D/g, "");
    if (d.startsWith("7")) d = d.slice(1);
    if (d.startsWith("7") || d.startsWith("8")) d = d.slice(1);
    return d.slice(0, 10);
  }
  function attachPhoneMask(input) {
    if (!input) return;
    input.addEventListener("input", () => {
      input.value = formatPhoneDigits(extractPhoneDigits(input.value));
      const pos = input.value.length;
      input.setSelectionRange(pos, pos);
    });
    if (!input.value) input.value = "+7 ";
  }
  function normalizedPhone(input) {
    if (!input) return "";
    const digits = extractPhoneDigits(input.value);
    return digits.length === 10 ? "+7" + digits : "";
  }

  /* ---- стили строк состава заказа ---- */
  (function injectStyles() {
    if (document.getElementById("co-inline-styles")) return;
    const s = document.createElement("style");
    s.id = "co-inline-styles";
    s.textContent = `
      #co-items .checkout-summary__item{
        display:flex;gap:14px;align-items:center;
        padding:14px 0;border-bottom:1px solid rgba(180,200,218,.4);
      }
      #co-items .checkout-summary__item:last-child{border-bottom:none;}
      #co-items .checkout-summary__thumb{
        flex:0 0 auto;width:56px;height:56px;border-radius:12px;
        background:rgba(230,238,247,.7);overflow:hidden;display:grid;place-items:center;
      }
      #co-items .checkout-summary__thumb img{width:100%;height:100%;object-fit:contain;}
      #co-items .checkout-summary__meta{flex:1 1 auto;min-width:0;}
      #co-items .checkout-summary__brand{font-size:12px;color:#6b7a89;}
      #co-items .checkout-summary__name{
        font-weight:600;color:#17222c;font-size:14px;line-height:1.35;
      }
      #co-items .checkout-summary__qty{font-size:13px;color:#6b7a89;margin-top:2px;}
      #co-items .checkout-summary__sum{
        flex:0 0 auto;font-weight:700;color:#17222c;font-size:14px;white-space:nowrap;
      }
      #co-items .checkout-summary__empty{color:#6b7a89;padding:18px 0;}
    `;
    document.head.appendChild(s);
  })();
  
  /* ---- элементы страницы ---- */
  const itemsBox = el("co-items");
  const totalBox = el("co-total");
  const submitBtn = el("co-submit");
  const errorBox = el("co-error");
  const formBox = el("checkout-form");
  const successBox = el("checkout-success");
  let cartVersion = "";
  const CURRENCY_NOTE = "Сумма по позициям в валюте предварительная — окончательная фиксируется в счёте по курсу ЦБ на дату подтверждения";

  function currencyNote(show) {
    let note = el("co-currency-note");
    if (!note && totalBox) {
      note = document.createElement("p");
      note.id = "co-currency-note";
      note.className = "checkout-summary__note";
      note.textContent = CURRENCY_NOTE;
      totalBox.closest(".checkout-summary__total").before(note);
    }
    if (note) note.hidden = !show;
  }

  attachPhoneMask(el("co-phone"));

  /* ============================================================
     1. Загрузка состава заказа
     ============================================================ */
  
  async function loadSummary() {
    try {
      const res = await fetch("/cart/");
      const data = await res.json();
      cartVersion = data.version || "";
      currencyNote(!!data.has_foreign_currency);
  
      if (!data.items || !data.items.length) {
        itemsBox.innerHTML =
          '<p class="checkout-summary__empty">Корзина пуста</p>';
        if (totalBox) totalBox.textContent = fmt(0);
        if (submitBtn) submitBtn.disabled = true;
        return;
      }
  
      itemsBox.innerHTML = data.items
        .map(
          (it) => `
        <div class="checkout-summary__item">
          <div class="checkout-summary__thumb">
            ${it.image ? `<img src="${img(it.image)}" alt="">` : ""}
          </div>
          <div class="checkout-summary__meta">
            <div class="checkout-summary__brand">${escapeHTML(it.brand)}</div>
            <div class="checkout-summary__name">${escapeHTML(it.name)}</div>
            <div class="checkout-summary__qty">${it.quantity} шт × ${fmt(it.price)}</div>
          </div>
          <div class="checkout-summary__sum">${fmt(it.line_total)}</div>
        </div>`
        )
        .join("");
  
      if (totalBox) totalBox.textContent = fmt(data.total);
      if (submitBtn) submitBtn.disabled = false;
    } catch (e) {
      console.error("Ошибка загрузки корзины:", e);
      itemsBox.innerHTML =
        '<p class="checkout-summary__empty">Не удалось загрузить корзину</p>';
    }
  }
  
  /* ============================================================
     2. Выбранный способ оплаты
     ============================================================ */
  
  function selectedPayment() {
    const checked = document.querySelector('input[name="pay"]:checked');
    return checked ? checked.value : "invoice";
  }
  
  /* ============================================================
     3. Отправка заказа
     ============================================================ */
  
  async function submitOrder() {
    if (errorBox) errorBox.textContent = "";
  
    const body = {
      contact_name: el("co-name")?.value.trim() || "",
      contact_phone: normalizedPhone(el("co-phone")),
      contact_email: el("co-email")?.value.trim() || "",
      payment_method: selectedPayment(),
      requisites: el("co-req")?.value.trim() || null,
      consent: true, // без галочки отправка остановится ниже
      cart_version: cartVersion,
    };
  
    if (!body.contact_phone) {
      if (errorBox) errorBox.textContent = "Укажите телефон для связи";
      return;
    }

    const consent = el("co-consent");
    if (consent && !consent.checked) {
      if (errorBox) errorBox.textContent = "Подтвердите согласие с политикой конфиденциальности и соглашением";
      return;
    }
  
    submitBtn.disabled = true;
  
    try {
      const data = await post("/cart/checkout/", body);

      if (data.error === "cart_changed") {
        await loadSummary();
        if (errorBox) errorBox.textContent = data.message || "Состав или цены изменились — проверьте заказ";
        submitBtn.disabled = false;
        return;
      }
  
      if (data.error) {
        if (errorBox) errorBox.textContent = data.error;
        submitBtn.disabled = false;
        return;
      }
  
      const numberBox = el("co-number");
      if (numberBox) numberBox.textContent = data.number;

      if (formBox) formBox.hidden = true;
  
      const layout = el("checkout-main-layout");
      if (layout) layout.hidden = true;
  
      if (successBox) successBox.hidden = false;
  
      if (window.updateCartBadge) window.updateCartBadge(0);
  
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      console.error("Ошибка оформления заказа:", e);
      if (errorBox)
        errorBox.textContent = "Что-то пошло не так. Попробуйте ещё раз.";
      submitBtn.disabled = false;
    }
  }
  
  /* ============================================================
     4. Инициализация
     ============================================================ */
  
  if (submitBtn) submitBtn.addEventListener("click", submitOrder);

  // Корзину изменил консультант в чате
  document.addEventListener("cart:changed", loadSummary);
  
  loadSummary();