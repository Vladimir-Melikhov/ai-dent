// Финансы: куда вложены деньги — столбцы по времени с разбивкой по категориям и структура по группам.
(() => {
  const data = JSON.parse(document.getElementById('spend-data').textContent);
  const NS = 'http://www.w3.org/2000/svg';
  const money = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
  const rub = (v) => `${money.format(Math.round(v))} ₽`;
  const compact = (v) => (v >= 1e6 ? `${+(v / 1e6).toFixed(1)} млн` : v >= 1e3 ? `${+(v / 1e3).toFixed(1)} тыс` : String(Math.round(v)));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const color = (n) => css(`--c${n}`);
  const svg = (name, attrs = {}, parent) => {
    const node = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    parent?.appendChild(node);
    return node;
  };
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const dateLabel = (iso, step) => {
    const d = new Date(`${iso}T00:00:00`);
    return step === 'month' ? d.toLocaleDateString('ru-RU', { month: 'short', year: '2-digit' }) : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  };
  const niceStep = (rough) => {
    const power = 10 ** Math.floor(Math.log10(rough));
    return ([1, 2, 5, 10].find((m) => m * power >= rough)) * power;
  };
  const label = (s) => (s.group === 'purchase' ? `${s.name} · закупка` : s.name);

  // --- структура по группам
  const groups = document.getElementById('spend-groups');
  if (!data.total) {
    groups.appendChild(el('p', 'empty', 'За период расходов нет'));
  } else {
    const split = el('div', 'spend-split');
    split.title = data.groups.map((g) => `${g.title}: ${Math.round(g.share)}%`).join(' · ');
    const tones = { purchase: '--c1', operating: '--c4' };
    for (const g of data.groups) {
      const part = el('i');
      part.style.width = `${g.share}%`;
      part.style.background = css(tones[g.key]);
      split.appendChild(part);
    }
    groups.appendChild(split);
    for (const g of data.groups) {
      const box = el('div', 'spend-group');
      const head = el('div', 'spend-head');
      const title = el('span');
      title.append(el('b', '', rub(g.total)), el('small', '', `  ${Math.round(g.share)}% · ${g.title}`));
      head.appendChild(title);
      box.append(head, el('p', '', g.hint));
      const bars = el('div', 'bars');
      const max = Math.max(...g.items.map((i) => i.amount), 1);
      if (!g.items.length) bars.appendChild(el('p', 'empty', 'Нет расходов'));
      for (const item of g.items) {
        const row = el('div', 'bar-row');
        const lab = el('div', 'bar-label');
        const name = el('span');
        const dot = el('i', 'dot');
        dot.style.background = color(item.color);
        name.append(dot, item.name);
        lab.append(name, el('b', '', `${rub(item.amount)} · ${Math.round(item.share)}%`));
        const track = el('div', 'bar-track');
        const fill = el('div', 'bar-fill');
        fill.style.background = color(item.color);
        fill.style.width = '0';
        track.appendChild(fill);
        row.append(lab, track);
        bars.appendChild(row);
        requestAnimationFrame(() => { fill.style.width = `${Math.max(2, (item.amount / max) * 100)}%`; });
      }
      box.appendChild(bars);
      groups.appendChild(box);
    }
  }

  // --- столбцы по времени
  const host = document.getElementById('chart-spend');
  const tip = el('div', 'tip');
  host.appendChild(tip);
  const legend = document.getElementById('legend-spend');
  for (const s of data.series) {
    const item = el('span');
    const dot = el('i', 'dot');
    dot.style.background = color(s.color);
    item.append(dot, label(s));
    legend.appendChild(item);
  }

  function draw() {
    host.querySelector('svg')?.remove();
    const W = host.clientWidth, H = host.clientHeight;
    const m = { l: 52, r: 8, t: 10, b: 26 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'presentation' });
    host.prepend(root);
    const rows = data.rows;
    const sums = rows.map((r) => r.values.reduce((a, b) => a + b, 0));
    if (!rows.length || !Math.max(...sums, 0)) {
      svg('text', { x: W / 2, y: H / 2, 'text-anchor': 'middle', class: 'axis' }, root).textContent = 'За период расходов нет';
      return;
    }
    const stepY = niceStep(Math.max(...sums) / 4);
    const top = Math.ceil(Math.max(...sums) / stepY) * stepY;
    const y = (v) => m.t + ih - (v / top) * ih;
    for (let v = 0; v <= top + stepY / 2; v += stepY) {
      svg('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), class: 'grid' }, root);
      svg('text', { x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end', class: 'axis' }, root).textContent = compact(v);
    }
    const group = iw / rows.length;
    const barW = Math.max(6, Math.min(34, group * 0.6));
    const x0 = (i) => m.l + group * i + group / 2;
    const every = Math.ceil(rows.length / Math.max(1, Math.floor(iw / 64)));
    const bands = [];
    rows.forEach((row, i) => {
      bands.push(svg('rect', { class: 'band', x: m.l + group * i, y: m.t, width: group, height: ih, rx: 6 }, root));
      let acc = 0;
      row.values.forEach((value, k) => {
        if (value <= 0) return;
        const yy = y(acc + value), h = y(acc) - yy;
        svg('rect', { class: 'bar', x: x0(i) - barW / 2, y: yy, width: barW, height: Math.max(1, h - 1), rx: 2, fill: color(data.series[k].color) }, root);
        acc += value;
      });
      if (i % every === 0) svg('text', { x: x0(i), y: H - 6, 'text-anchor': 'middle', class: 'axis' }, root).textContent = dateLabel(row.date, row.step);
    });
    const hit = svg('rect', { class: 'hit', x: m.l, y: m.t, width: iw, height: ih }, root);
    const show = (event) => {
      const box = host.getBoundingClientRect();
      const i = Math.max(0, Math.min(rows.length - 1, Math.floor((event.clientX - box.left - m.l) / group)));
      bands.forEach((b, j) => b.classList.toggle('on', j === i));
      tip.replaceChildren();
      tip.appendChild(el('b', '', `${row_title(rows[i])} · ${rub(sums[i])}`));
      rows[i].values.forEach((value, k) => {
        if (!value) return;
        const line = el('div');
        line.append(el('span', '', label(data.series[k])), el('span', '', rub(value)));
        tip.appendChild(line);
      });
      const left = x0(i) + 14 + 200 > W ? x0(i) - 14 - 200 : x0(i) + 14;
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `${m.t + 6}px`;
      tip.classList.add('on');
    };
    const hide = () => { tip.classList.remove('on'); bands.forEach((b) => b.classList.remove('on')); };
    hit.addEventListener('pointermove', show);
    hit.addEventListener('pointerdown', show);
    hit.addEventListener('pointerleave', hide);
  }
  const row_title = (r) => (r.step === 'week' ? `Неделя с ${dateLabel(r.date, 'day')}` : dateLabel(r.date, r.step));

  function table() {
    const t = document.createElement('table');
    const head = document.createElement('tr');
    for (const text of ['Период', ...data.series.map(label), 'Всего']) head.appendChild(el('th', '', text));
    const thead = document.createElement('thead');
    thead.appendChild(head);
    const body = document.createElement('tbody');
    for (const r of data.rows) {
      const tr = document.createElement('tr');
      for (const text of [dateLabel(r.date, r.step), ...r.values.map(rub), rub(r.values.reduce((a, b) => a + b, 0))]) tr.appendChild(el('td', '', text));
      body.appendChild(tr);
    }
    t.append(thead, body);
    document.getElementById('table-spend').replaceChildren(t);
  }
  table();
  draw();
  new ResizeObserver(draw).observe(host);
})();
