// Расход на ИИ по периодам: столбцы на чистом SVG, одна серия.
(() => {
  const data = JSON.parse(document.getElementById('ai-data').textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const usd = (v) => `$${v.toFixed(2)}`;
  const el = (name, attrs = {}, parent) => {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    parent?.appendChild(node);
    return node;
  };
  const label = (iso, step) => {
    const d = new Date(`${iso}T00:00:00`);
    return step === 'month' ? d.toLocaleDateString('ru-RU', { month: 'short', year: '2-digit' }) : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  };
  const niceStep = (rough) => {
    const p = 10 ** Math.floor(Math.log10(rough));
    return [1, 2, 5, 10].find((m) => m * p >= rough) * p;
  };
  const host = document.getElementById('chart-ai');
  const tip = document.createElement('div');
  tip.className = 'tip';
  host.appendChild(tip);

  function draw() {
    host.querySelector('svg')?.remove();
    const rows = data.series;
    const W = host.clientWidth, H = host.clientHeight, m = { l: 52, r: 8, t: 10, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const root = el('svg', { viewBox: `0 0 ${W} ${H}`, role: 'presentation' });
    host.prepend(root);
    if (!rows.length || rows.every((r) => !r.cost)) {
      el('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'axis' }, root).textContent = 'За период расхода на ИИ нет';
      return;
    }
    const maxValue = Math.max(...rows.map((r) => r.cost));
    const step = niceStep(maxValue / 4);
    const top = Math.ceil(maxValue / step) * step;
    const y = (v) => m.t + ih - (v / top) * ih;
    for (let v = 0; v <= top + step / 2; v += step) {
      el('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: 'grid' }, root);
      el('text', { x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'axis' }, root).textContent = `$${+v.toFixed(2)}`;
    }
    const group = iw / rows.length, barW = Math.max(4, Math.min(22, group * 0.5));
    const every = Math.ceil(rows.length / Math.max(1, Math.floor(iw / 64)));
    const bands = [];
    rows.forEach((row, i) => {
      const cx = m.l + group * i + group / 2;
      bands.push(el('rect', { class: 'band', x: m.l + group * i, y: m.t, width: group, height: ih, rx: 6 }, root));
      if (row.cost > 0) {
        const h = Math.max(2, y(0) - y(row.cost)), r = Math.min(4, barW / 2, h), x = cx - barW / 2, t = y(0) - h;
        el('path', { fill: getComputedStyle(document.documentElement).getPropertyValue('--c7').trim(),
          d: `M${x},${y(0)}V${t + r}Q${x},${t} ${x + r},${t}H${x + barW - r}Q${x + barW},${t} ${x + barW},${t + r}V${y(0)}Z` }, root);
      }
      if (i % every === 0) el('text', { x: cx, y: H - 6, 'text-anchor': 'middle', class: 'axis' }, root).textContent = label(row.date, row.step);
    });
    const hit = el('rect', { class: 'hit', x: m.l, y: m.t, width: iw, height: ih }, root);
    const show = (event) => {
      const box = host.getBoundingClientRect();
      const i = Math.max(0, Math.min(rows.length - 1, Math.floor((event.clientX - box.left - m.l) / group)));
      const row = rows[i];
      bands.forEach((b, j) => b.classList.toggle('on', j === i));
      tip.replaceChildren();
      const title = document.createElement('b');
      title.textContent = row.step === 'week' ? `Неделя с ${label(row.date, 'day')}` : label(row.date, row.step);
      tip.appendChild(title);
      for (const [name, value] of [['Расход', usd(row.cost)], ['Запросов', String(row.requests)]]) {
        const line = document.createElement('div');
        const a = document.createElement('span'), b = document.createElement('span');
        a.textContent = name; b.textContent = value;
        line.append(a, b);
        tip.appendChild(line);
      }
      const x = m.l + group * i + group / 2;
      tip.style.left = `${Math.max(0, x + 14 + 170 > W ? x - 14 - 170 : x + 14)}px`;
      tip.style.top = `${m.t + 6}px`;
      tip.classList.add('on');
    };
    const hide = () => { tip.classList.remove('on'); bands.forEach((b) => b.classList.remove('on')); };
    hit.addEventListener('pointermove', show);
    hit.addEventListener('pointerdown', show);
    hit.addEventListener('pointerleave', hide);
  }

  const table = document.createElement('table');
  table.innerHTML = '<thead><tr><th>Период</th><th>Расход</th><th>Запросов</th></tr></thead>';
  const body = document.createElement('tbody');
  for (const row of data.series) {
    const tr = document.createElement('tr');
    for (const text of [label(row.date, row.step), usd(row.cost), String(row.requests)]) {
      const td = document.createElement('td');
      td.textContent = text;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
  table.appendChild(body);
  document.getElementById('table-ai').replaceChildren(table);
  draw();
  new ResizeObserver(draw).observe(host);
})();
