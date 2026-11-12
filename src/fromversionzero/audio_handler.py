# src/fromversionzero/audio_handler.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Mixes the soundtrack (trimmed, faded) and timed sound effects into the rendered video.

from __future__ import annotations

import logging
import os
import subprocess

from fromversionzero.config import VideoConfig
from fromversionzero.encoder import FFmpegNotFoundError, find_ffmpeg

logger = logging.getLogger(__name__)


#: Every audio input is brought to this format before mixing, so a mono 8 kHz
#: effect and a stereo 44.1 kHz soundtrack mix predictably.
AUDIO_FORMAT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
#: Audio files come from the dataset. Only local files may be opened, so a
#: playlist disguised as ``soundtrack.mp3`` cannot make FFmpeg fetch URLs.
AUDIO_INPUT_OPTIONS = ["-protocol_whitelist", "file"]


def build_audio_command(
    ffmpeg: str,
    video_path: str,
    soundtrack: str | None,
    sfx_events: list[tuple[str, float]],
    duration: float,
    output_path: str,
    volume: float,
    fade_in: float,
    fade_out: float,
    loop_music: bool = True,
) -> list[str]:
    """FFmpeg command that muxes the video stream (copied) with the mixed audio.

    The audio track always lasts exactly ``duration`` seconds: the mix is
    padded with silence and trimmed, so a soundtrack or effect shorter than
    the video never shortens the video.
    """
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", video_path]
    filters: list[str] = []
    mix: list[str] = []
    index = 1
    if soundtrack:
        if loop_music:
            cmd += ["-stream_loop", "-1"]
        cmd += [*AUDIO_INPUT_OPTIONS, "-i", soundtrack]
        fade_in, fade_out = min(fade_in, duration), min(fade_out, duration)
        filters.append(
            f"[{index}:a]{AUDIO_FORMAT},atrim=end={duration},volume={volume},"
            f"afade=t=in:ss=0:d={fade_in},"
            f"afade=t=out:st={max(0.0, duration - fade_out)}:d={fade_out}[music]"
        )
        mix.append("[music]")
        index += 1
    for n, (path, start) in enumerate(sfx_events):
        cmd += [*AUDIO_INPUT_OPTIONS, "-i", path]
        delay = max(0, int(start * 1000))
        filters.append(f"[{index}:a]{AUDIO_FORMAT},adelay=delays={delay}:all=1[sfx{n}]")
        mix.append(f"[sfx{n}]")
        index += 1
    if len(mix) > 1:
        filters.append(
            f"{''.join(mix)}amix=inputs={len(mix)}:duration=longest:dropout_transition=0"
            ":normalize=0[mix]"
        )
        mixed = "[mix]"
    else:
        mixed = mix[0]
    # Silence after the last sound, then an exact cut at the video's end.
    filters.append(f"{mixed}apad,atrim=end={duration}[audio]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[audio]"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-c:v", "copy", "-t", f"{duration}"]
    cmd += ["-movflags", "+faststart", output_path]
    return cmd


class AudioHandler:
    """Adds music and sound effects to a silent render using FFmpeg."""

    def __init__(self, config: VideoConfig) -> None:
        self.config = config
        self.ffmpeg_exe: str | None
        try:
            self.ffmpeg_exe = find_ffmpeg()
        except FFmpegNotFoundError as e:
            logger.debug(str(e))
            self.ffmpeg_exe = None

    def merge_audio(
        self,
        video_input_path: str,
        soundtrack_path: str,
        sfx_events: list[tuple[str, float]],
        video_duration: float,
        output_path: str,
    ) -> bool:
        """Writes ``output_path``. Returns ``False`` if FFmpeg failed.

        Without any existing audio file the silent video is simply moved to
        ``output_path`` (no FFmpeg call).
        """
        soundtrack = (
            soundtrack_path if soundtrack_path and os.path.exists(soundtrack_path) else None
        )
        active_sfx = [event for event in sfx_events if os.path.exists(event[0])]
        if len(active_sfx) < len(sfx_events):
            logger.warning(f"{len(sfx_events) - len(active_sfx)} SFX file(s) not found; skipped.")

        if soundtrack is None and not active_sfx:
            logger.info("No soundtrack or SFX found; the video stays silent.")
            os.replace(video_input_path, output_path)
            return True
        if self.ffmpeg_exe is None:
            raise FFmpegNotFoundError("FFmpeg is required to merge audio. Install imageio-ffmpeg.")

        cmd = build_audio_command(
            self.ffmpeg_exe,
            video_input_path,
            soundtrack,
            active_sfx,
            video_duration,
            output_path,
            self.config.music_volume,
            self.config.music_fade_in,
            self.config.music_fade_out,
            self.config.music_loop,
        )
        logger.debug("FFmpeg audio: " + " ".join(cmd))
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            logger.warning(f"FFmpeg audio mixing failed:\n{e.stderr}")
            return False
        except OSError as e:
            logger.warning(f"Could not run FFmpeg: {e}")
            return False
        return True
