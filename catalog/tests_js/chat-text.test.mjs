// Ответ модели с тегами HTML отображается как текст. Запуск: node --test catalog/tests_js/*.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

// Модули витрины рассчитаны на браузер: подставляем то, что они читают при загрузке
globalThis.window = {};
globalThis.location = { origin: "https://ai-dent.ru" };
const { renderText, safeHref } = await import("../static/js/chat-text.js");

test("теги из ответа модели не становятся разметкой", () => {
  for (const attack of [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    "<a href=\"https://evil.example\">жми</a>",
    "<iframe src=//evil.example></iframe>",
  ]) {
    const html = renderText(attack);
    assert.doesNotMatch(html, /<(script|img|iframe|a )/i, html);
    assert.match(html, /&lt;/);
  }
});

test("ссылки — только на собственный сайт", () => {
  assert.equal(renderText("[Каталог](/catalog/)"), '<p><a href="/catalog/">Каталог</a></p>');
  assert.equal(renderText("[Товар](https://ai-dent.ru/product/x/)"),
    '<p><a href="https://ai-dent.ru/product/x/">Товар</a></p>');
  for (const url of ["https://evil.example/", "//evil.example/x", "javascript:alert(1)", "data:text/html,1"]) {
    const html = renderText(`[ссылка](${url})`);
    assert.doesNotMatch(html, /<a /, url);
    assert.match(html, /^<p>ссылка/, url);
    assert.equal(safeHref(url), null, url);
  }
});

test("кавычки в ссылке не вырываются в атрибут", () => {
  const html = renderText('[x](/a"onmouseover="alert(1))');
  assert.doesNotMatch(html, /"onmouseover/);
});

test("минимальная разметка: абзацы, списки, жирный", () => {
  assert.equal(
    renderText("**Лупа** — 48 500 ₽\n\n- в наличии\n- гарантия 12 мес.\n\n1. первое\n2. второе"),
    "<p><strong>Лупа</strong> — 48\u00a0500\u00a0₽</p><ul><li>в наличии</li><li>гарантия 12 мес.</li></ul>"
      + "<ol><li>первое</li><li>второе</li></ol>",
  );
});

test("вступление и список без пустой строки между ними", () => {
  assert.equal(
    renderText("Подобрал варианты:\n- **Лупа** — 48 500 ₽\n- Микроскоп\nМогу сравнить."),
    "<p>Подобрал варианты:</p><ul><li><strong>Лупа</strong> — 48\u00a0500\u00a0₽</li><li>Микроскоп</li></ul><p>Могу сравнить.</p>",
  );
});

test("нумерация продолжается после пустой строки, заголовок — жирной строкой", () => {
  assert.equal(
    renderText("### Шаги\n1. первое\n\n2. второе"),
    '<p><strong>Шаги</strong></p><ol><li>первое</li></ol><ol start="2"><li>второе</li></ol>',
  );
});
