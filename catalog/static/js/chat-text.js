/* ============================================================
   Текст агента в ленте чата. Сначала экранируется всё — сырой HTML из ответа модели
   в DOM не попадает; затем абзацы, списки, жирный и ссылки только на свой сайт.
   ============================================================ */

import { escapeHTML } from "./api.js";

export function safeHref(url) {
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  if (url.startsWith(location.origin + "/")) return url;
  return null;
}

function inline(escaped) {
  return escaped
    // «160 000 ₽» не разрывается переносом строки ни внутри числа, ни перед знаком рубля
    .replace(/\d[\d ]*\d ?₽|\d ?₽/g, m => m.replace(/ /g, "\u00a0"))
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
      const href = safeHref(url);
      return href ? `<a href="${href}">${label}</a>` : label;
    });
}

const BULLET = /^\s*[-*•] /;
const NUMBERED = /^\s*(\d+)[.)] /;
const HEADING = /^\s*#{1,6} +/;

// Подряд идущие строки одного вида: пункты списка или обычный текст
function renderRun(kind, lines, start) {
  if (kind === "p") return `<p>${lines.map(inline).join("<br>")}</p>`;
  const items = lines.map(l => `<li>${inline(l)}</li>`).join("");
  return kind === "ol" && start > 1 ? `<ol start="${start}">${items}</ol>` : `<${kind}>${items}</${kind}>`;
}

// Модель часто пишет вступление и список без пустой строки между ними: «Подобрал:\n- …\n- …»
function renderBlock(block) {
  const out = [];
  let kind = null;
  let lines = [];
  let start = 1;
  const flush = () => {
    if (kind) out.push(renderRun(kind, lines, start));
    kind = null;
    lines = [];
  };
  for (const line of block.split("\n")) {
    if (HEADING.test(line)) {
      // Заголовки Markdown — жирной строкой: отдельных размеров шрифта в ленте нет
      flush();
      out.push(`<p><strong>${inline(line.replace(HEADING, ""))}</strong></p>`);
      continue;
    }
    const number = line.match(NUMBERED);
    const lineKind = BULLET.test(line) ? "ul" : number ? "ol" : "p";
    if (lineKind !== kind) {
      flush();
      kind = lineKind;
      start = number ? Number(number[1]) : 1;
    }
    lines.push(lineKind === "ul" ? line.replace(BULLET, "") : lineKind === "ol" ? line.replace(NUMBERED, "") : line);
  }
  flush();
  return out.join("");
}

export function renderText(text) {
  return escapeHTML(String(text ?? "").trim())
    .split(/\n{2,}/)
    .map(renderBlock)
    .join("");
}
