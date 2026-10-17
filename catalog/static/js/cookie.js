(function () {
  var KEY = "ai-dent-cookie-accepted";
  var banner = document.getElementById("cookie-banner");
  if (!banner) return;

  var accepted = false;
  try {
    accepted = localStorage.getItem(KEY) === "1";
  } catch (e) {
    accepted = false;
  }

  if (!accepted) {
    banner.hidden = false;
  }

  var acceptBtn = document.getElementById("cookie-accept");
  if (acceptBtn) {
    acceptBtn.addEventListener("click", function () {
      try {
        localStorage.setItem(KEY, "1");
      } catch (e) {
        /* localStorage недоступен — просто скрываем баннер на сессию */
      }
      banner.hidden = true;
    });
  }
})();
