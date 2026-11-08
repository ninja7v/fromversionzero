# src/fromversionzero/renderer.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Core renderer: resolution-independent layout, shared graph primitives
# (grid, multiple-of-first-generation axis, trails, points, labels, legend)
# and the frame entry point. Format-specific composition lives in
# renderer_landscape.py and renderer_portrait.py.

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from PIL.ImageFont import FreeTypeFont

from fromversionzero.asset_manager import AssetManager
from fromversionzero.components import apply_alpha_to_image
from fromversionzero.config import VideoConfig
from fromversionzero.data_loader import Dataset, ProductDataPoint
from fromversionzero.interpolator import ease_sigmoid
from fromversionzero.renderer_landscape import render_frame_landscape
from fromversionzero.renderer_portrait import render_frame_shorts
from fromversionzero.themes import THEMES
from fromversionzero.timeline import FrameState

logger = logging.getLogger(__name__)

Color = tuple[int, int, int, int]
Bounds = tuple[float, float, float, float]

#: Layouts are authored on a reference canvas whose short side has this many
#: design units; every output resolution is a uniform scale of it.
DESIGN_SHORT_SIDE: dict[str, int] = {"landscape": 480, "portrait": 1080}

#: Trail opacity for already-revealed segments vs. the newest segment.
TRAIL_PAST_BRIGHTNESS: float = 0.45
TRAIL_HEAD_BRIGHTNESS: float = 1.0

#: Speculation question-card geometry (design units) per format.
QUESTION_CARD: dict[str, dict[str, Any]] = {
    "landscape": {
        "main": 27, "small": 9, "question": 150, "question_dy": 145,
        "rings": ((86, 75), (126, 48), (168, 28)), "kicker_x": 48, "kicker_y": 42,
        "wrap": 0.68, "line_h": 36, "pill_dy": 74, "footer_dy": 44,
    },
    "portrait": {
        "main": 64, "small": 24, "question": 300, "question_dy": 225,
        "rings": ((180, 75), (260, 48), (340, 28)), "kicker_x": 80, "kicker_y": 90,
        "wrap": 0.82, "line_h": 82, "pill_dy": 150, "footer_dy": 110,
    },
}  # fmt: skip

_RESAMPLING: dict[str, Image.Resampling] = {
    "lanczos": Image.Resampling.LANCZOS,
    "bilinear": Image.Resampling.BILINEAR,
    "nearest": Image.Resampling.NEAREST,
    "bicubic": Image.Resampling.BICUBIC,
}


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


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    clean = hex_str.lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def is_light_color(hex_str: str) -> bool:
    """True if the color's perceived (Rec. 601) luminance is above 50%."""
    try:
        r, g, b = hex_to_rgb(hex_str)
    except (ValueError, IndexError):
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0 > 0.5


def format_number(value: float) -> str:
    """Compact, human-friendly number: 1,505 / 68k / 12.5k / 1.2M / 0.3."""
    magnitude = abs(value)
    for threshold, divisor, suffix in ((1e9, 1e9, "B"), (1e6, 1e6, "M"), (1e4, 1e3, "k")):
        if magnitude >= threshold:
            scaled = value / divisor
            text = (
                f"{scaled:.0f}" if abs(scaled) >= 100 else f"{scaled:.1f}".rstrip("0").rstrip(".")
            )
            return f"{text}{suffix}"
    if float(value).is_integer():
        return f"{int(value):,}"
    if magnitude >= 100:
        return f"{value:,.0f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


_UNIT_HEURISTICS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("power", "hp"), "hp"),
    (("weight", "mass", "kg"), "kg"),
    (("price", "cost", "usd"), "USD"),
)


def format_metric_value(value: float, metric: str, unit: str = "") -> str:
    """Formats ``value`` with its display unit (from units.json or name heuristics)."""
    if not unit:
        lower = metric.lower()
        unit = next((u for keys, u in _UNIT_HEURISTICS if any(k in lower for k in keys)), "")
    if unit.upper() in ("USD", "$"):
        return f"${format_number(value)}"
    if unit.upper() == "EUR":
        return f"€{format_number(value)}"
    if unit in ("%", "×"):
        return f"{format_number(value)}{unit}"
    return f"{format_number(value)} {unit}".strip()


def metric_title(metric: str) -> str:
    """'acceleration_0_100' -> 'ACCELERATION 0–100', '0_to_60' -> '0–60'."""
    return re.sub(r"(\d+) (?:to )?(\d+)", "\\1–\\2", metric.replace("_", " ")).upper()


def format_multiple(ratio: float) -> str:
    """'×1', '×2.5', '×40', '×1.2k', '×0.5'."""
    if ratio >= 1e6:
        return f"×{format_number(ratio)}"
    if ratio >= 1000:
        return f"×{ratio / 1000:.1f}".removesuffix(".0") + "k"
    if ratio >= 10:
        return f"×{ratio:.0f}"
    if ratio >= 1:
        return f"×{ratio:.1f}".removesuffix(".0")
    return f"×{ratio:.2g}"


def _nice_linear_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    start = math.ceil(lo / step) * step
    ticks: list[float] = []
    value = start
    while value <= hi + step * 1e-9:
        ticks.append(round(value, 10))
        value += step
    return ticks


def multiple_ticks(dataset: Dataset, ymin: float, ymax: float, max_ticks: int = 6) -> list[float]:
    """Y-axis ticks as 'nice' multiples of the first generation, in plot coordinates."""
    lo, hi = dataset.y_to_ratio(ymin), dataset.y_to_ratio(ymax)
    if dataset.y_scale == "linear":
        return [dataset.ratio_to_y(r) for r in _nice_linear_ticks(lo, hi) if r > 0]
    if lo <= 0 or hi <= lo:
        return [0.0]
    first_decade, last_decade = math.floor(math.log10(lo)), math.ceil(math.log10(hi))
    for mantissas in ((1, 2, 5), (1,)):
        ticks = [
            m * 10.0**e
            for e in range(first_decade, last_decade + 1)
            for m in mantissas
            if lo <= m * 10.0**e <= hi
        ]
        if len(ticks) <= max_ticks:
            break
    else:
        step = math.ceil(len(ticks) / max_ticks)
        ticks = ticks[::step]
    if len(ticks) < 2:
        ticks = [r for r in _nice_linear_ticks(lo, hi, target=4) if r > 0]
    return [dataset.ratio_to_y(r) for r in ticks]


class Renderer:
    """Draws one frame from a :class:`FrameState`.

    All geometry is authored in *design units* on a reference canvas (480
    units tall for landscape, 1080 wide for portrait) and multiplied by
    ``self.unit``, an integer chosen so the drawing canvas is at least
    ``ssaa_factor`` times the output resolution. The canvas is then
    downsampled, so layouts look identical at every output size.
    """

    def __init__(self, config: VideoConfig, dataset: Dataset, dataset_dir: str) -> None:
        self.config: VideoConfig = config.for_output_mode()
        self.dataset: Dataset = dataset
        self.dataset_dir: str = dataset_dir
        self.data_points: list[ProductDataPoint] = dataset.points
        self.y_metrics: list[str] = dataset.y_metrics
        self.shorts_mode: bool = self.config.shorts

        width, height = self.config.width, self.config.height
        design_short = DESIGN_SHORT_SIDE["portrait" if self.shorts_mode else "landscape"]
        short_side = min(width, height)
        self.unit: int = max(
            1, math.ceil(self.config.ssaa_factor * short_side / design_short - 1e-9)
        )
        self.design_w: int = round(width * design_short / short_side)
        self.design_h: int = round(height * design_short / short_side)
        self.draw_w: int = self.design_w * self.unit
        self.draw_h: int = self.design_h * self.unit
        self._set_margins()

        self.asset_manager = AssetManager(dataset_dir, media=self.config.media)
        self.asset_manager.font_roles = dict(self.config.fonts)
        self.downsample_filter: Image.Resampling = _RESAMPLING[self.config.downsample_filter]
        self.ambient: bool = bool(THEMES.get(self.config.theme, {}).get("ambient", False))
        self.light_background: bool = is_light_color(self.config.colors["background"])

        self.base_colors: dict[str, Any] = {
            key: (*hex_to_rgb(value), 255) if isinstance(value, str) else list(value)
            for key, value in self.config.colors.items()
        }
        self.colors_rgba: dict[str, Any] = dict(self.base_colors)
        self.graph_alpha: float = 1.0

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _set_margins(self) -> None:
        """Graph area inside the canvas, leaving room for HUD panels."""
        u = self.unit
        if self.shorts_mode:
            # Portrait: graph in the lower ~55%, above the platform's UI safe zone.
            self.margin_left = 110 * u
            self.margin_right = 60 * u
            self.margin_top = int(self.draw_h * 0.42) + 40 * u
            self.margin_bottom = 125 * u
        else:
            # Landscape: left column holds product/facts cards and logos.
            self.margin_left = 300 * u
            self.margin_right = 60 * u
            self.margin_top = 40 * u
            self.margin_bottom = 100 * u

    @property
    def graph_box(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) of the plotting area in canvas pixels."""
        return (
            self.margin_left,
            self.margin_top,
            self.draw_w - self.margin_right,
            self.draw_h - self.margin_bottom,
        )

    def font(self, role: str, size: float) -> FreeTypeFont:
        """Font for a role ('heading', 'body', 'label') at a size in design units."""
        return self.asset_manager.get_font(role, size, self.unit)

    def _size(self, landscape: float, portrait: float) -> float:
        return portrait if self.shorts_mode else landscape

    # ------------------------------------------------------------------
    # Colors and fading
    # ------------------------------------------------------------------
    def rgba(self, key: str, alpha: float = 1.0) -> Color:
        """Theme color ``key`` with its alpha scaled by ``alpha`` and the graph fade."""
        r, g, b, a = self.base_colors[key]
        return (r, g, b, max(0, min(255, int(a * alpha * self.graph_alpha))))

    def _hex_to_rgba(self, hex_str: str, alpha: int = 255) -> Color:
        return (*hex_to_rgb(hex_str), int(alpha * self.graph_alpha))

    @contextmanager
    def faded(self, graph_alpha: float) -> Iterator[None]:
        """Temporarily fades every theme color (graph intro/outro), exception-safe."""
        self.graph_alpha = graph_alpha
        self.colors_rgba = {
            key: (v[0], v[1], v[2], int(v[3] * graph_alpha)) if isinstance(v, tuple) else v
            for key, v in self.base_colors.items()
        }
        try:
            yield
        finally:
            self.graph_alpha = 1.0
            self.colors_rgba = dict(self.base_colors)

    @contextmanager
    def clipped_to_graph(self, canvas: Image.Image) -> Iterator[None]:
        """Anything drawn inside this block is clipped to the plot area (plus a
        small bleed so edge points keep their glow) — curves never spill into
        the HUD while the camera is catching up."""
        bleed = 14 * self.unit
        left, top, right, bottom = self.graph_box
        box = (
            max(0, left - bleed),
            max(0, top - bleed),
            min(self.draw_w, right + bleed),
            min(self.draw_h, bottom + bleed),
        )
        regions = [
            (0, 0, self.draw_w, box[1]),
            (0, box[3], self.draw_w, self.draw_h),
            (0, box[1], box[0], box[3]),
            (box[2], box[1], self.draw_w, box[3]),
        ]
        saved = [(r, canvas.crop(r)) for r in regions if r[2] > r[0] and r[3] > r[1]]
        try:
            yield
        finally:
            for region, patch in saved:
                canvas.paste(patch, region[:2])

    @staticmethod
    def graph_alpha_for(state: FrameState) -> float:
        if state.intro_progress is not None:
            return max(0.0, (state.intro_progress - 0.5) / 0.5)
        if state.outro_progress is not None:
            return max(0.0, 1.0 - state.outro_progress / 0.5)
        return 1.0

    @staticmethod
    def _scale_color_alpha(color: Color, mult: float) -> Color:
        return (color[0], color[1], color[2], max(0, min(255, int(color[3] * mult))))

    def _metric_color(self, metric: str) -> Color:
        """Stable per-metric color, tied to the metric's position in ``y_metrics``."""
        palette: list[str] = self.config.colors["line_colors"]
        index = self.y_metrics.index(metric) if metric in self.y_metrics else 0
        return self._hex_to_rgba(palette[index % len(palette)])

    # ------------------------------------------------------------------
    # Frame entry point
    # ------------------------------------------------------------------
    def render(self, state: FrameState) -> np.ndarray:
        """Renders ``state`` to an RGB ``uint8`` array of the output size."""
        if state.kind == "fullscreen_text":
            return self.render_fullscreen_text_frame(state.text, state.subtext, state.text_alpha)
        if self.shorts_mode:
            return render_frame_shorts(self, state)
        return render_frame_landscape(self, state)

    def new_canvas(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        """Opaque RGB canvas with an alpha-*blending* draw handle.

        (``ImageDraw`` on an RGBA image overwrites pixels instead of blending,
        which breaks translucency on light themes.)
        """
        canvas = Image.new("RGB", (self.draw_w, self.draw_h), self.base_colors["background"][:3])
        return canvas, BlendDraw(canvas)

    def to_frame(self, canvas: Image.Image) -> np.ndarray:
        """Downsamples the canvas to the output size as an RGB ``uint8`` array."""
        final = canvas.resize((self.config.width, self.config.height), self.downsample_filter)
        return np.asarray(final, dtype=np.uint8)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------
    def last_revealed_index(self, revealed: tuple[float, ...] | list[float]) -> int:
        last = -1
        for i, pct in enumerate(revealed):
            if pct > 0.0:
                last = i
        return last

    def _world_to_screen(self, x: float, y: float, bounds: Bounds) -> tuple[float, float]:
        xmin, xmax, ymin, ymax = bounds
        x_range = (xmax - xmin) or 1.0
        y_range = (ymax - ymin) or 1.0
        left, top, right, bottom = self.graph_box
        return (
            left + (x - xmin) / x_range * (right - left),
            bottom - (y - ymin) / y_range * (bottom - top),
        )

    def _head_position(
        self, i: int, reveal_pct: float, metric: str, bounds: Bounds
    ) -> tuple[float, float]:
        """Screen position of point ``i``, eased toward it while it is being revealed."""
        ds = self.dataset
        point = self.data_points[i]
        sx, sy = self._world_to_screen(ds.x(point), ds.y(point, metric), bounds)
        prev = self.data_points[i - 1] if i > 0 else None
        # A metric's first point appears in place; later points grow from the previous one.
        if prev is not None and reveal_pct < 1.0 and ds.has(prev, metric):
            px, py = self._world_to_screen(ds.x(prev), ds.y(prev, metric), bounds)
            eased = ease_sigmoid(reveal_pct)
            sx, sy = px + (sx - px) * eased, py + (sy - py) * eased
        return sx, sy

    # ------------------------------------------------------------------
    # Ambient field
    # ------------------------------------------------------------------
    def draw_ambient(self, draw: ImageDraw.ImageDraw, time_elapsed: float) -> None:
        """Slow drifting dot field (signature themes only)."""
        if not self.ambient:
            return
        u = self.unit
        spacing = 72 * u
        drift = int((time_elapsed * 9 * u) % spacing)
        radius = max(1, u)
        fill = self.rgba("text_secondary", 18 / 255)
        for row, y in enumerate(range(-spacing, self.draw_h + spacing, spacing)):
            offset = (row * 31 * u + drift) % spacing
            for x in range(-spacing + offset, self.draw_w + spacing, spacing):
                draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=fill)

    # ------------------------------------------------------------------
    # Grid and axes
    # ------------------------------------------------------------------
    def _draw_dashed_line(
        self,
        draw: ImageDraw.ImageDraw,
        p1: tuple[float, float],
        p2: tuple[float, float],
        fill: Color,
        width: int,
        dash: int | None = None,
    ) -> None:
        dash_len = dash or 2 * self.unit
        (x1, y1), (x2, y2) = p1, p2
        dist = math.hypot(x2 - x1, y2 - y1)
        if dist == 0:
            return
        dx, dy = (x2 - x1) / dist * dash_len, (y2 - y1) / dist * dash_len
        for i in range(0, int(dist / dash_len), 2):
            draw.line(
                [(x1 + i * dx, y1 + i * dy), (x1 + (i + 1) * dx, y1 + (i + 1) * dy)],
                fill=fill,
                width=max(1, width),
            )

    def _x_ticks(self, xmin: float, xmax: float, revealed: tuple[float, ...]) -> list[float]:
        """Landscape: a regular grid. Shorts: first and latest revealed generation only."""
        if not self.shorts_mode:
            span = max(xmax - xmin, 1e-9)
            if span <= 1.5:
                step = 0.25
            else:
                step = next(
                    (s for limit, s in ((50, 10.0), (20, 5.0), (10, 2.0)) if span > limit), 1.0
                )
            start = math.ceil(xmin / step) * step
            return [float(v) for v in np.arange(start, xmax + 1e-9, step)]
        ticks: list[float] = []
        if self.data_points:
            first = self.dataset.x(self.data_points[0])
            ticks.append(first)
            last_idx = self.last_revealed_index(revealed)
            if last_idx >= 0:
                last = self.dataset.x(self.data_points[last_idx])
                if last != first:
                    ticks.append(last)
        return ticks

    @staticmethod
    def _x_tick_label(value: float, year_only: bool) -> str:
        if year_only:
            return f"{round(value)}"
        year = int(value)
        month = min(12, round((value - year) * 12) + 1)
        return f"{year}.{month:02d}"

    def draw_grid_and_axes(
        self, draw: ImageDraw.ImageDraw, bounds: Bounds, revealed: tuple[float, ...]
    ) -> None:
        """Texture grid, X ticks, multiple-of-first-generation Y gridlines and axes."""
        u = self.unit
        xmin, xmax, ymin, ymax = bounds
        left, top, right, bottom = self.graph_box
        font = self.font("label", self._size(11, 24))
        label_color = self.rgba("text_secondary")
        axis_color = self.rgba("text_primary", 0.85)

        # Faint square texture grid.
        texture = self.rgba("text_primary", 22 / 255)
        grid = 60 * u
        for gx in range(left, right + 1, grid):
            self._draw_dashed_line(draw, (gx, bottom), (gx, top), texture, u)
        for gy in range(bottom, top - 1, -grid):
            self._draw_dashed_line(draw, (left, gy), (right, gy), texture, u)

        # X axis ticks and labels.
        year_only = (xmax - xmin) > 1.5
        tick_len = self._size(6, 12) * u
        label_gap = self._size(12, 22) * u
        for value in self._x_ticks(xmin, xmax, revealed):
            sx, _ = self._world_to_screen(value, ymin, bounds)
            if not left - u <= sx <= right + u:
                continue
            draw.line([(sx, bottom), (sx, bottom + tick_len)], fill=axis_color, width=2 * u)
            text = self._x_tick_label(value, year_only)
            label_w = draw.textlength(text, font=font)
            draw.text((sx - label_w / 2, bottom + label_gap), text, fill=label_color, font=font)

        # Y gridlines labelled as multiples of the first generation.
        baseline = self.dataset.ratio_to_y(1.0)
        for value in multiple_ticks(self.dataset, ymin, ymax):
            _, sy = self._world_to_screen(xmin, value, bounds)
            if not top <= sy <= bottom:
                continue
            is_base = abs(value - baseline) < 1e-9
            line_color = self.rgba("text_primary", (70 if is_base else 34) / 255)
            self._draw_dashed_line(draw, (left, sy), (right, sy), line_color, u, dash=4 * u)
            text = format_multiple(self.dataset.y_to_ratio(value))
            label_w = draw.textlength(text, font=font)
            text_h = font.size
            draw.text(
                (left - label_w - 8 * u, sy - text_h * 0.62),
                text,
                fill=self.rgba("text_primary" if is_base else "text_secondary"),
                font=font,
            )

        draw.line([(left, bottom), (right, bottom)], fill=axis_color, width=u)
        draw.line([(left, bottom), (left, top)], fill=axis_color, width=u)
        if self.ambient:
            bracket = self.rgba("accent", 180 / 255)
            corner = 16 * u
            draw.line([(left, top), (left + corner, top)], fill=bracket, width=2 * u)
            draw.line([(left, top), (left, top + corner)], fill=bracket, width=2 * u)
            draw.line([(right - corner, bottom), (right, bottom)], fill=bracket, width=2 * u)

    # ------------------------------------------------------------------
    # Curves
    # ------------------------------------------------------------------
    def _draw_glowing_line(
        self,
        draw: ImageDraw.ImageDraw,
        path: list[tuple[float, float]],
        base_width: float,
        color: Color,
    ) -> None:
        """Stacked translucent strokes give a soft neon falloff."""
        if len(path) < 2:
            return
        for width_mult, opacity in ((6, 0.04), (3.5, 0.12), (2, 0.28), (1, 1.0)):
            draw.line(
                path,
                fill=self._scale_color_alpha(color, opacity),
                width=max(1, int(base_width * width_mult)),
                joint="curve",
            )

    def _draw_gradient_segment(
        self,
        draw: ImageDraw.ImageDraw,
        p0: tuple[float, float],
        p1: tuple[float, float],
        base_width: float,
        color: Color,
        start: float,
        end: float,
        dashed: bool,
    ) -> None:
        """A segment whose brightness ramps from ``start`` to ``end``."""
        if dashed:
            dash = 2 * self.unit
            dist = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            if dist == 0:
                return
            n = max(1, int(dist / dash))
            dx, dy = (p1[0] - p0[0]) / dist * dash, (p1[1] - p0[1]) / dist * dash
            for i in range(0, n, 2):
                bright = start + (end - start) * (i + 0.5) / n
                draw.line(
                    [
                        (p0[0] + i * dx, p0[1] + i * dy),
                        (p0[0] + (i + 1) * dx, p0[1] + (i + 1) * dy),
                    ],
                    fill=self._scale_color_alpha(color, bright * 0.8),
                    width=max(1, self.unit),
                )
            return
        steps = 12
        for k in range(steps):
            t0, t1 = k / steps, (k + 1) / steps
            a = (p0[0] + (p1[0] - p0[0]) * t0, p0[1] + (p1[1] - p0[1]) * t0)
            b = (p0[0] + (p1[0] - p0[0]) * t1, p0[1] + (p1[1] - p0[1]) * t1)
            bright = start + (end - start) * (t0 + t1) / 2
            self._draw_glowing_line(
                draw, [a, b], base_width, self._scale_color_alpha(color, bright)
            )

    def draw_trails(
        self, draw: ImageDraw.ImageDraw, revealed: tuple[float, ...], bounds: Bounds
    ) -> None:
        """One glowing polyline per metric; the newest segment brightens toward the head.

        Segments leading to a speculated generation are dashed.
        """
        base_width = self.unit * self._size(1.0, 1.5)
        for metric in self.y_metrics:
            color = self._metric_color(metric)
            points: list[tuple[float, float]] = []
            speculative: list[bool] = []
            for i, pct in enumerate(revealed):
                if pct <= 0.0 or not self.dataset.has(self.data_points[i], metric):
                    continue
                points.append(self._head_position(i, pct, metric, bounds))
                speculative.append(self.data_points[i].is_speculation)
            if len(points) < 2:
                continue
            past = self._scale_color_alpha(color, TRAIL_PAST_BRIGHTNESS)
            if len(points) > 2:
                self._draw_glowing_line(draw, points[:-1], base_width, past)
            self._draw_gradient_segment(
                draw,
                points[-2],
                points[-1],
                base_width,
                color,
                TRAIL_PAST_BRIGHTNESS,
                TRAIL_HEAD_BRIGHTNESS,
                dashed=speculative[-1],
            )

    # ------------------------------------------------------------------
    # Points and value labels
    # ------------------------------------------------------------------
    def _draw_point_glow(
        self,
        draw: ImageDraw.ImageDraw,
        sx: float,
        sy: float,
        radius: float,
        color: Color,
        bright: float,
    ) -> None:
        g = self.graph_alpha * bright
        draw.ellipse(
            [sx - radius, sy - radius, sx + radius, sy + radius],
            fill=(*color[:3], int(25 * g)),
            outline=(*color[:3], int(80 * g)),
            width=self.unit,
        )

    def _draw_point(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        metric: str,
        sx: float,
        sy: float,
        color: Color,
        is_head: bool,
        reveal_pct: float,
    ) -> None:
        u = self.unit
        size_mult = 1.1 if is_head else 0.55
        bright = 1.0 if is_head else 0.5
        if is_head and 0.0 < reveal_pct < 1.0:
            pulse = math.sin(math.pi * reveal_pct)
            ring = (8 + 18 * reveal_pct) * u
            draw.ellipse(
                [sx - ring, sy - ring, sx + ring, sy + ring],
                outline=(*color[:3], int(150 * pulse * self.graph_alpha)),
                width=u,
            )
        icon: Image.Image | None
        try:
            icon = self.asset_manager.get_metric_icon(metric, int(20 * u * size_mult))
        except (OSError, ValueError):
            icon = None
        if is_head:
            self._draw_point_glow(draw, sx, sy, (8 if icon else 6) * u * size_mult, color, bright)
        if icon is not None:
            tinted = Image.new("RGBA", icon.size, color[:3] + (255,))
            tinted.putalpha(icon.getchannel("A"))
            faded = apply_alpha_to_image(tinted, self.graph_alpha * bright)
            w, h = faded.size
            canvas.paste(faded, (int(sx - w / 2), int(sy - h / 2)), faded)
            return
        core = 2 * u * size_mult
        draw.ellipse(
            [sx - core, sy - core, sx + core, sy + core],
            fill=self.rgba("text_primary", bright),
            outline=color,
            width=2 * u,
        )

    @staticmethod
    def _deconflict_labels(
        labels: list[dict[str, Any]], min_gap: float, top: float, bottom: float
    ) -> None:
        """Spaces labels vertically (keeping order) while staying inside [top, bottom]."""
        n = len(labels)
        if n <= 1:
            return
        labels.sort(key=lambda item: item["target_y"])
        gap = min(float(min_gap), (bottom - top) / (n - 1))
        labels[0]["target_y"] = max(top, min(bottom, labels[0]["target_y"]))
        for i in range(1, n):
            labels[i]["target_y"] = max(labels[i]["target_y"], labels[i - 1]["target_y"] + gap)
        overflow = labels[-1]["target_y"] - bottom
        if overflow > 0:
            for item in labels:
                item["target_y"] -= overflow
        for i in range(n - 2, -1, -1):
            labels[i]["target_y"] = min(labels[i]["target_y"], labels[i + 1]["target_y"] - gap)

    def _point_label_min_gap(self) -> int:
        return int(self._size(16, 26) * self.unit)

    def draw_curves(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        revealed: tuple[float, ...],
        bounds: Bounds,
    ) -> None:
        """Trails, points and value labels; trails and points are clipped to the plot."""
        with self.clipped_to_graph(canvas):
            self.draw_trails(draw, revealed, bounds)
            labels = self.draw_points(canvas, draw, revealed, bounds)
        self.draw_value_labels(draw, labels)

    def draw_points_and_labels(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        revealed: tuple[float, ...],
        bounds: Bounds,
    ) -> None:
        self.draw_value_labels(draw, self.draw_points(canvas, draw, revealed, bounds))

    def draw_points(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        revealed: tuple[float, ...],
        bounds: Bounds,
    ) -> list[dict[str, Any]]:
        """Icon points on every revealed generation; returns the head labels to draw
        (value with unit, then its multiple of the first generation)."""
        last = self.last_revealed_index(revealed)
        labels: list[dict[str, Any]] = []
        for metric in self.y_metrics:
            color = self._metric_color(metric)
            head_xy: tuple[float, float] | None = None
            for i, pct in enumerate(revealed):
                if pct <= 0.0 or not self.dataset.has(self.data_points[i], metric):
                    continue
                sx, sy = self._head_position(i, pct, metric, bounds)
                self._draw_point(canvas, draw, metric, sx, sy, color, i == last, pct)
                head_xy = (sx, sy)
            if head_xy is None or last < 0:
                continue
            point = self.data_points[last]
            if not self.dataset.has(point, metric):
                continue
            value = self.dataset.value(point, metric)
            text = format_metric_value(value, metric, self.asset_manager.get_metric_unit(metric))
            if self.dataset.is_interpolated(point, metric):
                text = "≈ " + text
            if point.is_speculation:
                text += " ?"
            ratio = self.dataset.y_to_ratio(self.dataset.y(point, metric))
            labels.append(
                {
                    "sx": head_xy[0],
                    "sy": head_xy[1],
                    "target_y": head_xy[1],
                    "text": text,
                    "ratio": format_multiple(ratio) if last > self.dataset.start[metric] else "",
                    "color": color,
                }
            )

        return labels

    def draw_value_labels(self, draw: ImageDraw.ImageDraw, labels: list[dict[str, Any]]) -> None:
        """Deconflicted value labels with leader lines; flipped to the left of the
        point when there is no room on the right."""
        u = self.unit
        _, top, _, bottom = self.graph_box
        self._deconflict_labels(labels, self._point_label_min_gap(), top + 10 * u, bottom - 10 * u)
        font = self.font("label", self._size(11, 22))
        font_ratio = self.font("label", self._size(9, 18))
        text_color = self.rgba("text_primary")
        ratio_color = self.rgba("text_secondary")
        right_limit = self.draw_w - 12 * u
        spacer = draw.textlength("  ", font=font)

        def fits(item: dict[str, Any]) -> bool:
            end = item["sx"] + 30 * u + draw.textlength(item["text"], font=font)
            if item["ratio"]:
                end += spacer + draw.textlength(item["ratio"], font=font_ratio)
            return end <= right_limit

        # Multiples are shown on every label or on none, never on a random subset.
        if labels and not all(fits(item) for item in labels):
            for item in labels:
                item["ratio"] = ""
        for item in labels:
            sx, sy, ty, col = item["sx"], item["sy"], item["target_y"], item["color"]
            width = draw.textlength(item["text"], font=font)
            flip = sx + 30 * u + width > right_limit
            direction = -1 if flip else 1
            x0, x1 = sx + direction * 8 * u, sx + direction * 22 * u
            if top <= sy <= bottom:  # no leader for heads the camera hasn't caught yet
                draw.line(
                    [(x0, sy), (x1, ty)], fill=(*col[:3], int(140 * self.graph_alpha)), width=u
                )
            text_x = x1 - 8 * u - width if flip else x1 + 8 * u
            if flip:
                item["ratio"] = ""
            draw.text((text_x, ty - font.size * 0.62), item["text"], fill=text_color, font=font)
            if item["ratio"]:
                draw.text(
                    (text_x + width + spacer, ty - font_ratio.size * 0.62),
                    item["ratio"],
                    fill=ratio_color,
                    font=font_ratio,
                )

    # ------------------------------------------------------------------
    # Legend
    # ------------------------------------------------------------------
    def _legend_pump_targets(self, revealed: tuple[float, ...]) -> tuple[str, str, float]:
        """Metric that grew most / shrank most during the active reveal, and a 0..1..0 pulse."""
        last = self.last_revealed_index(revealed)
        if last <= 0 or not 0.0 < revealed[last] < 1.0:
            return "", "", 0.0
        prev, cur = self.data_points[last - 1], self.data_points[last]
        changes: dict[str, float] = {}
        for metric in self.y_metrics:
            if not self.dataset.has(prev, metric):
                continue
            old, new = self.dataset.value(prev, metric), self.dataset.value(cur, metric)
            if old == 0.0:
                changes[metric] = math.inf if new > 0 else 0.0
            else:
                changes[metric] = (new - old) / abs(old)
        if not changes:
            return "", "", 0.0
        grow = max(changes, key=lambda m: changes[m])
        shrink = min(changes, key=lambda m: changes[m])
        return (
            grow if changes[grow] > 0 else "",
            shrink if changes[shrink] < 0 else "",
            math.sin(math.pi * revealed[last]),
        )

    def _legend_row_positions(self, revealed: tuple[float, ...]) -> list[tuple[str, float, float]]:
        """(metric, value, animated slot) sorted top-to-bottom by current value.

        Metrics that start at a later generation join the legend when it is revealed.
        """
        last = self.last_revealed_index(revealed)
        ds = self.dataset
        current_index = max(last, 0)
        shown = [m for m in self.y_metrics if ds.start[m] <= current_index]
        if last < 0:
            return [(m, 0.0, float(i)) for i, m in enumerate(shown)]
        current = self.data_points[last]
        values = {m: ds.y(current, m) for m in shown}
        order = sorted(shown, key=lambda m: values[m], reverse=True)
        slots = {m: float(i) for i, m in enumerate(order)}
        pct = revealed[last]
        if last > 0 and 0.0 < pct < 1.0:
            prev = self.data_points[last - 1]
            before = [m for m in shown if ds.has(prev, m)]
            prev_order = sorted(before, key=lambda m: ds.y(prev, m), reverse=True)
            prev_slots = {m: float(i) for i, m in enumerate(prev_order)}
            k = ease_sigmoid(pct)
            slots = {
                m: prev_slots[m] + (slots[m] - prev_slots[m]) * k if m in prev_slots else slots[m]
                for m in shown
            }
        return sorted(((m, values[m], slots[m]) for m in shown), key=lambda row: row[2])

    def draw_legend(self, draw: ImageDraw.ImageDraw, revealed: tuple[float, ...]) -> None:
        """Legend ordered like the curves; the biggest grower pumps up, the biggest shrinker down."""
        u = self.unit
        grow, shrink, pulse = self._legend_pump_targets(revealed)
        left, top, _, _ = self.graph_box
        x = left + 25 * u
        y0 = top + 23 * u
        row_h = self._size(19, 40) * u
        base_size = self._size(9.5, 20)
        swatch_w, swatch_h = self._size(10, 28) * u, self._size(3, 6) * u
        rows = self._legend_row_positions(revealed)
        probe_font = self.font("label", base_size)
        text_x = x + self._size(15, 36) * u
        panel_w = max(draw.textlength(metric_title(m), font=probe_font) for m in self.y_metrics)
        pad = 8 * u
        draw.rounded_rectangle(
            [x - pad, y0 - pad, text_x + panel_w + pad * 1.5, y0 + len(rows) * row_h + pad * 0.3],
            radius=8 * u,
            fill=self.rgba("background", 0.72),
        )
        for metric, _value, slot in rows:
            scale = 1.0
            if grow and metric == grow and grow != shrink:
                scale += 0.35 * pulse
            elif shrink and metric == shrink and grow != shrink:
                scale -= 0.25 * pulse
            color = self._metric_color(metric)
            row_y = y0 + slot * row_h
            font = self.font("label", base_size * scale)
            cy = row_y + base_size * u * 0.55
            sw, sh = swatch_w * scale, swatch_h * scale
            draw.rounded_rectangle([x, cy - sh / 2, x + sw, cy + sh / 2], radius=u, fill=color)
            draw.text(
                (text_x, cy - font.size * 0.6),
                metric_title(metric),
                fill=color,
                font=font,
            )

    # ------------------------------------------------------------------
    # Motto
    # ------------------------------------------------------------------
    def draw_motto(self, draw: ImageDraw.ImageDraw) -> None:
        if not self.config.show_motto or not self.config.motto:
            return
        text = f"“{self.config.motto.strip(chr(34))}”"
        font = self.font("body", self._size(10.5, 24))
        left, _, right, _ = self.graph_box
        width = draw.textlength(text, font=font)
        y = self.draw_h - 60 * self.unit
        draw.text(
            ((left + right) / 2 - width / 2, y), text, fill=self.rgba("text_secondary"), font=font
        )

    # ------------------------------------------------------------------
    # Fullscreen speculation card
    # ------------------------------------------------------------------
    def render_fullscreen_text_frame(
        self, main_text: str, sub_text: str = "", alpha: float = 1.0
    ) -> np.ndarray:
        """Open-question card shown before speculated generations animate in."""
        canvas, draw = self.new_canvas()
        if alpha > 0.0:
            self._draw_question_card(draw, main_text, sub_text, alpha)
        return self.to_frame(canvas)

    def _draw_question_card(
        self, draw: ImageDraw.ImageDraw, main_text: str, sub_text: str, alpha: float
    ) -> None:
        u = self.unit
        k = QUESTION_CARD["portrait" if self.shorts_mode else "landscape"]
        labels = self.config.labels
        accent, primary = self.rgba("accent"), self.rgba("text_primary")
        font_main = self.font("display", k["main"])
        font_small = self.font("heading", k["small"])
        cx, cy = self.draw_w // 2, self.draw_h // 2
        a = int(255 * alpha)

        for radius, opacity in k["rings"]:
            r = radius * u
            draw.ellipse(
                [cx - r, cy - r, cx + r, cy + r],
                outline=(*accent[:3], int(opacity * alpha)),
                width=u,
            )
        font_question = self.font("heading", k["question"])
        question_w = draw.textlength("?", font=font_question)
        draw.text(
            (cx - question_w / 2, cy - k["question_dy"] * u),
            "?",
            fill=(*accent[:3], int(22 * alpha)),
            font=font_question,
        )
        draw.text(
            (k["kicker_x"] * u, k["kicker_y"] * u),
            labels.speculation_kicker.upper(),
            fill=(*accent[:3], a),
            font=font_small,
        )

        lines = wrap_text(draw, main_text.upper(), font_main, self.draw_w * k["wrap"])
        line_h = k["line_h"] * u
        text_y = cy - len(lines) * line_h / 2
        for line in lines:
            width = draw.textlength(line, font=font_main)
            draw.text((cx - width / 2, text_y), line, fill=(*primary[:3], a), font=font_main)
            text_y += line_h

        if sub_text:
            self._draw_pill(draw, sub_text.upper(), cx, cy + k["pill_dy"] * u, font_small, alpha)
        footer = labels.speculation_footer.upper()
        if footer:
            width = draw.textlength(footer, font=font_small)
            draw.text(
                (cx - width / 2, self.draw_h - k["footer_dy"] * u),
                footer,
                fill=self.rgba("text_secondary", 210 / 255 * alpha),
                font=font_small,
            )

    def _draw_pill(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        cx: float,
        y: float,
        font: FreeTypeFont,
        alpha: float,
    ) -> None:
        """Accent-filled rounded label (call-to-action)."""
        width = draw.textlength(text, font=font)
        pad_x, pad_y = 16 * self.unit, font.size * 0.55
        draw.rounded_rectangle(
            [cx - width / 2 - pad_x, y - pad_y, cx + width / 2 + pad_x, y + font.size + pad_y],
            radius=int(font.size),
            fill=self.rgba("accent", 235 / 255 * alpha),
        )
        draw.text(
            (cx - width / 2, y),
            text,
            fill=(*self.base_colors["background"][:3], int(255 * alpha)),
            font=font,
        )


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
