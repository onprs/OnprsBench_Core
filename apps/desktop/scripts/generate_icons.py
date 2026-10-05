"""生成 OnprsBench 应用图标（维护工具，输出到 src-tauri/icons/）。

设计：深色圆角方形背景 + 三根上升的柱状条（benchmark 含义），
柱条使用品牌蓝渐变，顶端叠加一条上升趋势线。

运行（仓库根目录）：
    server/.venv/Scripts/python apps/desktop/scripts/generate_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ICONS_DIR = Path(__file__).resolve().parents[1] / "src-tauri" / "icons"

# 品牌色
BG_TOP = (24, 28, 43)  # #181C2B
BG_BOTTOM = (13, 16, 25)  # #0D1019
BAR_COLORS = [(59, 130, 246), (96, 165, 250), (147, 197, 253)]  # blue-500/400/300
LINE_COLOR = (250, 204, 21)  # amber-400 趋势线


def vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    """垂直线性渐变底图。"""
    img = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        img.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img.resize((size, size))


def draw_icon(size: int) -> Image.Image:
    """按给定尺寸绘制图标（RGBA）。"""
    s = size
    img = vertical_gradient(s, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # 圆角方形蒙版
    mask = Image.new("L", (s, s), 0)
    mask_draw = ImageDraw.Draw(mask)
    radius = round(s * 0.22)
    mask_draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
    img.putalpha(mask)

    # 三根上升柱条
    bar_w = s * 0.13
    gap = s * 0.075
    total_w = bar_w * 3 + gap * 2
    x0 = (s - total_w) / 2
    baseline = s * 0.72
    heights = [s * 0.18, s * 0.32, s * 0.46]
    bar_radius = bar_w * 0.28
    tops = []
    for i, h in enumerate(heights):
        left = x0 + i * (bar_w + gap)
        top = baseline - h
        draw.rounded_rectangle(
            [left, top, left + bar_w, baseline], radius=bar_radius, fill=BAR_COLORS[i]
        )
        tops.append((left + bar_w / 2, top))

    # 趋势线（连接柱顶并向右上方延伸的折线 + 末端箭头）
    line_w = max(2, round(s * 0.018))
    pts = [(x, y - s * 0.055) for x, y in tops]
    pts.append((pts[-1][0] + s * 0.10, pts[-1][1] - s * 0.10))
    draw.line(pts, fill=LINE_COLOR, width=line_w, joint="curve")
    # 箭头
    ex, ey = pts[-1]
    a = s * 0.045
    draw.polygon(
        [(ex, ey), (ex - a * 1.15, ey - a * 0.15), (ex - a * 0.15, ey + a * 1.15)],
        fill=LINE_COLOR,
    )

    return img


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    master = draw_icon(1024)
    sizes = {
        "icon.png": 512,
        "128x128.png": 128,
        "128x128@2x.png": 256,
        "32x32.png": 32,
    }
    for name, size in sizes.items():
        master.resize((size, size), Image.LANCZOS).save(ICONS_DIR / name)

    # Windows ICO（内嵌多尺寸）
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(ICONS_DIR / "icon.ico", sizes=ico_sizes)

    # macOS ICNS
    master.save(ICONS_DIR / "icon.icns", sizes=[(s, s) for s in (16, 32, 64, 128, 256, 512)])

    print("icons written:", sorted(p.name for p in ICONS_DIR.iterdir()))


if __name__ == "__main__":
    main()
