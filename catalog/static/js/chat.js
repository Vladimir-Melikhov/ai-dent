/* ============================================================
   Чат консультанта. Тикет — POST /chat/ticket/ (Django), затем WebSocket /ws/chat (api).
   На главной чат встроен в первый экран (data-mode="inline"), на остальных страницах — панель.
   ============================================================ */

import { bindCartButtons, escapeHTML, formatPrice, productCard } from "./api.js";
import { renderComparison } from "./chat-compare.js";
import { renderText, safeHref } from "./chat-text.js";
import { initShortlist } from "./shortlist.js";

const OPEN_KEY = "ai-dent-chat-open";
const PING_INTERVAL = 25000;
const MAX_BACKOFF = 30000;
// Коды закрытия от сервера: 4401 — нужен новый тикет, 4403 — доступ запрещён, 4409 — диалог закрыт
const CLOSE_FORBIDDEN = 4403;
const STATUS_TEXT = {
  thinking: "Консультант думает…",
  searching: "Ищу в каталоге…",
  preparing: "Готовлю подборку…",
  cart: "Обновляю корзину…",
  quote: "Готовлю КП…",
};
// После этих ошибок строка на главной ищет по каталогу
const UNAVAILABLE_CODES = new Set(["model_unavailable", "limit_reached"]);

const CATALOG_SEARCH_PLACEHOLDER = "Найти оборудование, инструмент, материалы…";

const root = document.getElementById("chat-widget");

if (root) {
  const inlineMode = root.dataset.mode === "inline";
  const fab = document.getElementById("chat-open");
  const panel = document.getElementById("chat-panel");
  const feed = document.getElementById("chat-feed");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const stopBtn = document.getElementById("chat-stop");
  const statusEl = document.getElementById("chat-status");
  const notice = document.getElementById("chat-notice");

  let ws = null;
  let attempts = 0;
  let reconnectTimer = null;
  let pingTimer = null;
  let connecting = false;
  let forbidden = false;
  let streaming = null;    // { el, text } — ответ, который сейчас приходит
  let ready = false;       // история получена — можно отправлять
  let unavailable = false; // на главной: агент недоступен — строка ищет по каталогу
  let lastQuestion = "";
  const queued = [];       // вопросы, заданные до подключения (строка на главной)
  const pending = new Map(); // id реплики → текст, ждём подтверждения сервера

  /* ---------- служебное ---------- */

  function getCookie(name) {
    const m = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
    return m ? m[2] : "";
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    // Без защищённого контекста randomUUID недоступен
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = [...b].map(x => x.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }

  function setStatus(text) { statusEl.textContent = text || ""; }

  function showNotice(text, withLeadButton = false) {
    notice.textContent = text;
    if (withLeadButton) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chat__lead-btn";
      button.textContent = "Оставить заявку менеджеру";
      button.addEventListener("click", () => openLeadForm({ source: "fallback" }));
      notice.appendChild(button);
    }
    notice.hidden = !text;
  }

  /* ---------- форма заявки ---------- */

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

  function leadDone(number) {
    const el = document.createElement("div");
    el.className = "chat-event";
    if (number) el.dataset.lead = number;
    el.innerHTML = number
      ? `<strong>Заявка № ${escapeHTML(number)} отправлена.</strong> Менеджер перезвонит в рабочее время.`
      : "<strong>Заявка отправлена.</strong>";
    return el;
  }

  function renderLeadForm(source) {
    const form = document.createElement("form");
    form.className = "chat-widget chat-form";
    form.innerHTML = `
      <p class="chat-form__title">Заявка менеджеру</p>
      <input class="chat-form__input" name="phone" type="tel" autocomplete="tel" aria-label="Телефон" required>
      <input class="chat-form__input" name="name" autocomplete="name" placeholder="Имя (по желанию)" aria-label="Имя">
      <input class="chat-form__input" name="city" placeholder="Город (по желанию)" aria-label="Город">
      <label class="chat-form__consent"><input type="checkbox" name="consent">
        <span>Даю согласие на обработку персональных данных по <a href="/privacy/" target="_blank" rel="noopener">политике конфиденциальности</a></span>
      </label>
      <label class="chat-form__consent"><input type="checkbox" name="terms">
        <span>Принимаю условия <a href="/terms/" target="_blank" rel="noopener">пользовательского соглашения</a></span>
      </label>
      <button class="chat-form__submit" type="submit">Отправить заявку</button>
      <p class="chat-form__error" role="alert"></p>`;
    const phone = form.elements.phone;
    phone.value = "+7 ";
    form.addEventListener("input", () => { form.querySelector(".chat-form__error").textContent = ""; });
    phone.addEventListener("input", () => { phone.value = formatPhoneDigits(extractPhoneDigits(phone.value)); });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const error = form.querySelector(".chat-form__error");
      const digits = extractPhoneDigits(phone.value);
      if (digits.length !== 10) { error.textContent = "Введите телефон полностью"; return; }
      if (!form.elements.consent.checked || !form.elements.terms.checked) { error.textContent = "Подтвердите согласие и условия соглашения"; return; }
      error.textContent = "";
      form.querySelector("button").disabled = true;
      try {
        const res = await fetch("/chat/lead/", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
          body: JSON.stringify({
            phone: "+7" + digits, name: form.elements.name.value.trim(), city: form.elements.city.value.trim(),
            consent: true, source,
          }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Не удалось отправить заявку");
        // Событие о заявке могло прийти по сокету раньше ответа — тогда подтверждение уже в ленте
        if (feed.querySelector(`[data-lead="${CSS.escape(data.number)}"]`)) form.remove();
        else form.replaceWith(leadDone(data.number));
        scrollDown();
        showNotice("");
      } catch (e) {
        error.textContent = e.message;
        form.querySelector("button").disabled = false;
      }
    });
    return form;
  }

  /* ---------- форма заказа ---------- */

  const CURRENCY_NOTE = "Сумма по позициям в валюте предварительная — окончательная фиксируется в счёте по курсу ЦБ на дату подтверждения.";
  const BUYER_FIELDS = {
    legal: { company_name: "Название организации", inn: "ИНН организации — 10 цифр", contact_name: "Контактное лицо" },
    ip: { buyer_name: "ФИО индивидуального предпринимателя", inn: "ИНН ИП — 12 цифр" },
    individual: { buyer_name: "ФИО" },
  };

  function orderDone(number) {
    const el = document.createElement("div");
    el.className = "chat-event";
    if (number) el.dataset.order = number;
    el.innerHTML = number
      ? `<strong>Заказ № ${escapeHTML(number)} оформлен.</strong> Менеджер свяжется с вами для подтверждения, счёт придёт после него.`
      : "<strong>Заказ оформлен.</strong>";
    return el;
  }

  function renderOrderForm() {
    const form = document.createElement("form");
    form.className = "chat-widget chat-form chat-order";
    form.noValidate = true;
    form.innerHTML = `
      <p class="chat-form__title">Оформление заказа</p>
      <div class="chat-order__items">Загружаю корзину…</div>
      <p class="chat-order__note" hidden>${CURRENCY_NOTE}</p>
      <div class="chat-order__step" hidden></div>
      <div class="chat-order__rest">
      <div class="chat-order__types" role="radiogroup" aria-label="Покупатель">
        <label><input type="radio" name="buyer_type" value="legal" checked> Юрлицо</label>
        <label><input type="radio" name="buyer_type" value="ip"> ИП</label>
        <label><input type="radio" name="buyer_type" value="individual"> Физлицо</label>
      </div>
      <input class="chat-form__input" name="company_name" autocomplete="organization">
      <input class="chat-form__input" name="buyer_name" autocomplete="name">
      <input class="chat-form__input" name="inn" inputmode="numeric" maxlength="12">
      <input class="chat-form__input" name="contact_name" autocomplete="name">
      <input class="chat-form__input" name="contact_phone" type="tel" autocomplete="tel" aria-label="Телефон">
      <input class="chat-form__input" name="contact_email" type="email" autocomplete="email" placeholder="Email — пришлём счёт (по желанию)" aria-label="Email">
      <input class="chat-form__input" name="delivery_city" placeholder="Город доставки" aria-label="Город доставки">
      <input class="chat-form__input" name="delivery_address" placeholder="Адрес доставки (по желанию)" aria-label="Адрес доставки">
      <textarea class="chat-form__input" name="comment" rows="2" placeholder="Комментарий (по желанию)" aria-label="Комментарий"></textarea>
      <label class="chat-form__consent"><input type="checkbox" name="consent">
        <span>Даю согласие на обработку персональных данных по <a href="/privacy/" target="_blank" rel="noopener">политике конфиденциальности</a></span>
      </label>
      <label class="chat-form__consent"><input type="checkbox" name="terms">
        <span>Принимаю условия <a href="/terms/" target="_blank" rel="noopener">пользовательского соглашения</a></span>
      </label>
      <button class="chat-form__submit" type="submit" disabled>Оформить заказ</button>
      </div>
      <p class="chat-form__error" role="alert"></p>`;
    const f = form.elements;
    const error = form.querySelector(".chat-form__error");
    const submit = form.querySelector("button[type=submit]");
    let version = "";
    // Параметры бинокулярных луп: id товара → {zoom, distance, ipd, later}; шаг пройден → открывается форма
    const loupes = {};
    let stepDone = false;
    const step = form.querySelector(".chat-order__step");
    const rest = form.querySelector(".chat-order__rest");

    function loupeReady(item) {
      const c = loupes[item.product_id] || {};
      if (c.later) return true;
      const l = item.loupe;
      return !!c.zoom && (!l.distances.length || c.distSkip || !!c.distance) && (l.frames.length < 2 || !!c.frame) && (l.ipds.length === 1 || !l.ask_ipd || c.ipdSkip || !!(c.ipd || "").trim());
    }

    let syncStep = () => {};
    // Обработчики шага — один раз: состав перерисовывается при каждом изменении корзины
    step.addEventListener("click", e => {
      const b = e.target.closest(".chat-loupe__seg button");
      if (!b) return;
      const box = b.closest("[data-pid]");
      loupes[box.dataset.pid][b.closest(".chat-loupe__seg").dataset.group] = b.dataset.v;
      step.querySelector("[name=loupe_later]").checked = false;
      const group = b.closest(".chat-loupe__seg").dataset.group;
      if (group === "ipd") loupes[box.dataset.pid].ipdSkip = false;
      if (group === "distance") loupes[box.dataset.pid].distSkip = false;
      syncStep();
    });
    step.addEventListener("click", e => {
      const skip = e.target.closest("[data-skip]");
      if (!skip) return;
      const c = loupes[skip.closest("[data-pid]").dataset.pid];
      if (skip.dataset.skip === "distance") {
        c.distSkip = !c.distSkip;
        if (c.distSkip) c.distance = "";
      } else {
        c.ipdSkip = !c.ipdSkip;
        if (c.ipdSkip) { c.ipd = ""; step.querySelectorAll("[name=loupe_ipd]").forEach(i => { i.value = ""; }); }
      }
      syncStep();
    });
    step.addEventListener("input", () => syncStep());

    function renderStep(items) {
      const withLoupe = items.filter(i => i.loupe);
      step.hidden = stepDone || !withLoupe.length;
      rest.hidden = !stepDone && withLoupe.length > 0;
      if (step.hidden) return;
      const seg = (list, group, label) => `<div class="chat-loupe__label">${label}</div>
        <div class="chat-loupe__seg" data-group="${group}" style="--n:${list.length}">${
          list.map((t, n) => `<button type="button" data-v="${escapeHTML(t)}">${escapeHTML(t)}</button>`).join("")}</div>`;
      step.innerHTML = `<p class="chat-order__step-title">Для начала выберите параметры</p>` + withLoupe.map(i => `
        <div class="chat-order__loupe" data-pid="${i.product_id}">
          <div class="chat-loupe__name">${escapeHTML(i.name)}</div>
          ${seg(i.loupe.options.map(o => o.zoom), "zoom", "Увеличение")}
          ${i.loupe.distances.length ? seg(i.loupe.distances, "distance", "Рабочее расстояние")
            + '<button type="button" class="chat-order__skip" data-skip="distance">Пропустить — расстояние уточнит менеджер</button>' : ""}
          ${i.loupe.frames.length > 1 ? seg(i.loupe.frames, "frame", "Оправа") : ""}
          ${i.loupe.ipds.length > 1 ? seg(i.loupe.ipds, "ipd", "Межзрачковое расстояние") : ""}
          ${i.loupe.ask_ipd && !i.loupe.ipds.length ? `<div class="chat-loupe__label">Межзрачковое расстояние, мм</div>
            <input class="chat-form__input" name="loupe_ipd" inputmode="decimal" maxlength="40" placeholder="Например: 62"
              aria-label="Межзрачковое расстояние">` : ""}
          ${i.loupe.ask_ipd && i.loupe.ipds.length !== 1 ? '<button type="button" class="chat-order__skip" data-skip="ipd">Пропустить — межзрачковое уточнит менеджер</button>' : ""}
        </div>`).join("") + `
        <label class="chat-form__consent"><input type="checkbox" name="loupe_later">
          <span>Не нужно — параметры уточнит менеджер</span></label>
        <button class="chat-form__submit" type="button" name="loupe_next" disabled>Далее</button>`;
      const next = step.querySelector("[name=loupe_next]");
      const later = step.querySelector("[name=loupe_later]");
      const sync = () => {
        withLoupe.forEach(i => {
          const box = step.querySelector(`[data-pid="${i.product_id}"]`);
          const c = loupes[i.product_id] || (loupes[i.product_id] = {});
          c.later = later.checked;
          box.querySelectorAll(".chat-loupe__seg").forEach(g =>
            g.querySelectorAll("button").forEach(b => b.classList.toggle("is-on", c[g.dataset.group] === b.dataset.v)));
          const input = box.querySelector("[name=loupe_ipd]");
          if (input && input.value) { c.ipd = input.value; c.ipdSkip = false; }
          const skipIpd = box.querySelector("[data-skip=ipd]");
          if (skipIpd) skipIpd.classList.toggle("is-on", !!c.ipdSkip);
          const skipDistance = box.querySelector("[data-skip=distance]");
          if (skipDistance) skipDistance.classList.toggle("is-on", !!c.distSkip);
          box.classList.toggle("is-muted", later.checked);
        });
        next.disabled = !withLoupe.every(loupeReady);
      };
      syncStep = sync;
      later.addEventListener("change", sync);
      next.addEventListener("click", () => {
        stepDone = true;
        step.hidden = true;
        rest.hidden = false;
        scrollDown();
      });
      sync();
    }

    function buyerType() { return form.querySelector("[name=buyer_type]:checked").value; }

    function showBuyerFields() {
      const fields = BUYER_FIELDS[buyerType()];
      ["company_name", "buyer_name", "inn", "contact_name"].forEach(name => {
        const label = fields[name];
        f[name].hidden = !label;
        if (label) { f[name].placeholder = label; f[name].setAttribute("aria-label", label); }
      });
    }

    async function loadCart(message) {
      const box = form.querySelector(".chat-order__items");
      try {
        const data = await fetch("/cart/").then(r => r.json());
        version = data.version;
        renderStep(data.items);
        if (!data.items.length) {
          box.textContent = "Корзина пуста — добавьте товары, и консультант откроет форму снова.";
          submit.disabled = true;
          return;
        }
        // Лупы убрали из корзины — шаг параметров не нужен
        box.innerHTML = data.items.map(i => `
          <div class="chat-order__line"><span>${escapeHTML(i.name)} × ${i.quantity}</span>
          <span>${i.line_total != null ? formatPrice(i.line_total) : "—"}</span></div>`).join("")
          + `<div class="chat-order__line chat-order__total"><span>Итого</span><span>${formatPrice(data.total)}</span></div>`;
        form.querySelector(".chat-order__note").hidden = !data.has_foreign_currency;
        submit.disabled = false;
        if (message) error.textContent = message;
      } catch (e) {
        box.textContent = "Не удалось загрузить корзину. Обновите страницу.";
      }
    }

    form.querySelectorAll("[name=buyer_type]").forEach(r => r.addEventListener("change", showBuyerFields));
    form.addEventListener("input", () => { error.textContent = ""; });
    f.contact_phone.value = "+7 ";
    f.contact_phone.addEventListener("input", () => {
      f.contact_phone.value = formatPhoneDigits(extractPhoneDigits(f.contact_phone.value));
    });
    f.inn.addEventListener("input", () => { f.inn.value = f.inn.value.replace(/\D/g, "").slice(0, 12); });
    // Сервер сверяет версию состава при отправке (409)
    document.addEventListener("cart:changed", () => { if (form.isConnected) loadCart(); });

    form.addEventListener("submit", async event => {
      event.preventDefault();
      const type = buyerType();
      const digits = extractPhoneDigits(f.contact_phone.value);
      const required = Object.keys(BUYER_FIELDS[type]).filter(name => !f[name].value.trim());
      const innLength = { legal: 10, ip: 12 }[type];
      if (required.length) { error.textContent = `Заполните: ${BUYER_FIELDS[type][required[0]].split(" — ")[0]}`; return; }
      if (innLength && f.inn.value.length !== innLength) { error.textContent = `ИНН — ${innLength} цифр`; return; }
      if (digits.length !== 10) { error.textContent = "Введите телефон полностью"; return; }
      if (!f.delivery_city.value.trim()) { error.textContent = "Укажите город доставки"; return; }
      if (!f.consent.checked || !f.terms.checked) { error.textContent = "Подтвердите согласие и условия соглашения"; return; }
      error.textContent = "";
      submit.disabled = true;
      const body = { buyer_type: type, contact_phone: "+7" + digits, consent: true, cart_version: version, loupe: {} };
      Object.entries(loupes).forEach(([pid, c]) => {
        body.loupe[pid] = c.later ? { later: true } : { zoom: c.zoom, distance: c.distance, frame: c.frame, ipd: (c.ipd || "").trim(), ipd_skip: !!c.ipdSkip, distance_skip: !!c.distSkip };
      });
      ["company_name", "buyer_name", "inn", "contact_name", "contact_email", "delivery_city", "delivery_address", "comment"]
        .forEach(name => { if (!f[name].hidden) body[name] = f[name].value.trim(); });
      try {
        const res = await fetch("/chat/order/", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (res.status === 409) { await loadCart(data.message); return; }
        if (!res.ok) throw new Error(data.error || "Не удалось оформить заказ");
        if (window.updateCartBadge) window.updateCartBadge(0);
        if (window.refreshCartState) window.refreshCartState();
        if (feed.querySelector(`[data-order="${CSS.escape(data.number)}"]`)) form.remove();
        else form.replaceWith(orderDone(data.number));
        scrollDown();
        document.dispatchEvent(new CustomEvent("cart:changed"));
      } catch (e) {
        error.textContent = e.message;
        submit.disabled = false;
      }
    });

    showBuyerFields();
    loadCart();
    return form;
  }

  // Кнопка «Оставить заявку менеджеру» при сбое модели — заявка без агента
  let openLeadForm = ({ source = "fallback" } = {}) => {
    const empty = feed.querySelector(".chat__empty");
    if (empty) empty.remove();
    const form = renderLeadForm(source);
    feed.appendChild(form);
    setActive(true);
    scrollDown();
    form.elements.phone.focus();
  };

  function setStreaming(on) {
    stopBtn.hidden = !on;
    sendBtn.hidden = on;
  }

  function scrollDown() { feed.scrollTop = feed.scrollHeight; }

  // Картинки карточек догружаются позже — докручиваем, если клиент был внизу ленты
  function keepBottomOnLoad(el) {
    el.querySelectorAll("img").forEach(img => {
      if (img.complete) return;
      img.addEventListener("load", () => {
        const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < img.clientHeight + 120;
        if (nearBottom) scrollDown();
      }, { once: true });
    });
  }

  /* Телефон: высота чата не меняется; один раз после появления клавиатуры
     подводим низ чата к её верхней кромке */
  const phone = window.matchMedia("(max-width: 640px)");
  if (inlineMode) {
    input.addEventListener("focus", () => {
      if (!phone.matches) return;
      setTimeout(() => {
        if (root.classList.contains("is-active")) root.scrollIntoView({ block: "end", behavior: "auto" });
      }, 350);
    });
  }

  function setActive(on) {
    if (!inlineMode || root.classList.contains("is-active") === on) return;
    root.classList.toggle("is-active", on);
    requestAnimationFrame(() => root.scrollIntoView({ block: on ? "end" : "center", behavior: "smooth" }));
    document.dispatchEvent(new CustomEvent("aidentchat:active", { detail: { active: on } }));
  }

  /* ---------- лента ---------- */

  function eventText(type, payload) {
    const p = payload || {};
    const number = escapeHTML(p.number || "");
    switch (type) {
      case "order_created": return `<strong>Заказ № ${number} оформлен.</strong> Менеджер свяжется с вами для подтверждения.`;
      case "order_confirmed": {
        const total = typeof p.total === "number" ? ` Сумма — ${formatPrice(p.total)}.` : "";
        const invoice = p.invoice_url ? ` <a href="${escapeHTML(safeHref(p.invoice_url) || "#")}">Скачать счёт</a>` : "";
        const pay = p.pay_url && safeHref(p.pay_url) ? ` · <a href="${escapeHTML(safeHref(p.pay_url))}">Оплатить</a>` : "";
        if (p.issued) return `<strong>Вам выставлен счёт по заказу № ${number}.</strong>${total}${invoice}${pay}`;
        return `<strong>Заказ № ${number} подтверждён.</strong>${total}${invoice}${pay}`;
      }
      case "quote_issued": {
        const total = typeof p.total === "number" ? ` Сумма — ${formatPrice(p.total)}.` : "";
        const link = p.url ? ` <a href="${escapeHTML(safeHref(p.url) || "#")}">Скачать PDF</a>` : "";
        return `<strong>${p.updated ? "Мы обновили" : "Вам подготовили"} коммерческое предложение № ${number}.</strong>${total}${link}`;
      }
      case "order_paid": return `<strong>Заказ № ${number} оплачен.</strong>`;
      case "order_cancelled": return `<strong>Заказ № ${number} отменён.</strong>`;
      case "lead_created": return `<strong>Заявка № ${number} принята.</strong> Менеджер перезвонит вам.`;
      default: return "";
    }
  }

  function showEmpty() {
    if (inlineMode) {
      feed.innerHTML = "";
      setActive(false);
      return;
    }
    feed.innerHTML = '<p class="chat__empty">Здравствуйте! Я консультант Ай-Дент. Расскажите, какое оборудование вы подбираете.</p>';
  }

  /* ---------- виджеты ---------- */

  function renderCards(products) {
    const el = document.createElement("div");
    el.className = "chat-widget chat-cards";
    el.innerHTML = products.map(productCard).join("");
    return el;
  }

  const LOUPE_KINDS = { universal: "Универсальные", individual: "Индивидуальные" };

  function numberOf(text) {
    const n = parseFloat(String(text || "").replace(",", "."));
    return Number.isFinite(n) ? n : null;
  }

  // Бинокулярная лупа. Увеличение и рабочее расстояние выбираются независимо; от увеличения меняется
  // только поле обзора — оно рисуется кругом, который плавно сужается, а число «докручивается»
  function renderLoupe(ui) {
    const el = document.createElement("div");
    el.className = "chat-widget chat-loupe";
    const p = ui.product;
    const options = ui.options;
    const distances = ui.distances || [];
    const kind = LOUPE_KINDS[ui.kind] ? `<span class="chat-loupe__kind">${LOUPE_KINDS[ui.kind]}</span>` : "";
    const name = p.slug ? `<a href="/product/${encodeURIComponent(p.slug)}/">${escapeHTML(p.name)}</a>` : escapeHTML(p.name);
    const price = p.price_rub != null ? formatPrice(p.price_rub) : "цена по запросу";
    // Поле обзора рисуется полосой из центра: шире полоса — больше видно. Без цифр: по миллиметрам, если они
    // заполнены у всех увеличений, иначе по увеличению (чем оно больше, тем поле уже)
    const mm = options.map(o => numberOf(o.field));
    const zooms = options.map(o => numberOf(o.zoom));
    const sizes = mm.every(f => f) ? mm : zooms.every(z => z) ? zooms.map(z => 1 / z) : null;
    const widest = sizes ? Math.max(...sizes) : 0;
    const seg = (items, group, label) => `<div class="chat-loupe__label">${label}</div>
      <div class="chat-loupe__seg" data-group="${group}" style="--n:${items.length}">${
        items.map((t, i) => `<button type="button" data-i="${i}">${escapeHTML(t)}</button>`).join("")}</div>`;
    el.innerHTML = `
      <div class="chat-loupe__head"><div class="chat-loupe__name">${name}</div>${kind}</div>
      <div class="chat-loupe__meta">${price} · ${escapeHTML(p.availability)}</div>
      ${options.length > 1 ? seg(options.map(o => o.zoom), "zoom", "Увеличение")
        : `<div class="chat-loupe__label">Увеличение</div><div class="chat-loupe__fixed">${escapeHTML(options[0].zoom)}</div>`}
      ${distances.length > 1 ? seg(distances, "distance", "Рабочее расстояние")
        : distances.length ? `<div class="chat-loupe__label">Рабочее расстояние</div><div class="chat-loupe__fixed">${escapeHTML(distances[0])}</div>` : ""}
      ${ui.ipd ? `<div class="chat-loupe__label">Межзрачковое расстояние</div><div class="chat-loupe__fixed">${escapeHTML(ui.ipd)}</div>` : ""}
      ${sizes ? `<div class="chat-loupe__view">
        <div class="chat-loupe__label">Поле обзора</div>
        <div class="chat-loupe__track" aria-hidden="true"><u class="chat-loupe__bar"></u></div>
        <div class="chat-loupe__ends"><span>уже</span><span>шире</span></div>
      </div>` : ""}`;
    const bar = el.querySelector(".chat-loupe__bar");
    function pick(group, i) {
      el.querySelectorAll(`[data-group="${group}"] button`).forEach((b, n) => b.classList.toggle("is-on", n === i));
    }
    function selectZoom(i) {
      pick("zoom", i);
      if (!bar) return;
      // Одно увеличение — сравнивать не с чем, полоса средней ширины
      const width = options.length === 1 ? 60 : Math.max(22, (sizes[i] / widest) * 100);
      bar.style.width = `${width}%`;
    }
    el.querySelectorAll('[data-group="zoom"] button').forEach(b => b.addEventListener("click", () => selectZoom(Number(b.dataset.i))));
    el.querySelectorAll('[data-group="distance"] button').forEach(b => b.addEventListener("click", () => pick("distance", Number(b.dataset.i))));
    pick("distance", 0);
    selectZoom(options.length - 1);
    return el;
  }

  function renderQuote(q) {
    const el = document.createElement("div");
    el.className = "chat-widget chat-quote";
    const until = new Date(q.valid_until).toLocaleDateString("ru-RU");
    el.innerHTML = `
      <div class="chat-quote__title">Коммерческое предложение № ${escapeHTML(q.number)}</div>
      <div class="chat-quote__meta">${q.items_count} поз. · ${formatPrice(q.total)} · действует до ${escapeHTML(until)}</div>
      <a class="chat-quote__download" href="${escapeHTML(q.url)}">Скачать PDF</a>`;
    return el;
  }

  function renderWidget(ui, options = {}) {
    if (ui.widget === "product_cards") return renderCards(ui.products);
    if (ui.widget === "comparison") return renderComparison(ui.table);
    if (ui.widget === "loupe") return renderLoupe(ui);
    if (ui.widget === "quote") return renderQuote(ui);
    // sent — номер заявки или заказа, отправленных из этой формы; true — форма уже неактуальна, номера нет
    const done = sent => (sent === true ? null : sent);
    if (ui.widget === "lead_form") return options.leadSent ? leadDone(done(options.leadSent)) : renderLeadForm("ai");
    if (ui.widget === "order_form") return options.orderSent ? orderDone(done(options.orderSent)) : renderOrderForm();
    return null;
  }

  function afterWidgets() {
    bindCartButtons();
    initShortlist();
  }

  function append(message) {
    const empty = feed.querySelector(".chat__empty");
    if (empty) empty.remove();
    if (message.id && feed.querySelector(`[data-id="${message.id}"]`)) return;

    let el;
    if (message.role === "ui") {
      el = renderWidget(message.ui, message);
      if (!el) return;
      keepBottomOnLoad(el);
    } else if (message.role === "user") {
      el = document.createElement("div");
      el.className = "chat-msg chat-msg--user";
      el.textContent = message.text;
    } else if (message.role === "assistant") {
      if (!message.text) return;
      el = document.createElement("div");
      el.className = "chat-msg chat-msg--assistant";
      el.innerHTML = renderText(message.text);
    } else if (message.role === "event") {
      // Заявку и заказ из формы клиент уже видит подтверждёнными — второе подтверждение от события лишнее
      const number = message.event_payload && message.event_payload.number;
      if (message.event_type === "lead_created" && number && feed.querySelector(`[data-lead="${CSS.escape(number)}"]`)) return;
      if (message.event_type === "order_created" && number && feed.querySelector(`[data-order="${CSS.escape(number)}"]`)) return;
      const html = eventText(message.event_type, message.event_payload);
      if (!html) return;
      el = document.createElement("div");
      el.className = "chat-event";
      el.innerHTML = html;
      if (message.event_type === "lead_created" && number) el.dataset.lead = number;
      if (message.event_type === "order_created" && number) el.dataset.order = number;
    } else {
      return;
    }
    if (message.id) el.dataset.id = message.id;
    feed.appendChild(el);
    setActive(true);
    scrollDown();
    if (message.role === "ui") afterWidgets();
  }

  function renderHistory(messages) {
    feed.innerHTML = "";
    streaming = null;
    setStreaming(false);
    if (!messages.length) showEmpty();
    // Форма, после которой была заявка или заказ, показывается отправленной; событие второй раз не выводится
    const sent = new Map(); // индекс формы → номер или true
    const open = { lead_form: [], order_form: [] };
    const formOf = { lead_created: "lead_form", order_created: "order_form" };
    messages.forEach((m, i) => {
      if (m.role === "ui" && open[m.ui && m.ui.widget]) open[m.ui.widget].push(i);
      const kind = formOf[m.event_type];
      if (!kind || !open[kind].length) return;
      const number = (m.event_payload && m.event_payload.number) || true;
      open[kind].forEach((index, n) => sent.set(index, n === open[kind].length - 1 ? number : true));
      open[kind] = [];
    });
    messages.forEach((m, i) => {
      const widget = m.role === "ui" && m.ui && m.ui.widget;
      append(widget ? { ...m, leadSent: widget === "lead_form" && sent.get(i), orderSent: widget === "order_form" && sent.get(i) } : m);
    });
  }

  function sendNow(text) {
    showNotice("");
    lastQuestion = text;
    const id = uuid();
    pending.set(id, text);
    ws.send(JSON.stringify({ type: "user_message", id, text }));
  }

  /* ---------- сообщения сервера ---------- */

  function handle(msg) {
    switch (msg.type) {
      case "history":
        renderHistory(msg.messages);
        ready = true;
        while (queued.length) sendNow(queued.shift());
        break;
      case "user_message":
        if (pending.has(msg.id)) {
          if (input.value.trim() === pending.get(msg.id)) input.value = "";
          pending.delete(msg.id);
          autosize();
        }
        append(msg.message);
        break;
      case "status":
        setStatus(STATUS_TEXT[msg.status] || STATUS_TEXT.thinking);
        break;
      case "ui":
        append({ role: "ui", ui: msg.ui });
        break;
      case "assistant_start": {
        const el = document.createElement("div");
        el.className = "chat-msg chat-msg--assistant chat-msg--streaming";
        feed.appendChild(el);
        setActive(true);
        streaming = { el, text: "" };
        setStreaming(true);
        scrollDown();
        break;
      }
      case "assistant_delta":
        if (streaming) {
          streaming.text += msg.text;
          streaming.el.innerHTML = renderText(streaming.text);
          scrollDown();
        }
        break;
      case "assistant_end":
        if (streaming) {
          streaming.el.classList.remove("chat-msg--streaming");
          streaming.el.innerHTML = renderText(msg.message.text);
          streaming.el.dataset.id = msg.message.id;
        } else {
          append(msg.message);
        }
        streaming = null;
        setStreaming(false);
        setStatus("");
        break;
      case "assistant_discard":
        // Ответ отменён (остановка, отказ, обрыв) — его текст убирается
        if (streaming) streaming.el.remove();
        streaming = null;
        setStreaming(false);
        setStatus("");
        break;
      case "event":
        append(msg.message);
        break;
      case "cart_updated":
        fetch("/cart/count/").then(r => r.json()).then(d => window.updateCartBadge && window.updateCartBadge(d.count));
        if (window.refreshCartState) window.refreshCartState();
        document.dispatchEvent(new CustomEvent("cart:changed"));
        break;
      case "conversation_closed":
        // Сервер следом закроет сокет — переподключимся к новому текущему диалогу
        feed.innerHTML = "";
        break;
      case "error":
        setStatus("");
        showNotice(msg.message || "Не удалось отправить сообщение", msg.show_lead_button);
        if (UNAVAILABLE_CODES.has(msg.code)) {
          if (inlineMode) {
            unavailable = true;
            input.placeholder = CATALOG_SEARCH_PLACEHOLDER;
          }
          document.dispatchEvent(new CustomEvent("aidentchat:unavailable", {
            detail: { code: msg.code, question: lastQuestion },
          }));
        }
        break;
      default:
        break;
    }
  }

  /* ---------- соединение ---------- */

  async function fetchTicket(isNew) {
    const res = await fetch("/chat/ticket/", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
      body: JSON.stringify({ new: isNew }),
    });
    if (!res.ok) throw new Error("ticket " + res.status);
    return res.json();
  }

  function openSocket(ticket) {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/ws/chat?ticket=${encodeURIComponent(ticket)}`);
    ws = socket;

    socket.addEventListener("open", () => {
      attempts = 0;
      setStatus("");
      showNotice("");
      clearInterval(pingTimer);
      pingTimer = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" }));
      }, PING_INTERVAL);
    });

    socket.addEventListener("message", event => {
      try { handle(JSON.parse(event.data)); } catch (e) { console.error("Чат: сообщение не разобрано", e); }
    });

    socket.addEventListener("close", event => {
      // Сокет прежнего диалога после «Новый диалог» — уже не наш
      if (ws !== socket) return;
      ws = null;
      ready = false;
      clearInterval(pingTimer);
      if (streaming) { streaming.el.remove(); streaming = null; setStreaming(false); }
      if (event.code === CLOSE_FORBIDDEN) {
        forbidden = true;
        showNotice("Чат недоступен. Обновите страницу.");
        return;
      }
      scheduleReconnect(event.code === 4409 ? 0 : null);
    });
  }

  async function connect() {
    if (forbidden || connecting || (ws && ws.readyState <= WebSocket.OPEN)) return;
    connecting = true;
    setStatus("Подключение…");
    try {
      const { ticket } = await fetchTicket(false);
      openSocket(ticket);
    } catch (e) {
      console.error("Чат: не удалось подключиться", e);
      scheduleReconnect(null);
    } finally {
      connecting = false;
    }
  }

  function scheduleReconnect(delay) {
    clearTimeout(reconnectTimer);
    const wait = delay ?? Math.min(MAX_BACKOFF, 1000 * 2 ** attempts) + Math.random() * 500;
    attempts += 1;
    setStatus("Нет соединения, переподключаемся…");
    reconnectTimer = setTimeout(connect, wait);
  }

  async function newDialog() {
    clearTimeout(reconnectTimer);
    const old = ws;
    ws = null;
    if (old) old.close(1000);
    showNotice("");
    showEmpty();
    try {
      const { ticket } = await fetchTicket(true);
      openSocket(ticket);
    } catch (e) {
      console.error("Чат: не удалось начать новый диалог", e);
      showNotice("Не удалось начать новый диалог. Попробуйте ещё раз.");
      scheduleReconnect(null);
    }
  }

  /* ---------- ввод ---------- */

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  }

  // Вопрос с главной: до первого сообщения соединения нет — гостевая сессия не создаётся зря
  function ask(text) {
    const question = String(text || "").trim();
    if (!question) return;
    if (inlineMode) rememberOpen(true); // на других страницах этот же диалог откроется в панели
    if (ws && ws.readyState === WebSocket.OPEN && ready) sendNow(question);
    else {
      if (!queued.includes(question)) queued.push(question);
      lastQuestion = question;
      connect();
    }
  }

  function send() {
    const text = input.value.trim();
    if (inlineMode && unavailable) {
      location.href = text ? `/catalog/?q=${encodeURIComponent(text)}` : "/catalog/";
      return;
    }
    if (!text) return;
    if (inlineMode) { ask(text); return; }
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      showNotice("Нет соединения с консультантом. Подождите несколько секунд.");
      connect();
      return;
    }
    sendNow(text);
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    send();
  });

  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });
  input.addEventListener("input", autosize);

  stopBtn.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" }));
  });

  document.getElementById("chat-new").addEventListener("click", newDialog);

  /* ---------- панель ---------- */

  function rememberOpen(open) {
    try { sessionStorage.setItem(OPEN_KEY, open ? "1" : "0"); } catch (e) { /* хранилище недоступно */ }
  }

  function openPanel() {
    if (inlineMode) {
      connect();
      input.focus();
      return;
    }
    panel.hidden = false;
    fab.hidden = true;
    fab.setAttribute("aria-expanded", "true");
    rememberOpen(true);
    connect();
    input.focus();
  }

  function closePanel() {
    panel.hidden = true;
    fab.hidden = false;
    fab.setAttribute("aria-expanded", "false");
    rememberOpen(false);
  }

  if (fab) fab.addEventListener("click", openPanel);
  const closeBtn = document.getElementById("chat-close");
  if (closeBtn) closeBtn.addEventListener("click", closePanel);

  /* ---------- вопрос извне: подсказки под строкой на главной ---------- */

  window.aidentChat = {
    open: openPanel,
    ask(text) {
      if (!inlineMode) openPanel();
      ask(text);
    },
    setLeadForm(handler) { openLeadForm = handler; },
  };

  if (inlineMode) {
    // Начатый диалог сразу показывается на главной; новый — после первого вопроса
    if (root.dataset.hasDialog === "1") connect();
  } else {
    let wasOpen = false;
    try { wasOpen = sessionStorage.getItem(OPEN_KEY) === "1"; } catch (e) { /* хранилище недоступно */ }
    if (wasOpen) openPanel();
  }
}
