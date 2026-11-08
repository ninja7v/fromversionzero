# tests/test_examples.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Validates the example dataset shipped in examples/. The full collection lives
# in the fromversionzero-data repository, whose CI validates it.

from pathlib import Path

import pytest
from conftest import ROOT

from fromversionzero.cli import _validate_one, iter_datasets

DATA_ROOT = ROOT / "examples"
DATASETS = iter_datasets(DATA_ROOT)

pytestmark = pytest.mark.datasets


def test_examples_are_found() -> None:
    assert DATASETS


@pytest.mark.parametrize("dataset_dir", DATASETS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_example_dataset_is_valid(dataset_dir: Path) -> None:
    error, warnings = _validate_one(dataset_dir, ROOT / "video.json")
    assert error is None, error
    assert not warnings, warnings
