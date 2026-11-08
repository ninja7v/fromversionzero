# Contributing to FromVersionZero

Thanks for your interest in contributing!

## Setup

```bash
git clone https://github.com/ninja7v/fromversionzero.git
cd fromversionzero
uv sync --extra dev          # or: python -m venv .venv && pip install -e ".[dev]"
```

FFmpeg comes with `imageio-ffmpeg` and SVG rendering with `resvg-py`, so no
system library is needed on Linux, macOS or Windows.

## Checks (same as CI)

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run lizard -L 120 -C 15 -w src tests
uv run pytest --cov
```

## Visual changes

Key frames of the synthetic fixture are compared with reviewed images in
`tests/golden/`. When you change the look on purpose:

```bash
FVZ_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py
```

then open the changed PNGs and commit them with the code. A failing CI run
uploads the `*.actual.png` frames as an artifact.

## Config changes

`schema/config.schema.json` is generated from `config.py` and checked by a
test. After changing the config model run
`uv run fromversionzero schema -o schema/config.schema.json`. Breaking changes
to `config.json` or `data.csv` semantics bump `SCHEMA_VERSION`.

## Guidelines

- Add or update tests for every behavioral change; keep functions under the
  lizard limits (CCN ≤ 15, ≤ 120 lines).
- Keep rendering pure: `Renderer.render(FrameState)` must not depend on
  previous frames (tests check parallel == sequential output).
- Update `CHANGELOG.md` under "Unreleased".
- Datasets are contributed to
  [fromversionzero-data](https://github.com/ninja7v/fromversionzero-data),
  which defines the data format. The generator follows it: a dataset that
  passes that repository's tests must also pass
  `fromversionzero validate --strict`.
- Never commit generated videos, caches or secrets.

## Releases

1. Move the "Unreleased" changelog entries under a new version heading.
2. Bump `__version__` in `src/fromversionzero/__init__.py`.
3. Tag `vX.Y.Z` and push the tag; the release workflow tests, builds and
   publishes a GitHub release with the changelog notes.

Code contributions are licensed under the [MIT License](LICENSE.txt).
