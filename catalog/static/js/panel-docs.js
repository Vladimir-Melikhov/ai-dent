// КП и счета: выбор товаров, скидка и итог считаются в браузере; сервер пересчитывает всё сам по каталогу.
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const products = JSON.parse($('#doc-products').textContent);
  const restore = JSON.parse($('#doc-restore').textContent || '{}');
  const form = $('#doc-form');
  const linesBox = $('#lines');
  const fmt = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  const rub = (v) => `${fmt.format(Math.round(v * 100) / 100)} ₽`;
  const label = (p) => `${p.name}${p.sku ? ` · ${p.sku}` : ''}`;
  const byLabel = new Map(products.map((p) => [label(p), p]));
  const number = (raw) => {
    const v = parseFloat(String(raw).replace(/[\s ₽%]/g, '').replace(',', '.'));
    return Number.isFinite(v) ? v : 0;
  };
  // та же арифметика, что на сервере: цена после скидки округляется до копеек
  const unitPrice = (list, discount) => Math.round(list * (100 - discount)) / 100;

  function addLine(data = {}) {
    const row = $('#line-tpl').content.firstElementChild.cloneNode(true);
    linesBox.appendChild(row);
    if (data.id) {
      const found = products.find((p) => String(p.id) === String(data.id));
      if (found) $('.l-product', row).value = label(found);
    }
    if (data.qty) $('.l-qty', row).value = data.qty;
    if (data.discount) $('.l-disc', row).value = data.discount;
    recalc();
    return row;
  }

  function rowState(row) {
    const input = $('.l-product', row);
    const product = byLabel.get(input.value) || null;
    input.setCustomValidity(product ? '' : 'Выберите товар из списка');
    const qty = Math.max(1, Math.round(number($('.l-qty', row).value)) || 1);
    const discount = Math.min(99, Math.max(0, number($('.l-disc', row).value)));
    return { row, product, qty, discount };
  }

  function recalc() {
    let full = 0, paid = 0, count = 0;
    for (const row of linesBox.children) {
      const { product, qty, discount } = rowState(row);
      const price = $('.l-price', row), sum = $('.l-sum', row);
      if (!product) { price.textContent = '—'; price.classList.remove('was'); sum.textContent = '—'; continue; }
      count += 1;
      const unit = unitPrice(product.price, discount);
      full += product.price * qty;
      paid += unit * qty;
      price.textContent = rub(unit);
      price.title = unit < product.price ? `по прайсу ${rub(product.price)}` : '';
      sum.textContent = rub(unit * qty);
    }
    const delivery = number($('#delivery').value);
    const saved = full - paid;
    $('#s-full').textContent = count ? rub(full) : '—';
    $('#s-disc').textContent = count ? (saved > 0 ? `−${rub(saved)}` : 'нет') : '—';
    $('#s-disc-name').textContent = saved > 0 ? `Скидка ${fmt.format(Math.round((saved / full) * 1000) / 10)}%` : 'Скидка';
    $('#row-delivery').hidden = !(delivery > 0);
    $('#s-delivery').textContent = rub(delivery);
    $('#s-total').textContent = count ? rub(paid + (delivery > 0 ? delivery : 0)) : '—';
    $('#sum-note').textContent = count ? `${count} поз.` : 'Добавьте товары';
  }

  function syncBuyer() {
    const type = $('#buyer-type').value;
    $('#name-label').textContent = type === 'legal' ? 'Организация' : type === 'ip' ? 'ФИО предпринимателя' : 'ФИО покупателя';
    $('#buyer-name').placeholder = type === 'legal' ? 'ООО «Стом-Центр»' : 'Иванов Иван Иванович';
    $('#inn-fld').hidden = type === 'individual';
    $('#contact-fld').hidden = type !== 'legal';
    const inn = $('#inn-fld input');
    inn.placeholder = type === 'ip' ? '12 цифр' : '10 цифр';
  }

  function collect() {
    return [...linesBox.children].map(rowState).filter((s) => s.product).map((s) => ({
      id: s.product.id, qty: s.qty, discount: s.discount,
    }));
  }

  linesBox.addEventListener('input', recalc);
  linesBox.addEventListener('change', recalc);
  linesBox.addEventListener('click', (event) => {
    const del = event.target.closest('.l-del');
    if (!del) return;
    del.closest('.line').remove();
    if (!linesBox.children.length) addLine();
    recalc();
  });
  $('#add-line').addEventListener('click', () => $('.l-product', addLine()).focus());
  $('#apply-all').addEventListener('click', () => {
    const value = $('#all-discount').value;
    for (const row of linesBox.children) $('.l-disc', row).value = value;
    recalc();
  });
  $('#delivery').addEventListener('input', recalc);
  $('#buyer-type').addEventListener('change', syncBuyer);

  form.addEventListener('submit', (event) => {
    const submitter = event.submitter;
    if (submitter?.dataset.confirm && !window.confirm(submitter.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    $('#doc-items').value = JSON.stringify(collect());
  });

  // после ошибки сервера введённое возвращается в форму
  for (const [name, value] of Object.entries(restore)) {
    if (name === 'items') continue;
    const field = form.elements[name];
    if (field) field.value = value;
  }
  syncBuyer();
  let saved = [];
  try { saved = JSON.parse(restore.items || '[]'); } catch { saved = []; }
  if (saved.length) saved.forEach(addLine); else addLine();
  recalc();
})();
