"""Кадр рендера → jaw.webp: щели внутри силуэта челюсти красятся в цвет полости рта,
снаружи остаётся прозрачно (на сайте виден светлый фон круга).

python fill_holes.py <кадр.png> <jaw.webp> [цвет=7d2d36] [радиус=14]

Щель от внешнего фона отличается тем, что не связана с краем кадра. Узкие каналы, которыми щели
всё же выходят наружу, перекрываются «закрытием» маски радиусом R пикселей.
"""
import sys

import numpy as np
from PIL import Image, ImageDraw

src, out = sys.argv[1], sys.argv[2]
color = sys.argv[3] if len(sys.argv) > 3 else "7d2d36"
R = int(sys.argv[4]) if len(sys.argv) > 4 else 14

im = Image.open(src).convert("RGBA")
rgba = np.asarray(im).astype(np.float32) / 255
transparent = rgba[..., 3] < 0.5
H, W = transparent.shape


def dilate(m, r):
    o = m.copy()
    for s in range(1, r + 1):
        o[:, s:] |= m[:, :-s]
        o[:, :-s] |= m[:, s:]
    t = o.copy()
    for s in range(1, r + 1):
        o[s:, :] |= t[:-s, :]
        o[:-s, :] |= t[s:, :]
    return o


def erode(m, r):
    return ~dilate(~m, r)


free = ~erode(dilate(~transparent, R), R)  # прозрачное после перекрытия узких каналов
# copy(): без неё floodfill в Pillow 13 не меняет картинку, собранную из массива
mask = Image.fromarray(np.where(free, 255, 0).astype(np.uint8)).copy()
border = [(x, 0) for x in range(0, W, 4)] + [(x, H - 1) for x in range(0, W, 4)] + \
         [(0, y) for y in range(0, H, 4)] + [(W - 1, y) for y in range(0, H, 4)]
for x, y in border:
    if mask.getpixel((x, y)) == 255:
        ImageDraw.floodfill(mask, (x, y), 128)
exterior = dilate(np.asarray(mask) == 128, R + 1) & transparent
hole = dilate(transparent & ~exterior, 2) & ~exterior  # +2 px — сгладить край щели
print(f"Внешний фон: {int(exterior.sum())} px, щели: {int((transparent & ~exterior).sum())} px")

cav = np.array([int(color[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255
al = rgba[..., 3:4]
res = rgba.copy()
res[..., :3] = np.where(hole[..., None], rgba[..., :3] * al + cav * (1 - al), rgba[..., :3])
res[..., 3] = np.where(hole, 1, rgba[..., 3])
Image.fromarray((res * 255 + 0.5).astype(np.uint8), "RGBA").save(out, "WEBP", quality=88, method=6)
print("Снимок:", out)
