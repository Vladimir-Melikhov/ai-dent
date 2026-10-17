/* ============================================================
   ИЗБРАННОЕ
   ============================================================ */

   function getCookie(name) {
    const m = document.cookie.match(
      new RegExp("(^| )" + name + "=([^;]+)")
    );
  
    return m ? m[2] : "";
  }
  
  
  /* ============================================================
     TOGGLE ИЗБРАННОГО
     ============================================================ */
  
  async function toggleFav(productId) {
    const res = await fetch("/accounts/shortlist/toggle/", {
      method: "POST",
  
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken"),
      },
  
      body: JSON.stringify({
        product_id: productId,
      }),
    });
  
    if (!res.ok) {
      throw new Error("Ошибка изменения избранного");
    }
  
    const data = await res.json();
  
    return data.active;
  }
  
  
  /* ============================================================
     ЗАГРУЗКА ID ИЗБРАННЫХ ТОВАРОВ
     ============================================================ */
  
  async function loadFavorites() {
    try {
      const res = await fetch("/accounts/shortlist/ids/");
  
      if (!res.ok) {
        throw new Error("Ошибка загрузки избранного");
      }
  
      const data = await res.json();
  
      return new Set(data.product_ids || []);
  
    } catch (error) {
  
      console.error(
        "Ошибка загрузки избранного:",
        error
      );
  
      return new Set();
    }
  }
  
  
  /* ============================================================
     ИНИЦИАЛИЗАЦИЯ СЕРДЕЧЕК
     ============================================================ */
  
  export async function initShortlist() {
  
    const favIds = await loadFavorites();
  
    document
      .querySelectorAll(".product-card__fav")
      .forEach(btn => {
  
        /* Защита от повторной привязки */
  
        if (btn.dataset.favBound) {
          return;
        }
  
        btn.dataset.favBound = "1";
  
  
        const productId =
          Number(btn.dataset.productId);
  
  
        /* Уже есть в избранном */
  
        if (favIds.has(productId)) {
          btn.classList.add("is-active");
        }
  
  
        /* Нажатие */
  
        btn.addEventListener(
          "click",
          async event => {
  
            event.preventDefault();
            event.stopPropagation();
  
  
            if (btn.disabled) {
              return;
            }
  
  
            btn.disabled = true;
  
  
            try {
  
              const active =
                await toggleFav(productId);
  
  
              btn.classList.toggle(
                "is-active",
                active
              );
  
  
            } catch (error) {
  
              console.error(
                "Ошибка изменения избранного:",
                error
              );
  
            } finally {
  
              btn.disabled = false;
  
            }
  
          }
        );
  
      });
  }