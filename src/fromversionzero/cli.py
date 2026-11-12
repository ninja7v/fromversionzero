# src/fromversionzero/cli.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Command-line interface: render videos, validate datasets, export the config schema.

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fromversionzero import __version__
from fromversionzero.config import VideoConfig, config_json_schema

logger = logging.getLogger("fromversionzero")

COMMANDS = ("render", "preview", "validate", "list", "schema", "mark")
DATA_ROOT_ENV = "FVZ_DATA_ROOT"
CONFIG_ENV = "FVZ_CONFIG"


class CliError(Exception):
    """User-facing error: printed without a traceback, exit code 1."""


# ----------------------------------------------------------------------
# Path resolution
# ----------------------------------------------------------------------
def resolve_config_path(explicit: str | None) -> Path | None:
    """Project config: ``--config`` > ``$FVZ_CONFIG`` > ``./video.json`` > none."""
    candidate = explicit or os.environ.get(CONFIG_ENV)
    if candidate:
        path = Path(candidate).expanduser()
        if not path.is_file():
            raise CliError(f"Config file not found: {path}")
        return path.resolve()
    local = Path.cwd() / "video.json"
    return local.resolve() if local.is_file() else None


def resolve_data_root(explicit: str | None) -> Path | None:
    """Dataset root: ``--data-root`` > ``$FVZ_DATA_ROOT`` > ``./data`` > none."""
    candidate = explicit or os.environ.get(DATA_ROOT_ENV)
    if candidate:
        path = Path(candidate).expanduser()
        if not path.is_dir():
            raise CliError(f"Data root is not a directory: {path}")
        return path.resolve()
    local = Path.cwd() / "data"
    return local.resolve() if local.is_dir() else None


def is_dataset_dir(path: Path) -> bool:
    return (path / "data.csv").is_file() and (path / "config.json").is_file()


def iter_datasets(data_root: Path) -> list[Path]:
    """All dataset directories directly under ``data_root`` or one category deep."""
    found = [p for p in sorted(data_root.iterdir()) if p.is_dir() and is_dataset_dir(p)]
    for category in sorted(data_root.iterdir()):
        if category.is_dir() and not is_dataset_dir(category):
            found.extend(p for p in sorted(category.iterdir()) if p.is_dir() and is_dataset_dir(p))
    return found


def find_dataset(product: str, data_root: Path | None) -> Path:
    """Resolves ``product`` as a path, ``Category/Name`` or a bare name under the data root."""
    as_path = Path(product).expanduser()
    if as_path.is_dir():
        return as_path.resolve()
    if data_root is None:
        raise CliError(
            f"Product '{product}' is not a directory and no data root was found. "
            f"Pass --data-root, set ${DATA_ROOT_ENV}, or run from a folder containing data/ "
            "(e.g. a clone of fromversionzero-data, or --data-root examples)."
        )
    direct = data_root / product
    if direct.is_dir():
        return direct.resolve()
    matches = [p for p in iter_datasets(data_root) if p.name == product]
    if len(matches) == 1:
        return matches[0].resolve()
    if len(matches) > 1:
        choices = "\n  ".join(m.relative_to(data_root).as_posix() for m in matches)
        raise CliError(f"Product name '{product}' is ambiguous. Use Category/Name:\n  {choices}")
    raise CliError(f"Product '{product}' not found under {data_root}.")


def load_product_config(
    dataset_dir: Path, config_path: Path | None, overrides: dict[str, Any] | None = None
) -> VideoConfig:
    product_config = dataset_dir / "config.json"
    if not product_config.is_file():
        raise CliError(f"config.json not found in dataset: {dataset_dir}")
    if not (dataset_dir / "data.csv").is_file():
        raise CliError(f"data.csv not found in dataset: {dataset_dir}")
    try:
        return VideoConfig.load(config_path, product_config, overrides)
    except ValueError as e:
        raise CliError(str(e)) from None


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def _render_overrides(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if args.shorts:
        overrides["shorts"] = True
    if getattr(args, "output_folder", None):
        overrides["output_folder"] = os.path.abspath(args.output_folder)
    if args.no_speculation:
        overrides["speculation"] = False
    return overrides


def _resolve_render(args: argparse.Namespace) -> tuple[Path, Path | None, VideoConfig]:
    """Dataset folder, project config path and final config of a render/preview."""
    config_path = resolve_config_path(args.config)
    data_root = resolve_data_root(args.data_root)
    product = args.product
    if not product and config_path is not None:
        product = VideoConfig.load(config_path).product_name or None
    if not product:
        raise CliError("No product specified. Pass --product or set 'product_name' in video.json.")

    dataset_dir = find_dataset(product, data_root)
    overrides = _render_overrides(args)
    config = load_product_config(dataset_dir, config_path, overrides)
    if args.resolution:
        # --resolution sets the size of the format being rendered: Shorts take
        # theirs from shorts_overrides, which would otherwise ignore the flag.
        if config.shorts:
            overrides["shorts_overrides"] = {"resolution": args.resolution}
        else:
            overrides["resolution"] = args.resolution
        config = load_product_config(dataset_dir, config_path, overrides)
    if not config.product_name:
        config = config.replace(product_name=dataset_dir.name)
    return dataset_dir, config_path, config


def cmd_render(args: argparse.Namespace) -> int:
    from fromversionzero.video_pipeline import VideoPipeline

    dataset_dir, config_path, config = _resolve_render(args)

    print(f"FromVersionZero {__version__}")
    print(f"  dataset     {dataset_dir}")
    print(f"  config      {config_path or '(built-in defaults)'}")
    print(f"  format      {'portrait (Shorts)' if config.shorts else 'landscape'}")
    print(f"  theme       {config.theme}")

    start = time.perf_counter()
    pipeline = VideoPipeline(config, str(dataset_dir), max_workers=args.workers)
    output = pipeline.generate_video()
    print(f"Done in {time.perf_counter() - start:.1f}s -> {output}")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Renders one frame of the video to a PNG (seconds instead of a full render)."""
    from fromversionzero.video_pipeline import VideoPipeline

    dataset_dir, _, config = _resolve_render(args)
    pipeline = VideoPipeline(config, str(dataset_dir))
    image, t, total = pipeline.render_still(args.time)
    output = Path(args.output or f"{config.product_name}_preview.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    print(f"Frame at {t:.2f}s of {total:.2f}s -> {output}")
    return 0


def _validate_one(
    dataset_dir: Path, config_path: Path | None, check_media: bool = False
) -> tuple[str | None, list[str]]:
    """Returns ``(error, warnings)``; ``error`` is ``None`` when the dataset is valid.

    With ``check_media``, every linked logo and image is downloaded (into the
    media cache) and unreachable or unreadable links are reported as warnings.
    """
    from fromversionzero.asset_manager import AssetManager
    from fromversionzero.data_loader import Dataset

    try:
        config = load_product_config(dataset_dir, config_path)
        dataset = Dataset.load(
            str(dataset_dir),
            x_metric=config.x_metric,
            metrics=config.metrics,
            y_scale=config.y_scale,
        )
        assets = AssetManager(str(dataset_dir), media=config.media)
    except (CliError, ValueError, FileNotFoundError) as e:
        return str(e), []
    logo = config.media.logo
    if logo and not logo.startswith(("http://", "https://")) and not (dataset_dir / logo).is_file():
        return f"media.logo '{logo}' is neither an http(s) URL nor a file in the dataset.", []
    warnings = list(dataset.warnings)
    unitless = [m for m in dataset.y_metrics if not assets.get_metric_unit(m)]
    if unitless:
        warnings.append(f"No display unit for {unitless} (add them to units.json).")
    if check_media:
        refs = [p.image_filename for p in dataset.points if p.image_filename]
        for url in assets.prefetch(refs):
            reason = assets.media_cache.failed.get(url, "unavailable")
            warnings.append(f"Linked media failed ({reason}): {url}")
    return None, warnings


def cmd_validate(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    data_root = resolve_data_root(args.data_root)
    if args.product:
        targets = [find_dataset(args.product, data_root)]
    else:
        if data_root is None:
            raise CliError(f"No data root found. Pass --data-root or set ${DATA_ROOT_ENV}.")
        targets = iter_datasets(data_root)
        if not targets:
            raise CliError(f"No datasets found under {data_root}.")

    failures = warned = 0
    for dataset_dir in targets:
        error, warnings = _validate_one(dataset_dir, config_path, args.check_media)
        label = (
            dataset_dir.name if data_root is None else dataset_dir.relative_to(data_root).as_posix()
        )
        if error is not None:
            failures += 1
            print(f"FAIL  {label}\n      " + error.replace("\n", "\n      "))
            continue
        print(f"{'warn' if warnings else 'ok'}  {label}")
        for warning in warnings:
            print(f"      - {warning}")
        warned += bool(warnings)
    print(f"{len(targets) - failures}/{len(targets)} datasets valid, {warned} with warnings.")
    if args.strict and warned:
        return 1
    return 1 if failures else 0


def cmd_mark(args: argparse.Namespace) -> int:
    """Redraws a local image or a URL as FromVersionZero line art."""
    from PIL import Image

    from fromversionzero.marks import stylize
    from fromversionzero.media import MediaCache, is_url

    if not 16 <= args.size <= 4096:
        raise CliError("--size must be between 16 and 4096 pixels.")
    line = args.line_color
    if line is not None and not re.fullmatch(r"#[0-9A-Fa-f]{6}", line):
        raise CliError("--line-color must be a #RRGGBB colour.")
    if is_url(args.source):
        mark = MediaCache().mark(
            args.source, levels=args.levels, invert=args.invert, size=args.size, line=line
        )
        if mark is None:
            raise CliError(f"Could not download or read {args.source}.")
    else:
        try:
            with Image.open(args.source) as source:
                mark = stylize(
                    source, size=args.size, levels=args.levels, invert=args.invert, line=line
                )
        except OSError as e:
            raise CliError(f"Could not read image '{args.source}': {e}") from None
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    mark.save(args.output, optimize=True)
    print(f"Mark written to {args.output}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    data_root = resolve_data_root(args.data_root)
    if data_root is None:
        raise CliError(f"No data root found. Pass --data-root or set ${DATA_ROOT_ENV}.")
    datasets = iter_datasets(data_root)
    if not datasets:
        raise CliError(f"No datasets found under {data_root}.")
    for dataset_dir in datasets:
        print(dataset_dir.relative_to(data_root).as_posix())
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    text = json.dumps(config_json_schema(), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Schema written to {args.output}")
    else:
        sys.stdout.write(text)
    return 0


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------
def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        help=f"Project config file (default: ${CONFIG_ENV} or ./video.json).",
    )
    parser.add_argument(
        "--data-root",
        help=f"Folder containing datasets (default: ${DATA_ROOT_ENV} or ./data).",
    )


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def _add_render_options(parser: argparse.ArgumentParser) -> None:
    _add_common(parser)
    parser.add_argument("--product", "-p", help="Dataset name, Category/Name, or directory path.")
    parser.add_argument("--shorts", action="store_true", help="Portrait Shorts output.")
    parser.add_argument(
        "--resolution",
        help="Size of the rendered format (e.g. 720p, 1920x1080; 1080x1920 with --shorts).",
    )
    parser.add_argument(
        "--no-speculation", action="store_true", help="Skip speculated generations."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fromversionzero",
        description="Render animated product-evolution videos from timeline datasets.",
        epilog="Running without a command is the same as 'render'.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="Show debug logging.")

    render = sub.add_parser("render", parents=[common], help="Render a video (default command).")
    _add_render_options(render)
    render.add_argument("--output-folder", help="Write the MP4 here instead of the dataset.")
    render.add_argument(
        "--workers",
        type=_positive_int,
        help="Render processes (default: one per CPU core, at most 32).",
    )
    render.set_defaults(func=cmd_render)

    preview = sub.add_parser(
        "preview", parents=[common], help="Render a single frame to a PNG, to check the look."
    )
    _add_render_options(preview)
    preview.add_argument(
        "--time",
        "-t",
        type=float,
        help="Moment to draw, in seconds (default: the full timeline, before the outro).",
    )
    preview.add_argument("--output", "-o", help="PNG path (default: ./<product>_preview.png).")
    preview.set_defaults(func=cmd_preview)

    validate = sub.add_parser(
        "validate", parents=[common], help="Validate one dataset or every dataset."
    )
    _add_common(validate)
    validate.add_argument("--product", "-p", help="Validate only this dataset.")
    validate.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    validate.add_argument(
        "--check-media",
        action="store_true",
        help="Download every linked logo and image and report broken links.",
    )
    validate.set_defaults(func=cmd_validate)

    mark = sub.add_parser(
        "mark",
        parents=[common],
        help="Redraw an image file or URL as FromVersionZero line art.",
    )
    mark.add_argument("source", help="Image file or http(s) URL (PNG, JPEG, WebP or SVG).")
    mark.add_argument("output", help="Output PNG path.")
    mark.add_argument("--size", type=int, default=512, help="Longest side in pixels.")
    mark.add_argument(
        "--levels",
        type=int,
        default=3,
        choices=range(2, 6),
        help="Tone bands, 2-5: 3 for logos, 2 for photos.",
    )
    mark.add_argument("--invert", action="store_true", help="Invert tones before banding.")
    mark.add_argument(
        "--line-color",
        metavar="#RRGGBB",
        help="Draw every line in this colour (default: the colours of the source).",
    )
    mark.set_defaults(func=cmd_mark)

    list_cmd = sub.add_parser("list", parents=[common], help="List datasets under the data root.")
    _add_common(list_cmd)
    list_cmd.set_defaults(func=cmd_list)

    schema = sub.add_parser(
        "schema", parents=[common], help="Print the JSON Schema of config files."
    )
    schema.add_argument("--output", "-o", help="Write to a file instead of stdout.")
    schema.set_defaults(func=cmd_schema)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list or (
        args_list[0] not in COMMANDS and args_list[0] not in ("-h", "--help", "--version")
    ):
        args_list.insert(0, "render")

    parser = build_parser()
    args = parser.parse_args(args_list)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        code = args.func(args)
    except CliError as e:
        print(f"Error: {e}", file=sys.stderr)
        code = 1
    except (ValueError, OSError, RuntimeError) as e:
        # Bad datasets, unreadable files and FFmpeg failures are user-facing
        # problems: one readable message, the traceback only with --verbose.
        if args.verbose:
            logger.exception("Command failed")
        print(f"Error: {e}", file=sys.stderr)
        if not args.verbose:
            print("(run again with --verbose for the full traceback)", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
