# tests/test_pipeline.py
# MIT License - Copyright (c) 2026 Luc Prevost
# End-to-end pipeline: planning -> rendering -> FFmpeg encoding -> audio mixing.

import os
import re
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from conftest import make_config

from fromversionzero.audio_handler import AudioHandler
from fromversionzero.config import VideoConfig
from fromversionzero.encoder import FFmpegVideoWriter, build_ffmpeg_command, find_ffmpeg
from fromversionzero.timeline import SfxCue, Timeline, TimelinePlanner
from fromversionzero.video_pipeline import VideoPipeline


class RecordingWriter:
    def __init__(self) -> None:
        self.frames: list[np.ndarray] = []
        self.closed = self.aborted = False

    def write(self, frame: np.ndarray) -> None:
        self.frames.append(frame)

    def close(self) -> None:
        self.closed = True
        # Pretend we encoded something so finalization has a file to publish.

    def abort(self) -> None:
        self.aborted = True


def probe(path: Path) -> str:
    result = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-i", str(path), "-map", "0:v", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stderr


def frame_count(probe_output: str) -> int:
    return int(re.findall(r"frame=\s*(\d+)", probe_output)[-1])


def test_pipeline_writes_every_planned_frame(widget_dir: Path) -> None:
    writer = RecordingWriter()

    def factory(path: str, config: VideoConfig) -> RecordingWriter:
        Path(path).write_bytes(b"video")
        return writer

    pipeline = VideoPipeline(make_config(output_folder=str(widget_dir)), str(widget_dir), factory)
    output = pipeline.generate_video()
    planner = TimelinePlanner(pipeline.config, pipeline.load_dataset())
    assert len(writer.frames) == planner.timeline.total_frames
    assert writer.closed and not writer.aborted
    assert all(f.shape == (240, 426, 3) and f.dtype == np.uint8 for f in writer.frames)
    assert Path(output).name == "Widget_landscape_240p.mp4"


def test_parallel_and_sequential_rendering_are_identical(widget_dir: Path) -> None:
    config = make_config(fps=2)
    sequential = VideoPipeline(config, str(widget_dir), max_workers=1)
    parallel = VideoPipeline(config, str(widget_dir), max_workers=2)
    dataset = sequential.load_dataset()
    frames = TimelinePlanner(sequential.config, dataset).plan()
    a = list(sequential.render_frames(frames, dataset))
    b = list(parallel.render_frames(frames, dataset))
    assert len(a) == len(b) == len(frames)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))


def test_encoder_failure_aborts_and_cleans_up(widget_dir: Path) -> None:
    class FailingWriter(RecordingWriter):
        def write(self, frame: np.ndarray) -> None:
            raise RuntimeError("disk full")

    writer = FailingWriter()
    pipeline = VideoPipeline(
        make_config(output_folder=str(widget_dir / "out")), str(widget_dir), lambda p, c: writer
    )
    with pytest.raises(RuntimeError, match="disk full"):
        pipeline.generate_video()
    assert writer.aborted
    assert not list((widget_dir / "out").glob("*.mp4"))


def test_real_render_is_h264_with_expected_frames(widget_dir: Path) -> None:
    config = make_config(
        output_folder=str(widget_dir), speculation=False, intro_duration=0, outro_duration=0
    )
    output = Path(VideoPipeline(config, str(widget_dir)).generate_video())
    info = probe(output)
    expected = TimelinePlanner(
        config, VideoPipeline(config, str(widget_dir)).load_dataset()
    ).timeline
    assert "h264" in info and "426x240" in info and "bt709" in info
    assert frame_count(info) == expected.total_frames
    assert not [p for p in widget_dir.iterdir() if "_silent" in p.name or "_audio" in p.name]
    if os.name == "posix":
        assert output.stat().st_mode & 0o044, "output must not keep mkstemp's 0600 mode"


def test_shorts_output_name_uses_portrait_dimensions(widget_dir: Path) -> None:
    pipeline = VideoPipeline(make_config(shorts=True), str(widget_dir))
    assert pipeline.resolve_output_path().endswith("Widget_portrait_1080x1920.mp4")


def test_ffmpeg_writer_rejects_wrong_frames_and_reports_errors(tmp_path: Path) -> None:
    config = make_config()
    writer = FFmpegVideoWriter(str(tmp_path / "x.mp4"), 64, 32, 4, config.encoding)
    with pytest.raises(ValueError, match="shape"):
        writer.write(np.zeros((10, 10, 3), dtype=np.uint8))
    writer.abort()
    bad = FFmpegVideoWriter(str(tmp_path / "missing-dir" / "x.mp4"), 64, 32, 4, config.encoding)
    with pytest.raises(RuntimeError, match="FFmpeg"):
        for _ in range(50):
            bad.write(np.zeros((32, 64, 3), dtype=np.uint8))
        bad.close()


def test_h265_command_is_tagged_for_apple_players() -> None:
    config = make_config(encoding={"codec": "h265", "crf": 22, "preset": "slow"})
    cmd = build_ffmpeg_command("ffmpeg", "out.mp4", 64, 32, 30, config.encoding)
    assert cmd[cmd.index("-c:v") + 1] == "libx265"
    assert "hvc1" in cmd and cmd[cmd.index("-crf") + 1] == "22"


def _silent_video(path: Path) -> None:
    writer = FFmpegVideoWriter(str(path), 64, 64, 4, make_config().encoding)
    for _ in range(4):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.close()


def _wav(path: Path, seconds: float = 1.0) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(np.zeros(int(8000 * seconds), dtype=np.int16).tobytes())


def test_audio_merge_adds_music_and_sfx(tmp_path: Path) -> None:
    silent, music, sfx = tmp_path / "silent.mp4", tmp_path / "music.wav", tmp_path / "hit.wav"
    _silent_video(silent)
    _wav(music)
    _wav(sfx, 0.2)
    output = tmp_path / "with-audio.mp4"
    handler = AudioHandler(make_config(music={"volume": 0.5, "fade_in": 0, "fade_out": 0}))
    assert handler.merge_audio(str(silent), str(music), [(str(sfx), 0.25)], 1.0, str(output))
    info = probe(output)
    assert "Audio:" in info and "h264" in info


def test_audio_failure_preserves_the_silent_render(tmp_path: Path) -> None:
    pipeline = VideoPipeline(make_config(output_folder=str(tmp_path)), str(tmp_path))
    output, silent = tmp_path / "final.mp4", tmp_path / "silent.mp4"
    silent.write_bytes(b"complete-silent-video")

    def fail_merge(**kwargs: object) -> bool:
        Path(str(kwargs["output_path"])).write_bytes(b"partial")
        return False

    pipeline.audio_handler.merge_audio = fail_merge  # type: ignore[method-assign]
    timeline = Timeline.build(pipeline.config, 1, False)
    pipeline.finalize_output(str(output), str(silent), timeline, [SfxCue("x.wav", 0.0)])
    assert output.read_bytes() == b"complete-silent-video"
    assert not silent.exists()
    assert not list(tmp_path.glob("*_audio.mp4"))


def test_parallel_rendering_keeps_a_bounded_number_of_frames_in_flight() -> None:
    """Workers outpace the encoder; frames waiting for it must not pile up."""
    from concurrent.futures import Future

    from fromversionzero import video_pipeline

    class FakePool:
        def __init__(self) -> None:
            self.submitted = 0

        def submit(self, fn, states):  # type: ignore[no-untyped-def]
            self.submitted += len(states)
            future: Future = Future()
            future.set_result([np.full((1,), s) for s in states])
            return future

    pool = FakePool()
    frames = list(range(10_000))
    workers, frame_bytes = 4, 6 * 1024 * 1024  # 1080p RGB
    consumed = 0
    worst = 0
    for frame in VideoPipeline._render_bounded(pool, frames, workers, frame_bytes):  # type: ignore[arg-type]
        assert int(frame[0]) == consumed  # order is preserved
        consumed += 1
        worst = max(worst, pool.submitted - consumed)
    assert consumed == len(frames)
    assert worst * frame_bytes <= video_pipeline.IN_FLIGHT_BYTES + frame_bytes * workers * 2


def _durations(path: Path) -> tuple[float, float]:
    """(video, audio) stream durations in seconds, decoded with FFmpeg."""

    def decoded(stream: str) -> float:
        result = subprocess.run(
            [find_ffmpeg(), "-hide_banner", "-i", str(path), "-map", stream, "-f", "null", "-"],
            capture_output=True,
            text=True,
            check=True,
        )
        h, m, s = re.findall(r"time=(\d+):(\d+):([\d.]+)", result.stderr)[-1]
        return int(h) * 3600 + int(m) * 60 + float(s)

    return decoded("0:v"), decoded("0:a")


def _long_silent_video(path: Path, seconds: int = 3) -> None:
    writer = FFmpegVideoWriter(str(path), 64, 64, 4, make_config().encoding)
    for _ in range(4 * seconds):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.close()


@pytest.mark.parametrize("loop", [True, False])
def test_short_soundtrack_never_shortens_the_video(tmp_path: Path, loop: bool) -> None:
    silent, music, output = tmp_path / "silent.mp4", tmp_path / "music.wav", tmp_path / "out.mp4"
    _long_silent_video(silent)
    _wav(music, 1.0)
    handler = AudioHandler(make_config(music={"fade_in": 0, "fade_out": 0, "loop": loop}))
    assert handler.merge_audio(str(silent), str(music), [], 3.0, str(output))
    video, audio = _durations(output)
    assert video == pytest.approx(3.0, abs=0.05)
    assert audio == pytest.approx(3.0, abs=0.1)


def test_sfx_without_music_never_shortens_the_video(tmp_path: Path) -> None:
    silent, sfx, output = tmp_path / "silent.mp4", tmp_path / "hit.wav", tmp_path / "out.mp4"
    _long_silent_video(silent)
    _wav(sfx, 0.2)
    handler = AudioHandler(make_config())
    events = [(str(sfx), 0.25), (str(sfx), 2.0)]
    assert handler.merge_audio(str(silent), "", events, 3.0, str(output))
    video, audio = _durations(output)
    assert video == pytest.approx(3.0, abs=0.05)
    assert audio == pytest.approx(3.0, abs=0.1)


def test_long_soundtrack_is_cut_at_the_end_of_the_video(tmp_path: Path) -> None:
    silent, music, output = tmp_path / "silent.mp4", tmp_path / "music.wav", tmp_path / "out.mp4"
    _silent_video(silent)
    _wav(music, 5.0)
    assert AudioHandler(make_config()).merge_audio(str(silent), str(music), [], 1.0, str(output))
    video, audio = _durations(output)
    assert video == pytest.approx(1.0, abs=0.05)
    assert audio == pytest.approx(1.0, abs=0.1)


def test_audio_inputs_cannot_open_network_protocols() -> None:
    from fromversionzero.audio_handler import build_audio_command

    cmd = build_audio_command("ffmpeg", "v.mp4", "m.mp3", [("s.wav", 1.0)], 5.0, "o.mp4", 1, 0, 0)
    for audio_input in ("m.mp3", "s.wav"):
        at = cmd.index(audio_input)
        assert cmd[at - 3 : at] == ["-protocol_whitelist", "file", "-i"]
    assert "-shortest" not in cmd


def test_render_workers_never_download_media(
    widget_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Links are fetched once by the pipeline; renderers only read the cache."""
    from fromversionzero import media
    from fromversionzero.renderer import Renderer

    monkeypatch.setenv(media.CACHE_ENV, str(tmp_path / "cache"))
    calls: list[str] = []

    def no_network(request: object, timeout: float) -> object:
        calls.append(request.full_url)  # type: ignore[attr-defined]
        raise OSError("offline")

    monkeypatch.setattr(media, "_open", no_network)
    csv = (widget_dir / "data.csv").read_text(encoding="utf-8")
    (widget_dir / "data.csv").write_text(
        csv.replace(",w1.png,", ",https://example.org/w1.png,"), encoding="utf-8"
    )
    pipeline = VideoPipeline(make_config(), str(widget_dir))
    dataset = pipeline.load_dataset()
    pipeline._prefetch_media(dataset)
    assert calls == ["https://example.org/w1.png"]
    renderer = Renderer(pipeline.config, dataset, str(widget_dir), offline_media=True)
    frames = TimelinePlanner(pipeline.config, dataset).plan()
    renderer.render(frames[len(frames) // 2])
    assert len(calls) == 1, "a renderer retried a download the pipeline already attempted"


def test_render_still_defaults_to_the_complete_timeline(widget_dir: Path) -> None:
    pipeline = VideoPipeline(make_config(), str(widget_dir))
    image, t, total = pipeline.render_still()
    assert image.size == (426, 240)
    timeline = TimelinePlanner(pipeline.config, pipeline.load_dataset()).timeline
    assert t == pytest.approx(timeline.hold_end - timeline.dt)
    assert total == pytest.approx(timeline.total_duration)
    assert pipeline.render_still(999.0)[1] < total  # clamped to the last frame
