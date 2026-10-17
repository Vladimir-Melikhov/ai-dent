/* ============================================================
   ИИ-консультант: чат (ТЗ §6, §11)
   Тикет — POST /chat/ticket/ (Django), затем WebSocket /ws/chat (api).
   Два вида одного текущего диалога: на главной чат встроен в первый экран
   (data-mode="inline", как у Gemini), на остальных страницах — панель с плавающей кнопкой.
   ============================================================ */

import { bindCartButtons, escapeHTML, formatPrice, productCard } from "./api.js";
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
// После этих ошибок на главной включается запасной поиск по каталогу (ТЗ §11)
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

  /* ---------- форма заявки (ТЗ §10.3): данные уходят в Django, в модель — нет ---------- */

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
        <span>Соглашаюсь на обработку персональных данных по <a href="/privacy/" target="_blank" rel="noopener">политике конфиденциальности</a></span>
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
      if (!form.elements.consent.checked) { error.textContent = "Подтвердите согласие"; return; }
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

  /* ---------- форма заказа (ТЗ §10.2, D6): состав — текущая корзина, данные — в Django ---------- */

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
        <span>Соглашаюсь на обработку персональных данных по <a href="/privacy/" target="_blank" rel="noopener">политике конфиденциальности</a></span>
      </label>
      <button class="chat-form__submit" type="submit" disabled>Оформить заказ</button>
      <p class="chat-form__error" role="alert"></p>`;
    const f = form.elements;
    const error = form.querySelector(".chat-form__error");
    const submit = form.querySelector("button[type=submit]");
    let version = "";

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
        if (!data.items.length) {
          box.textContent = "Корзина пуста — добавьте товары, и консультант откроет форму снова.";
          submit.disabled = true;
          return;
        }
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
    // Клиент исправляет поле — прежняя ошибка уже не про него
    form.addEventListener("input", () => { error.textContent = ""; });
    f.contact_phone.value = "+7 ";
    f.contact_phone.addEventListener("input", () => {
      f.contact_phone.value = formatPhoneDigits(extractPhoneDigits(f.contact_phone.value));
    });
    f.inn.addEventListener("input", () => { f.inn.value = f.inn.value.replace(/\D/g, "").slice(0, 12); });
    // Состав мог измениться, пока форма была открыта: при сохранении сервер сверит версию (409)
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
      if (!f.consent.checked) { error.textContent = "Подтвердите согласие"; return; }
      error.textContent = "";
      submit.disabled = true;
      const body = { buyer_type: type, contact_phone: "+7" + digits, consent: true, cart_version: version };
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
        // Корзина очищена — бейдж и витрина узнают об этом сразу
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

  // Лента листается внутри панели или окна на главной — страница не растягивается
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

  // На главной первый экран — заголовок и строка ввода; с первым сообщением строка становится окном чата
  function setActive(on) {
    if (!inlineMode || root.classList.contains("is-active") === on) return;
    root.classList.toggle("is-active", on);
    // Окно целиком на экране; если уже видно — страница не двигается.
    // «Новый диалог» сворачивает окно в строку — страница становится короче, строку возвращаем в центр экрана
    requestAnimationFrame(() => root.scrollIntoView({ block: on ? "nearest" : "center", behavior: "smooth" }));
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
        return `<strong>Заказ № ${number} подтверждён.</strong>${total}${invoice}${pay}`;
      }
      case "order_paid": return `<strong>Заказ № ${number} оплачен.</strong>`;
      case "order_cancelled": return `<strong>Заказ № ${number} отменён.</strong>`;
      case "lead_created": return `<strong>Заявка № ${number} принята.</strong> Менеджер перезвонит вам.`;
      default: return "";
    }
  }

  function showEmpty() {
    if (inlineMode) {
      // Приветствие на главной — заголовок над строкой ввода
      feed.innerHTML = "";
      setActive(false);
      return;
    }
    feed.innerHTML = '<p class="chat__empty">Здравствуйте! Я консультант Ai-Dent. Расскажите, какое оборудование вы подбираете.</p>';
  }

  /* ---------- виджеты: данные каталога приходят с сервера, не от модели ---------- */

  function renderCards(products) {
    const el = document.createElement("div");
    el.className = "chat-widget chat-cards";
    el.innerHTML = products.map(productCard).join("");
    return el;
  }

  // Характеристики в каталоге бывают строками, списками и словарями
  function formatValue(value) {
    if (Array.isArray(value)) return value.map(formatValue).join("; ");
    if (value && typeof value === "object") return Object.entries(value).map(([k, v]) => `${k}: ${formatValue(v)}`).join("; ");
    return value == null ? "—" : String(value);
  }

  function renderComparison(table) {
    const el = document.createElement("div");
    el.className = "chat-widget chat-compare";
    // Название ведёт на страницу товара; у сравнений из старой истории slug нет — тогда просто текст
    const head = table.products.map(p => {
      const name = p.slug ? `<a href="/product/${encodeURIComponent(p.slug)}/">${escapeHTML(p.name)}</a>` : escapeHTML(p.name);
      return `
      <th>${name}
      <span>${p.price_rub != null ? formatPrice(p.price_rub) : "цена по запросу"} · ${escapeHTML(p.availability)}</span></th>`;
    }).join("");
    const rows = table.rows.map(r => `
      <tr><td>${escapeHTML(r.name)}</td>${r.values.map(v => `<td>${escapeHTML(formatValue(v))}</td>`).join("")}</tr>`).join("");
    el.innerHTML = `<div class="chat-compare__scroll"><table><thead><tr><th></th>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
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
    if (ui.widget === "quote") return renderQuote(ui);
    // sent — номер заявки или заказа, отправленных из этой формы; true — форма уже неактуальна, номера нет
    const done = sent => (sent === true ? null : sent);
    if (ui.widget === "lead_form") return options.leadSent ? leadDone(done(options.leadSent)) : renderLeadForm("ai");
    if (ui.widget === "order_form") return options.orderSent ? orderDone(done(options.orderSent)) : renderOrderForm();
    return null;
  }

  function afterWidgets() {
    // Кнопки «В корзину» и «Избранное» в карточках — общие с витриной
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
    // Формы, после которых были заявка или заказ, показываются отправленными — как сразу после отправки:
    // на месте последней формы перед событием — подтверждение с номером, само событие второй раз не выводится (append)
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
        // Ответ отменён (остановка, отказ, обрыв) — его текст убирается (ТЗ §6.2)
        if (streaming) streaming.el.remove();
        streaming = null;
        setStreaming(false);
        setStatus("");
        break;
      case "event":
        append(msg.message);
        break;
      case "cart_updated":
        // Консультант изменил корзину — бейдж, кнопки карточек, открытая корзина и /checkout/ (ТЗ §11)
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
            // Запасной поиск: строка на главной ищет по каталогу (ТЗ §11)
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

  /* ---------- вопрос извне: подсказки под строкой на главной (ТЗ §11) ---------- */

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
