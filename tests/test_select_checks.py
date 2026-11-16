# tests/test_select_checks.py
# MIT License - Copyright (c) 2026 Luc Prevost
# CI check selection (.github/scripts/select_checks.py) on this repository's
# real import graph: each kind of change runs what it can affect.

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import ROOT

_spec = importlib.util.spec_from_file_location(
    "select_checks", ROOT / ".github" / "scripts" / "select_checks.py"
)
assert _spec and _spec.loader
sc = importlib.util.module_from_spec(_spec)
sys.modules["select_checks"] = sc  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(sc)

RENDERING = {"tests/test_renderer.py", "tests/test_golden.py", "tests/test_pipeline.py"}
CLI = {"tests/test_cli.py", "tests/test_media.py", "tests/test_examples.py"}
UNRELATED_TO_RENDERING = {"tests/test_config.py", "tests/test_data_loader.py"}


def selected(*changed: str) -> dict[str, str]:
    return sc.outputs(sc.plan_for("pull_request", list(changed), ROOT))


def chosen(out: dict[str, str]) -> set[str]:
    return set(out["tests"].split())


def jobs(out: dict[str, str]) -> list[dict]:
    return json.loads(out["matrix"])["include"]


def test_documentation_runs_nothing() -> None:
    out = selected("README.md", "CHANGELOG.md", "docs/DATASET_GUIDE.md", "docs/images/x.png")
    assert out["tests"] == "" and out["tier"] == "none" and jobs(out) == []
    assert (out["quality"], out["package"], out["datasets"]) == ("false", "false", "false")


@pytest.mark.parametrize("module", ["config", "data_loader", "__init__"])
def test_modules_everything_imports_run_every_test(module: str) -> None:
    out = selected(f"src/fromversionzero/{module}.py")
    assert out["tests"] == "tests" and out["tier"] == "standard"
    assert out["quality"] == out["package"] == out["datasets"] == "true"


def test_schema_snapshot_runs_the_config_tests_on_one_job() -> None:
    out = selected("schema/config.schema.json")
    assert chosen(out) == {"tests/test_config.py"} and len(jobs(out)) == 1


def test_core_logic_runs_its_tests_and_its_dependents() -> None:
    out = selected("src/fromversionzero/interpolator.py")
    assert {"tests/test_interpolator.py", "tests/test_timeline.py"} | RENDERING <= chosen(out)
    assert "tests/test_config.py" not in chosen(out)


@pytest.mark.parametrize(
    "module", ["renderer_landscape", "renderer_portrait", "drawing", "components"]
)
def test_rendering_changes_run_renderer_and_golden_tests(module: str) -> None:
    out = selected(f"src/fromversionzero/{module}.py")
    assert RENDERING | CLI <= chosen(out)  # renderer.py dispatches to both orientations
    assert not UNRELATED_TO_RENDERING & chosen(out)
    assert out["datasets"] == "false"  # validation never renders


@pytest.mark.parametrize(
    "path", ["src/fromversionzero/asset_manager.py", "src/fromversionzero/resources/icons/x.svg"]
)
def test_shared_assets_reach_rendering_and_validation(path: str) -> None:
    out = selected(path)
    assert RENDERING | CLI | {"tests/test_asset_manager.py"} <= chosen(out)
    assert out["datasets"] == "true"


@pytest.mark.parametrize("module", ["encoder", "audio_handler", "video_pipeline"])
def test_encoding_and_audio_run_pipeline_tests_without_rendering_ones(module: str) -> None:
    out = selected(f"src/fromversionzero/{module}.py")
    assert {"tests/test_pipeline.py"} | CLI <= chosen(out)
    assert not {"tests/test_renderer.py", "tests/test_golden.py"} & chosen(out)


def test_cli_runs_cli_tests() -> None:
    out = selected("src/fromversionzero/cli.py")
    assert CLI <= chosen(out) and out["datasets"] == "true"
    assert not RENDERING & chosen(out)


def test_example_dataset_runs_its_validation_and_the_published_comparison() -> None:
    out = selected("examples/Vehicle/Porsche_911/data.csv")
    assert chosen(out) == {"tests/test_examples.py"} and len(jobs(out)) == 1
    assert out["datasets"] == "true" and out["lowest_tests"] == ""


def test_test_changes_run_that_file_and_infrastructure_runs_all() -> None:
    assert chosen(selected("tests/test_timeline.py")) == {"tests/test_timeline.py"}
    assert chosen(selected("tests/golden/landscape_hold.png")) == {"tests/test_golden.py"}
    assert selected("tests/conftest.py")["tests"] == "tests"
    assert selected("tests/fixtures/data/Gadgets/units.json")["tests"] == "tests"


@pytest.mark.parametrize(
    "path", ["pyproject.toml", "uv.lock", ".github/workflows/tests.yml", "new/unknown.file"]
)
def test_dependencies_ci_and_unknown_paths_run_everything(path: str) -> None:
    out = selected(path)
    assert out["tests"] == "tests" and out["tier"] == "full" and len(jobs(out)) == 12
    assert out["quality"] == out["package"] == out["datasets"] == "true"


def test_mixed_changes_combine_their_checks() -> None:
    out = selected("README.md", "examples/Vehicle/units.json", "src/fromversionzero/encoder.py")
    assert chosen(out) == {"tests/test_pipeline.py"} | CLI
    assert out["tier"] == "standard" and out["datasets"] == out["package"] == "true"


def test_pull_requests_use_one_macos_and_one_windows_job() -> None:
    oses = [j["os"] for j in jobs(selected("src/fromversionzero/timeline.py"))]
    assert oses.count("macos-latest") == oses.count("windows-latest") == 1


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_scheduled_and_manual_runs_check_everything(event: str) -> None:
    out = sc.outputs(sc.plan_for(event, None, ROOT))
    assert out["tests"] == "tests" and len(jobs(out)) == 12 and out["datasets"] == "true"


def test_test_files_in_subfolders_are_never_skipped() -> None:
    out = selected("tests/integration/test_new.py")
    assert out["tests"] == "tests" and out["quality"] == "true"


def test_deleted_files_select_by_name_and_never_pass_missing_files() -> None:
    # Modules are matched by name, so a deleted module still selects the files
    # that (still) import it; a deleted test file is not passed to pytest.
    assert "tests/test_media.py" in chosen(selected("src/fromversionzero/marks.py"))
    assert selected("src/fromversionzero/gone.py")["tests"] == ""
    assert selected("tests/test_removed.py")["tests"] == ""


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_diff_covers_every_commit_of_a_pull_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(*args: str) -> str:
        identity = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        cmd = ["git", *identity, "-c", "core.hooksPath=", *args]
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()

    def commit(name: str) -> str:
        Path(name).write_text(name)
        git("add", name)
        git("commit", "-qm", name)
        return git("rev-parse", "HEAD")

    monkeypatch.chdir(tmp_path)
    git("init", "-q", "-b", "main")
    commit("base.txt")
    git("checkout", "-qb", "feature")
    commit("first.txt")
    head = commit("second.txt")
    git("checkout", "-q", "main")
    base = commit("main_only.txt")  # main moved on: not part of the pull request
    assert sc.changed_files("pull_request", base, head) == ["first.txt", "second.txt"]
    assert sc.changed_files("pull_request", "f" * 40, head) is None  # missing commit
    assert sc.changed_files("workflow_dispatch", "", "") is None  # no diff: everything
