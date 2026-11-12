# src/fromversionzero/timeline.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Pure timeline planning: turns (config, dataset) into one immutable FrameState
# per output frame plus the sound-effect cues. No drawing happens here.

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from fromversionzero.config import VideoConfig
from fromversionzero.data_loader import Dataset, ProductDataPoint
from fromversionzero.interpolator import CameraViewport, ease_sigmoid

Bounds = tuple[float, float, float, float]


@dataclass(frozen=True)
class FrameState:
    """Everything a renderer needs to draw one frame (picklable, immutable)."""

    kind: Literal["graph", "fullscreen_text"]
    t: float
    revealed_pcts: tuple[float, ...] = ()
    camera_bounds: Bounds = (0.0, 0.0, 0.0, 0.0)
    active_fact_index: int = -1
    fact_card_alpha: float = 0.0
    intro_progress: float | None = None
    outro_progress: float | None = None
    text: str = ""
    subtext: str = ""
    text_alpha: float = 0.0


@dataclass(frozen=True)
class SfxCue:
    path: str
    start: float


@dataclass(frozen=True)
class Timeline:
    """Phase boundaries (seconds) and frame bookkeeping."""

    fps: int
    dt: float
    cycle_duration: float
    evolution_duration: float
    speculation_duration: float
    intro_end: float
    evolution_end: float
    hold_end: float
    spec_end: float
    total_duration: float
    total_frames: int

    @classmethod
    def build(cls, config: VideoConfig, n_released: int, has_speculation: bool) -> Timeline:
        cycle = config.reveal_duration + config.steady_duration
        evolution = n_released * cycle
        speculation = config.speculation_duration if has_speculation else 0.0
        intro_end = config.intro_duration
        evolution_end = intro_end + evolution
        hold_end = evolution_end + config.hold_duration
        spec_end = hold_end + speculation
        total = spec_end + config.outro_duration
        return cls(
            fps=config.fps,
            dt=1.0 / config.fps,
            cycle_duration=cycle,
            evolution_duration=evolution,
            speculation_duration=speculation,
            intro_end=intro_end,
            evolution_end=evolution_end,
            hold_end=hold_end,
            spec_end=spec_end,
            total_duration=total,
            total_frames=int(round(total * config.fps, 6)),
        )


def card_alpha(elapsed: float, duration: float, fade_cap: float = 0.5) -> float:
    """Symmetric fade-in / hold / fade-out envelope for a timed card."""
    if duration <= 0 or elapsed < 0 or elapsed >= duration:
        return 0.0
    fade = min(fade_cap, duration / 2.0)
    if elapsed < fade:
        return elapsed / fade
    if elapsed > duration - fade:
        return (duration - elapsed) / fade
    return 1.0


class TimelinePlanner:
    """Plans every frame sequentially (the camera is stateful), without rendering."""

    def __init__(self, config: VideoConfig, dataset: Dataset, dataset_dir: str = "") -> None:
        self.config = config
        self.dataset = dataset
        self.dataset_dir = dataset_dir
        self.released: list[ProductDataPoint] = dataset.released
        self.speculative: list[ProductDataPoint] = dataset.speculative
        if not self.released:
            raise ValueError("The dataset must contain at least one released generation.")
        self.timeline = Timeline.build(config, len(self.released), bool(self.speculative))

        xs = [dataset.x(p) for p in self.released]
        ys = [dataset.y(p, m) for p in self.released for m in dataset.y_metrics]
        self.x_range = max(xs) - min(xs)
        self.y_min, self.y_max = min(ys), max(ys)
        self.y_range = self.y_max - self.y_min

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def plan(self) -> list[FrameState]:
        camera = self._initial_camera()
        tl = self.timeline
        return [self._plan_frame(f * tl.dt, camera) for f in range(tl.total_frames)]

    def sfx_cues(self) -> list[SfxCue]:
        """One cue per released generation with an ``sfx`` file, at its reveal start."""
        if not self.config.mix_sfx:
            return []
        tl = self.timeline
        cues: list[SfxCue] = []
        for i, point in enumerate(self.released):
            if not point.sfx_filename:
                continue
            path = point.sfx_filename
            if not os.path.isabs(path):
                path = os.path.join(self.dataset_dir, path)
            start = tl.intro_end + i * tl.cycle_duration
            if start < tl.total_duration:
                cues.append(SfxCue(path, start))
        return cues

    # ------------------------------------------------------------------
    # Phase dispatch
    # ------------------------------------------------------------------
    def _plan_frame(self, t: float, camera: CameraViewport) -> FrameState:
        tl = self.timeline
        has_spec = bool(self.speculative)
        if self.config.shorts:
            if has_spec and t >= tl.hold_end:
                return self._speculation(t, camera)
            return self._evolution(t, camera)
        if t < tl.intro_end:
            return self._intro(t, camera)
        if t >= tl.spec_end:
            return self._outro(t, camera)
        if has_spec and t >= tl.hold_end:
            return self._speculation(t, camera)
        return self._evolution(t, camera)

    def _intro(self, t: float, camera: CameraViewport) -> FrameState:
        duration = self.config.intro_duration
        return FrameState(
            kind="graph",
            t=t,
            revealed_pcts=(0.0,) * len(self.dataset),
            camera_bounds=camera.get_bounds(),
            intro_progress=t / duration if duration > 0 else 1.0,
        )

    def _outro(self, t: float, camera: CameraViewport) -> FrameState:
        duration = self.config.outro_duration
        elapsed = t - self.timeline.spec_end
        return FrameState(
            kind="graph",
            t=t,
            revealed_pcts=(1.0,) * len(self.dataset),
            camera_bounds=camera.get_bounds(),
            outro_progress=elapsed / duration if duration > 0 else 1.0,
        )

    def _speculation(self, t: float, camera: CameraViewport) -> FrameState:
        tl = self.timeline
        spec_t = t - tl.hold_end
        text_duration = min(3.0, tl.speculation_duration * 0.4)

        # Sub-phase 1: fullscreen question card.
        if spec_t < text_duration:
            local = spec_t / text_duration
            if local < 0.15:
                alpha = ease_sigmoid(local / 0.15)
            elif local > 0.85:
                alpha = ease_sigmoid(1.0 - (local - 0.85) / 0.15)
            else:
                alpha = 1.0
            return FrameState(
                kind="fullscreen_text",
                t=t,
                text=self.config.speculation_text,
                subtext=self.config.labels.speculation_prompt,
                text_alpha=alpha,
            )

        # Sub-phase 2: graph returns and the speculated point animates in.
        sub_duration = tl.speculation_duration - text_duration
        progress = (spec_t - text_duration) / sub_duration if sub_duration > 0 else 1.0
        reveal = min(1.0, progress / 0.60)
        if progress < 0.05:
            fact_alpha = 0.0
        elif progress < 0.20:
            fact_alpha = ease_sigmoid((progress - 0.05) / 0.15)
        elif progress < 0.90:
            fact_alpha = 1.0
        else:
            fact_alpha = ease_sigmoid(1.0 - (progress - 0.90) / 0.10)

        ds = self.dataset
        xs = [ds.x(p) for p in self.released]
        ys = [ds.y(p, m) for p in self.released for m in ds.y_metrics]
        prev = self.released[-1]
        eased = ease_sigmoid(reveal)
        for point in self.speculative:
            xs.append(ds.x(prev) + (ds.x(point) - ds.x(prev)) * eased)
            ys.extend(
                ds.y(prev, m) + (ds.y(point, m) - ds.y(prev, m)) * eased for m in ds.y_metrics
            )

        self._target(camera, xs, ys, self._pad(0.10, 1.0), self._pad(0.24, 5.0))
        camera.update(tl.dt, speed=self.config.camera_speed)
        n_real = len(self.released)
        return FrameState(
            kind="graph",
            t=t,
            revealed_pcts=(1.0,) * n_real + (reveal,) * len(self.speculative),
            camera_bounds=camera.get_bounds(),
            active_fact_index=n_real,
            fact_card_alpha=fact_alpha,
        )

    def _evolution(self, t: float, camera: CameraViewport) -> FrameState:
        tl = self.timeline
        cfg = self.config
        ds = self.dataset
        graph_t = t - tl.intro_end
        revealed = [0.0] * len(ds)
        fact_index, fact_alpha = -1, 0.0
        xs: list[float] = []
        ys: list[float] = []

        for i, point in enumerate(self.released):
            start = i * tl.cycle_duration
            if graph_t < start:
                continue
            pct = min(1.0, (graph_t - start) / cfg.reveal_duration)
            revealed[i] = pct
            # A card lasts at most its generation's cycle; the last one may run
            # on into the hold. One envelope per card, so it never reappears.
            window = tl.cycle_duration
            if i == len(self.released) - 1:
                window += cfg.hold_duration
            fact_duration = min(window, cfg.fact_card_duration)
            if graph_t < start + window and graph_t - start < fact_duration:
                fact_index = i
                fact_alpha = card_alpha(graph_t - start, fact_duration)
            # Camera follows the moving head of the newest curve segment.
            if i > 0 and pct < 1.0:
                eased = ease_sigmoid(pct)
                prev = self.released[i - 1]
                xs.append(ds.x(prev) + (ds.x(point) - ds.x(prev)) * eased)
                ys.extend(
                    ds.y(prev, m) + (ds.y(point, m) - ds.y(prev, m)) * eased for m in ds.y_metrics
                )
            else:
                xs.append(ds.x(point))
                ys.extend(ds.y(point, m) for m in ds.y_metrics)

        # Hold phase: every released point visible.
        if graph_t >= tl.evolution_duration:
            for i in range(len(self.released)):
                revealed[i] = 1.0

        if xs:
            # Wide framing on the first point, then ease into asymmetric
            # padding that leaves room ahead of the moving head.
            init_pad = self._pad(0.5, 5.0)
            if graph_t < tl.cycle_duration:
                pad_left = pad_right = init_pad
            else:
                k = ease_sigmoid(min(1.0, (graph_t - tl.cycle_duration) / cfg.reveal_duration))
                pad_left = init_pad + (self._pad(0.10, 1.0) - init_pad) * k
                pad_right = init_pad + (self._pad(0.22, 5.0) - init_pad) * k
            self._target(camera, xs, ys, pad_left, pad_right)

        camera.update(tl.dt, speed=cfg.camera_speed)
        return FrameState(
            kind="graph",
            t=t,
            revealed_pcts=tuple(revealed),
            camera_bounds=camera.get_bounds(),
            active_fact_index=fact_index,
            fact_card_alpha=fact_alpha,
        )

    # ------------------------------------------------------------------
    # Camera helpers
    # ------------------------------------------------------------------
    def _pad(self, fraction: float, fallback: float) -> float:
        return self.x_range * fraction if self.x_range > 0 else fallback

    def _pad_y(self) -> float:
        return self.y_range * 0.08 if self.y_range > 0 else 0.1

    def _initial_camera(self) -> CameraViewport:
        x0 = self.dataset.x(self.released[0])
        pad_x = self._pad(0.5, 5.0)
        pad_y = self._pad_y()
        return CameraViewport(x0 - pad_x, x0 + pad_x, self.y_min - pad_y, self.y_max + pad_y)

    def _target(
        self,
        camera: CameraViewport,
        xs: list[float],
        ys: list[float],
        pad_left: float,
        pad_right: float,
    ) -> None:
        pad_y = max(0.05, self._pad_y())
        camera.set_target(
            xmin=min(xs) - max(0.5, pad_left),
            xmax=max(xs) + max(1.0, pad_right),
            ymin=min(ys) - pad_y,
            ymax=max(ys) + pad_y,
        )
