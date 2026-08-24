import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 项目根目录（脚本位于 scripts/ 下，向上取一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = PROJECT_ROOT / "icons"

# 渐变配色（与 index.html 中 --grad-start/--grad-mid/--grad-end 保持一致）
COLOR_START = (34, 211, 238)     # 青色 #22d3ee
COLOR_MID = (99, 102, 241)       # 靛蓝 #6366f1
COLOR_END = (168, 85, 247)       # 紫色 #a855f7


def lerp_color(color_a, color_b, k):
    """两色线性插值。"""
    return tuple(int(color_a[c] + (color_b[c] - color_a[c]) * k) for c in range(3))


def linear_gradient(size: int) -> Image.Image:
    """生成 135° 方向的线性渐变底图（青 → 靛 → 紫）。

    Args:
        size: 正方形边长（像素）

    Returns:
        RGB 模式的渐变图像
    """
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)

    # 135° 方向上的投影映射：
    # 投影值 p = x + y，范围为 0 到 2*(size-1)
    # 左上角 p 最小（青色），右下角 p 最大（紫色）
    max_p = 2 * (size - 1)

    for x in range(size):
        for y in range(size):
            t = (x + y) / max_p  # 归一化到 [0, 1]
            if t <= 0.55:
                k = t / 0.55
                color = lerp_color(COLOR_START, COLOR_MID, k)
            else:
                k = (t - 0.55) / 0.45
                color = lerp_color(COLOR_MID, COLOR_END, k)
            draw.point((x, y), fill=color)

    return img


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """按平台加载合适的粗体字体（Windows 优先 Arial Bold，缺失时用默认字体）。

    Args:
        size: 字号

    Returns:
        FreeTypeFont 对象
    """
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",      # Windows Arial Bold
        "C:/Windows/Fonts/msyhbd.ttc",       # Windows 微软雅黑 Bold
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    # 兜底：Pillow 内置默认字体（无衬线，仅支持英文）
    return ImageFont.load_default()


def make_icon(size: int) -> Image.Image:
    """绘制单个尺寸的应用图标。

    Args:
        size: 图标边长（192 或 512）

    Returns:
        RGBA 图标图像（带圆角与内容）
    """
    # 圆角半径按比例取 22%，视觉上类似 iOS 应用圆角
    radius = int(size * 0.22)

    # 1. 渐变底图 + 圆角蒙版
    base = linear_gradient(size).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    base.putalpha(mask)

    draw = ImageDraw.Draw(base)

    # 2. 装饰：白色半透明圆环（科技感轨道）
    ring_pad = int(size * 0.14)
    draw.ellipse(
        [ring_pad, ring_pad, size - ring_pad, size - ring_pad],
        outline=(255, 255, 255, 70),
        width=max(2, int(size * 0.012)),
    )

    # 3. 中心 "AI" 字样（占画布约 40%）
    font = load_font(int(size * 0.36))
    text = "AI"
    # 测量文本尺寸以精确居中
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )

    # 4. 右上角小光点（渐变亮色点缀）
    dot_r = max(3, int(size * 0.045))
    dot_x, dot_y = size - int(size * 0.26), int(size * 0.24)
    draw.ellipse(
        [dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r],
        fill=(236, 72, 153, 255),  # 粉色 #ec4899
    )

    return base


def main() -> None:
    """生成 192 与 512 两种尺寸的图标到 icons/ 目录。"""
    ICONS_DIR.mkdir(exist_ok=True)

    for size in (192, 512):
        icon = make_icon(size)
        out_path = ICONS_DIR / f"icon-{size}.png"
        icon.save(out_path, "PNG")
        print(f"✅ 已生成 {out_path}（{size}x{size}）")


if __name__ == "__main__":
    main()
