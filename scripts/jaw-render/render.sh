#!/usr/bin/env bash
# Перерендер снимка челюсти для блока «Как это выглядит» → catalog/static/img/jaw.webp
#
#   scripts/jaw-render/render.sh [span=36] [yaw=35] [dx=8]
#
# span — поперечник кадра в мм модели; после смены поставьте такое же JAW_SPAN в catalog/static/js/product.js.
# yaw — поворот вбок, °; dx — сдвиг центра кадра вбок, мм. Переменные: PX (размер, 2800), PITCH (6),
# CAVITY (цвет полости рта, 7d2d36), OUT (куда писать), PYTHON (нужны numpy и pillow), CHROME.
# Нужны Google Chrome, Node и интернет (three.js с CDN). Инструкция — docs/калибровка-увеличений.pdf
set -euo pipefail

DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$DIR/../.." && pwd)
SPAN=${1:-36}
YAW=${2:-35}
DX=${3:-8}
PX=${PX:-2800}
PITCH=${PITCH:-6}
CAVITY=${CAVITY:-7d2d36}
OUT=${OUT:-$ROOT/catalog/static/img/jaw.webp}
PORT=8765

# Python с numpy и pillow: свой venv рядом со скриптом (в git не попадает)
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "== Ставлю numpy и pillow в $DIR/.venv"
    python3 -m venv "$DIR/.venv"
    "$DIR/.venv/bin/pip" install -q numpy pillow
  fi
  PY="$DIR/.venv/bin/python"
fi

TMP=$(mktemp -d)
python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$DIR" >/dev/null 2>&1 &
SRV=$!
disown "$SRV"
trap 'kill $SRV 2>/dev/null; rm -rf "$TMP"' EXIT
sleep 1

echo "== Рендер: кадр ${SPAN} мм, поворот ${YAW}°, сдвиг ${DX} мм, ${PX} px"
node "$DIR/render_shot.mjs" "http://127.0.0.1:$PORT/render.html?px=$PX&span=$SPAN&yaw=$YAW&dx=$DX&pitch=$PITCH" "$TMP/frame.png"
"$PY" "$DIR/fill_holes.py" "$TMP/frame.png" "$OUT" "$CAVITY"
echo "Готово. Если меняли span — JAW_SPAN = $SPAN в catalog/static/js/product.js"
