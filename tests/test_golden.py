# tests/test_golden.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Visual regression: key frames of the Widget fixture compared to reviewed PNGs.
#
# After an intentional visual change, regenerate and review the images:
#     FVZ_UPDATE_GOLDEN=1 pytest tests/test_golden.py
# then inspect `git diff --stat tests/golden` and the PNGs before committing.

import os
from pathlib import Path

import numpy as np
import pytest
from conftest import WIDGET_DIR, make_config
from PIL import Image

from fromversionzero.data_loader import Dataset
from fromversionzero.renderer import Renderer
from fromversionzero.timeline import TimelinePlanner

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("FVZ_UPDATE_GOLDEN") == "1"

#: Mean absolute difference (0-255). Pillow wheels bundle FreeType, so output is
#: reproducible for a locked Pillow version; tolerances only absorb tiny noise.
MAX_MEAN_DIFF = 0.1
#: Fraction of pixels allowed to differ by more than 48 levels (~40 px at 480p).
MAX_CHANGED_FRACTION = 0.0001

# (name, config overrides, time in seconds)
CASES = [
    ("landscape_intro", {}, 0.30),
    ("landscape_reveal", {}, 1.70),
    ("landscape_hold", {}, 3.60),
    ("landscape_speculation_card", {}, 4.25),
    ("landscape_speculation_reveal", {}, 4.90),
    ("landscape_outro", {}, 5.30),
    ("landscape_paper", {"theme": "paper"}, 2.60),
    ("landscape_blueprint_linear", {"theme": "blueprint", "y_scale": "linear"}, 2.60),
    ("portrait_reveal", {"shorts": True, "shorts_overrides": {"resolution": "270x480"}}, 1.40),
]


def render_case(overrides: dict, t: float) -> np.ndarray:
    config = make_config(fps=10, resolution="480p", ssaa_factor=1, **overrides).for_output_mode()
    dataset = Dataset.load(str(WIDGET_DIR), "date", y_scale=config.y_scale)
    frames = TimelinePlanner(config, dataset, str(WIDGET_DIR)).plan()
    state = frames[min(len(frames) - 1, round(t * config.fps))]
    return Renderer(config, dataset, str(WIDGET_DIR)).render(state)


@pytest.mark.parametrize(("name", "overrides", "t"), CASES, ids=[c[0] for c in CASES])
def test_golden_frame(name: str, overrides: dict, t: float) -> None:
    frame = render_case(overrides, t)
    path = GOLDEN_DIR / f"{name}.png"
    if UPDATE or not path.exists():
        if not UPDATE:
            pytest.fail(f"Missing golden image {path.name}; run with FVZ_UPDATE_GOLDEN=1.")
        Image.fromarray(frame).save(path, optimize=True)
        return
    expected = np.asarray(Image.open(path).convert("RGB")).astype(np.int16)
    assert expected.shape == frame.shape, f"{name}: size changed"
    diff = np.abs(frame.astype(np.int16) - expected)
    mean = float(diff.mean())
    changed = float((diff.max(axis=2) > 48).mean())
    if mean > MAX_MEAN_DIFF or changed > MAX_CHANGED_FRACTION:
        actual = GOLDEN_DIR / f"{name}.actual.png"
        Image.fromarray(frame).save(actual)
        pytest.fail(
            f"{name} differs from golden (mean {mean:.2f}, changed {changed:.2%}). "
            f"Wrote {actual.name}; if intended, rerun with FVZ_UPDATE_GOLDEN=1."
        )
