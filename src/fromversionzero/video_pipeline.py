# src/fromversionzero/video_pipeline.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Orchestration: load dataset -> plan frames -> render (process pool) -> encode -> mix audio.

from __future__ import annotations

import logging
import multiprocessing
import os
import tempfile
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ProcessPoolExecutor

import numpy as np
from PIL import Image

from fromversionzero.audio_handler import AudioHandler
from fromversionzero.config import VideoConfig
from fromversionzero.data_loader import Dataset
from fromversionzero.encoder import FFmpegVideoWriter, VideoWriter
from fromversionzero.renderer import Renderer
from fromversionzero.timeline import FrameState, SfxCue, Timeline, TimelinePlanner

logger = logging.getLogger(__name__)

WriterFactory = Callable[[str, VideoConfig], VideoWriter]

# --------------------------------------------------------------------------
# Worker processes: each builds its own Renderer (fonts and image caches are
# per-process), then renders frames as a pure function of FrameState.
# --------------------------------------------------------------------------
_WORKER: dict[str, Renderer] = {}


def _worker_init(config: VideoConfig, dataset: Dataset, dataset_dir: str) -> None:
    _WORKER["renderer"] = Renderer(config, dataset, dataset_dir, offline_media=True)


def _worker_render(states: Sequence[FrameState]) -> list[np.ndarray]:
    renderer = _WORKER["renderer"]
    return [renderer.render(state) for state in states]


#: Rendered frames wait for the encoder, so without a limit fast workers would
#: buffer the whole video in RAM. At most ``BATCHES_PER_WORKER`` batches per
#: worker are in flight, sized to keep in-flight frames near this budget.
BATCHES_PER_WORKER = 2
IN_FLIGHT_BYTES = 1 << 30
MAX_BATCH = 8


def _pool_context() -> multiprocessing.context.BaseContext:
    """``forkserver`` where available (Python 3.14's default on Linux), else
    ``spawn`` (Windows). Plain ``fork`` copies a process that already runs
    threads (numpy's math library starts some), which can deadlock workers."""
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("forkserver" if "forkserver" in methods else "spawn")


def default_writer_factory(path: str, config: VideoConfig) -> VideoWriter:
    return FFmpegVideoWriter(path, config.width, config.height, config.fps, config.encoding)


class VideoPipeline:
    """Renders one dataset to an MP4 according to a :class:`VideoConfig`."""

    def __init__(
        self,
        config: VideoConfig,
        dataset_dir: str,
        writer_factory: WriterFactory = default_writer_factory,
        max_workers: int | None = None,
    ) -> None:
        self.config: VideoConfig = config.for_output_mode()
        self.dataset_dir: str = dataset_dir
        self.writer_factory = writer_factory
        self.max_workers = max_workers
        self.audio_handler = AudioHandler(self.config)

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def load_dataset(self) -> Dataset:
        cfg = self.config
        return Dataset.load(
            self.dataset_dir,
            x_metric=cfg.x_metric,
            metrics=cfg.metrics,
            include_speculation=cfg.speculation,
            y_scale=cfg.y_scale,
        )

    def generate_video(self) -> str:
        """Renders the video and returns the output path."""
        dataset = self.load_dataset()
        if dataset.x_metric != self.config.x_metric:
            logger.info(f"Using X-metric column '{dataset.x_metric}'.")
            self.config = self.config.replace(x_metric=dataset.x_metric)

        self._prefetch_media(dataset)
        planner = TimelinePlanner(self.config, dataset, self.dataset_dir)
        frames = planner.plan()
        if not frames:
            raise ValueError("The timeline is empty: every duration is zero.")
        cues = planner.sfx_cues()
        timeline = planner.timeline
        self._log_summary(dataset, timeline)

        output_path = self.resolve_output_path()
        silent_path = self._temp_path(output_path, "_silent.mp4")
        writer = self.writer_factory(silent_path, self.config)
        try:
            for index, frame in enumerate(self.render_frames(frames, dataset)):
                writer.write(frame)
                self._log_progress(index, len(frames))
            writer.close()
        except BaseException:
            writer.abort()
            if os.path.exists(silent_path):
                os.remove(silent_path)
            raise

        self.finalize_output(output_path, silent_path, timeline, cues)
        return output_path

    def render_still(self, t: float | None = None) -> tuple[Image.Image, float, float]:
        """One frame as an image: ``(image, time, total_duration)``.

        ``t`` is clamped to the video; by default the frame is the end of the
        hold, when every released generation is on screen.
        """
        dataset = self.load_dataset()
        self._prefetch_media(dataset)
        planner = TimelinePlanner(self.config, dataset, self.dataset_dir)
        frames = planner.plan()
        if not frames:
            raise ValueError("The timeline is empty: every duration is zero.")
        timeline = planner.timeline
        if t is None:
            t = timeline.hold_end - timeline.dt
        index = max(0, min(len(frames) - 1, round(t * timeline.fps)))
        renderer = Renderer(self.config, dataset, self.dataset_dir, offline_media=True)
        frame = renderer.render(frames[index])
        return Image.fromarray(frame), frames[index].t, timeline.total_duration

    def _prefetch_media(self, dataset: Dataset) -> None:
        """Downloads linked media once, before parallel workers start rendering."""
        from fromversionzero.asset_manager import AssetManager

        refs = [p.image_filename for p in dataset.points if p.image_filename]
        failed = AssetManager(self.dataset_dir, media=self.config.media).prefetch(refs)
        if failed:
            logger.warning(
                f"{len(failed)} linked image(s) unavailable; placeholders will be drawn instead."
            )

    def render_frames(self, frames: Sequence[FrameState], dataset: Dataset) -> Iterator[np.ndarray]:
        """Yields rendered frames in order, in parallel when worthwhile.

        Linked media is read from the cache only; :meth:`generate_video`
        downloads it first (see :meth:`_prefetch_media`).
        """
        workers = self.max_workers or min(32, os.cpu_count() or 1)
        workers = min(workers, len(frames))
        if workers > 1 and len(frames) >= 4:
            try:
                pool = ProcessPoolExecutor(
                    max_workers=workers,
                    mp_context=_pool_context(),
                    initializer=_worker_init,
                    initargs=(self.config, dataset, self.dataset_dir),
                )
            except (OSError, NotImplementedError, ImportError) as e:
                # Platforms without working multiprocessing primitives.
                logger.warning(f"Process pool unavailable ({e}); rendering sequentially.")
            else:
                try:
                    frame_bytes = self.config.width * self.config.height * 3
                    yield from self._render_bounded(pool, frames, workers, frame_bytes)
                finally:
                    # On error (or early close) don't wait for queued frames.
                    pool.shutdown(wait=False, cancel_futures=True)
                return
        renderer = Renderer(self.config, dataset, self.dataset_dir, offline_media=True)
        for state in frames:
            yield renderer.render(state)

    @staticmethod
    def _render_bounded(
        pool: ProcessPoolExecutor,
        frames: Sequence[FrameState],
        workers: int,
        frame_bytes: int,
    ) -> Iterator[np.ndarray]:
        """Yields frames in order with at most ``workers * BATCHES_PER_WORKER``
        batches in flight, so memory stays flat however long the video is."""
        slots = workers * BATCHES_PER_WORKER
        batch = max(1, min(MAX_BATCH, IN_FLIGHT_BYTES // (slots * frame_bytes)))
        pending: deque[Future[list[np.ndarray]]] = deque()
        next_start = 0
        while next_start < len(frames) or pending:
            while next_start < len(frames) and len(pending) < slots:
                pending.append(pool.submit(_worker_render, frames[next_start : next_start + batch]))
                next_start += batch
            yield from pending.popleft().result()

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def resolve_output_path(self) -> str:
        """``<product>_<landscape|portrait>_<resolution>.mp4`` in the output folder."""
        cfg = self.config
        name = cfg.product_name or os.path.basename(os.path.abspath(self.dataset_dir)) or "output"
        if cfg.shorts:
            fmt, resolution = "portrait", f"{cfg.width}x{cfg.height}"
        else:
            fmt, resolution = "landscape", cfg.resolution_name
        folder = cfg.output_folder or self.dataset_dir
        return os.path.join(folder, f"{name}_{fmt}_{resolution}.mp4")

    @staticmethod
    def _temp_path(output_path: str, suffix: str) -> str:
        folder = os.path.dirname(output_path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, path = tempfile.mkstemp(suffix=suffix, dir=folder)
        os.close(fd)
        return path

    @staticmethod
    def _publish(source: str, output_path: str) -> None:
        """Atomically moves ``source`` to ``output_path`` with normal file permissions
        (temp files are created 0600)."""
        os.replace(source, output_path)
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(output_path, 0o666 & ~umask)

    def _soundtrack_path(self) -> str:
        music = self.config.music_file
        if os.path.isabs(music):
            return music
        path = os.path.join(self.dataset_dir, music)
        if not os.path.exists(path):
            path = os.path.join(self.dataset_dir, "music", music)
        return path

    def finalize_output(
        self,
        output_path: str,
        silent_path: str,
        timeline: Timeline,
        cues: list[SfxCue],
    ) -> None:
        """Mixes audio into the silent render and atomically publishes the result.

        If mixing fails the complete silent render is kept as the output.
        """
        audio_path = self._temp_path(output_path, "_audio.mp4")
        os.remove(audio_path)
        try:
            merged = self.audio_handler.merge_audio(
                video_input_path=silent_path,
                soundtrack_path=self._soundtrack_path(),
                sfx_events=[(cue.path, cue.start) for cue in cues],
                video_duration=timeline.total_duration,
                output_path=audio_path,
            )
            if merged and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                self._publish(audio_path, output_path)
                if os.path.exists(silent_path):
                    os.remove(silent_path)
                logger.info(f"Video written to {output_path}")
                return
        except Exception as e:  # noqa: BLE001 - keep the silent render on any audio error
            logger.warning(f"Audio merging failed: {e}")
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)

        if os.path.exists(silent_path):
            self._publish(silent_path, output_path)
            logger.warning(f"Audio merging failed. Silent video preserved at: {output_path}")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def _log_summary(self, dataset: Dataset, timeline: Timeline) -> None:
        logger.info(f"Dataset: {len(dataset)} generations, metrics {dataset.y_metrics}")
        if dataset.speculative:
            logger.info(f"Speculation: {len(dataset.speculative)} speculated generation(s).")
        logger.info(
            f"Timeline: {timeline.total_duration:.2f}s, {timeline.total_frames} frames "
            f"@ {timeline.fps} fps, {self.config.width}x{self.config.height}"
        )

    @staticmethod
    def _log_progress(index: int, total: int) -> None:
        done = index + 1
        if done % max(1, total // 10) == 0 or done == total:
            logger.info(f"Rendering: {done / total:.0%} ({done}/{total} frames)")
