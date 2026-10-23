import { fetchProducts, productCard, bindCartButtons } from "./api.js";
import { initShortlist } from "./shortlist.js";

async function loadCategoryBlocks() {
  const blocks = document.querySelectorAll("[data-category-products]");
  for (const block of blocks) {
    const slug = block.dataset.categoryProducts;
    try {
      const data = await fetchProducts({ category: slug, sort: "priority", page_size: 4 });
      block.innerHTML = data.items.length
        ? data.items.map(productCard).join("")
        : '<p class="empty">Скоро появится</p>';
    } catch (error) {
      console.error(`Ошибка загрузки категории "${slug}":`, error);
      block.innerHTML = '<p class="empty">Не удалось загрузить</p>';
    }
  }
  initShortlist();
  bindCartButtons();
}

function catalogSearch(q) {
  window.location.href = q ? `/catalog/?q=${encodeURIComponent(q)}` : "/catalog/";
}

/* Если агент недоступен, строка под заголовком ищет по каталогу */
function initSearch() {
  const form = document.getElementById("hero-search");
  const down = document.getElementById("agent-down");
  const downLink = document.getElementById("agent-down-search");
  let chatOn = Boolean(document.getElementById("chat-widget"));

  function searchHref(q) {
    return q ? `/catalog/?q=${encodeURIComponent(q)}` : "/catalog/";
  }

  if (form) {
    const input = form.querySelector('[name="q"]');
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      catalogSearch(input ? input.value.trim() : "");
    });
  }

  const typed = document.getElementById("chat-input") || document.getElementById("search-input");
  if (typed && downLink) {
    typed.addEventListener("input", () => { downLink.href = searchHref(typed.value.trim()); });
  }

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const label = chip.textContent.trim().toLowerCase();
      if (chatOn && window.aidentChat) window.aidentChat.ask(`Помогите подобрать: ${label}`);
      else catalogSearch(chip.dataset.q || "");
    });
  });

  // Чат сообщил, что модель недоступна или исчерпан бюджет
  document.addEventListener("aidentchat:unavailable", (event) => {
    chatOn = false;
    if (!down) return;
    down.hidden = false;
    if (downLink) downLink.href = searchHref((event.detail && event.detail.question) || "");
    requestAnimationFrame(() => down.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  });
}

function initCategoryAccordion() {
  const sections = document.querySelectorAll(".cat-section");
  if (!sections.length) return;
  const isMobile = () => window.innerWidth <= 1024;

  function updateMode(forceClose = false) {
    const mobile = isMobile();
    sections.forEach((s) => {
      if (mobile) {
        s.classList.remove("cat-desktop");
        if (forceClose) s.removeAttribute("open");
      } else {
        s.classList.add("cat-desktop");
        s.setAttribute("open", "");
      }
    });
  }
  updateMode(true);

  sections.forEach((s) => {
    s.addEventListener("toggle", () => {
      if (!isMobile() && !s.open) s.setAttribute("open", "");
    });
    const more = s.querySelector(".cat-more-btn");
    if (more) more.addEventListener("click", (e) => e.stopPropagation());
  });

  let timer = null, wasMobile = isMobile();
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const now = isMobile();
      if (now !== wasMobile) { updateMode(true); wasMobile = now; }
      else updateMode(false);
    }, 100);
  });
}

loadCategoryBlocks();
initSearch();
initCategoryAccordion();