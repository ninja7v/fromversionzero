# src/fromversionzero/drawing.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Drawing primitives shared by the renderer and its composition modules:
# the alpha-blending draw handle and word wrapping.

from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageDraw
from PIL.ImageFont import FreeTypeFont


class BlendDraw(ImageDraw.ImageDraw):
    """``ImageDraw`` that alpha-blends onto an RGB image, *including text*.

    Pillow's blend mode honours fill alpha for shapes but draws text fully
    opaque; translucent text is therefore rendered through a coverage mask.
    """

    def __init__(self, image: Image.Image) -> None:
        super().__init__(image, "RGBA")
        self._target = image

    def text(
        self, xy: Any, text: Any, fill: Any = None, font: Any = None, *args: Any, **kwargs: Any
    ) -> None:
        alpha = fill[3] if isinstance(fill, tuple) and len(fill) == 4 else 255
        if alpha >= 255 or not text:
            super().text(xy, text, fill, font, *args, **kwargs)
            return
        if alpha <= 0:
            return
        left, top, right, bottom = self.textbbox(xy, text, font=font, **kwargs)
        x0, y0 = math.floor(left) - 1, math.floor(top) - 1
        x1, y1 = math.ceil(right) + 1, math.ceil(bottom) + 1
        if x1 <= x0 or y1 <= y0:
            return
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(mask).text((xy[0] - x0, xy[1] - y0), text, fill=alpha, font=font, **kwargs)
        self._target.paste(tuple(fill[:3]), (x0, y0, x1, y1), mask)


def blend_draw(image: Image.Image) -> BlendDraw:
    return BlendDraw(image)


def wrap_text(
    draw: ImageDraw.ImageDraw, text: str, font: FreeTypeFont, max_width: float
) -> list[str]:
    """Greedy word wrap to ``max_width`` pixels."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if not current or draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
