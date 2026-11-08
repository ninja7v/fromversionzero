# tests/test_data_loader.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Tests CSV parsing, metric resolution and chronological sorting of product data points.

import os
import tempfile

import pytest

from fromversionzero.data_loader import DataLoader, ProductDataPoint


def test_data_loader_success() -> None:
    # Create temp directory
    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path: str = os.path.join(tmp_dir, "data.csv")

        # Write mock CSV data
        csv_content = (
            "name,date,power,weight,image,fact\n"
            "Model B,2010,200,1200,,Fact B\n"
            "Model A,2000,150,1100,,Fact A\n"
            "Model C,2020,300,1300,,Fact C\n"
        )
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(csv_content)

        # Load dataset and sort by year
        dataset: list[ProductDataPoint] = DataLoader.load_dataset(tmp_dir, x_metric="year")

        assert len(dataset) == 3
        # Verify chronological sorting
        assert dataset[0].name == "Model A"
        assert dataset[0].get_metric("year") == 2000.0
        assert dataset[0].get_metric("power") == 150.0
        assert dataset[0].fact == "Fact A"

        assert dataset[1].name == "Model B"
        assert dataset[1].get_metric("year") == 2010.0

        assert dataset[2].name == "Model C"
        assert dataset[2].get_metric("year") == 2020.0
        assert dataset[2].get_metric("power") == 300.0


def test_data_loader_missing_metric() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path: str = os.path.join(tmp_dir, "data.csv")

        csv_content = "name,date,power\nModel A,2000,150\n"
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(csv_content)

        # Expect error when X-metric is missing from schema
        with pytest.raises(ValueError, match="X-metric 'missing' not found"):
            DataLoader.load_dataset(tmp_dir, x_metric="missing")


def test_data_loader_invalid_sorting() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path: str = os.path.join(tmp_dir, "data.csv")

        csv_content = "name,date,power\nModel A,invalid_year,150\n"
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(csv_content)

        # Expect error since sorting requires numeric values in X column
        with pytest.raises(ValueError, match="non-numeric X-metric"):
            DataLoader.load_dataset(tmp_dir, x_metric="year")


def test_data_loader_fuzzy_metric_mapping() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path: str = os.path.join(tmp_dir, "data.csv")

        csv_content = "name,date,power,weight,image,fact\nModel A,2000,150,1100,,Fact A\n"
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(csv_content)

        dataset: list[ProductDataPoint] = DataLoader.load_dataset(tmp_dir, x_metric="year")
        assert len(dataset) == 1
        assert dataset[0].get_metric("power") == 150.0
        assert dataset[0].get_metric("weight") == 1100.0


def test_data_loader_rejects_missing_image(tmp_path) -> None:
    (tmp_path / "data.csv").write_text(
        "name,year,power,image\nV1,2020,100,missing.png\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="references missing image"):
        DataLoader.load_dataset(str(tmp_path), x_metric="year")


def test_data_loader_rejects_dataset_without_y_metric(tmp_path) -> None:
    (tmp_path / "data.csv").write_text("name,year\nV1,2020\n", encoding="utf-8")
    with pytest.raises(ValueError, match="numeric metric"):
        DataLoader.load_dataset(str(tmp_path), x_metric="year")


def test_data_loader_rejects_all_speculation_dataset(tmp_path) -> None:
    (tmp_path / "data.csv").write_text(
        "name,year,power,speculation\nV1,2020,100,true\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="non-speculation"):
        DataLoader.load_dataset(str(tmp_path), x_metric="year")


def test_data_loader_rejects_mixed_numeric_column(tmp_path) -> None:
    (tmp_path / "data.csv").write_text(
        "name,year,power\nV1,2020,100\nV2,2021,unknown\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="non-numeric metric"):
        DataLoader.load_dataset(str(tmp_path), x_metric="year")


# --------------------------------------------------------------------------
# Dataset: metric selection and normalization
# --------------------------------------------------------------------------
import math
from pathlib import Path

from fromversionzero.data_loader import Dataset


def _write(tmp_path: Path, body: str) -> str:
    (tmp_path / "data.csv").write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_dataset_orders_points_and_selects_all_numeric_metrics(tmp_path: Path) -> None:
    ds = Dataset.load(
        _write(tmp_path, "name,year,power,label\nB,2010,200,x\nA,2000,100,y\n"), "year"
    )
    assert [p.name for p in ds.points] == ["A", "B"]
    assert [p.order for p in ds.points] == [0, 1]
    assert ds.y_metrics == ["power"]


def test_dataset_metric_selection_is_fuzzy_and_ordered(tmp_path: Path) -> None:
    path = _write(tmp_path, "name,year,power_hp,weight_kg,price\nA,2000,1,2,3\nB,2001,2,3,4\n")
    ds = Dataset.load(path, "year", metrics=["price", "power"])
    assert ds.y_metrics == ["price", "power_hp"]
    with pytest.raises(ValueError, match="Metric 'torque'"):
        Dataset.load(path, "year", metrics=["torque"])


def test_log_scale_is_relative_to_first_generation(tmp_path: Path) -> None:
    ds = Dataset.load(_write(tmp_path, "name,year,p\nA,2000,100\nB,2001,200\nC,2002,50\n"), "year")
    assert ds.y_scale == "log"
    ys = [ds.y(p, "p") for p in ds.points]
    assert ys == pytest.approx([0.0, math.log10(2), math.log10(0.5)])
    assert ds.y_to_ratio(ds.ratio_to_y(2.0)) == pytest.approx(2.0)


def test_linear_scale_keeps_plain_ratios(tmp_path: Path) -> None:
    path = _write(tmp_path, "name,year,p,w\nA,2000,100,1500\nB,2001,150,1400\n")
    ds = Dataset.load(path, "year", y_scale="linear")
    assert [ds.y(p, "p") for p in ds.points] == pytest.approx([1.0, 1.5])
    assert [ds.y(p, "w") for p in ds.points] == pytest.approx([1.0, 1400 / 1500])


def test_auto_scale_falls_back_to_linear_on_zero_values(tmp_path: Path) -> None:
    path = _write(tmp_path, "name,year,price\nA,2000,0\nB,2001,20\n")
    ds = Dataset.load(path, "year")
    assert ds.y_scale == "linear"
    assert ds.warnings and "Linear Y axis" in ds.warnings[0]
    forced = Dataset.load(path, "year", y_scale="log")
    assert forced.y_scale == "log" and "clamped" in forced.warnings[0]


def test_speculation_rows_can_be_excluded(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "name,year,p,speculation\nA,2000,1,false\nB,2001,2,false\nC,2002,3,true\n"
    )
    assert len(Dataset.load(path, "year")) == 3
    ds = Dataset.load(path, "year", include_speculation=False)
    assert [p.name for p in ds.points] == ["A", "B"]
    assert ds.speculative == [] and len(ds.released) == 2


def test_metric_may_start_after_the_first_generation(tmp_path: Path) -> None:
    path = _write(tmp_path, "name,year,p,q\nA,2000,1,\nB,2001,2,10\nC,2002,4,20\n")
    ds = Dataset.load(path, "year")
    assert ds.y_metrics == ["p", "q"]
    assert ds.start == {"p": 0, "q": 1}
    assert [ds.has(p, "q") for p in ds.points] == [False, True, True]
    # Ratios are relative to the metric's own first value; earlier points repeat it.
    assert [ds.y(p, "q") for p in ds.points] == pytest.approx([0.0, 0.0, math.log10(2)])


def test_blank_between_values_is_interpolated_along_x(tmp_path: Path) -> None:
    path = _write(tmp_path, "name,year,p,q\nA,2000,1,10\nB,2001,2,\nC,2004,4,50\n")
    ds = Dataset.load(path, "year")
    assert ds.y_metrics == ["p", "q"]
    _a, b, c = ds.points
    assert ds.value(b, "q") == pytest.approx(20.0)  # one quarter of the way from 2000 to 2004
    assert ds.is_interpolated(b, "q") and not ds.is_interpolated(c, "q")
    assert ds.y(b, "q") == pytest.approx(math.log10(2))


def test_metric_needs_a_value_on_the_last_row(tmp_path: Path) -> None:
    path = _write(tmp_path, "name,year,p,q\nA,2000,1,5\nB,2001,2,6\nC,2002,4,\n")
    assert Dataset.load(path, "year").y_metrics == ["p"]
    with pytest.raises(ValueError, match="last row"):
        Dataset.load(path, "year", metrics=["q"])


def test_metric_starting_only_on_speculation_is_skipped(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "name,year,p,q,speculation\nA,2000,1,,false\nB,2001,2,,false\nC,2002,3,9,true\n"
    )
    assert Dataset.load(path, "year").y_metrics == ["p"]


def test_late_metric_zero_check_ignores_blank_cells(tmp_path: Path) -> None:
    path = _write(tmp_path, "name,year,p,q\nA,2000,1,\nB,2001,2,10\n")
    assert Dataset.load(path, "year").y_scale == "log"


def test_row_with_every_metric_blank_is_interpolated(tmp_path: Path) -> None:
    path = _write(tmp_path, "name,year,p,q\nA,2000,1,10\nB,2001,,\nC,2002,4,30\n")
    ds = Dataset.load(path, "year")
    b = ds.points[1]
    assert ds.value(b, "p") == pytest.approx(2.5) and ds.value(b, "q") == pytest.approx(20.0)
