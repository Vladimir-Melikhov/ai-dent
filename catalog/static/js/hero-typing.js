/* Заголовок главной: фразы печатаются как на машинке, курсор мигает (CSS).
   Фразы и пауза приходят из .env (HERO_PHRASES, HERO_TYPING_HOLD_SECONDS). «/» внутри фразы — перенос строки. */
(function () {
  var title = document.getElementById("hero-title");
  var typed = document.getElementById("hero-typed");
  var source = document.getElementById("hero-phrases");
  if (!title || !typed || !source) return;

  var phrases;
  try { phrases = JSON.parse(source.textContent); } catch (e) { return; }
  if (!Array.isArray(phrases) || phrases.length < 2) return;
  if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var hold = parseInt(title.dataset.hold, 10) || 6500;
  var TYPE_MS = 55;
  var ERASE_MS = 28;
  var index = 0;

  function render(text) {
    // textContent + <br>: фразы из .env никогда не попадают в HTML как разметка
    typed.textContent = "";
    text.split("/").forEach(function (line, i) {
      if (i) typed.appendChild(document.createElement("br"));
      typed.appendChild(document.createTextNode(line));
    });
  }

  function erase(text, done) {
    var length = text.length;
    (function step() {
      length -= 1;
      render(text.slice(0, Math.max(length, 0)));
      if (length > 0) setTimeout(step, ERASE_MS); else setTimeout(done, 350);
    })();
  }

  function type(text, done) {
    var length = 0;
    (function step() {
      length += 1;
      render(text.slice(0, length));
      if (length < text.length) setTimeout(step, TYPE_MS); else done();
    })();
  }

  function cycle() {
    setTimeout(function () {
      erase(phrases[index], function () {
        index = (index + 1) % phrases.length;
        type(phrases[index], cycle);
      });
    }, hold);
  }

  cycle();
})();
