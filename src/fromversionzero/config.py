# src/fromversionzero/config.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Configuration schema, layered loading (defaults < video.json < config.json < CLI)
# and the immutable, fully-resolved VideoConfig consumed by the pipeline.

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import os
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from fromversionzero.themes import THEMES

logger = logging.getLogger(__name__)

#: Version of the dataset/config contract. Bump when a breaking change is made
#: to ``config.json`` or ``data.csv`` semantics.
SCHEMA_VERSION: int = 1

#: Where the schema of this contract version is published (the file in ``schema/``).
SCHEMA_ID: str = (
    "https://raw.githubusercontent.com/ninja7v/fromversionzero/main/schema/config.schema.json"
)

RESOLUTIONS: dict[str, tuple[int, int]] = {
    "240p": (426, 240),
    "360p": (640, 360),
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
    "8k": (7680, 4320),
}


#: Smallest and largest side accepted for ``WIDTHxHEIGHT`` (8K is 7680 wide).
#: The drawing canvas is at least ``ssaa_factor`` times larger, so an unbounded
#: size would exhaust memory before anything is drawn.
MIN_SIDE, MAX_SIDE = 64, 8192


def parse_resolution(value: str) -> tuple[int, int]:
    """Resolves a named resolution or a ``WIDTHxHEIGHT`` string."""
    name = value.strip().lower()
    if name in RESOLUTIONS:
        return RESOLUTIONS[name]
    if "x" in name:
        try:
            w_str, h_str = name.split("x", 1)
            width, height = int(w_str), int(h_str)
        except ValueError:
            pass
        else:
            if width > 0 and height > 0:
                if width % 2 or height % 2:
                    raise ValueError(
                        f"Resolution '{value}' must use even dimensions (H.264 yuv420p)."
                    )
                if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
                    raise ValueError(
                        f"Resolution '{value}' is out of range: each side must be "
                        f"{MIN_SIDE}-{MAX_SIDE} pixels."
                    )
                return width, height
    raise ValueError(f"Invalid resolution '{value}'. Use a named resolution or WIDTHxHEIGHT.")


def _check_hex(value: str) -> str:
    clean = value.removeprefix("#")
    if len(clean) not in (3, 6) or any(c not in "0123456789abcdefABCDEF" for c in clean):
        raise ValueError(f"'{value}' is not a 3- or 6-digit hexadecimal color")
    return value


def _check_landscape(value: str) -> str:
    width, height = parse_resolution(value)
    if height > width:
        raise ValueError(
            f"'{value}' is taller than wide. The landscape layout needs a landscape "
            "resolution; for a portrait video use Shorts mode (--shorts) and "
            "'shorts_overrides.resolution'."
        )
    return value.strip().lower()


def _check_portrait(value: str) -> str:
    width, height = parse_resolution(value)
    if width > height:
        raise ValueError(
            f"'{value}' is wider than tall. The Shorts layout needs a portrait "
            "resolution such as 1080x1920."
        )
    return value.strip().lower()


HexColor = Annotated[str, AfterValidator(_check_hex)]
LandscapeResolution = Annotated[str, AfterValidator(_check_landscape)]
PortraitResolution = Annotated[str, AfterValidator(_check_portrait)]
NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]


class _Section(BaseModel):
    """Base for every config object: strict types, unknown keys rejected."""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class ColorOverrides(_Section):
    background: HexColor | None = None
    grid: HexColor | None = None
    grid_dots: HexColor | None = None
    line: HexColor | None = None
    line_glow: HexColor | None = None
    text_primary: HexColor | None = None
    text_secondary: HexColor | None = None
    accent: HexColor | None = None
    accent_glow: HexColor | None = None
    line_colors: list[HexColor] | None = Field(default=None, min_length=1)


class FontOverrides(_Section):
    display: str | None = None
    heading: str | None = None
    body: str | None = None
    label: str | None = None


class CameraSettings(_Section):
    speed: Positive = 2.5


class MusicSettings(_Section):
    file: str = "soundtrack.mp3"
    volume: Annotated[float, Field(ge=0, le=1)] = 0.7
    fade_in: NonNegative = 2.0
    fade_out: NonNegative = 3.0
    #: Repeat a soundtrack shorter than the video (False: silence after it ends).
    loop: bool = True


class ShortsOverrides(_Section):
    intro_duration: NonNegative = 0.0
    outro_duration: NonNegative = 0.0
    reveal_duration: Positive = 2.0
    steady_duration: NonNegative = 1.0
    hold_duration: NonNegative = 3.0
    speculation_duration: NonNegative = 5.0
    resolution: PortraitResolution = "1080x1920"


class EncodingSettings(_Section):
    codec: Literal["h264", "h265"] = "h264"
    crf: Annotated[int, Field(ge=0, le=51)] = 18
    preset: Literal[
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
        "slower",
        "veryslow",
    ] = "medium"


class MediaSettings(_Section):
    """Logo and generation images referenced by URL (see ``fromversionzero.media``)."""

    #: URL (http/https) of the source logo, or a path relative to the dataset.
    logo: str | None = None
    #: Number of tone bands the logo is reduced to (3 suits logos, 2 photos).
    logo_levels: Annotated[int, Field(ge=2, le=5)] = 3
    logo_invert: bool = False
    #: Tone bands for generation images given as URLs in ``data.csv``.
    image_levels: Annotated[int, Field(ge=2, le=5)] = 2
    image_invert: bool = False
    #: Redraw remote media as FromVersionZero line art (False shows it unchanged).
    stylize: bool = True
    #: Line colour of the line art: "source" keeps the media's own colours.
    line_color: Annotated[str, Field(pattern=r"^(source|#[0-9A-Fa-f]{6})$")] = "source"

    @property
    def line(self) -> str | None:
        """Fixed line colour for :func:`fromversionzero.marks.stylize`, or None for the source's."""
        return None if self.line_color == "source" else self.line_color


class Labels(_Section):
    """On-screen copy. Override to localize or rebrand a video."""

    intro_kicker: str = "From Version Zero  /  Evolution dossier"
    facts: str = "Insight"
    speculation_kicker: str = "Open question  /  Future signal"
    speculation_prompt: str = "Drop your prediction  ↓"
    speculation_footer: str = "Imagine it  •  Argue it  •  Build it"
    outro_kicker: str = "Evolution complete"
    outro_question: str = "What should we trace next?"
    outro_prompt: str = "Drop your pick in the comments  ↓"


class ConfigFile(_Section):
    """Schema shared by ``video.json`` (project) and ``config.json`` (product).

    Every key is optional; missing keys fall back to the defaults below. The
    JSON Schema exported from this model (``fromversionzero schema``) is the
    contract between this generator and dataset repositories.
    """

    json_schema: str | None = Field(default=None, alias="$schema")
    schema_version: Annotated[int, Field(ge=1, le=SCHEMA_VERSION)] = SCHEMA_VERSION

    # --- Content ---
    title: str = "Product Evolution"
    motto: str = ""
    x_metric: str = "date"
    metrics: list[str] | None = Field(default=None, min_length=1)
    y_scale: Literal["auto", "log", "linear"] = "auto"
    speculation_text: str = "What will the next generation be?"
    labels: Labels = Labels()

    # --- Video ---
    fps: Annotated[int, Field(gt=0, le=240)] = 30
    resolution: LandscapeResolution = "1080p"
    ssaa_factor: Annotated[int, Field(ge=1, le=4)] = 2
    downsample_filter: Literal["bicubic", "lanczos", "bilinear", "nearest"] = "bicubic"
    image_transition: Literal["fade", "small_to_big_to_medium", "none"] = "fade"
    encoding: EncodingSettings = EncodingSettings()

    # --- Style ---
    theme: str = Field(default="signal", json_schema_extra={"enum": [*sorted(THEMES)]})
    colors: ColorOverrides = ColorOverrides()
    fonts: FontOverrides = FontOverrides()

    # --- Timing (seconds) ---
    intro_duration: NonNegative = 3.0
    reveal_duration: Positive = 4.0
    steady_duration: NonNegative = 2.0
    hold_duration: NonNegative = 5.0
    outro_duration: NonNegative = 3.0
    speculation_duration: NonNegative = 9.0
    fact_card_duration: NonNegative = 4.0
    camera: CameraSettings = CameraSettings()
    shorts_overrides: ShortsOverrides = ShortsOverrides()

    # --- Media ---
    media: MediaSettings = MediaSettings()

    # --- Audio ---
    music: MusicSettings = MusicSettings()
    mix_sfx: bool = True

    # --- Output and visibility ---
    product_name: str = ""
    output_folder: str = ""
    shorts: bool = False
    speculation: bool = True
    show_motto: bool = True
    show_logo: bool = True
    show_legend: bool = True
    show_facts: bool = True
    show_progress_bar: bool = True

    @field_validator("theme")
    @classmethod
    def _known_theme(cls, value: str) -> str:
        name = value.lower()
        if name not in THEMES:
            raise ValueError(f"Invalid theme '{value}'. Expected one of: {sorted(THEMES)}")
        return name


def config_json_schema() -> dict[str, Any]:
    """JSON Schema for ``video.json`` / ``config.json`` files."""
    schema = ConfigFile.model_json_schema(by_alias=True, mode="validation")
    schema["$id"] = SCHEMA_ID
    schema["title"] = "FromVersionZero configuration"
    return schema


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _read_json_object(path: str, label: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Failed to load {label} '{path}': {e}") from e
    if not isinstance(data, dict):
        # ValueError (not TypeError): every config problem surfaces as one error type.
        raise ValueError(f"Failed to load {label} '{path}': root must be a JSON object")  # noqa: TRY004
    return data


def _format_validation_error(error: ValidationError, source: str) -> str:
    lines = [f"Invalid configuration ({source}):"]
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "<root>"
        lines.append(f"  - {location}: {item['msg']}")
    return "\n".join(lines)


def validate_config_dict(data: dict[str, Any], source: str = "config") -> ConfigFile:
    """Validates one raw config layer, raising ``ValueError`` with readable messages."""
    try:
        return ConfigFile.model_validate(data)
    except ValidationError as e:
        raise ValueError(_format_validation_error(e, source)) from None


@dataclasses.dataclass(frozen=True)
class VideoConfig:
    """Fully-resolved, immutable render settings.

    Build with :meth:`load` (files) or :meth:`from_dict` (in-memory). Use
    :func:`dataclasses.replace` to derive variants and :meth:`for_output_mode`
    to apply the Shorts overrides.
    """

    RESOLUTIONS: ClassVar[dict[str, tuple[int, int]]] = RESOLUTIONS
    THEMES: ClassVar[dict[str, dict[str, Any]]] = THEMES

    title: str
    motto: str
    x_metric: str
    metrics: tuple[str, ...] | None
    y_scale: str
    speculation_text: str
    labels: Labels
    fps: int
    resolution_name: str
    width: int
    height: int
    ssaa_factor: int
    downsample_filter: str
    image_transition: str
    encoding: EncodingSettings
    theme: str
    colors: dict[str, Any]
    fonts: dict[str, str]
    intro_duration: float
    reveal_duration: float
    steady_duration: float
    hold_duration: float
    outro_duration: float
    speculation_duration: float
    fact_card_duration: float
    camera_speed: float
    shorts_intro_duration: float
    shorts_outro_duration: float
    shorts_reveal_duration: float
    shorts_steady_duration: float
    shorts_hold_duration: float
    shorts_speculation_duration: float
    shorts_resolution: tuple[int, int]
    music_file: str
    music_volume: float
    music_fade_in: float
    music_fade_out: float
    music_loop: bool
    mix_sfx: bool
    product_name: str
    output_folder: str
    shorts: bool
    speculation: bool
    show_motto: bool
    show_logo: bool
    show_legend: bool
    show_facts: bool
    show_progress_bar: bool
    media: MediaSettings = dataclasses.field(default_factory=MediaSettings)
    #: True once :meth:`for_output_mode` has applied Shorts overrides.
    mode_applied: bool = False

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def load(
        cls,
        general_config_path: str | os.PathLike[str] | None = None,
        product_config_path: str | os.PathLike[str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> VideoConfig:
        """Loads layered configuration: defaults < video.json < config.json < overrides.

        Nested objects (``camera``, ``music``, ``shorts_overrides``...) are
        deep-merged, so a product can override a single nested key.
        """
        merged: dict[str, Any] = {}
        sources: list[str] = []
        for path, label in (
            (general_config_path, "global config"),
            (product_config_path, "product config"),
        ):
            if not path:
                continue
            layer = _read_json_object(os.fspath(path), label)
            validate_config_dict(layer, os.fspath(path))
            merged = _deep_merge(merged, layer)
            sources.append(os.fspath(path))
        if overrides:
            validate_config_dict(overrides, "overrides")
            merged = _deep_merge(merged, overrides)
            sources.append("overrides")
        return cls.from_dict(merged, source=" + ".join(sources) or "defaults")

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: str = "config") -> VideoConfig:
        """Builds a resolved config from a single raw dictionary."""
        settings = validate_config_dict(data, source)
        return cls._from_settings(settings)

    @classmethod
    def _from_settings(cls, s: ConfigFile) -> VideoConfig:
        theme: dict[str, Any] = THEMES[s.theme]
        color_overrides = s.colors.model_dump(exclude_none=True)
        colors: dict[str, Any] = {
            key: color_overrides.get(key, theme[key])
            for key in (
                "background",
                "grid",
                "grid_dots",
                "line",
                "line_glow",
                "text_primary",
                "text_secondary",
                "accent",
                "accent_glow",
                "line_colors",
            )
        }
        colors["line_colors"] = list(colors["line_colors"])
        font_overrides = s.fonts.model_dump(exclude_none=True)
        fonts = {role: font_overrides.get(role, theme["fonts"][role]) for role in theme["fonts"]}
        width, height = parse_resolution(s.resolution)
        return cls(
            title=s.title,
            motto=s.motto,
            x_metric=s.x_metric,
            metrics=tuple(s.metrics) if s.metrics else None,
            y_scale=s.y_scale,
            speculation_text=s.speculation_text,
            labels=s.labels,
            fps=s.fps,
            resolution_name=s.resolution,
            width=width,
            height=height,
            ssaa_factor=s.ssaa_factor,
            downsample_filter=s.downsample_filter,
            image_transition=s.image_transition,
            encoding=s.encoding,
            theme=s.theme,
            colors=colors,
            fonts=fonts,
            intro_duration=s.intro_duration,
            reveal_duration=s.reveal_duration,
            steady_duration=s.steady_duration,
            hold_duration=s.hold_duration,
            outro_duration=s.outro_duration,
            speculation_duration=s.speculation_duration,
            fact_card_duration=s.fact_card_duration,
            camera_speed=s.camera.speed,
            shorts_intro_duration=s.shorts_overrides.intro_duration,
            shorts_outro_duration=s.shorts_overrides.outro_duration,
            shorts_reveal_duration=s.shorts_overrides.reveal_duration,
            shorts_steady_duration=s.shorts_overrides.steady_duration,
            shorts_hold_duration=s.shorts_overrides.hold_duration,
            shorts_speculation_duration=s.shorts_overrides.speculation_duration,
            shorts_resolution=parse_resolution(s.shorts_overrides.resolution),
            music_file=s.music.file,
            music_volume=s.music.volume,
            music_fade_in=s.music.fade_in,
            music_fade_out=s.music.fade_out,
            music_loop=s.music.loop,
            mix_sfx=s.mix_sfx,
            product_name=s.product_name,
            output_folder=s.output_folder,
            shorts=s.shorts,
            speculation=s.speculation,
            show_motto=s.show_motto,
            show_logo=s.show_logo,
            show_legend=s.show_legend,
            show_facts=s.show_facts,
            show_progress_bar=s.show_progress_bar,
            media=s.media,
        )

    # ------------------------------------------------------------------
    # Derived variants
    # ------------------------------------------------------------------
    def replace(self, **changes: Any) -> VideoConfig:
        """Returns a copy with ``changes`` applied (the original is untouched)."""
        return dataclasses.replace(self, **changes)

    def for_output_mode(self) -> VideoConfig:
        """Returns the config with Shorts overrides applied when ``shorts`` is set.

        Idempotent: calling it on an already-resolved config is a no-op.
        """
        if not self.shorts or self.mode_applied:
            return self
        width, height = self.shorts_resolution
        return self.replace(
            intro_duration=self.shorts_intro_duration,
            outro_duration=self.shorts_outro_duration,
            reveal_duration=self.shorts_reveal_duration,
            steady_duration=self.shorts_steady_duration,
            hold_duration=self.shorts_hold_duration,
            speculation_duration=self.shorts_speculation_duration,
            width=width,
            height=height,
            mode_applied=True,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serializes back to the config-file shape (round-trips through :meth:`from_dict`)."""
        return {
            "title": self.title,
            "motto": self.motto,
            "x_metric": self.x_metric,
            **({"metrics": list(self.metrics)} if self.metrics else {}),
            "y_scale": self.y_scale,
            "speculation_text": self.speculation_text,
            "labels": self.labels.model_dump(),
            "fps": self.fps,
            "resolution": self.resolution_name,
            "ssaa_factor": self.ssaa_factor,
            "downsample_filter": self.downsample_filter,
            "image_transition": self.image_transition,
            "encoding": self.encoding.model_dump(),
            "theme": self.theme,
            "colors": copy.deepcopy(self.colors),
            "fonts": dict(self.fonts),
            "intro_duration": self.intro_duration,
            "reveal_duration": self.reveal_duration,
            "steady_duration": self.steady_duration,
            "hold_duration": self.hold_duration,
            "outro_duration": self.outro_duration,
            "speculation_duration": self.speculation_duration,
            "fact_card_duration": self.fact_card_duration,
            "camera": {"speed": self.camera_speed},
            "shorts_overrides": {
                "intro_duration": self.shorts_intro_duration,
                "outro_duration": self.shorts_outro_duration,
                "reveal_duration": self.shorts_reveal_duration,
                "steady_duration": self.shorts_steady_duration,
                "hold_duration": self.shorts_hold_duration,
                "speculation_duration": self.shorts_speculation_duration,
                "resolution": f"{self.shorts_resolution[0]}x{self.shorts_resolution[1]}",
            },
            "music": {
                "file": self.music_file,
                "volume": self.music_volume,
                "fade_in": self.music_fade_in,
                "fade_out": self.music_fade_out,
                "loop": self.music_loop,
            },
            "mix_sfx": self.mix_sfx,
            "product_name": self.product_name,
            "output_folder": self.output_folder,
            "shorts": self.shorts,
            "speculation": self.speculation,
            "show_motto": self.show_motto,
            "show_logo": self.show_logo,
            "show_legend": self.show_legend,
            "show_facts": self.show_facts,
            "show_progress_bar": self.show_progress_bar,
            "media": self.media.model_dump(),
        }
