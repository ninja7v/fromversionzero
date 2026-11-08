# tests/test_config.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Configuration schema, layering, validation and derived variants.

import dataclasses
import json
from pathlib import Path

import pytest

from fromversionzero.config import (
    SCHEMA_VERSION,
    VideoConfig,
    config_json_schema,
    parse_resolution,
)
from fromversionzero.themes import THEMES


def write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_defaults() -> None:
    config = VideoConfig.from_dict({})
    assert (config.width, config.height) == (1920, 1080)
    assert config.fps == 30
    assert config.theme == "signal"
    assert config.colors["background"] == THEMES["signal"]["background"]
    assert config.fonts["display"] == "Montserrat-Light"
    assert config.y_scale == "auto"
    assert config.encoding.codec == "h264" and config.encoding.crf == 18
    assert config.speculation and config.show_motto and not config.shorts
    assert config.labels.facts == "Insight"


def test_config_is_immutable() -> None:
    config = VideoConfig.from_dict({})
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.fps = 60  # type: ignore[misc]
    assert config.replace(fps=60).fps == 60
    assert config.fps == 30


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"show_lgo": True}, "show_lgo: Extra inputs are not permitted"),
        ({"camera": {"sped": 2}}, "camera.sped: Extra inputs"),
        ({"fps": True}, "fps"),
        ({"fps": 2.5}, "fps"),
        ({"fps": 0}, "fps"),
        ({"intro_duration": "3"}, "intro_duration"),
        ({"reveal_duration": 0}, "reveal_duration"),
        ({"hold_duration": -1}, "hold_duration"),
        ({"camera": {"speed": 0}}, "camera.speed"),
        ({"music": {"volume": 1.5}}, "music.volume"),
        ({"ssaa_factor": 0}, "ssaa_factor"),
        ({"downsample_filter": "box"}, "downsample_filter"),
        ({"image_transition": "spin"}, "image_transition"),
        ({"colors": {"accent": "orange"}}, "colors.accent"),
        ({"colors": {"line_colors": []}}, "colors.line_colors"),
        ({"theme": "nope"}, "Invalid theme"),
        ({"resolution": "huge"}, "Invalid resolution"),
        ({"resolution": "801x600"}, "even dimensions"),
        ({"encoding": {"crf": 60}}, "encoding.crf"),
        ({"schema_version": SCHEMA_VERSION + 1}, "schema_version"),
    ],
)
def test_invalid_settings_are_rejected(settings: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        VideoConfig.from_dict(settings)


def test_layering_deep_merges_nested_objects(tmp_path: Path) -> None:
    general = write(
        tmp_path / "video.json",
        {
            "fps": 24,
            "music": {"volume": 0.4, "fade_in": 1.0},
            "shorts_overrides": {"hold_duration": 9},
        },
    )
    product = write(tmp_path / "config.json", {"title": "X", "music": {"volume": 0.9}})
    config = VideoConfig.load(general, product, {"fps": 60})
    assert config.fps == 60  # CLI overrides win
    assert config.title == "X"
    assert config.music_volume == 0.9  # product wins...
    assert config.music_fade_in == 1.0  # ...without wiping sibling keys
    assert config.shorts_hold_duration == 9


def test_product_overrides_formerly_global_only_keys(tmp_path: Path) -> None:
    """mix_sfx and fact_card_duration used to be read from video.json only."""
    general = write(tmp_path / "video.json", {"mix_sfx": True, "fact_card_duration": 4})
    product = write(tmp_path / "config.json", {"mix_sfx": False, "fact_card_duration": 2.5})
    config = VideoConfig.load(general, product)
    assert config.mix_sfx is False
    assert config.fact_card_duration == 2.5


def test_errors_name_the_offending_file(tmp_path: Path) -> None:
    product = write(tmp_path / "config.json", {"fps": "fast"})
    with pytest.raises(ValueError, match="config.json"):
        VideoConfig.load(None, product)
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError, match="Failed to load"):
        VideoConfig.load(broken)
    array = write(tmp_path / "array.json", [])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="JSON object"):
        VideoConfig.load(array)


def test_deprecated_keys_and_themes_still_load(caplog: pytest.LogCaptureFixture) -> None:
    config = VideoConfig.from_dict({"moto": "Old", "show_moto": False, "theme": "light_modern"})
    assert config.motto == "Old"
    assert config.show_motto is False
    assert config.theme == "paper"
    assert "deprecated" in caplog.text and "retired" in caplog.text


def test_theme_overrides() -> None:
    config = VideoConfig.from_dict(
        {
            "theme": "blueprint",
            "colors": {"accent": "#123456", "line_colors": ["#fff"]},
            "fonts": {"heading": "Montserrat-Black"},
        }
    )
    assert config.colors["background"] == THEMES["blueprint"]["background"]
    assert config.colors["accent"] == "#123456"
    assert config.colors["line_colors"] == ["#fff"]
    assert config.fonts["heading"] == "Montserrat-Black"
    assert config.fonts["body"] == THEMES["blueprint"]["fonts"]["body"]


def test_every_theme_has_eight_distinct_curve_colors() -> None:
    for name, theme in THEMES.items():
        colors = [c.lower() for c in theme["line_colors"]]
        assert len(colors) == 8 and len(set(colors)) == 8, name


def test_for_output_mode_applies_shorts_overrides_once() -> None:
    config = VideoConfig.from_dict(
        {"shorts": True, "reveal_duration": 4, "shorts_overrides": {"reveal_duration": 1.5}}
    )
    shorts = config.for_output_mode()
    assert (shorts.width, shorts.height) == (1080, 1920)
    assert shorts.reveal_duration == 1.5
    assert shorts.for_output_mode() is shorts
    assert config.reveal_duration == 4  # original untouched
    landscape = VideoConfig.from_dict({})
    assert landscape.for_output_mode() is landscape


def test_to_dict_round_trips() -> None:
    config = VideoConfig.from_dict(
        {
            "title": "T",
            "metrics": ["power"],
            "fps": 25,
            "shorts_overrides": {"resolution": "720x1280"},
        }
    )
    assert VideoConfig.from_dict(config.to_dict()) == config


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4k", (3840, 2160)),
        ("480P", (854, 480)),
        ("800x600", (800, 600)),
        (" 720x1280 ", (720, 1280)),
    ],
)
def test_parse_resolution(text: str, expected: tuple[int, int]) -> None:
    assert parse_resolution(text) == expected


def test_json_schema_documents_every_key() -> None:
    schema = config_json_schema()
    props = schema["properties"]
    for key in ("title", "motto", "metrics", "y_scale", "theme", "encoding", "labels", "$schema"):
        assert key in props
    assert schema["additionalProperties"] is False


def test_committed_schema_is_up_to_date() -> None:
    committed = Path(__file__).resolve().parents[1] / "schema" / "config.schema.json"
    assert json.loads(committed.read_text(encoding="utf-8")) == config_json_schema(), (
        "Run: fromversionzero schema -o schema/config.schema.json"
    )
