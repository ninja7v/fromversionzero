# src/fromversionzero/components.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Reusable HUD widgets: product card, facts card, Shorts product hero, and the
# typographic generation mark used when a generation has no image.

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw
from PIL.ImageFont import FreeTypeFont

from fromversionzero.data_loader import ProductDataPoint

if TYPE_CHECKING:
    from fromversionzero.renderer import Renderer

MONTH_NAMES: tuple[str, ...] = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)  # fmt: skip

Box = tuple[float, float, float, float]  # x, y, w, h


def apply_alpha_to_image(img: Image.Image, alpha: float) -> Image.Image:
    """Scales the alpha channel of an RGBA image (for fades)."""
    if alpha >= 1.0:
        return img
    if alpha <= 0.0:
        return Image.new("RGBA", img.size, (0, 0, 0, 0))
    faded = img.copy()
    faded.putalpha(img.getchannel("A").point(lambda p: int(p * alpha)))
    return faded


def ease_out_back(t: float, overshoot: float = 1.70158) -> float:
    """0 -> slightly above 1 -> settles at 1."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    p = t - 1.0
    return p * p * ((overshoot + 1) * p + overshoot) + 1.0


def ease_in_back(t: float, overshoot: float = 1.70158) -> float:
    """Dips slightly below 0, then accelerates to 1."""
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * ((overshoot + 1) * t - overshoot)


def handoff(alpha: float) -> tuple[float, float]:
    """Sequential swap: the outgoing content fades out over the first 45%,
    the incoming fades in over the last 45% (no overlapping ghost text)."""
    return max(0.0, 1.0 - alpha / 0.45), max(0.0, min(1.0, (alpha - 0.55) / 0.45))


def date_label(point: ProductDataPoint, x_value: float) -> str:
    """'SEP 1993' when the CSV used YYYY.MM, else '1993'; '?' for speculation."""
    year = int(x_value)
    if point.has_month:
        month = max(1, min(12, round((x_value - year) * 12) + 1))
        label = f"{MONTH_NAMES[month - 1].upper()} {year}"
    else:
        label = str(year)
    return f"{label} ?" if point.is_speculation else label


def generation_code(point: ProductDataPoint, x_value: float) -> str:
    """Short identity for typographic marks: '(992)' -> '992', otherwise the year."""
    match = re.search(r"\(([^()]{1,10})\)\s*$", point.name)
    return match.group(1).strip() if match else str(int(x_value))


def fit_font(
    r: Renderer, role: str, size: float, text: str, max_width: float, min_size: float = 6.0
) -> FreeTypeFont:
    """Largest font (starting at ``size`` design units) that fits ``text`` in ``max_width``."""
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    font = r.font(role, size)
    while size > min_size and probe.textlength(text, font=font) > max_width:
        size *= 0.92
        font = r.font(role, size)
    return font


def draw_generation_mark(
    r: Renderer,
    draw: ImageDraw.ImageDraw,
    point: ProductDataPoint,
    cx: float,
    cy: float,
    max_w: float,
    max_h: float,
    alpha: float,
) -> None:
    """Typographic stand-in for a missing product image: a large, light generation code."""
    if alpha <= 0.0:
        return
    code = generation_code(point, r.dataset.x(point))
    font = fit_font(r, "display", max_h * 0.62 / r.unit, code, max_w * 0.9)
    width = draw.textlength(code, font=font)
    _left, top, _right, bottom = draw.textbbox((0, 0), code, font=font)
    text_h = bottom - top
    draw.text(
        (cx - width / 2, cy - text_h / 2 - top),
        code,
        fill=r.rgba("text_primary", 0.92 * alpha),
        font=font,
    )
    rule_y = cy + text_h / 2 + max_h * 0.08
    half = max_w * 0.08
    draw.line(
        [(cx - half, rule_y), (cx + half, rule_y)], fill=r.rgba("accent", alpha), width=2 * r.unit
    )


def _draw_speculation_badge(
    r: Renderer, draw: ImageDraw.ImageDraw, cx: float, cy: float, size: float, alpha: float
) -> None:
    font = r.font("heading", size)
    width = draw.textlength("?", font=font)
    radius = size * 0.75 * r.unit
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=r.rgba("background", 0.75 * alpha),
        outline=r.rgba("accent", 0.85 * alpha),
        width=2 * r.unit,
    )
    _left, top, _right, bottom = draw.textbbox((0, 0), "?", font=font)
    draw.text(
        (cx - width / 2, cy - (bottom - top) / 2 - top),
        "?",
        fill=r.rgba("text_primary", alpha),
        font=font,
    )


def _draw_visual(
    r: Renderer,
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    point: ProductDataPoint,
    cx: float,
    top: float,
    max_w: int,
    max_h: int,
    alpha: float,
    scale: float = 1.0,
) -> tuple[float, float]:
    """Draws the product image (or generation mark) centered at ``cx`` within
    ``max_h`` below ``top``. Returns the visual's (top, bottom) in pixels."""
    image = r.asset_manager.get_resized_product_image(point.image_filename, max_w, max_h)
    badge = (cx, top + max_h / 2)
    if image is None:
        draw_generation_mark(
            r, draw, point, cx, top + max_h / 2, max_w * scale, max_h * scale, alpha
        )
        bottom = top + max_h
        badge = (cx + max_w * 0.42, top + max_h * 0.12)  # corner, clear of the mark
    else:
        if scale != 1.0:
            image = image.resize(
                (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                r.downsample_filter,
            )
        faded = apply_alpha_to_image(image, alpha)
        x = int(cx - image.width / 2)
        y = int(top + (max_h - image.height) / 2)
        canvas.paste(faded, (x, y), faded)
        bottom = y + image.height
    if point.is_speculation and alpha > 0.0:
        size = (30 if r.shorts_mode else 22) * (0.7 if image is None else 1.0)
        _draw_speculation_badge(r, draw, badge[0], badge[1], size, alpha)
    return top, bottom


# ----------------------------------------------------------------------
# Landscape product card
# ----------------------------------------------------------------------
def draw_product_card(
    r: Renderer,
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    point: ProductDataPoint,
    prev: ProductDataPoint | None,
    box: Box,
    alpha: float,
) -> None:
    """Product picture (or generation mark), name and date in a framed card."""
    u = r.unit
    x, y, w, h = box
    draw.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=14 * u,
        fill=r.rgba("background", 0.86),
        outline=r.rgba("grid", 0.8),
        width=u,
    )
    draw.line([(x + 14 * u, y), (x + 64 * u, y)], fill=r.rgba("accent", 0.9), width=3 * u)

    def content(target: ProductDataPoint, a: float, rise: float) -> None:
        if a <= 0.0:
            return
        img_w, img_h = int(w * 0.82), int(h * 0.56)
        _draw_visual(r, canvas, draw, target, x + w / 2, y + 14 * u + rise, img_w, img_h, a)
        name = target.name.upper()
        font_name = fit_font(r, "label", 14, name, w - 24 * u)
        font_date = r.font("label", 10)
        name_y = y + h - 36 * u + rise
        name_w = draw.textlength(name, font=font_name)
        draw.text(
            (x + (w - name_w) / 2, name_y), name, fill=r.rgba("text_primary", a), font=font_name
        )
        date = date_label(target, r.dataset.x(target))
        date_w = draw.textlength(date, font=font_date)
        draw.text(
            (x + (w - date_w) / 2, name_y + 18 * u),
            date,
            fill=r.rgba("text_secondary", a),
            font=font_date,
        )

    if prev is not None and alpha < 1.0:
        out_a, in_a = handoff(alpha)
        content(prev, out_a, 0.0)
        content(point, in_a, (1.0 - in_a) * 6 * u)
    else:
        content(point, alpha if prev is None else 1.0, 0.0)


# ----------------------------------------------------------------------
# Facts card
# ----------------------------------------------------------------------
def split_bullets(fact_text: str) -> list[str]:
    """Splits a fact on ';' into bullets, capitalizing each one."""
    bullets = [part.strip() for part in fact_text.split(";") if part.strip()]
    return [b[0].upper() + b[1:] for b in bullets]


SPECULATION_PREFIX = "speculation:"


def fact_label_and_text(fact_text: str, default_label: str) -> tuple[str, str]:
    """Card label and body for a fact.

    Datasets start the fact of a speculation row with ``Speculation:``; that
    prefix becomes the card label instead of the first words of the body.
    """
    stripped = fact_text.strip()
    if stripped.lower().startswith(SPECULATION_PREFIX):
        return "Speculation", stripped[len(SPECULATION_PREFIX) :].strip()
    return default_label, fact_text


def draw_facts_card(
    r: Renderer,
    draw: ImageDraw.ImageDraw,
    point: ProductDataPoint | None,
    box: Box,
    alpha: float,
) -> None:
    """Framed card with a label and the generation's fact bullets."""
    from fromversionzero.renderer import wrap_text

    u = r.unit
    x, y, w, h = box
    if point is None or not point.fact or alpha <= 0.0:
        return
    # The whole card breathes in and out with each generation's insight.
    draw.rounded_rectangle(
        [x, y, x + w, y + h],
        radius=12 * u,
        fill=r.rgba("background", 0.86 * alpha),
        outline=r.rgba("accent", 0.43 * alpha),
        width=u,
    )
    draw.line(
        [(x, y + 16 * u), (x, y + h - 16 * u)], fill=r.rgba("accent", 0.86 * alpha), width=3 * u
    )

    label, fact_text = fact_label_and_text(point.fact, r.config.labels.facts)
    margin = 16 * u
    font_label = r.font("heading", 9)
    font_body = r.font("body", 10.5)
    draw.text(
        (x + margin, y + 11 * u),
        label.upper(),
        fill=r.rgba("accent", alpha),
        font=font_label,
    )
    line_h = 14 * u
    indent = 9 * u
    marker = 3 * u
    cursor = y + 29 * u
    limit = y + h - 8 * u
    for bullet in split_bullets(fact_text):
        lines = wrap_text(draw, bullet, font_body, w - 2 * margin - indent)
        if cursor + line_h > limit:
            break
        my = cursor + font_body.size * 0.45
        draw.ellipse(
            [x + margin, my - marker / 2, x + margin + marker, my + marker / 2],
            fill=r.rgba("accent", alpha),
        )
        for line in lines:
            if cursor + line_h > limit:
                break
            draw.text(
                (x + margin + indent, cursor),
                line,
                fill=r.rgba("text_primary", 0.88 * alpha),
                font=font_body,
            )
            cursor += line_h
        cursor += 4 * u


# ----------------------------------------------------------------------
# Shorts product hero (no frame, large visual, title above)
# ----------------------------------------------------------------------
def draw_product_hero_shorts(
    r: Renderer,
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    point: ProductDataPoint,
    prev: ProductDataPoint | None,
    progress: float,
    time_elapsed: float,
    cx: float,
    top: float,
    max_w: int,
    max_h: int,
) -> None:
    """Large product visual with its name above, swapped with the configured transition:
    ``fade`` (sequential), ``small_to_big_to_medium`` (shrink out, overshoot in) or ``none``."""
    u = r.unit
    p = max(0.0, min(1.0, progress))
    float_dy = math.sin(time_elapsed * 1.6) * 7 * u

    def title(target: ProductDataPoint, a: float) -> None:
        if a <= 0.0:
            return
        text = target.name.upper()
        font = fit_font(r, "heading", 80, text, r.draw_w - 80 * u)
        width = draw.textlength(text, font=font)
        # Baseline anchored above the visual's box, clear of its float, so a
        # tall image that fills the box never runs into the title.
        tx, ty = cx - width / 2, top - 20 * u
        shadow = r.rgba("background", 0.8 * a)
        draw.text((tx + 2 * u, ty + 2 * u), text, fill=shadow, font=font, anchor="ls")
        draw.text((tx, ty), text, fill=r.rgba("text_primary", a), font=font, anchor="ls")

    def visual(target: ProductDataPoint, a: float, scale: float = 1.0, dy: float = 0.0) -> None:
        if a > 0.0 and scale > 0.0:
            _draw_visual(r, canvas, draw, target, cx, top + dy, max_w, max_h, a, scale)

    transition = r.config.image_transition
    if transition == "none" or prev is None:
        visual(point, 1.0, dy=float_dy if p >= 1.0 else 0.0)
        title(point, 1.0)
    elif transition == "small_to_big_to_medium":
        if p < 0.2:
            sp = p / 0.2
            visual(prev, 1.0 - sp, max(0.0, 1.0 - ease_in_back(sp)))
            title(prev, 1.0 - sp)
        else:
            gp = (p - 0.2) / 0.8
            visual(
                point,
                min(1.0, gp * 2.0),
                ease_out_back(gp, overshoot=2.6),
                float_dy if p >= 1.0 else 0.0,
            )
            title(point, min(1.0, gp * 2.0))
    else:  # sequential fade
        if p < 1.0:
            out_a, in_a = handoff(p)
            visual(prev, out_a)
            title(prev, out_a)
            visual(point, in_a, dy=(1.0 - in_a) * 12 * u)
            title(point, in_a)
        else:
            visual(point, 1.0, dy=float_dy)
            title(point, 1.0)
