# .github/scripts/select_checks.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Decides which CI checks a change needs (the "changes" job of workflows/tests.yml).
#
# Test selection follows the real import graph of src/ and tests/, so it cannot
# go stale: a test file runs when it (or conftest.py) references a module that
# a change reaches, directly or through any chain of imports. String references
# such as resources.files("fromversionzero.resources") count as imports. Paths
# without a rule, dependency files and CI files run everything.
#
# Usage: select_checks.py <event> [<base> <head>]   (stdlib only)

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

PACKAGE = "fromversionzero"
SRC = f"src/{PACKAGE}"
ALL_TESTS = "tests"
TIERS = ("none", "light", "standard", "full")


def _jobs(oses: list[str], pythons: list[str], experimental: bool = False) -> list[dict]:
    return [{"os": o, "python": p, "experimental": experimental} for o in oses for p in pythons]


OSES = ["ubuntu-latest", "macos-latest", "windows-latest"]
MATRICES: dict[str, list[dict]] = {
    "none": [],
    # Data-only changes (examples, schema, video.json) are OS-independent.
    "light": _jobs(["ubuntu-latest"], ["3.14"]),
    # Pull requests: every Python on Linux, the newest stable on macOS and
    # Windows (macOS runners queue for 10-20 minutes: they set the PR latency).
    "standard": _jobs(["ubuntu-latest"], ["3.12", "3.13", "3.14"])
    + _jobs(["macos-latest", "windows-latest"], ["3.14"])
    + _jobs(["ubuntu-latest"], ["3.15"], experimental=True),
    # Dependency/CI changes, scheduled and manual runs.
    "full": _jobs(OSES, ["3.12", "3.13", "3.14"]) + _jobs(OSES, ["3.15"], experimental=True),
}

#: Paths that change no tested behaviour.
DOCS = ["*.md", "docs/*", ".github/ISSUE_TEMPLATE/*", ".github/FUNDING.yml", ".gitignore"]
#: Paths that can change everything: dependencies, build/test configuration, CI.
FULL = ["pyproject.toml", "uv.lock", ".gitattributes", ".github/*"]
#: Paths only the package build reads.
PACKAGING = ["MANIFEST.in", "LICENSE.txt", f"{SRC}/py.typed"]

#: What `fromversionzero validate` loads besides cli.py (mirrors cli._validate_one).
#: Changes to these, or to the example, re-validate the fromversionzero-data datasets.
VALIDATION_ENTRY = ["data_loader", "asset_manager", "config"]


@dataclass
class Plan:
    tier: str = "none"
    tests: set[str] = field(default_factory=set)  # test files, or {ALL_TESTS}
    quality: bool = False
    package: bool = False
    datasets: bool = False
    reasons: list[str] = field(default_factory=list)

    def bump(self, tier: str) -> None:
        self.tier = max(self.tier, tier, key=TIERS.index)

    def run_everything(self, reason: str) -> None:
        self.bump("full")
        self.tests = {ALL_TESTS}
        self.quality = self.package = self.datasets = True
        self.reasons.append(reason)


# ----------------------------------------------------------------------
# Import graph
# ----------------------------------------------------------------------
_MODULE_STRING = re.compile(rf"^{PACKAGE}(\.\w+)*$")


def _names(node: ast.AST) -> list[str]:
    """Module names an AST node refers to (imports anywhere, module-name strings)."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and not node.level:
        return [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value] if _MODULE_STRING.match(node.value) else []
    return []


def _references(source: str, local: set[str]) -> set[str]:
    """Package modules (and local test modules in ``local``) that ``source`` uses."""
    refs = {name for node in ast.walk(ast.parse(source)) for name in _names(node)}
    # Importing fromversionzero.x also runs fromversionzero/__init__.py.
    for name in list(refs):
        parts = name.split(".")
        refs.update(".".join(parts[:i]) for i in range(1, len(parts)))
    return {r for r in refs if r == PACKAGE or r.startswith(f"{PACKAGE}.") or r in local}


def _module_name(path: Path) -> str:
    parts = list(path.relative_to("src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@dataclass
class Graph:
    imports: dict[str, set[str]]  # src module -> modules it references
    tests: dict[str, set[str]]  # test file -> modules it (or conftest) references

    @classmethod
    def load(cls, root: Path) -> Graph:
        imports = {
            _module_name(p.relative_to(root)): _references(p.read_text(encoding="utf-8"), set())
            for p in sorted((root / SRC).rglob("*.py"))
        }
        test_dir = root / "tests"
        files = [p for p in sorted(test_dir.rglob("*.py")) if "fixtures" not in p.parts]
        helpers = {p.stem: p for p in files if p.parent == test_dir}  # importable by stem
        direct = {p: _references(p.read_text(encoding="utf-8"), set(helpers)) for p in files}
        tests: dict[str, set[str]] = {}
        for p in files:
            if p.stem.startswith("test_"):
                seen: set[Path] = set()
                todo = [p, helpers.get("conftest")]  # conftest applies to every test file
                while todo:
                    q = todo.pop()
                    if q is not None and q not in seen:
                        seen.add(q)
                        todo.extend(helpers[name] for name in direct[q] & set(helpers))
                tests[p.relative_to(root).as_posix()] = set().union(*(direct[q] for q in seen))
        return cls(imports, tests)

    def dependents(self, modules: set[str]) -> set[str]:
        """``modules`` plus every module that imports one of them, transitively."""
        affected: set[str] = set()
        todo = list(modules)
        while todo:
            name = todo.pop()
            if name not in affected:
                affected.add(name)
                todo.extend(m for m, refs in self.imports.items() if name in refs)
        return affected

    def dependencies(self, modules: set[str]) -> set[str]:
        """``modules`` plus everything they import, transitively."""
        found: set[str] = set()
        todo = list(modules)
        while todo:
            name = todo.pop()
            if name not in found:
                found.add(name)
                todo.extend(self.imports.get(name, ()))
        return found

    def tests_for(self, modules: set[str]) -> set[str]:
        affected = self.dependents(modules)
        return {test for test, refs in self.tests.items() if refs & affected}

    def owner(self, path: str) -> str:
        """The module a file under src/ belongs to; data files belong to their package."""
        name = _module_name(Path(path))
        if not path.endswith(".py"):  # e.g. resources/icons/car.svg
            while name not in self.imports and "." in name:
                name = name.rsplit(".", 1)[0]
        return name


# ----------------------------------------------------------------------
# Change classification: the first matching rule wins
# ----------------------------------------------------------------------
def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, p) for p in patterns)


def _validation_modules(graph: Graph) -> set[str]:
    return graph.dependencies({f"{PACKAGE}.{m}" for m in VALIDATION_ENTRY}) | {f"{PACKAGE}.cli"}


def _classify_src(path: str, graph: Graph, plan: Plan) -> None:
    module = graph.owner(path)
    plan.bump("standard")
    plan.tests |= graph.tests_for({module})
    plan.quality |= path.endswith(".py")
    plan.package = True
    plan.datasets |= module in _validation_modules(graph)
    plan.reasons.append(f"{path}: tests reaching {module}")


def _classify_tests(path: str, graph: Graph, plan: Plan) -> None:
    plan.bump("standard")
    plan.quality |= path.endswith(".py")
    if path.startswith("tests/golden/"):
        plan.tests.add("tests/test_golden.py")
        plan.reasons.append(f"{path}: golden frames")
    elif re.fullmatch(r"tests/[^/]+\.py", path) and path != "tests/conftest.py":
        stem = Path(path).stem  # a test file, or a helper module test files import
        plan.tests |= {t for t, refs in graph.tests.items() if t == path or stem in refs}
        plan.reasons.append(f"{path}: this test file and its importers")
    else:  # conftest.py, fixtures and anything else under tests/
        plan.tests.add(ALL_TESTS)
        plan.package |= path.startswith("tests/fixtures/")  # the wheel smoke test uses them
        plan.reasons.append(f"{path}: shared test infrastructure, every test")


def classify(path: str, graph: Graph, plan: Plan) -> None:
    """Adds what a change to ``path`` requires to ``plan``."""
    if _matches(path, PACKAGING):
        plan.package = True
        plan.reasons.append(f"{path}: package build")
    elif path.startswith(f"{SRC}/"):
        _classify_src(path, graph, plan)
    elif path.startswith("tests/"):
        _classify_tests(path, graph, plan)
    elif path.startswith("schema/"):
        plan.bump("light")
        plan.tests.add("tests/test_config.py")  # checks the committed schema snapshot
        plan.reasons.append(f"{path}: schema snapshot")
    elif path.startswith("examples/"):
        plan.bump("light")
        plan.tests.add("tests/test_examples.py")
        plan.datasets = True  # the example must match its published version
        plan.reasons.append(f"{path}: example dataset")
    elif path == "video.json":
        # The CLI reads ./video.json when tests run it from the repository root.
        plan.bump("light")
        plan.tests |= graph.tests_for({f"{PACKAGE}.cli"})
        plan.reasons.append(f"{path}: project config read by the CLI tests and examples")
    elif path == "main.py":
        plan.quality = True
        plan.reasons.append(f"{path}: launcher shim, lint only")
    elif _matches(path, DOCS):
        plan.reasons.append(f"{path}: documentation, nothing to run")
    elif _matches(path, FULL):
        plan.run_everything(f"{path}: dependencies or CI configuration, everything")
    else:
        plan.run_everything(f"{path}: no rule for this path, everything")


def plan_for(event: str, changed: list[str] | None, root: Path) -> Plan:
    """``changed`` is ``None`` when the changed files are unknown."""
    plan = Plan()
    if event != "pull_request" or changed is None:
        plan.run_everything(f"{event} event or unknown diff: everything")
        return plan
    graph = Graph.load(root)
    for path in changed:
        classify(path, graph, plan)
    if ALL_TESTS in plan.tests or plan.tests >= graph.tests.keys():
        plan.tests = {ALL_TESTS}
    else:  # deleted test files cannot run
        plan.tests = {t for t in plan.tests if (root / t).is_file()}
    if not plan.tests:
        plan.tier = "none"
    return plan


# ----------------------------------------------------------------------
# GitHub Actions entry point
# ----------------------------------------------------------------------
def changed_files(event: str, base: str, head: str) -> list[str] | None:
    """Files changed by every commit of the pull request; ``None`` when unknown."""
    if event != "pull_request" or not base:
        return None  # scheduled or manual run
    # Three dots: everything since the merge base, i.e. every commit of the PR.
    cmd = ["git", "diff", "--name-only", "--no-renames", f"{base}...{head}"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:  # e.g. a commit is missing from the checkout
        print(result.stderr, file=sys.stderr)
        return None
    return [line for line in result.stdout.splitlines() if line]


def outputs(plan: Plan) -> dict[str, str]:
    tests = sorted(plan.tests)
    lowest = [t for t in tests if t != "tests/test_golden.py"]  # goldens need locked deps
    return {
        "tier": plan.tier,
        "tests": " ".join(tests),
        "matrix": json.dumps({"include": MATRICES[plan.tier]}),
        "lowest_tests": " ".join(lowest) if plan.tier in ("standard", "full") else "",
        "quality": str(plan.quality).lower(),
        "package": str(plan.package).lower(),
        "datasets": str(plan.datasets).lower(),
    }


def main(argv: list[str]) -> int:
    event = argv[1]
    base, head = (argv[2], argv[3]) if len(argv) > 3 else ("", "")
    changed = changed_files(event, base, head)
    plan = plan_for(event, changed, Path.cwd())
    result = outputs(plan)
    report = [f"Changed files: {'unknown' if changed is None else len(changed)}"]
    report += [f"- {r}" for r in plan.reasons]
    report += [f"{k}: {v}" for k, v in result.items() if k != "matrix"]
    print("\n".join(report))
    for env, text in (
        ("GITHUB_OUTPUT", "".join(f"{k}={v}\n" for k, v in result.items())),
        ("GITHUB_STEP_SUMMARY", "## Selected checks\n\n```\n" + "\n".join(report) + "\n```\n"),
    ):
        if os.environ.get(env):
            with open(os.environ[env], "a", encoding="utf-8") as file:
                file.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
