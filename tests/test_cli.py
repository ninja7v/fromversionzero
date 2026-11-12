# tests/test_cli.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Command-line interface: discovery, validation, schema export and rendering.

import json
from pathlib import Path

import pytest
from conftest import FAST, FIXTURE_DATA

from fromversionzero import __version__
from fromversionzero.cli import CliError, find_dataset, main


def run(args: list[str]) -> int:
    with pytest.raises(SystemExit) as exit_info:
        main(args)
    return int(exit_info.value.code or 0)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_find_dataset_by_name_category_and_path(tmp_path: Path) -> None:
    root = FIXTURE_DATA
    expected = (root / "Gadgets" / "Widget").resolve()
    assert find_dataset("Widget", root) == expected
    assert find_dataset("Gadgets/Widget", root) == expected
    assert find_dataset(str(expected), None) == expected
    with pytest.raises(CliError, match="not found"):
        find_dataset("Nope", root)
    with pytest.raises(CliError, match="no data root"):
        find_dataset("Widget", None)


def test_ambiguous_names_are_rejected(tmp_path: Path) -> None:
    for category in ("A", "B"):
        folder = tmp_path / category / "Same"
        folder.mkdir(parents=True)
        (folder / "data.csv").write_text("name,year,p\nx,2000,1\n", encoding="utf-8")
        (folder / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CliError, match="ambiguous"):
        find_dataset("Same", tmp_path)


def test_list_and_validate(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["list", "--data-root", str(FIXTURE_DATA)]) == 0
    assert "Gadgets/Widget" in capsys.readouterr().out
    assert run(["validate", "--data-root", str(FIXTURE_DATA), "--strict"]) == 0
    assert "1/1 datasets valid" in capsys.readouterr().out


def test_validate_reports_broken_datasets(
    widget_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (widget_dir / "config.json").write_text(json.dumps({"show_lgo": True}), encoding="utf-8")
    assert run(["validate", "--data-root", str(widget_dir.parents[1])]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "show_lgo" in out


def test_validate_strict_fails_on_warnings(widget_dir: Path) -> None:
    (widget_dir.parent / "units.json").write_text("{}", encoding="utf-8")
    root = str(widget_dir.parents[1])
    assert run(["validate", "--data-root", root]) == 0
    assert run(["validate", "--data-root", root, "--strict"]) == 1


def test_schema_command(tmp_path: Path) -> None:
    target = tmp_path / "schema.json"
    assert run(["schema", "-o", str(target)]) == 0
    assert (
        json.loads(target.read_text(encoding="utf-8"))["title"] == "FromVersionZero configuration"
    )


def test_render_end_to_end(
    widget_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "video.json"
    config.write_text(json.dumps({**FAST, "speculation_duration": 0.5}), encoding="utf-8")
    monkeypatch.setenv("FVZ_DATA_ROOT", str(widget_dir.parents[1]))
    out = tmp_path / "out"
    code = run(["--product", "Widget", "--config", str(config), "--output-folder", str(out)])
    assert code == 0
    assert (out / "Widget_landscape_240p.mp4").stat().st_size > 0


def test_render_errors_are_reported_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run(["render"]) == 1
    assert "No product specified" in capsys.readouterr().err
    assert run(["--product", "Widget", "--data-root", str(tmp_path / "missing")]) == 1
    assert "Data root is not a directory" in capsys.readouterr().err


def test_dataset_errors_during_render_have_no_traceback(
    widget_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv = (widget_dir / "data.csv").read_text(encoding="utf-8")
    (widget_dir / "data.csv").write_text(csv.replace(",180,", ",abc,"), encoding="utf-8")
    assert run(["render", "--product", str(widget_dir)]) == 1
    err = capsys.readouterr().err
    assert "Error: Row 2" in err and "non-numeric metric" in err
    assert "Traceback" not in err and "--verbose" in err


def test_invalid_project_config_is_reported_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "video.json"
    config.write_text('{"fps": "fast"}', encoding="utf-8")
    assert run(["render", "--config", str(config)]) == 1
    err = capsys.readouterr().err
    assert "Invalid configuration" in err and "fps" in err and "Traceback" not in err


def test_resolution_flag_applies_to_shorts(
    widget_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "video.json"
    fast_shorts = {k: v for k, v in FAST.items() if k.endswith("_duration") and k != "fps"}
    fast_shorts["reveal_duration"] = 0.5
    config.write_text(json.dumps({**FAST, "shorts_overrides": fast_shorts}), encoding="utf-8")
    out = tmp_path / "out"
    args = ["--product", str(widget_dir), "--config", str(config), "--output-folder", str(out)]
    assert run([*args, "--shorts", "--resolution", "180x320", "--workers", "1"]) == 0
    assert (out / "Widget_portrait_180x320.mp4").stat().st_size > 0
    # A portrait size without --shorts is a configuration error, not a broken layout.
    assert run([*args, "--resolution", "180x320"]) == 1


def test_preview_writes_a_single_frame(
    widget_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from PIL import Image

    config = tmp_path / "video.json"
    config.write_text(json.dumps(FAST), encoding="utf-8")
    target = tmp_path / "frames" / "still.png"
    args = ["preview", "--product", str(widget_dir), "--config", str(config), "-o", str(target)]
    assert run(args) == 0
    assert Image.open(target).size == (426, 240)
    assert "Frame at" in capsys.readouterr().out
    assert run([*args, "--shorts", "--resolution", "180x320", "--time", "0.5"]) == 0
    assert Image.open(target).size == (180, 320)


def test_mark_rejects_bad_arguments(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["mark", str(tmp_path / "missing.png"), str(tmp_path / "o.png")]) == 1
    assert "Could not read image" in capsys.readouterr().err
    assert run(["mark", "a.png", "o.png", "--size", "0"]) == 1
    assert run(["mark", "a.png", "o.png", "--levels", "7"]) == 2


def test_schema_and_list_edge_cases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["schema", "-o", str(tmp_path / "new" / "dir" / "schema.json")]) == 0
    assert run(["list", "--data-root", str(tmp_path)]) == 1
    assert "No datasets found" in capsys.readouterr().err
