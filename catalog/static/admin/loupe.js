// Товар в админке: галочка «Бинокулярная лупа» раскрывает поля лупы,
// межзрачковое расстояние показывается только для индивидуальных (универсальным оно не нужно).
document.addEventListener("DOMContentLoaded", () => {
  const box = document.getElementById("id_is_loupe");
  if (!box) return;
  const kind = document.getElementById("id_loupe_kind");
  const row = name => document.querySelector(`.field-${name}`);
  const group = name => document.getElementById(`${name}-group`);
  const show = (el, on) => { if (el) el.style.display = on ? "" : "none"; };

  function sync() {
    const on = box.checked;
    show(row("loupe_kind"), on);
    show(group("loupe_options"), on);
    show(group("loupe_distances"), on);
    show(row("loupe_ipd"), on && kind && kind.value === "individual");
  }

  box.addEventListener("change", sync);
  if (kind) kind.addEventListener("change", sync);
  sync();
});
