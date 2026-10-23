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
    const data = await res.json();
    return { ok: res.ok, data };
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

  const modal = document.getElementById("auth-modal");
  const stepPhone = document.getElementById("auth-step-phone");
  const stepCode = document.getElementById("auth-step-code");
  let currentPhone = "";

  attachPhoneMask(document.getElementById("auth-phone"));

  /* ---- согласие с политикой/соглашением: мягко блокирует отправку кода ---- */
  const consentBox = document.getElementById("auth-consent");
  const sendBtn = document.getElementById("auth-send");
  function syncConsentState() {
    if (!consentBox || !sendBtn) return;
    sendBtn.disabled = !consentBox.checked;
  }
  if (consentBox) {
    consentBox.addEventListener("change", syncConsentState);
    syncConsentState();
  }

  function openModal() { modal.hidden = false; }
  function closeModal() {
    modal.hidden = true;
    stepPhone.hidden = false;
    stepCode.hidden = true;
    document.getElementById("auth-phone").value = "+7 ";
    document.getElementById("auth-code").value = "";
    document.getElementById("auth-error-phone").textContent = "";
    document.getElementById("auth-error-code").textContent = "";
    if (consentBox) consentBox.checked = false;
    syncConsentState();
  }
  
  document.getElementById("auth-open").addEventListener("click", openModal);
  document.getElementById("auth-x").addEventListener("click", closeModal);
  document.getElementById("auth-close").addEventListener("click", closeModal);
  document.getElementById("auth-back").addEventListener("click", () => {
    stepCode.hidden = true;
    stepPhone.hidden = false;
  });
  
  document.getElementById("auth-send").addEventListener("click", async () => {
    const phone = normalizedPhone(document.getElementById("auth-phone"));
    const err = document.getElementById("auth-error-phone");
    err.textContent = "";
    if (consentBox && !consentBox.checked) {
      err.textContent = "Подтвердите согласие с условиями";
      return;
    }
    if (!phone) { err.textContent = "Введите телефон"; return; }
  
    const { ok, data } = await post("/accounts/request-code/", { phone });
    if (!ok) { err.textContent = data.error || "Ошибка"; return; }
  
    currentPhone = phone;
    document.getElementById("auth-code-sub").textContent = `Код отправлен на ${phone}`;
    stepPhone.hidden = true;
    stepCode.hidden = false;
  });
  
  document.getElementById("auth-verify").addEventListener("click", async () => {
    const code = document.getElementById("auth-code").value.trim();
    const err = document.getElementById("auth-error-code");
    err.textContent = "";
    if (!code) { err.textContent = "Введите код"; return; }
  
    const { ok, data } = await post("/accounts/verify-code/", { phone: currentPhone, code });
    if (!ok) { err.textContent = data.error || "Неверный код"; return; }
  
    window.location.reload();
  });
