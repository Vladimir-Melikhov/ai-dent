// Калькулятор маржи: всё считается в браузере, ничего не отправляется и не сохраняется.
(() => {
  const $ = (id) => document.getElementById(id);
  const products = JSON.parse($('c-data').textContent);
  const fmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
  const rub = (v) => `${fmt.format(Math.round(v))} ₽`;
  const pct = (v) => `${(Math.round(v * 10) / 10).toLocaleString('ru-RU')}%`;
  const num = (id, fallback = 0) => {
    const raw = $(id).value.replace(/[\s ₽%]/g, '').replace(',', '.');
    const v = parseFloat(raw);
    return Number.isFinite(v) ? v : fallback;
  };
  const has = (id) => $(id).value.trim() !== '';

  function calc() {
    const cost = num('c-cost'), price = num('c-price'), qty = Math.max(1, Math.round(num('c-qty', 1))), tax = num('c-tax') / 100;
    const extra = num('c-extra');
    const ready = has('c-cost') && has('c-price') && price > 0;
    const out = (id, text) => { $(id).textContent = text; };
    if (!ready) {
      for (const id of ['o-profit', 'o-revenue', 'o-cogs', 'o-tax', 'o-extra', 'o-unit', 'o-markup', 'o-breakeven']) out(id, '—');
      out('o-margin', 'маржа —');
      $('o-margin').className = 'chip flat';
      out('o-note', 'Заполните закупку и цену');
    } else {
      const revenue = price * qty, cogs = cost * qty, taxSum = revenue * tax;
      const profit = revenue - cogs - taxSum - extra;
      const margin = (profit / revenue) * 100;
      out('o-profit', rub(profit));
      $('o-profit').className = profit < 0 ? 'neg' : '';
      out('o-revenue', rub(revenue));
      out('o-cogs', rub(cogs));
      out('o-tax', rub(taxSum));
      out('o-extra', rub(extra));
      out('o-unit', rub(profit / qty));
      out('o-markup', cost > 0 ? pct(((price - cost) / cost) * 100) : '—');
      out('o-margin', `маржа ${pct(margin)}`);
      $('o-margin').className = `chip ${profit < 0 ? 'bad' : margin < 10 ? 'flat' : 'good'}`;
      out('o-breakeven', tax < 1 ? rub((cost + extra / qty) / (1 - tax)) : '—');
      out('o-note', profit < 0 ? 'Продажа в убыток' : `${qty} шт. по ${rub(price)}`);
    }
    // обратная задача: цена под нужную маржу. P·q·(1 − налог − маржа) = закупка·q + расходы
    const target = num('c-target') / 100, k = 1 - tax - target;
    out('o-need', has('c-cost') && k > 0 ? rub((cost + extra / qty) / k) : '—');
  }

  $('c-product').addEventListener('input', () => {
    const value = $('c-product').value;
    const found = products.find((p) => `${p.name}${p.sku ? ` · ${p.sku}` : ''}` === value);
    if (found) { $('c-price').value = String(Math.round(found.price)); calc(); }
  });
  try { const tax = localStorage.getItem('calc-tax'); if (tax !== null) $('c-tax').value = tax; } catch { /* без хранилища — по умолчанию 0 */ }
  $('c-tax').addEventListener('input', () => { try { localStorage.setItem('calc-tax', $('c-tax').value); } catch { /* не страшно */ } });
  document.querySelectorAll('.calc input').forEach((input) => input.addEventListener('input', calc));
  calc();
})();
