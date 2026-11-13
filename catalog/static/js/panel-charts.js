// Графики обзора: чистый SVG, без библиотек. Данные приходят из json_script «dash-data».
(() => {
  const data = JSON.parse(document.getElementById('dash-data').textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const money = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
  const rub = (value) => `${money.format(Math.round(value))} ₽`;
  const compact = (value) => {
    const abs = Math.abs(value);
    if (abs >= 1e6) return `${+(value / 1e6).toFixed(1)} млн`;
    if (abs >= 1e3) return `${+(value / 1e3).toFixed(1)} тыс`;
    return String(Math.round(value));
  };
  const svg = (name, attrs = {}, parent) => {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    parent?.appendChild(node);
    return node;
  };
  const dateLabel = (iso, step) => {
    const date = new Date(`${iso}T00:00:00`);
    return step === 'month'
      ? date.toLocaleDateString('ru-RU', { month: 'short', year: '2-digit' })
      : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  };
  // «Круглый» шаг шкалы (1, 2, 5 × 10^k), чтобы подписи оси были читаемыми
  const niceStep = (rough) => {
    const power = 10 ** Math.floor(Math.log10(rough));
    return ([1, 2, 5, 10].find((mult) => mult * power >= rough)) * power;
  };
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // --- мини-графики в карточках
  const sparkColors = { revenue: '--c1', expenses: '--c2', profit: '--c3', orders: '--c7' };
  document.querySelectorAll('svg.spark').forEach((node) => {
    const card = data.cards[Number(node.dataset.spark)];
    const points = card.spark;
    if (!points || points.length < 2) return;
    const W = 110, H = 34, min = Math.min(...points), max = Math.max(...points), span = max - min || 1;
    const xy = points.map((v, i) => [(i / (points.length - 1)) * W, H - 3 - ((v - min) / span) * (H - 6)]);
    const color = css(sparkColors[card.key] || '--c1');
    node.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg('polyline', { points: xy.map((p) => p.join(',')).join(' '), fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, node);
    const last = xy[xy.length - 1];
    svg('circle', { cx: last[0], cy: last[1], r: 3, fill: color }, node);
  });

  // --- доходы и расходы
  const host = document.getElementById('chart-flow');
  const tip = document.createElement('div');
  tip.className = 'tip';
  host.appendChild(tip);

  function drawFlow() {
    host.querySelector('svg')?.remove();
    const rows = data.series;
    const W = host.clientWidth, H = host.clientHeight;
    const m = { l: 52, r: 8, t: 10, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'presentation' });
    host.prepend(root);
    if (!rows.length || rows.every((r) => !r.income && !r.expenses)) {
      const text = svg('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'axis' }, root);
      text.textContent = 'За период нет доходов и расходов';
      return;
    }
    const dataMax = Math.max(...rows.map((r) => Math.max(r.income, r.expenses, r.profit)), 1);
    const dataMin = Math.min(0, ...rows.map((r) => r.profit));
    const tickStep = niceStep((dataMax - dataMin) / 4);
    const top = Math.ceil(dataMax / tickStep) * tickStep;
    const low = Math.floor(dataMin / tickStep) * tickStep;
    const y = (v) => m.t + ih - ((v - low) / (top - low)) * ih;

    for (let value = low; value <= top + tickStep / 2; value += tickStep) {
      svg('line', { x1: m.l, x2: W - m.r, y1: y(value), y2: y(value), class: 'grid' }, root);
      const label = svg('text', { x: m.l - 8, y: y(value) + 4, 'text-anchor': 'end', class: 'axis' }, root);
      label.textContent = compact(value);
    }
    const group = iw / rows.length;
    const barW = Math.max(3, Math.min(18, group * 0.3));
    const x0 = (i) => m.l + group * i + group / 2;
    const step = Math.ceil(rows.length / Math.max(1, Math.floor(iw / 64)));
    const bands = [];

    const bar = (cx, value, color) => {
      if (value <= 0) return;
      const h = Math.max(2, y(0) - y(value)), r = Math.min(4, barW / 2, h);
      const x = cx, top = y(0) - h;
      svg('path', { class: 'bar', fill: color, d: `M${x},${y(0)}V${top + r}Q${x},${top} ${x + r},${top}H${x + barW - r}Q${x + barW},${top} ${x + barW},${top + r}V${y(0)}Z` }, root);
    };
    rows.forEach((row, i) => {
      const cx = x0(i);
      bands.push(svg('rect', { class: 'band', x: m.l + group * i, y: m.t, width: group, height: ih, rx: 6 }, root));
      bar(cx - barW - 1, row.income, css('--c1'));
      bar(cx + 1, row.expenses, css('--c2'));
      if (i % step === 0) {
        const label = svg('text', { x: cx, y: H - 6, 'text-anchor': 'middle', class: 'axis' }, root);
        label.textContent = dateLabel(row.date, row.step);
      }
    });
    svg('line', { x1: m.l, x2: W - m.r, y1: y(0), y2: y(0), stroke: 'rgba(255,255,255,.18)' }, root);
    if (rows.length > 1) {
      svg('polyline', { class: 'profit-line', points: rows.map((r, i) => `${x0(i)},${y(r.profit)}`).join(' ') }, root);
    }

    const hit = svg('rect', { class: 'hit', x: m.l, y: m.t, width: iw, height: ih }, root);
    const show = (event) => {
      const box = host.getBoundingClientRect();
      const i = Math.max(0, Math.min(rows.length - 1, Math.floor((event.clientX - box.left - m.l) / group)));
      const row = rows[i];
      bands.forEach((band, j) => band.classList.toggle('on', j === i));
      tip.replaceChildren();
      const title = document.createElement('b');
      title.textContent = row.step === 'week' ? `Неделя с ${dateLabel(row.date, 'day')}` : dateLabel(row.date, row.step);
      tip.appendChild(title);
      for (const [name, value] of [['Доходы', row.income], ['Расходы', row.expenses], ['Прибыль', row.profit]]) {
        const line = document.createElement('div');
        const a = document.createElement('span'), b = document.createElement('span');
        a.textContent = name; b.textContent = rub(value);
        line.append(a, b);
        tip.appendChild(line);
      }
      const left = x0(i) + 14 + 170 > W ? x0(i) - 14 - 170 : x0(i) + 14;
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `${m.t + 6}px`;
      tip.classList.add('on');
    };
    const hide = () => { tip.classList.remove('on'); bands.forEach((band) => band.classList.remove('on')); };
    hit.addEventListener('pointermove', show);
    hit.addEventListener('pointerdown', show);
    hit.addEventListener('pointerleave', hide);
  }

  function fillTable() {
    const table = document.createElement('table');
    table.innerHTML = '<thead><tr><th>Период</th><th>Доходы</th><th>Расходы</th><th>Прибыль</th></tr></thead>';
    const body = document.createElement('tbody');
    for (const row of data.series) {
      const tr = document.createElement('tr');
      for (const text of [dateLabel(row.date, row.step), rub(row.income), rub(row.expenses), rub(row.profit)]) {
        const td = document.createElement('td');
        td.textContent = text;
        tr.appendChild(td);
      }
      body.appendChild(tr);
    }
    table.appendChild(body);
    document.getElementById('table-flow').replaceChildren(table);
  }

  // --- горизонтальные полосы: воронка и расходы по категориям
  function drawBars(id, items, empty) {
    const node = document.getElementById(id);
    node.replaceChildren();
    const max = Math.max(...items.map((i) => i.value), 0);
    if (!items.length || max === 0) {
      const p = document.createElement('p');
      p.className = 'empty';
      p.textContent = empty;
      node.appendChild(p);
      return;
    }
    for (const item of items) {
      const row = document.createElement('div');
      row.className = 'bar-row';
      row.title = `${item.name}: ${item.text}`;
      const label = document.createElement('div');
      label.className = 'bar-label';
      const name = document.createElement('span');
      const dot = document.createElement('i');
      dot.className = 'dot';
      dot.style.background = item.color;
      name.append(dot, item.name);
      const value = document.createElement('b');
      value.textContent = item.text;
      label.append(name, value);
      const track = document.createElement('div');
      track.className = 'bar-track';
      const fill = document.createElement('div');
      fill.className = 'bar-fill';
      fill.style.background = item.color;
      fill.style.width = '0';
      track.appendChild(fill);
      row.append(label, track);
      node.appendChild(row);
      requestAnimationFrame(() => { fill.style.width = `${Math.max(2, (item.value / max) * 100)}%`; });
    }
  }

  const stageColors = { new: '--c1', offer: '--c4', payment: '--c2' };
  drawBars('chart-pipeline', data.pipeline.map((s) => ({
    name: `${s.label} · ${s.count}`, value: s.amount || s.count, color: css(stageColors[s.stage]), text: rub(s.amount),
  })), 'Сделок в работе нет');
  drawBars('chart-expenses', data.expenses.map((e) => ({
    name: e.name, value: e.amount, color: css(`--c${e.color}`), text: `${rub(e.amount)} · ${Math.round(e.share)}%`,
  })), 'За период расходов нет');

  fillTable();
  drawFlow();
  new ResizeObserver(drawFlow).observe(host);
})();
