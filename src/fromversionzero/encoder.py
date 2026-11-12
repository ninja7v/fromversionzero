# src/fromversionzero/encoder.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Streams raw RGB frames into FFmpeg (libx264/libx265, BT.709, yuv420p).

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import tempfile
from typing import IO, Protocol

import numpy as np

from fromversionzero.config import EncodingSettings

logger = logging.getLogger(__name__)


class FFmpegNotFoundError(RuntimeError):
    pass


def find_ffmpeg() -> str:
    """Locates FFmpeg: the imageio-ffmpeg bundled binary first, then ``PATH``."""
    try:
        import imageio_ffmpeg

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, RuntimeError, OSError) as e:
        logger.debug(f"imageio-ffmpeg binary unavailable: {e}")
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise FFmpegNotFoundError(
            "FFmpeg not found. Install the 'imageio-ffmpeg' package or put ffmpeg on PATH."
        )
    return exe


class VideoWriter(Protocol):
    """Sink for RGB ``uint8`` frames of shape ``(height, width, 3)``."""

    def write(self, frame: np.ndarray) -> None: ...

    def close(self) -> None: ...

    def abort(self) -> None: ...


_CODECS: dict[str, list[str]] = {
    "h264": ["-c:v", "libx264", "-tune", "animation", "-profile:v", "high"],
    "h265": ["-c:v", "libx265", "-tag:v", "hvc1"],
}


def build_ffmpeg_command(
    ffmpeg: str,
    output_path: str,
    width: int,
    height: int,
    fps: int,
    encoding: EncodingSettings,
) -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        *_CODECS[encoding.codec],
        "-preset",
        encoding.preset,
        "-crf",
        str(encoding.crf),
        # Convert with the BT.709 matrix and tag it, so players don't shift colors.
        "-vf",
        "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-movflags",
        "+faststart",
        output_path,
    ]


class FFmpegVideoWriter:
    """Encodes frames by piping them to an FFmpeg subprocess."""

    def __init__(
        self,
        output_path: str,
        width: int,
        height: int,
        fps: int,
        encoding: EncodingSettings,
        ffmpeg: str | None = None,
    ) -> None:
        self.output_path = output_path
        self.shape = (height, width, 3)
        self.frames_written = 0
        # Lives as long as the subprocess; closed in close()/abort().
        self._stderr: IO[bytes] = tempfile.TemporaryFile()  # noqa: SIM115
        cmd = build_ffmpeg_command(
            ffmpeg or find_ffmpeg(), output_path, width, height, fps, encoding
        )
        logger.debug("FFmpeg: " + " ".join(cmd))
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=self._stderr)

    def _error_text(self) -> str:
        self._stderr.seek(0)
        return self._stderr.read().decode(errors="replace").strip()

    def write(self, frame: np.ndarray) -> None:
        if frame.shape != self.shape or frame.dtype != np.uint8:
            raise ValueError(
                f"Frame must be uint8 with shape {self.shape}, got {frame.dtype} {frame.shape}."
            )
        assert self._proc.stdin is not None
        try:
            # The buffer is written as is (no intermediate bytes copy).
            self._proc.stdin.write(np.ascontiguousarray(frame).data)
        except OSError:
            # FFmpeg exited: BrokenPipeError on POSIX, OSError(EINVAL) on Windows.
            self._close_stdin()
            self._proc.wait()
            raise RuntimeError(f"FFmpeg stopped accepting frames: {self._error_text()}") from None
        self.frames_written += 1

    def _close_stdin(self) -> None:
        """Closes the pipe, ignoring errors from an FFmpeg that already exited
        (its return code and stderr explain why). Closing here, not at garbage
        collection, keeps Python from reporting the failed flush later."""
        stdin = self._proc.stdin
        if stdin is not None and not stdin.closed:
            with contextlib.suppress(OSError):
                stdin.close()

    def close(self) -> None:
        self._close_stdin()
        code = self._proc.wait()
        error = self._error_text()
        self._stderr.close()
        if code != 0:
            raise RuntimeError(f"FFmpeg exited with code {code}: {error}")

    def abort(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        self._close_stdin()
        if not self._stderr.closed:
            self._stderr.close()
