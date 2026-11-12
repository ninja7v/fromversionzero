# tests/test_timeline.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Pure frame planning: phase boundaries, reveal progression, cards, camera, SFX cues.

import itertools
from pathlib import Path

import pytest
from conftest import WIDGET_DIR, make_config

from fromversionzero.data_loader import Dataset
from fromversionzero.timeline import Timeline, TimelinePlanner, card_alpha


def plan(**overrides: object) -> tuple[TimelinePlanner, list]:
    config = make_config(**overrides).for_output_mode()
    dataset = Dataset.load(str(WIDGET_DIR), "date", include_speculation=config.speculation)
    planner = TimelinePlanner(config, dataset, str(WIDGET_DIR))
    return planner, planner.plan()


def test_timeline_phase_boundaries() -> None:
    config = make_config()
    tl = Timeline.build(config, n_released=4, has_speculation=True)
    assert tl.cycle_duration == pytest.approx(0.75)
    assert tl.intro_end == pytest.approx(0.5)
    assert tl.evolution_end == pytest.approx(0.5 + 4 * 0.75)
    assert tl.hold_end == pytest.approx(tl.evolution_end + 0.5)
    assert tl.spec_end == pytest.approx(tl.hold_end + 1.0)
    assert tl.total_duration == pytest.approx(tl.spec_end + 0.5)
    assert tl.total_frames == round(tl.total_duration * config.fps)
    no_spec = Timeline.build(config, n_released=4, has_speculation=False)
    assert no_spec.speculation_duration == 0.0


def test_plan_has_one_state_per_frame_and_every_phase() -> None:
    planner, frames = plan(fps=10)
    assert len(frames) == planner.timeline.total_frames
    assert frames[0].intro_progress == 0.0
    assert any(f.kind == "fullscreen_text" for f in frames)
    assert frames[-1].outro_progress is not None
    assert all(f.t == pytest.approx(i / 10) for i, f in enumerate(frames))


def test_reveal_progress_never_goes_backwards() -> None:
    _, frames = plan(fps=10)
    graph_frames = [f for f in frames if f.kind == "graph" and f.intro_progress is None]
    for before, after in itertools.pairwise(graph_frames):
        if after.outro_progress is None:
            assert all(b <= a + 1e-9 for b, a in zip(before.revealed_pcts, after.revealed_pcts))


def test_fact_card_alpha_envelope() -> None:
    assert card_alpha(-0.1, 2.0) == 0.0
    assert card_alpha(0.25, 2.0) == pytest.approx(0.5)
    assert card_alpha(1.0, 2.0) == 1.0
    assert card_alpha(1.75, 2.0) == pytest.approx(0.5)
    assert card_alpha(2.0, 2.0) == 0.0
    _, frames = plan(fps=20, fact_card_duration=0.6)
    shown = [f for f in frames if f.active_fact_index >= 0]
    assert shown and all(0.0 <= f.fact_card_alpha <= 1.0 for f in shown)


def test_speculation_card_uses_configured_copy() -> None:
    _, frames = plan(fps=10, speculation_text="Next?", labels={"speculation_prompt": "Tell us"})
    card = next(f for f in frames if f.kind == "fullscreen_text")
    assert card.text == "Next?" and card.subtext == "Tell us"


def test_no_speculation_phase_when_disabled() -> None:
    planner, frames = plan(speculation=False)
    assert not any(f.kind == "fullscreen_text" for f in frames)
    assert planner.timeline.speculation_duration == 0.0


def test_shorts_has_no_intro_or_outro() -> None:
    _, frames = plan(shorts=True)
    assert all(f.intro_progress is None and f.outro_progress is None for f in frames)


def test_camera_keeps_revealed_points_in_view_after_settling() -> None:
    planner, frames = plan(fps=30, steady_duration=3.0, camera={"speed": 6.0})
    ds = planner.dataset
    tl = planner.timeline
    settled = frames[int((tl.intro_end + tl.cycle_duration - 0.05) * 30)]
    xmin, xmax, ymin, ymax = settled.camera_bounds
    first = ds.released[0]
    assert xmin < ds.x(first) < xmax
    assert all(ymin < ds.y(first, m) < ymax for m in ds.y_metrics)


def test_sfx_cues_are_planned_at_reveal_starts(tmp_path: Path) -> None:
    elsewhere = str(tmp_path.parent / "elsewhere" / "c.wav")  # absolute on every OS
    (tmp_path / "data.csv").write_text(
        f"name,year,p,sfx\nA,2000,1,a.wav\nB,2001,2,\nC,2002,3,{elsewhere}\n", encoding="utf-8"
    )
    config = make_config(mix_sfx=True, speculation=False)
    dataset = Dataset.load(str(tmp_path), "year")
    assert sum("sfx file not found" in w for w in dataset.warnings) == 2
    planner = TimelinePlanner(config, dataset, str(tmp_path))
    cues = planner.sfx_cues()
    assert [c.path for c in cues] == [str(tmp_path / "a.wav"), elsewhere]
    assert cues[0].start == pytest.approx(0.5)
    assert cues[1].start == pytest.approx(0.5 + 2 * 0.75)
    muted = TimelinePlanner(make_config(mix_sfx=False), planner.dataset, str(tmp_path))
    assert muted.sfx_cues() == []


@pytest.mark.parametrize("fact_card_duration", [0.75, 1.0, 4.0])
def test_fact_card_never_pops_between_frames(fact_card_duration: float) -> None:
    """A card fades in and out once: its opacity never jumps between frames,
    including when the hold starts."""
    config = make_config(fps=40, fact_card_duration=fact_card_duration, hold_duration=1.0)
    planner = TimelinePlanner(config, Dataset.load(str(WIDGET_DIR), "date"))
    hold_end = planner.timeline.hold_end
    alphas = [f.fact_card_alpha for f in planner.plan() if f.kind == "graph" and f.t < hold_end]
    assert max(alphas) == 1.0
    assert max(abs(b - a) for a, b in itertools.pairwise(alphas)) <= 0.2
