# tests/conftest.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Shared fixtures: the synthetic Widget dataset and fast config builders.

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from fromversionzero.config import VideoConfig
from fromversionzero.data_loader import Dataset

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DATA = Path(__file__).resolve().parent / "fixtures" / "data"
WIDGET_DIR = FIXTURE_DATA / "Gadgets" / "Widget"

FAST: dict[str, Any] = {
    "fps": 4,
    "resolution": "240p",
    "ssaa_factor": 1,
    "intro_duration": 0.5,
    "reveal_duration": 0.5,
    "steady_duration": 0.25,
    "hold_duration": 0.5,
    "speculation_duration": 1.0,
    "outro_duration": 0.5,
    "mix_sfx": False,
}


def make_config(**overrides: Any) -> VideoConfig:
    """Fast, deterministic config (product config of the Widget fixture + overrides)."""
    return VideoConfig.load(None, WIDGET_DIR / "config.json", {**FAST, **overrides})


@pytest.fixture
def widget_dir(tmp_path: Path) -> Path:
    """A writable copy of the Widget fixture inside its category folder."""
    target = tmp_path / "data" / "Gadgets"
    shutil.copytree(FIXTURE_DATA / "Gadgets", target)
    return target / "Widget"


@pytest.fixture
def widget_dataset() -> Dataset:
    return Dataset.load(str(WIDGET_DIR), x_metric="date")
