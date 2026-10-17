(function () {
  var toggle = document.getElementById("nav-toggle");
  var panel = document.getElementById("mobnav");
  var overlay = document.getElementById("mobnav-overlay");
  var closeBtn = document.getElementById("mobnav-x");
  if (!toggle || !panel) return;

  function open() {
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
  }

  function close() {
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  toggle.addEventListener("click", open);
  if (closeBtn) closeBtn.addEventListener("click", close);
  if (overlay) overlay.addEventListener("click", close);

  panel.querySelectorAll(".mobnav__links a").forEach(function (a) {
    a.addEventListener("click", close);
  });
})();
