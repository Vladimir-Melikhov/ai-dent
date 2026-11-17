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
    ["kind", "demo", "depth", "weight", "tilt", "vertical", "optics", "mount"].forEach(n => show(row("loupe_" + n), on));
    show(group("loupe_options"), on);
    show(group("loupe_distances"), on);
    show(group("loupe_frames"), on);
    show(group("loupe_ipds"), on && kind && kind.value === "individual");
  }

  box.addEventListener("change", sync);
  if (kind) kind.addEventListener("change", sync);
  sync();
});
