"""页面版式几何：文本渲染与 bbox 定位的唯一事实来源。

合成票据的页面图片由本模块按固定版式渲染（白底黑字、左侧 16px、行高 26px），
抽取阶段再用同一套几何把字段值换算成像素坐标，因此证据框与图片文字严格对齐。

接入真实 OCR/VLM 后，bbox 由模型返回，本模块仅用于本地替身（stub）链路。
"""

from __future__ import annotations

import functools
import io

PAGE_WIDTH = 900
MARGIN_X = 16
TOP_Y = 12
FONT_SIZE = 18
LINE_HEIGHT = 26
FALLBACK_LINE_HEIGHT = 16

_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


@functools.lru_cache(maxsize=1)
def load_font():
    """加载中文字体；缺少 Pillow 或字体时返回 None。"""
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    for candidate in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, FONT_SIZE)
        except (OSError, IOError):
            continue
    return None


def line_height() -> int:
    return LINE_HEIGHT if load_font() is not None else FALLBACK_LINE_HEIGHT


def measure(text: str) -> float:
    """测量文本像素宽度；无字体时按「中文 18px、西文 9px」估算。"""
    font = load_font()
    if font is not None:
        try:
            return float(font.getlength(text))
        except AttributeError:  # 极旧版本 Pillow 无 getlength
            pass
    return sum(FONT_SIZE if _is_wide(ch) else FONT_SIZE / 2 for ch in text)


def _is_wide(ch: str) -> bool:
    return ord(ch) > 0x2E7F


def render_text_png(text: str, width: int = PAGE_WIDTH) -> bytes:
    """把文本渲染为白底黑字 PNG；缺少 Pillow 时返回空字节。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return b""

    font = load_font()
    lh = line_height()
    lines = text.splitlines() or [""]
    height = max(200, 24 + lh * len(lines))
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    y = TOP_Y
    for line in lines:
        draw.text((MARGIN_X, y), line, fill="black", font=font)
        y += lh

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def locate_bbox(text: str, start: int, end: int) -> list[int] | None:
    """把文本区间换算为页面像素 bbox [x1, y1, x2, y2]。

    区间跨行时只取首行片段（票据字段均为单行）。
    """
    if start < 0 or end <= start or start >= len(text):
        return None
    line_start = text.rfind("\n", 0, start) + 1
    line_index = text.count("\n", 0, line_start)
    newline = text.find("\n", start)
    line_end = end if newline < 0 or newline > end else newline

    prefix = text[line_start:start]
    segment = text[start:line_end].rstrip()
    if not segment:
        return None

    x1 = MARGIN_X + measure(prefix)
    x2 = x1 + measure(segment)
    y1 = TOP_Y + line_index * line_height()
    box = [
        max(0, int(round(x1)) - 2),
        max(0, int(round(y1)) - 2),
        int(round(x2)) + 2,
        int(round(y1)) + FONT_SIZE + 4,
    ]
    return box
