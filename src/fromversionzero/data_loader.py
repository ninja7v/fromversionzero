# src/fromversionzero/data_loader.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Parses data.csv files into validated, chronologically sorted product data points.

import csv
import logging
import math
import os
import re
from dataclasses import dataclass, field
from typing import ClassVar

logger = logging.getLogger(__name__)

_YEAR_MONTH = re.compile(r"^(\d{4})\.(\d{1,2})$")
_TRUE = frozenset({"true", "yes", "1", "y", "t"})
_FALSE = frozenset({"false", "no", "0", "n", "f", ""})
#: Columns with a fixed meaning; matched ignoring case, never plotted.
RESERVED_COLUMNS: frozenset[str] = frozenset({"name", "image", "fact", "sfx", "speculation"})


def parse_date_to_fractional_year(val: str) -> tuple[float, bool]:
    """Parses date string formatted as YYYY.MM (e.g. 2018.09) to a continuous float.

    Returns (fractional_year, has_month). has_month is True when the input used
    the YYYY.MM form (even for January, which would otherwise be indistinguishable
    from a bare integer year). A month outside 01-12 is an error, not a decimal year.
    """
    clean = val.strip()
    match = _YEAR_MONTH.match(clean)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"month {match.group(2)} is not between 01 and 12")
        return year + (month - 1) / 12.0, True
    return float(clean), False


def is_remote(ref: str) -> bool:
    return ref.startswith(("http://", "https://"))


def resolve_inside(base_dir: str, ref: str) -> str | None:
    """Path of ``ref`` under ``base_dir``, or ``None`` when it points outside.

    References in ``data.csv`` come with the dataset, which may be someone
    else's work: they may only name files of that dataset.
    """
    if os.path.isabs(ref):
        return None
    base = os.path.realpath(base_dir)
    path = os.path.realpath(os.path.join(base, ref))
    try:
        inside = os.path.commonpath([base, path]) == base
    except ValueError:  # different drives on Windows
        inside = False
    return path if inside else None


class ProductDataPoint:
    """Represents a single iteration/version of a product."""

    def __init__(self, raw_row: dict[str, str], index: int) -> None:
        self.index: int = index
        #: Position in the loaded, sorted :class:`Dataset` (set by ``Dataset``).
        self.order: int = index
        self.raw_data: dict[str, str] = raw_row
        self.name: str = raw_row.get("name", f"Version {index}")

        self.metrics: dict[str, float] = {}
        self.image_filename: str = raw_row.get("image", "")
        self.fact: str = raw_row.get("fact", "")
        self.sfx_filename: str = raw_row.get("sfx", "")
        self.has_month: bool = False
        # True when this row is a speculation about an unreleased generation.
        self.is_speculation: bool = False

        # Every other column is numeric where possible.
        for col, val in raw_row.items():
            if col in ("name", "image", "fact", "sfx"):
                continue
            if col == "speculation":
                flag = val.strip().lower()
                if flag not in _TRUE and flag not in _FALSE:
                    raise ValueError(
                        f"Row {index + 1} ('{self.name}') has an invalid 'speculation' "
                        f"value '{val}': use true or false."
                    )
                self.is_speculation = flag in _TRUE
                continue
            # Thousands separators and dollar signs are tolerated.
            clean_val: str = val.replace("$", "").replace(",", "").strip()
            if col.lower() in ("date", "year"):
                if not clean_val:
                    continue
                try:
                    parsed, has_month = parse_date_to_fractional_year(clean_val)
                except ValueError as e:
                    if "month" not in str(e):
                        continue  # not a number: reported by the loader's X-axis check
                    raise ValueError(
                        f"Row {index + 1} ('{self.name}') has an invalid {col} '{val}': {e}."
                    ) from None
                self.metrics[col] = parsed
                self.has_month = self.has_month or has_month
                continue
            try:
                self.metrics[col] = float(clean_val)
            except ValueError:
                # Text columns stay in raw_data; they are not plotted.
                pass

    def get_metric(self, name: str, default: float = 0.0) -> float:
        """Retrieves a numeric metric value or returns default."""
        if name in self.metrics:
            return self.metrics[name]
        # Fallback between year and date aliases
        if name == "year" and "date" in self.metrics:
            return self.metrics["date"]
        if name == "date" and "year" in self.metrics:
            return self.metrics["year"]
        return default

    def __repr__(self) -> str:
        return f"<ProductDataPoint '{self.name}' metrics={self.metrics}>"


@dataclass
class LoadedCsv:
    """Result of reading ``data.csv``: sorted points, the X column, data-quality notes."""

    points: list[ProductDataPoint]
    x_metric: str
    warnings: list[str] = field(default_factory=list)


class DataLoader:
    """Handles loading, parsing, sorting, and validating evolution datasets."""

    NON_METRIC_COLUMNS: ClassVar[frozenset[str]] = RESERVED_COLUMNS

    @staticmethod
    def _normalize_metric_key(metric_name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", metric_name.lower().strip())

    @staticmethod
    def _find_matching_header(requested_metric: str, headers: list[str]) -> str | None:
        """Exact match ignoring case and punctuation, else the shortest header
        that contains the request or is contained in it."""
        normalized_request: str = DataLoader._normalize_metric_key(requested_metric)
        if not normalized_request:
            return None
        normalized = {header: DataLoader._normalize_metric_key(header) for header in headers}
        for header, key in normalized.items():
            if key == normalized_request:
                return header
        candidates = [
            header
            for header, key in normalized.items()
            if key and (normalized_request in key or key in normalized_request)
        ]
        # Prefer the shortest matching header to avoid overly broad matches.
        return min(candidates, key=lambda h: len(normalized[h])) if candidates else None

    @staticmethod
    def _resolve_metric(
        metric: str, headers: list[str], allow_year_date_swap: bool = False
    ) -> str | None:
        """Resolves a metric name to a CSV column: exact name, then the
        ``year``/``date`` alias, then fuzzy matching."""
        if metric in headers:
            return metric
        if allow_year_date_swap:
            if metric == "year" and "date" in headers:
                return "date"
            if metric == "date" and "year" in headers:
                return "year"
        return DataLoader._find_matching_header(metric, headers)

    @staticmethod
    def _read_headers(fieldnames: list[str] | None) -> list[str]:
        """Trimmed header names; reserved columns are lower-cased (``Name`` -> ``name``)."""
        if not fieldnames:
            raise ValueError("data.csv is empty or has no headers.")
        headers = [h.strip() for h in fieldnames]
        headers = [h.lower() if h.lower() in RESERVED_COLUMNS else h for h in headers]
        if any(not h for h in headers):
            raise ValueError("data.csv has a column without a name in its header row.")
        duplicates = sorted({h for h in headers if headers.count(h) > 1})
        if duplicates:
            raise ValueError(f"data.csv has duplicate columns: {duplicates}")
        if "name" not in headers:
            raise ValueError(f"data.csv needs a 'name' column. Found: {headers}")
        return headers

    @staticmethod
    def _check_references(dp: ProductDataPoint, row: int, dataset_dir: str) -> str | None:
        """Validates the row's ``image``; returns a warning for a missing ``sfx`` file."""
        if dp.image_filename and not is_remote(dp.image_filename):
            images_dir = os.path.join(dataset_dir, "images")
            img_path = resolve_inside(images_dir, dp.image_filename)
            if img_path is None:
                raise ValueError(
                    f"Row {row} ('{dp.name}') image '{dp.image_filename}' must be a file "
                    "inside the dataset's images/ folder or an http(s) URL."
                )
            if not os.path.isfile(img_path):
                raise ValueError(
                    f"Row {row} ('{dp.name}') references missing image: "
                    f"{os.path.join(images_dir, dp.image_filename)}"
                )
        # ``sfx`` may be an absolute path (a shared effects library).
        if dp.sfx_filename and not os.path.isfile(os.path.join(dataset_dir, dp.sfx_filename)):
            return f"Row {row} ('{dp.name}') sfx file not found: {dp.sfx_filename}"
        return None

    @staticmethod
    def _parse_rows(
        reader: "csv.DictReader[str]",
        headers: list[str],
        resolved_x: str,
        dataset_dir: str,
        warnings: list[str],
    ) -> list[ProductDataPoint]:
        """Parses CSV rows into ProductDataPoint objects with validation."""
        data_points: list[ProductDataPoint] = []
        for i, row in enumerate(reader):
            if None in row:
                raise ValueError(f"Row {i + 1} has more cells than the header has columns.")
            if any(v is None for v in row.values()):
                raise ValueError(f"Row {i + 1} has fewer cells than the header has columns.")
            clean_row = {h: (v or "").strip() for h, v in zip(headers, row.values())}
            if not clean_row["name"]:
                raise ValueError(f"Row {i + 1} has a blank required 'name' value.")
            dp = ProductDataPoint(clean_row, i)
            x_value = dp.metrics.get(resolved_x)
            if x_value is None or not math.isfinite(x_value):
                raise ValueError(
                    f"Row {i + 1} ('{dp.name}') has a missing or non-numeric X-metric "
                    f"'{resolved_x}': '{clean_row.get(resolved_x)}'"
                )
            if re.fullmatch(r"\d{4}\.\d", clean_row.get(resolved_x, "")):
                warnings.append(
                    f"Row {i + 1} ('{dp.name}') date '{clean_row[resolved_x]}' is read as "
                    f"month {clean_row[resolved_x][-1]}; write the month with two digits "
                    "(2018.01 for January, 2018.10 for October)."
                )
            warning = DataLoader._check_references(dp, i + 1, dataset_dir)
            if warning:
                warnings.append(warning)
            data_points.append(dp)
        return data_points

    @staticmethod
    def _check_numeric_columns(
        headers: list[str], resolved_x: str, data_points: list[ProductDataPoint]
    ) -> None:
        """A metric column may be blank on some rows, but never non-numeric."""
        for header in headers:
            if header == resolved_x or header in RESERVED_COLUMNS:
                continue
            for point in data_points:
                raw = point.raw_data.get(header, "").strip()
                value = point.metrics.get(header)
                if raw and (value is None or not math.isfinite(value)):
                    if not any(header in p.metrics for p in data_points):
                        break  # a text column (e.g. 'teams'), not a metric
                    raise ValueError(
                        f"Row {point.index + 1} ('{point.name}') has a non-numeric metric "
                        f"'{header}': '{raw}'"
                    )

    @staticmethod
    def _check_row_set(data_points: list[ProductDataPoint]) -> None:
        """At least one released row, and speculation rows only after the last of them."""
        if not data_points:
            raise ValueError("No valid product iterations loaded from data.csv.")
        flags = [point.is_speculation for point in data_points]
        if all(flags):
            raise ValueError("data.csv must contain at least one non-speculation row.")
        if True in flags and not all(flags[flags.index(True) :]):
            first = data_points[flags.index(True)]
            raise ValueError(
                f"Speculation row '{first.name}' is dated before a released row. "
                "Speculation rows must come after every released generation."
            )

    @staticmethod
    def load(dataset_dir: str, x_metric: str) -> LoadedCsv:
        """Loads, parses, validates and chronologically sorts ``data.csv``."""
        csv_path: str = os.path.join(dataset_dir, "data.csv")
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Missing data.csv in dataset directory: {dataset_dir}")

        warnings: list[str] = []
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader: csv.DictReader[str] = csv.DictReader(f)
            headers = DataLoader._read_headers(
                list(reader.fieldnames) if reader.fieldnames else None
            )
            resolved_x = DataLoader._resolve_metric(x_metric, headers, allow_year_date_swap=True)
            if resolved_x is None or resolved_x in RESERVED_COLUMNS:
                raise ValueError(f"X-metric '{x_metric}' not found in CSV columns: {headers}")
            if resolved_x != x_metric:
                logger.debug(f"Resolved X-metric '{x_metric}' to CSV column '{resolved_x}'.")
            data_points = DataLoader._parse_rows(reader, headers, resolved_x, dataset_dir, warnings)

        DataLoader._check_numeric_columns(headers, resolved_x, data_points)

        # Chronological order (X values are already validated as finite numbers).
        data_points.sort(key=lambda dp: dp.get_metric(resolved_x))

        DataLoader._check_row_set(data_points)

        if not any(
            header != resolved_x
            and header not in RESERVED_COLUMNS
            and any(header in point.metrics for point in data_points)
            for header in headers
        ):
            raise ValueError(
                "data.csv must contain at least one numeric metric besides the X axis."
            )
        return LoadedCsv(data_points, resolved_x, warnings)

    @staticmethod
    def load_dataset(dataset_dir: str, x_metric: str) -> list[ProductDataPoint]:
        """Loads data.csv from directory, parses it, validates assets, and sorts chronologically."""
        return DataLoader.load(dataset_dir, x_metric).points


#: Smallest ratio drawn on a log axis; values at or below zero are clamped here.
LOG_RATIO_FLOOR: float = 1e-2


class Dataset:
    """A loaded, validated timeline plus its precomputed plotting series.

    ``points`` are chronologically sorted; each point's ``order`` attribute is
    its index in this list. Y values are expressed relative to the first
    generation: ``ratio = value / first_value``. On the ``"log"`` scale the
    plotted coordinate is ``log10(ratio)`` (so ×2 and ×0.5 are equidistant
    from the ×1 baseline); on the ``"linear"`` scale it is the ratio itself.
    ``"auto"`` picks log when every plotted value is positive, else linear.

    A metric may start after the first generation (blank cells before its first
    value): its curve then starts at that generation, and its ratios are
    relative to its own first value. ``has(point, metric)`` tells whether a
    point is on the metric's curve.

    A blank cell between two values is filled by linear interpolation along the
    X axis between the previous and the next known value. ``value(point,
    metric)`` returns the known or interpolated value, and
    ``is_interpolated(point, metric)`` tells which one it is.
    """

    def __init__(
        self,
        points: list[ProductDataPoint],
        x_metric: str,
        y_metrics: list[str],
        y_scale: str = "auto",
    ) -> None:
        if not points:
            raise ValueError("A dataset needs at least one data point.")
        if y_scale not in ("auto", "log", "linear"):
            raise ValueError(f"Invalid y_scale '{y_scale}'. Expected 'auto', 'log' or 'linear'.")
        self.points: list[ProductDataPoint] = points
        self.x_metric: str = x_metric
        self.y_metrics: list[str] = y_metrics
        #: Human-readable data-quality notes (surfaced by ``fromversionzero validate``).
        self.warnings: list[str] = []
        self._check_coverage(points, y_metrics)
        #: Index of the first point carrying each metric (its curve starts there).
        self.start: dict[str, int] = {
            m: next(i for i, p in enumerate(points) if m in p.metrics) for m in y_metrics
        }
        y_scale = self._resolve_scale(points, y_metrics, y_scale)
        self.y_scale: str = y_scale
        for order, point in enumerate(points):
            point.order = order
        self._x: list[float] = [p.get_metric(x_metric) for p in points]
        #: Indices of points whose value of each metric is interpolated.
        self.interpolated: dict[str, set[int]] = {}
        self._values: dict[str, list[float]] = {m: self._fill(m) for m in y_metrics}
        self._y: dict[str, list[float]] = {m: self._normalize(m) for m in y_metrics}

    @staticmethod
    def _check_coverage(points: list[ProductDataPoint], y_metrics: list[str]) -> None:
        """Every metric needs a value somewhere, and one on the last row."""
        missing = [m for m in y_metrics if not any(m in p.metrics for p in points)]
        if missing:
            raise ValueError(f"Metrics {missing} have no value in any row.")
        last_rows = [m for m in y_metrics if m not in points[-1].metrics]
        if last_rows:
            raise ValueError(f"Metrics {last_rows} must have a value on the last row.")

    def _resolve_scale(
        self, points: list[ProductDataPoint], y_metrics: list[str], y_scale: str
    ) -> str:
        """Picks the Y scale for ``"auto"`` and records zero/negative warnings."""
        non_positive = [m for m in y_metrics if any(p.metrics.get(m, 1.0) <= 0.0 for p in points)]
        if not non_positive:
            return "log" if y_scale == "auto" else y_scale
        if y_scale == "auto":
            self.warnings.append(
                f"Linear Y axis used because {non_positive} contain zero/negative values "
                "(a log axis needs positive values)."
            )
            return "linear"
        if y_scale == "log":
            self.warnings.append(
                f"{non_positive} contain zero/negative values; clamped to ×{LOG_RATIO_FLOOR:g}."
            )
        return y_scale

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @classmethod
    def load(
        cls,
        dataset_dir: str,
        x_metric: str = "date",
        metrics: tuple[str, ...] | list[str] | None = None,
        include_speculation: bool = True,
        y_scale: str = "auto",
    ) -> "Dataset":
        """Loads ``data.csv`` and selects the plotted metrics.

        ``metrics`` picks and orders the plotted columns (fuzzy-matched like
        ``x_metric``). By default every plottable numeric column is plotted, in
        CSV order. A column is plottable when its first value is on a released
        generation and the last row has a value: leading blanks are allowed (the
        curve starts later) and blanks in between are interpolated.
        """
        loaded = DataLoader.load(dataset_dir, x_metric)
        points, resolved_x = loaded.points, loaded.x_metric
        if not include_speculation:
            points = [p for p in points if not p.is_speculation]
        available = cls._plottable_metrics(points, resolved_x)
        if metrics:
            selected: list[str] = []
            for requested in metrics:
                resolved = DataLoader._resolve_metric(requested, available)
                if resolved is None:
                    raise ValueError(
                        f"Metric '{requested}' is not a plottable numeric column "
                        "(it needs a value on a released row and on the last row). "
                        f"Available: {available}"
                    )
                if resolved not in selected:
                    selected.append(resolved)
        else:
            selected = available
        if not selected:
            raise ValueError(
                "data.csv must contain at least one numeric metric with a value on a "
                "released row and on the last row."
            )
        dataset = cls(points, resolved_x, selected, y_scale)
        dataset.warnings[:0] = loaded.warnings
        return dataset

    @staticmethod
    def _plottable_metrics(points: list[ProductDataPoint], x_metric: str) -> list[str]:
        """Numeric columns, in CSV order, starting on a released row and set on the last row."""
        columns: list[str] = []
        for p in points:
            columns.extend(m for m in p.metrics if m != x_metric and m not in columns)
        last_released = max((i for i, p in enumerate(points) if not p.is_speculation), default=-1)
        plottable = []
        for m in columns:
            present = [m in p.metrics for p in points]
            first = present.index(True)
            if first <= last_released and present[-1]:
                plottable.append(m)
        return plottable

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------
    def _fill(self, metric: str) -> list[float]:
        """Values from the metric's first point on; blank cells are interpolated
        linearly along X between the previous and next known values."""
        start = self.start[metric]
        known = [i for i in range(start, len(self.points)) if metric in self.points[i].metrics]
        values: list[float] = []
        gaps: set[int] = set()
        nxt = 0
        for i in range(start, len(self.points)):
            if metric in self.points[i].metrics:
                values.append(self.points[i].metrics[metric])
                nxt += 1
                continue
            lo, hi = known[nxt - 1], known[nxt]
            v_lo, v_hi = self.points[lo].metrics[metric], self.points[hi].metrics[metric]
            span = self._x[hi] - self._x[lo]
            k = (self._x[i] - self._x[lo]) / span if span else (i - lo) / (hi - lo)
            values.append(v_lo + (v_hi - v_lo) * k)
            gaps.add(i)
        self.interpolated[metric] = gaps
        return values

    def _normalize(self, metric: str) -> list[float]:
        """Plotted Y per point; points before the metric's first value repeat its
        first coordinate, so interpolation and camera bounds stay well defined."""
        start = self.start[metric]
        values = self._values[metric]
        if self.y_scale == "linear":
            base = values[0] if values[0] != 0.0 else max(values)
            ys = [0.0 for _ in values] if base == 0.0 else [v / base for v in values]
        else:
            base = next((v for v in values if v > 0.0), 0.0)
            ys = (
                [0.0 for _ in values]
                if base == 0.0
                else [math.log10(max(v / base, LOG_RATIO_FLOOR)) for v in values]
            )
        return [ys[0]] * start + ys

    def has(self, point: ProductDataPoint, metric: str) -> bool:
        """Whether ``point`` is on the metric's curve (False before its first value)."""
        return point.order >= self.start[metric]

    def value(self, point: ProductDataPoint, metric: str) -> float:
        """Known or interpolated value of ``metric`` at ``point`` (on its curve)."""
        return self._values[metric][point.order - self.start[metric]]

    def is_interpolated(self, point: ProductDataPoint, metric: str) -> bool:
        return point.order in self.interpolated[metric]

    def y(self, point: ProductDataPoint, metric: str) -> float:
        """Plotted Y coordinate of ``metric`` at ``point``."""
        return self._y[metric][point.order]

    def x(self, point: ProductDataPoint) -> float:
        return self._x[point.order]

    def ratio_to_y(self, ratio: float) -> float:
        """Converts a multiple of the first generation to a plotted Y coordinate."""
        if self.y_scale == "linear":
            return ratio
        return math.log10(max(ratio, LOG_RATIO_FLOOR))

    def y_to_ratio(self, y: float) -> float:
        return y if self.y_scale == "linear" else 10.0**y

    # ------------------------------------------------------------------
    # Partitions
    # ------------------------------------------------------------------
    @property
    def released(self) -> list[ProductDataPoint]:
        return [p for p in self.points if not p.is_speculation]

    @property
    def speculative(self) -> list[ProductDataPoint]:
        return [p for p in self.points if p.is_speculation]

    def __len__(self) -> int:
        return len(self.points)
