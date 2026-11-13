// Ai Панель: окна создания и правки, удаление, перетаскивание сделок, проверка серверов.
(() => {
  // --- маска телефона на входе: +7 стоит сам, вводить нужно только с девятки (как на сайте)
  function phoneDigits(raw) {
    let d = String(raw || '').replace(/\D/g, '');
    // подставленная «7», затем случайно набранные 7 или 8
    if (d.startsWith('7')) d = d.slice(1);
    if (d.startsWith('7') || d.startsWith('8')) d = d.slice(1);
    return d.slice(0, 10);
  }
  function phoneText(d) {
    let out = '+7 ' + d.slice(0, 3);
    if (d.length > 3) out += ' ' + d.slice(3, 6);
    if (d.length > 6) out += ' ' + d.slice(6, 8);
    if (d.length > 8) out += ' ' + d.slice(8, 10);
    return out;
  }
  document.querySelectorAll('[data-phone-mask]').forEach((input) => {
    input.value = phoneText(phoneDigits(input.value));
    input.addEventListener('input', () => {
      input.value = phoneText(phoneDigits(input.value));
      input.setSelectionRange(input.value.length, input.value.length);
    });
    // курсор всегда после введённых цифр, префикс не стереть
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Backspace' && input.value.length <= 3) event.preventDefault();
    });
    input.setSelectionRange(input.value.length, input.value.length);
  });

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const json = (id) => {
    const node = document.getElementById(id);
    return node ? JSON.parse(node.textContent) : {};
  };
  const csrf = () => $('[name=csrfmiddlewaretoken]')?.value || '';

  // --- окно формы
  const dialog = $('#dlg');
  const objects = json('objects');
  const categoryKinds = json('category-kinds');

  function filterCategories(form) {
    const kind = form.elements.kind, category = form.elements.category;
    if (!kind || !category || !Object.keys(categoryKinds).length) return;
    let firstMatch = null;
    for (const option of category.options) {
      const match = categoryKinds[option.value] === kind.value;
      option.hidden = option.disabled = !match;
      if (match && !firstMatch) firstMatch = option;
    }
    if (category.selectedOptions[0]?.hidden && firstMatch) category.value = firstMatch.value;
  }

  function openDialog(id) {
    if (!dialog) return;
    const form = $('form', dialog);
    form.reset();
    form.elements.id.value = id || '';
    $('h2', dialog).textContent = id ? dialog.dataset.titleEdit : dialog.dataset.titleNew;
    $$('.dlg-delete', dialog).forEach((node) => node.remove());
    const bar = $('.dlg-actions', dialog) || $('.dlg-foot', dialog);
    if (bar.matches('.dlg-actions')) bar.hidden = true;
    $$('[name]', form).forEach((field) => { field.disabled = false; });
    const links = $('.dlg-links', dialog);
    if (links) { links.hidden = true; links.replaceChildren(); }
    if (id && objects[id]) {
      for (const [name, value] of Object.entries(objects[id])) {
        const field = form.elements[name];
        if (!field) continue;
        if (field instanceof RadioNodeList) field.value = String(value);
        else field.value = value;
      }
      const info = objects[id];
      if (form.elements.cost) form.elements.cost.placeholder = 'не указана';
      // карточка из заказа или чата: этап, сумму и клиента задаёт заказ
      (info.locked || []).forEach((name) => { if (form.elements[name]) form.elements[name].disabled = true; });
      if (links && (info.origin || (info.links || []).length)) {
        const badge = document.createElement('span');
        badge.className = 'tag';
        badge.textContent = info.origin;
        links.appendChild(badge);
        for (const [label, href] of info.links || []) {
          const a = document.createElement('a');
          a.className = 'btn ghost sm';
          a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
          a.textContent = label;
          links.appendChild(a);
        }
        links.hidden = false;
      }
      for (const action of info.actions || []) {
        if (action.href) {
          const link = document.createElement('a');
          link.className = 'btn primary dlg-delete';
          link.href = action.href;
          link.textContent = action.label;
          bar.append(link);
          continue;
        }
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `btn dlg-delete ${action.danger ? 'danger' : 'ghost'}`;
        button.textContent = action.label;
        button.dataset.action = action.url;
        button.dataset.confirm = action.confirm;
        if (action.ask_cost) { button.dataset.askCost = '1'; button.dataset.value = action.cost || ''; }
        bar.append(button);
      }
      const extra = $('#dlg-extra');
      if (extra && !(info.locked || []).length) {
        const button = extra.content.firstElementChild.cloneNode(true);
        button.dataset.delete = button.dataset.delete.replace('{id}', id);
        button.classList.add('dlg-delete');
        bar.append(button);
      }
      if (bar.matches('.dlg-actions')) bar.hidden = !bar.children.length;
    }
    filterCategories(form);
    dialog.showModal();
  }

  if (dialog) {
    $('form', dialog).elements.kind?.addEventListener('change', (event) => filterCategories(event.target.form));
    dialog.addEventListener('click', (event) => {
      // клик по затемнению закрывает окно
      if (event.target === dialog) dialog.close();
    });
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('a.phone')) return; // звонок по номеру на карточке не открывает окно
    const target = event.target.closest('[data-new],[data-edit],[data-close],[data-delete],[data-action],[data-ask-cost],[data-copy],[data-check],[data-check-all]');
    if (!target) return;
    if (target.matches('[data-ask-cost]')) payWithCost(target.dataset.url, target.dataset.text, target.dataset.value || '');
    else if (target.matches('[data-action]')) {
      if (target.dataset.askCost) { payWithCost(target.dataset.action, target.dataset.confirm, target.dataset.value || ''); return; }
      if (target.dataset.confirm && !confirm(target.dataset.confirm)) return;
      const form = $('#del-form');
      form.action = target.dataset.action;
      form.submit();
    } else if (target.matches('[data-new]')) openDialog('');
    else if (target.matches('[data-edit]')) openDialog((target.closest('[data-id]') || target).dataset.id);
    else if (target.matches('[data-close]')) target.closest('dialog')?.close();
    else if (target.matches('[data-delete]')) {
      if (!confirm('Удалить? Это нельзя отменить.')) return;
      const form = $('#del-form');
      form.action = target.dataset.delete;
      form.submit();
    } else if (target.matches('[data-copy]')) {
      navigator.clipboard?.writeText(target.dataset.copy);
      const was = target.textContent;
      target.textContent = 'Скопировано';
      setTimeout(() => { target.textContent = was; }, 1100);
    } else if (target.matches('[data-check]')) checkServer(target);
    else if (target.matches('[data-check-all]')) $$('[data-check]').forEach(checkServer);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.matches?.('.deal')) openDialog(event.target.dataset.id);
  });

  // формы с вопросом перед отправкой (подтвердить заказ, оплата пришла)
  document.addEventListener('submit', (event) => {
    const text = event.target.dataset?.confirm;
    if (text && !confirm(text)) event.preventDefault();
  });

  $$('[data-auto]').forEach((select) => select.addEventListener('change', () => select.form.submit()));

  // --- закупка при оплате: вопрос в окне, ответ уходит вместе с действием
  const costDialog = $('#cost-dlg');
  let costResolve = null;
  function askCost(text, value) {
    if (!costDialog) return Promise.resolve(null);
    const form = $('#cost-form');
    $('#cost-text').textContent = text;
    form.elements.cost.value = value;
    costDialog.showModal();
    form.elements.cost.focus();
    return new Promise((resolve) => { costResolve = resolve; });
  }
  if (costDialog) {
    $('#cost-form').addEventListener('submit', (event) => {
      event.preventDefault();
      const value = event.target.elements.cost.value.trim();
      if (!value) return;
      const resolve = costResolve;
      costResolve = null;
      costDialog.close();
      resolve?.(value);
    });
    costDialog.addEventListener('close', () => { costResolve?.(null); costResolve = null; });
    costDialog.addEventListener('click', (event) => { if (event.target === costDialog) costDialog.close(); });
  }
  async function payWithCost(url, text, value) {
    const cost = await askCost(text, value);
    if (cost === null) return;
    const form = document.createElement('form');
    form.method = 'post';
    form.action = url;
    for (const [name, val] of Object.entries({ csrfmiddlewaretoken: csrf(), back: location.pathname + location.search, cost })) {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = name; input.value = val;
      form.appendChild(input);
    }
    document.body.appendChild(form);
    form.submit();
  }

  // --- проверка сервера
  async function checkServer(button) {
    const label = $('span', button);
    button.classList.remove('ok', 'fail');
    button.classList.add('busy');
    label.textContent = 'проверяю';
    try {
      const response = await fetch(button.dataset.check, {
        method: 'POST', headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'fetch' },
      });
      const result = await response.json();
      button.classList.add(result.ok ? 'ok' : 'fail');
      label.textContent = result.ok ? `${result.status} · ${result.ms} мс` : (result.status ? `ошибка ${result.status}` : (result.error || 'не отвечает'));
    } catch {
      button.classList.add('fail');
      label.textContent = 'нет связи';
    } finally {
      button.classList.remove('busy');
    }
  }

  // --- воронка: перетаскивание карточек
  const board = $('.board');
  if (board) {
    let dragged = null;
    board.addEventListener('dragstart', (event) => {
      dragged = event.target.closest('.deal');
      if (!dragged) return;
      dragged.classList.add('drag');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', dragged.dataset.id);
    });
    board.addEventListener('dragend', () => {
      dragged?.classList.remove('drag');
      $$('.col.over').forEach((col) => col.classList.remove('over'));
    });
    board.addEventListener('dragover', (event) => {
      const col = event.target.closest('.col');
      if (!col || !dragged) return;
      event.preventDefault();
      $$('.col.over').forEach((other) => other !== col && other.classList.remove('over'));
      col.classList.add('over');
    });
    board.addEventListener('drop', async (event) => {
      const col = event.target.closest('.col');
      if (!col || !dragged) return;
      event.preventDefault();
      const body = new FormData();
      body.set('stage', col.dataset.stage);
      const title = dragged.querySelector('b').textContent;
      if (dragged.classList.contains('system') && col.dataset.stage === 'lost' && dragged.closest('.col') !== col
          && !confirm(`«${title}»: отметить проигранной? Заказ будет отменён, КП уйдёт в архив.`)) return;
      if (col.dataset.stage === 'won') {
        // выигранная сделка — сначала закупка, иначе прибыль не посчитать
        const cost = await askCost(`«${title}»: во сколько вам обошёлся этот товар?`, '');
        if (cost === null) return;
        body.set('cost', cost);
      }
      try {
        const response = await fetch(board.dataset.stageUrl.replace('/0/', `/${dragged.dataset.id}/`), {
          method: 'POST', body, headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'fetch' },
        });
        if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || '');
      } catch (error) {
        alert(error.message || 'Не удалось перенести сделку');
      }
      location.reload();
    });
  }
})();
