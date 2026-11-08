# tests/test_renderer.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Renderer behaviour: frame format, phases, toggles, determinism, resolution
# independence, value formatting, Y-axis multiples and label layout.

from itertools import pairwise
from typing import Any

import numpy as np
import pytest
from conftest import WIDGET_DIR, make_config
from PIL import Image

from fromversionzero.data_loader import Dataset
from fromversionzero.renderer import (
    BlendDraw,
    Renderer,
    format_metric_value,
    format_multiple,
    format_number,
    metric_title,
    multiple_ticks,
)
from fromversionzero.timeline import FrameState

BOUNDS = (2008.0, 2023.0, -0.2, 1.0)


def build(**overrides: Any) -> Renderer:
    config = make_config(**overrides)
    dataset = Dataset.load(str(WIDGET_DIR), "date", y_scale=config.y_scale)
    return Renderer(config, dataset, str(WIDGET_DIR))


def graph(revealed: tuple[float, ...] = (1, 1, 0.5, 0, 0), **kw: Any) -> FrameState:
    return FrameState(
        kind="graph",
        t=kw.pop("t", 3.0),
        revealed_pcts=revealed,
        camera_bounds=kw.pop("camera_bounds", BOUNDS),
        active_fact_index=kw.pop("active_fact_index", 1),
        fact_card_alpha=kw.pop("fact_card_alpha", 1.0),
        **kw,
    )


@pytest.fixture(scope="module")
def landscape() -> Renderer:
    return build()


@pytest.fixture(scope="module")
def portrait() -> Renderer:
    return build(shorts=True, shorts_overrides={"resolution": "270x480"})


# --------------------------------------------------------------------------
# Frame format and phases
# --------------------------------------------------------------------------
@pytest.mark.parametrize("fixture", ["landscape", "portrait"])
def test_frames_are_rgb_uint8_at_output_size(fixture: str, request: pytest.FixtureRequest) -> None:
    renderer: Renderer = request.getfixturevalue(fixture)
    frame = renderer.render(graph())
    assert frame.dtype == np.uint8
    assert frame.shape == (renderer.config.height, renderer.config.width, 3)
    assert frame.std() > 0


@pytest.mark.parametrize(
    "state",
    [
        graph((0, 0, 0, 0, 0), intro_progress=0.3),
        graph((1, 1, 1, 1, 1), outro_progress=0.6),
        FrameState(kind="fullscreen_text", t=9.0, text="Next?", subtext="Tell us", text_alpha=1.0),
    ],
    ids=["intro", "outro", "speculation-card"],
)
def test_every_phase_renders_content(landscape: Renderer, state: FrameState) -> None:
    assert landscape.render(state).std() > 0


def test_intro_and_outro_animate(landscape: Renderer) -> None:
    for key in ("intro_progress", "outro_progress"):
        frames = [landscape.render(graph(**{key: p})) for p in (0.15, 0.5, 0.9)]
        assert not np.array_equal(frames[0], frames[1])
        assert not np.array_equal(frames[1], frames[2])


def test_rendering_is_deterministic(landscape: Renderer) -> None:
    assert np.array_equal(landscape.render(graph()), landscape.render(graph()))


def test_fade_state_is_restored_after_an_exception(landscape: Renderer) -> None:
    with pytest.raises(RuntimeError), landscape.faded(0.1):
        raise RuntimeError("boom")
    assert landscape.graph_alpha == 1.0
    assert landscape.colors_rgba == landscape.base_colors


def test_progress_changes_the_frame(landscape: Renderer) -> None:
    a = landscape.render(graph((1, 0.3, 0, 0, 0)))
    b = landscape.render(graph((1, 0.9, 0, 0, 0)))
    assert not np.array_equal(a, b)


@pytest.mark.parametrize(
    "flag", ["show_legend", "show_facts", "show_motto", "show_logo", "show_progress_bar"]
)
def test_visibility_flags_change_the_frame(flag: str) -> None:
    on = build(**{flag: True}).render(graph())
    off = build(**{flag: False}).render(graph())
    assert not np.array_equal(on, off)


@pytest.mark.parametrize("theme", ["signal", "blueprint", "paper"])
def test_themes_render_with_their_background(theme: str) -> None:
    renderer = build(theme=theme)
    frame = renderer.render(graph())
    corner = frame[2, -3].astype(int)
    expected = np.array(renderer.base_colors["background"][:3])
    assert np.abs(corner - expected).max() <= 12


# --------------------------------------------------------------------------
# Resolution independence
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("resolution", "ssaa", "unit"),
    [("240p", 2, 1), ("480p", 1, 1), ("480p", 2, 2), ("720p", 2, 3), ("1080p", 2, 5)],
)
def test_layout_unit_scales_with_resolution(resolution: str, ssaa: int, unit: int) -> None:
    renderer = build(resolution=resolution, ssaa_factor=ssaa)
    assert renderer.unit == unit
    assert renderer.draw_h == 480 * unit
    assert renderer.draw_w >= renderer.config.width * ssaa * 0.99


def test_same_layout_at_every_resolution() -> None:
    small = build(resolution="480p", ssaa_factor=2).render(graph())
    large = build(resolution="1080p", ssaa_factor=1).render(graph())
    downsized = np.asarray(Image.fromarray(large).resize((854, 480), Image.Resampling.BOX))
    assert np.abs(small.astype(int) - downsized.astype(int)).mean() < 6.0


# --------------------------------------------------------------------------
# Value formatting and axis
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.29, "0.29"),
        (5.0, "5"),
        (1505, "1,505"),
        (9999, "9,999"),
        (12500, "12.5k"),
        (97000, "97k"),
        (190000, "190k"),
        (1_200_000, "1.2M"),
        (3_400_000_000, "3.4B"),
    ],
)
def test_format_number(value: float, expected: str) -> None:
    assert format_number(value) == expected


def test_format_metric_value_units() -> None:
    assert format_metric_value(97000, "price", "USD") == "$97k"
    assert format_metric_value(385, "power", "hp") == "385 hp"
    assert format_metric_value(4.2, "acceleration_0_100", "s") == "4.2 s"
    assert format_metric_value(55, "coding", "%") == "55%"
    assert format_metric_value(300, "engine_power") == "300 hp"  # name heuristic
    assert format_metric_value(7, "mystery") == "7"


def test_format_multiple_and_titles() -> None:
    assert [format_multiple(r) for r in (1, 2.96, 0.77, 15.2, 2500)] == [
        "×1", "×3", "×0.77", "×15", "×2.5k",
    ]  # fmt: skip
    assert metric_title("acceleration_0_100") == "ACCELERATION 0–100"
    assert metric_title("0_to_60") == "0–60"


def test_log_ticks_are_nice_multiples(widget_dataset: Dataset) -> None:
    ds = widget_dataset
    ticks = [
        round(ds.y_to_ratio(t), 6)
        for t in multiple_ticks(ds, ds.ratio_to_y(0.6), ds.ratio_to_y(12))
    ]
    assert ticks == [1, 2, 5, 10]
    narrow = [ds.y_to_ratio(t) for t in multiple_ticks(ds, ds.ratio_to_y(0.95), ds.ratio_to_y(1.3))]
    assert len(narrow) >= 2 and all(0.95 <= r <= 1.3 for r in narrow)


def test_deconflict_labels_keeps_order_spacing_and_bounds() -> None:
    labels = [{"target_y": 100.0 + i} for i in range(8)]
    Renderer._deconflict_labels(labels, min_gap=24, top=50, bottom=330)
    ys = [item["target_y"] for item in labels]
    assert ys[0] >= 50 and ys[-1] <= 330
    assert all(b - a >= 24 - 1e-9 for a, b in pairwise(ys))


def test_legend_order_animates_between_generations(landscape: Renderer) -> None:
    def slots(revealed: tuple[float, ...]) -> dict[str, float]:
        return {m: s for m, _, s in landscape._legend_row_positions(revealed)}

    start, end = slots((1, 0, 0, 0, 0)), slots((1, 1, 0, 0, 0))
    moving = slots((1, 0.5, 0, 0, 0))
    for metric in landscape.y_metrics:
        lo, hi = sorted((start[metric], end[metric]))
        assert lo - 1e-9 <= moving[metric] <= hi + 1e-9


def test_blend_draw_respects_text_alpha() -> None:
    image = Image.new("RGB", (120, 40))
    draw = BlendDraw(image)
    from fromversionzero.asset_manager import AssetManager

    font = AssetManager(".").get_font("heading", 28)
    draw.text((2, 2), "HIDDEN", fill=(255, 255, 255, 0), font=font)
    assert np.asarray(image).max() == 0
    draw.text((2, 2), "HALF", fill=(255, 255, 255, 128), font=font)
    assert 100 < np.asarray(image).max() < 160


# --------------------------------------------------------------------------
# Metrics that start after the first generation
# --------------------------------------------------------------------------
def test_metric_starting_late_joins_curves_labels_and_legend(widget_dir) -> None:
    csv_path = widget_dir / "data.csv"
    rows = csv_path.read_text(encoding="utf-8").splitlines()
    header = rows[0].split(",")
    col = header.index("price")
    patched = [rows[0]]
    for i, line in enumerate(rows[1:]):
        cells = line.split(",")
        if i < 2:  # W1 and W2 have no price
            cells[col] = ""
        patched.append(",".join(cells))
    csv_path.write_text("\n".join(patched) + "\n", encoding="utf-8")

    config = make_config()
    dataset = Dataset.load(str(widget_dir), "date", y_scale=config.y_scale)
    assert "price" in dataset.y_metrics and dataset.start["price"] == 2
    renderer = Renderer(config, dataset, str(widget_dir))

    def legend(revealed: tuple[float, ...]) -> list[str]:
        return [m for m, _, _ in renderer._legend_row_positions(revealed)]

    assert "price" not in legend((1, 1, 0, 0, 0))
    assert "price" in legend((1, 1, 0.5, 0, 0)) and "price" in legend((1, 1, 1, 0, 0))

    canvas, draw = renderer.new_canvas()
    labels = renderer.draw_points(canvas, draw, (1, 1, 0, 0, 0), BOUNDS)
    assert len(labels) == len(dataset.y_metrics) - 1
    labels = renderer.draw_points(canvas, draw, (1, 1, 1, 1, 0), BOUNDS)
    assert len(labels) == len(dataset.y_metrics)

    for revealed in [(1, 0, 0, 0, 0), (1, 1, 0.5, 0, 0), (1, 1, 1, 0.5, 0), (1, 1, 1, 1, 0.5)]:
        frame = renderer.render(graph(revealed))
        assert frame.dtype == np.uint8


def test_interpolated_value_is_labelled_approximate(widget_dir) -> None:
    csv_path = widget_dir / "data.csv"
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    col = lines[0].split(",").index("price")
    cells = lines[2].split(",")  # W2
    cells[col] = ""
    lines[2] = ",".join(cells)
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    config = make_config()
    dataset = Dataset.load(str(widget_dir), "date", y_scale=config.y_scale)
    assert "price" in dataset.y_metrics
    renderer = Renderer(config, dataset, str(widget_dir))
    canvas, draw = renderer.new_canvas()
    labels = renderer.draw_points(canvas, draw, (1, 1, 0, 0, 0), BOUNDS)
    texts = [item["text"] for item in labels]
    assert sum(text.startswith("≈ ") for text in texts) == 1
    assert renderer.render(graph((1, 1, 0.5, 0, 0))).std() > 0


def test_speculation_prefix_becomes_the_fact_card_label() -> None:
    from fromversionzero.components import fact_label_and_text, split_bullets

    label, text = fact_label_and_text("Speculation: next generation; Hybrid.", "Insight")
    assert label == "Speculation"
    assert split_bullets(text) == ["Next generation", "Hybrid."]
    assert fact_label_and_text("Faster; lighter.", "Insight") == ("Insight", "Faster; lighter.")
