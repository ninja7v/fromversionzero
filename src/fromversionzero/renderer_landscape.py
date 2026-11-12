# src/fromversionzero/renderer_landscape.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Landscape composition: left HUD column (product card, facts, logos), the
# generation rail, and the kinetic intro/outro cards. Coordinates are design
# units (reference canvas 480 units tall) multiplied by ``r.unit``.

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance

from fromversionzero.asset_manager import text_logo
from fromversionzero.components import (
    apply_alpha_to_image,
    draw_facts_card,
    draw_generation_mark,
    draw_product_card,
)
from fromversionzero.drawing import blend_draw, wrap_text
from fromversionzero.interpolator import ease_sigmoid

if TYPE_CHECKING:
    from fromversionzero.renderer import Renderer
    from fromversionzero.timeline import FrameState

r"""
Landscape layout (design units, 480 tall):
 ______________________________________________________________________
| [product card 20..256]  | GEN 05/12 • NAME  ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬ |
|   image / generation    | ▬ LEGEND                            ● value|
|   NAME  ·  date      ×10|- - - - - - - - - - - - - - - - - -/- - - - |
| [facts card]          ×2|- - - - - - - - - - - - - - -_____/- - - - -|
|  • insight             ×1|______________________/                    |
|                         |                                            |
| [FVZ] [brand]           |__|____|____|____|____|____|____|____|______|
|                           1960  1970  1980  1990  2000  2010  2020   |
|                                 “Brand motto”                        |
|______________________________________________________________________|
"""

CARD_X, CARD_W = 20, 236
PRODUCT_CARD = (CARD_X, 40, CARD_W, 160)
FACTS_CARD = (CARD_X, 210, CARD_W, 118)
LOGO_BOX, LOGO_GAP, LOGO_MARGIN = 104, 12, 20


def _box(r: Renderer, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(v * r.unit for v in box)  # type: ignore[return-value]


def render_frame_landscape(r: Renderer, state: FrameState) -> np.ndarray:
    canvas, draw = r.new_canvas()
    with r.faded(r.graph_alpha_for(state)):
        r.draw_ambient(draw, state.t)
        r.draw_grid_and_axes(draw, state.camera_bounds, state.revealed_pcts)
        r.draw_curves(canvas, draw, state.revealed_pcts, state.camera_bounds)
        if r.config.show_legend:
            r.draw_legend(draw, state.revealed_pcts)
        if state.intro_progress is None and state.outro_progress is None:
            draw_hud(r, canvas, draw, state)
    if state.intro_progress is not None:
        draw_intro_overlay(r, canvas, state.intro_progress)
    elif state.outro_progress is not None:
        draw_outro_overlay(r, canvas, state.outro_progress)
    return r.to_frame(canvas)


def draw_hud(
    r: Renderer, canvas: Image.Image, draw: ImageDraw.ImageDraw, state: FrameState
) -> None:
    last = r.last_revealed_index(state.revealed_pcts)
    if last >= 0:
        point = r.data_points[last]
        prev = r.data_points[last - 1] if last > 0 else None
        alpha = min(1.0, state.revealed_pcts[last])
        draw_product_card(r, canvas, draw, point, prev, _box(r, PRODUCT_CARD), ease_sigmoid(alpha))
        if r.config.show_facts:
            fact_point = (
                r.data_points[state.active_fact_index] if state.active_fact_index >= 0 else None
            )
            draw_facts_card(r, draw, fact_point, _box(r, FACTS_CARD), state.fact_card_alpha)
    if r.config.show_progress_bar:
        draw_generation_rail(r, draw, state.revealed_pcts)
    draw_logos(r, canvas, state.t)
    r.draw_motto(draw)


def draw_generation_rail(
    r: Renderer, draw: ImageDraw.ImageDraw, revealed: tuple[float, ...]
) -> None:
    """Segmented progress rail above the graph with the current generation's name."""
    n = len(r.data_points)
    if not n:
        return
    u = r.unit
    left, _, right, _ = r.graph_box
    y, gap, height = 24 * u, 3 * u, 3 * u
    segment = max(2 * u, (right - left - gap * (n - 1)) / n)
    for i, pct in enumerate(revealed):
        x0 = left + i * (segment + gap)
        draw.rounded_rectangle(
            [x0, y, x0 + segment, y + height], radius=u, fill=r.rgba("grid", 0.6)
        )
        if pct > 0:
            draw.rounded_rectangle(
                [x0, y, x0 + segment * min(1.0, pct), y + height],
                radius=u,
                fill=r.rgba("accent", 0.9),
            )
    last = r.last_revealed_index(revealed)
    if last >= 0:
        font = r.font("heading", 8.5)
        name = r.data_points[last].name.upper()[:48]
        draw.text(
            (left, 8 * u),
            f"GEN {last + 1:02d}/{n:02d}  •  {name}",
            fill=r.rgba("accent", 0.9),
            font=font,
        )


def _logo_brightness(t: float) -> float:
    """Slow pseudo-random 'breathing' of the brand mark."""
    return 0.85 + 0.35 * (
        math.sin(0.6 * t) * 0.5 + math.sin(0.25 * t + 1.5) * 0.35 + math.sin(1.1 * t + 3.8) * 0.15
    )


def product_logo(r: Renderer, box: int) -> Image.Image:
    logo = r.asset_manager.get_product_logo(box)
    if logo is not None:
        return logo
    # Drawn once per size: this fallback is pasted on every frame.
    cache = r.asset_manager.image_cache
    key = f"__text_logo_{box}"
    if cache.get(key) is None:
        brand = os.path.basename(os.path.normpath(r.dataset_dir)).split("_")[0].upper()
        cache[key] = text_logo(brand, r.font("heading", 10), box, r.rgba("accent")[:3])
    fallback = cache[key]
    assert fallback is not None
    return fallback


def draw_logos(r: Renderer, canvas: Image.Image, time_elapsed: float) -> None:
    """FromVersionZero mark and the product's brand logo, bottom-left, equal squares."""
    if not r.config.show_logo:
        return
    u = r.unit
    box = LOGO_BOX * u
    x, y = LOGO_MARGIN * u, r.draw_h - box - LOGO_MARGIN * u
    mark = r.asset_manager.get_brand_mark(box)
    if mark is not None:
        lit = ImageEnhance.Brightness(mark).enhance(_logo_brightness(time_elapsed))
        lit.putalpha(mark.getchannel("A"))
        canvas.paste(lit, (x, y), lit)
    brand = product_logo(r, box)
    canvas.paste(brand, (x + box + LOGO_GAP * u, y), brand)


def _paste_centered(
    r: Renderer,
    layer: Image.Image,
    image: Image.Image,
    cx: float,
    cy: float,
    width: int,
    alpha: float,
) -> None:
    if alpha <= 0.0:
        return
    height = max(1, int(width * image.height / image.width))
    resized = apply_alpha_to_image(image.resize((width, height), r.downsample_filter), alpha)
    layer.paste(resized, (int(cx - width / 2), int(cy - height / 2)), resized)


def _hero_image(r: Renderer, index: int, size: int) -> Image.Image | None:
    """Product image of generation ``index`` or, failing that, the category icon."""
    point = r.data_points[index]
    image = r.asset_manager.get_product_image(point.image_filename)
    if image is not None:
        return image
    return r.asset_manager.get_category_icon(size, r.rgba("line")[:3])


def _stage(progress: float, start: float, duration: float) -> float:
    return ease_sigmoid(max(0.0, min(1.0, (progress - start) / duration)))


def draw_intro_overlay(r: Renderer, canvas: Image.Image, intro_progress: float) -> None:
    """Kinetic 'dossier' title card: kicker, title, key stats and the first product."""
    u = r.unit
    progress = max(0.0, min(1.0, intro_progress))
    visible = 1.0 - _stage(progress, 0.72, 0.28)
    accent, primary, secondary = r.rgba("accent"), r.rgba("text_primary"), r.rgba("text_secondary")

    layer = canvas
    draw = blend_draw(canvas)
    draw.rectangle(
        [0, 0, r.draw_w, r.draw_h], fill=(*r.base_colors["background"][:3], int(248 * visible))
    )
    for i in range(8):
        x = r.draw_w - (55 + i * 28) * u
        draw.line(
            [(x, 0), (x - 180 * u, r.draw_h)],
            fill=(*accent[:3], int((14 + i * 2) * visible)),
            width=8 * u,
        )

    kicker = _stage(progress, 0.0, 0.18)
    title_in = _stage(progress, 0.08, 0.30)
    image_in = _stage(progress, 0.16, 0.34)
    stats_in = _stage(progress, 0.38, 0.25)
    text_x = (62 - 28 * (1.0 - title_in)) * u

    draw.text(
        (62 * u, 52 * u),
        r.config.labels.intro_kicker.upper(),
        fill=(*accent[:3], int(255 * kicker * visible)),
        font=r.font("heading", 9),
    )
    draw.line(
        [(62 * u, 74 * u), ((62 + 185 * kicker) * u, 74 * u)],
        fill=(*accent[:3], int(230 * visible)),
        width=3 * u,
    )
    font_title = r.font("display", 34)
    title_y = 100 * u
    for line in wrap_text(draw, r.config.title.upper(), font_title, 440 * u)[:3]:
        draw.text(
            (text_x, title_y),
            line,
            fill=(*primary[:3], int(255 * title_in * visible)),
            font=font_title,
        )
        title_y += 42 * u

    released = r.dataset.released
    if released:
        first, last = int(r.dataset.x(released[0])), int(r.dataset.x(released[-1]))
        stats = (
            f"{len(released):02d} GENERATIONS   •   {len(r.y_metrics):02d} METRICS   •   "
            f"{first} — {last}"
        )
        draw.text(
            (62 * u, max(title_y + 18 * u, 270 * u)),
            stats,
            fill=(*secondary[:3], int(245 * stats_in * visible)),
            font=r.font("label", 11),
        )

    hero = _hero_image(r, 0, 280 * u)
    if hero is not None and image_in > 0:
        cx, cy = r.draw_w - 205 * u, r.draw_h // 2
        for radius in (88, 116, 148):
            rr = radius * u
            draw.ellipse(
                [cx - rr, cy - rr, cx + rr, cy + rr],
                outline=(*accent[:3], int(max(15, 120 * image_in // (radius // 50)) * visible)),
                width=u,
            )
        sweep = int(360 * progress)
        draw.arc(
            [cx - 132 * u, cy - 132 * u, cx + 132 * u, cy + 132 * u],
            start=sweep,
            end=sweep + 82,
            fill=(*accent[:3], int(255 * image_in * visible)),
            width=4 * u,
        )
        width = int((185 + 22 * math.sin(math.pi * image_in)) * u)
        _paste_centered(r, layer, hero, cx, cy, width, image_in * visible)
    if progress > 0.58:
        bar = _stage(progress, 0.58, 0.42)
        draw.rectangle(
            [62 * u, r.draw_h - 34 * u, (62 + 260 * bar) * u, r.draw_h - 30 * u],
            fill=(*accent[:3], int(230 * visible)),
        )


def draw_outro_overlay(r: Renderer, canvas: Image.Image, outro_progress: float) -> None:
    """Recap card: first -> latest generation, then the call to action."""
    released = r.dataset.released or r.data_points
    if not released:
        return
    u = r.unit
    progress = _stage(outro_progress, 0.0, 1.0)
    labels = r.config.labels
    accent, primary, secondary = r.rgba("accent"), r.rgba("text_primary"), r.rgba("text_secondary")
    background = r.base_colors["background"]
    cx = r.draw_w / 2

    layer = canvas
    draw = blend_draw(canvas)
    cover = int(255 * min(1.0, max(0.0, outro_progress) / 0.25))
    draw.rectangle([0, 0, r.draw_w, r.draw_h], fill=(*background[:3], cover))

    def centered(
        text: str, y: float, font_role: str, size: float, color: tuple[int, ...], a: float
    ) -> None:
        font = r.font(font_role, size)
        width = draw.textlength(text, font=font)
        draw.text((cx - width / 2, y), text, fill=(*color[:3], int(255 * a)), font=font)

    centered(labels.outro_kicker.upper(), 40 * u, "heading", 9, accent, progress)
    first_x, last_x = int(r.dataset.x(released[0])), int(r.dataset.x(released[-1]))
    centered(f"{first_x}  →  {last_x}", 66 * u, "display", 27, primary, progress)
    centered(
        f"{len(released):02d} RELEASES  •  {len(r.y_metrics):02d} METRICS MAPPED",
        108 * u,
        "body",
        11,
        secondary,
        progress * 0.95,
    )

    image_in = _stage(outro_progress, 0.12, 0.46)
    image_y = 232 * u
    font_label = r.font("heading", 9)
    for index, x in ((0, cx - 160 * u), (len(released) - 1, cx + 160 * u)):
        point = released[index]
        ring = 86 * u
        draw.ellipse(
            [x - ring, image_y - ring, x + ring, image_y + ring],
            outline=(*accent[:3], int(90 * image_in)),
            width=2 * u,
        )
        image = r.asset_manager.get_product_image(point.image_filename)
        if image is not None:
            _paste_centered(r, layer, image, x, image_y, 130 * u, image_in)
        else:
            draw_generation_mark(r, draw, point, x, image_y, 120 * u, 90 * u, image_in)
        name = point.name.upper()[:26]
        width = draw.textlength(name, font=font_label)
        draw.text(
            (x - width / 2, image_y + 96 * u),
            name,
            fill=(*secondary[:3], int(240 * image_in)),
            font=font_label,
        )

    arrow = (*accent[:3], int(220 * image_in))
    draw.line([(cx - 55 * u, image_y), (cx + 55 * u, image_y)], fill=arrow, width=2 * u)
    draw.polygon(
        [(cx + 55 * u, image_y), (cx + 45 * u, image_y - 6 * u), (cx + 45 * u, image_y + 6 * u)],
        fill=arrow,
    )

    cta = _stage(outro_progress, 0.48, 0.42)
    centered(labels.outro_question.upper(), r.draw_h - 104 * u, "display", 24, primary, cta)
    prompt = labels.outro_prompt.upper()
    if prompt:
        width = draw.textlength(prompt, font=font_label)
        draw.rounded_rectangle(
            [
                cx - width / 2 - 14 * u,
                r.draw_h - 58 * u,
                cx + width / 2 + 14 * u,
                r.draw_h - 34 * u,
            ],
            radius=12 * u,
            fill=(*accent[:3], int(235 * cta)),
        )
        draw.text(
            (cx - width / 2, r.draw_h - 51 * u),
            prompt,
            fill=(*background[:3], int(255 * cta)),
            font=font_label,
        )
