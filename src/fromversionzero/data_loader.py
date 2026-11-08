# src/data_loader.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Parses data.csv files into validated, chronologically sorted product data points.

import csv
import logging
import math
import os
import re
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


def parse_date_to_fractional_year(val: str) -> tuple[float, bool]:
    """Parses date string formatted as YYYY.MM (e.g. 2018.09) to a continuous float.

    Returns (fractional_year, has_month). has_month is True when the input used
    the YYYY.MM form (even for January, which would otherwise be indistinguishable
    from a bare integer year).
    """
    clean = val.strip()
    match = re.match(r"^(\d{4})\.(\d{1,2})$", clean)
    if match:
        year = int(match.group(1))
        month = int(match.group(2))
        if 1 <= month <= 12:
            return year + (month - 1) / 12.0, True
    return float(clean), False


class ProductDataPoint:
    """Represents a single iteration/version of a product."""

    def __init__(self, raw_row: dict[str, str], index: int) -> None:
        self.index: int = index
        #: Position in the loaded, sorted :class:`Dataset` (set by ``Dataset``).
        self.order: int = index
        self.raw_data: dict[str, str] = raw_row
        self.name: str = raw_row.get("name", f"Version {index}")

        # We will parse values dynamically
        self.metrics: dict[str, float] = {}
        self.image_filename: str = raw_row.get("image", "")
        self.fact: str = raw_row.get("fact", "")
        self.sfx_filename: str = raw_row.get("sfx", "")
        self.has_month: bool = False
        # True when this row is a speculation about an unreleased generation.
        self.is_speculation: bool = False

        # Try to parse all columns as floats where possible
        for col, val in raw_row.items():
            if col in ["name", "image", "fact", "sfx"]:
                continue
            if col.lower() == "speculation":
                self.is_speculation = val.strip().lower() in (
                    "true",
                    "yes",
                    "1",
                    "y",
                    "t",
                )
                continue
            try:
                # Remove commas, dollar signs, spaces, etc.
                clean_val: str = val.replace("$", "").replace(",", "").strip()
                if col.lower() in ["date", "year"]:
                    parsed, has_month = parse_date_to_fractional_year(clean_val)
                    self.metrics[col] = parsed
                    if has_month:
                        self.has_month = True
                else:
                    self.metrics[col] = float(clean_val)
            except ValueError:
                # Keep string metrics if needed, but they won't be plot-axes metrics
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
        return self.metrics.get(name, default)

    def __repr__(self) -> str:
        return f"<ProductDataPoint '{self.name}' metrics={self.metrics}>"


class DataLoader:
    """Handles loading, parsing, sorting, and validating evolution datasets."""

    NON_METRIC_COLUMNS: ClassVar[set[str]] = {"name", "image", "fact", "sfx", "speculation"}

    @staticmethod
    def _normalize_metric_key(metric_name: str) -> str:
        clean = metric_name.lower().strip()
        clean = re.sub(r"[^a-z0-9]", "", clean)
        return clean

    @staticmethod
    def _find_matching_header(requested_metric: str, headers: list[str]) -> str | None:
        normalized_request: str = DataLoader._normalize_metric_key(requested_metric)
        candidates: list[str] = []

        header: str
        for header in headers:
            normalized_header: str = DataLoader._normalize_metric_key(header)
            if normalized_header == normalized_request:
                return header

        for header in headers:
            normalized_header = DataLoader._normalize_metric_key(header)
            if normalized_request in normalized_header or normalized_header in normalized_request:
                candidates.append(header)

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            # Prefer the shortest matching header to avoid overly broad matches
            return min(candidates, key=lambda h: len(DataLoader._normalize_metric_key(h)))

        # Fallback to token intersection matching
        req_tokens: set[Any] = set(re.findall(r"[a-z0-9]+", normalized_request))
        for header in headers:
            header_tokens: set[Any] = set(
                re.findall(r"[a-z0-9]+", DataLoader._normalize_metric_key(header))
            )
            if req_tokens and req_tokens.issubset(header_tokens):
                return header

        return None

    @staticmethod
    def _resolve_metric(
        metric: str, headers: list[str], allow_year_date_swap: bool = False
    ) -> str | None:
        """Resolves a metric name to a CSV column, with fuzzy matching."""
        if metric in headers:
            return metric
        resolved = DataLoader._find_matching_header(metric, headers)
        if resolved is not None:
            return resolved
        if allow_year_date_swap:
            if metric == "year" and "date" in headers:
                return "date"
            if metric == "date" and "year" in headers:
                return "year"
        return None

    @staticmethod
    def _resolve_all_metrics(
        headers: list[str],
        x_metric: str,
    ) -> str | None:
        """Resolves the x metric against CSV headers."""
        return DataLoader._resolve_metric(x_metric, headers, allow_year_date_swap=True)

    @staticmethod
    def _validate_resolved_metrics(
        headers: list[str],
        x_metric: str,
        resolved_x: str | None,
    ) -> str:
        """Validates the resolved X metric and returns it."""
        if resolved_x is None:
            raise ValueError(f"X-metric '{x_metric}' not found in CSV columns: {headers}")
        if resolved_x != x_metric:
            logger.debug(f"Resolved X-metric '{x_metric}' to CSV column '{resolved_x}'.")
        return resolved_x

    @staticmethod
    def _parse_rows(
        reader: csv.DictReader,
        headers: list[str],
        resolved_x: str,
        images_dir: str,
    ) -> list[ProductDataPoint]:
        """Parses CSV rows into ProductDataPoint objects with validation."""
        data_points: list[ProductDataPoint] = []
        for i, row in enumerate(reader):
            clean_row: dict[str | Any, str | Any] = {
                k.strip(): (v.strip() if v is not None else "")
                for k, v in row.items()
                if k is not None
            }
            dp = ProductDataPoint(clean_row, i)
            if not dp.name.strip():
                raise ValueError(f"Row {i + 1} has a blank required 'name' value.")
            if resolved_x not in dp.metrics:
                raise ValueError(
                    f"Row {i + 1} ('{dp.name}') has a missing or non-numeric X-metric '{resolved_x}': '{clean_row.get(resolved_x)}'"
                )
            if dp.image_filename and not dp.image_filename.startswith(("http://", "https://")):
                img_path: str = os.path.join(images_dir, dp.image_filename)
                if not os.path.exists(img_path):
                    raise ValueError(
                        f"Row {i + 1} ('{dp.name}') references missing image: {img_path}"
                    )
            data_points.append(dp)
        return data_points

    @staticmethod
    def _check_numeric_columns(
        headers: list[str], resolved_x: str, data_points: list[ProductDataPoint]
    ) -> None:
        """A metric column may be blank on some rows, but never non-numeric."""
        for header in headers:
            if header == resolved_x or header.lower() in DataLoader.NON_METRIC_COLUMNS:
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
    def _load_dataset_impl(
        dataset_dir: str,
        x_metric: str,
    ) -> tuple[list[ProductDataPoint], str]:
        """Core implementation: loads, parses, validates and sorts data.csv.

        Returns ``(data_points, resolved_x_metric)``.
        """
        csv_path: str = os.path.join(dataset_dir, "data.csv")
        images_dir: str = os.path.join(dataset_dir, "images")

        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Missing data.csv in dataset directory: {dataset_dir}")

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader: csv.DictReader[str] = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("data.csv is empty or has no headers.")
            headers: list[str] = [h.strip() for h in reader.fieldnames]

            resolved_x = DataLoader._validate_resolved_metrics(
                headers, x_metric, DataLoader._resolve_all_metrics(headers, x_metric)
            )
            data_points = DataLoader._parse_rows(reader, headers, resolved_x, images_dir)

        DataLoader._check_numeric_columns(headers, resolved_x, data_points)

        # Chronological order (X values are already validated as numeric).
        data_points.sort(key=lambda dp: dp.get_metric(resolved_x))

        if not data_points:
            raise ValueError("No valid product iterations loaded from data.csv.")
        if not any(not point.is_speculation for point in data_points):
            raise ValueError("data.csv must contain at least one non-speculation row.")

        y_metrics = [
            header
            for header in headers
            if header != resolved_x
            and header.lower() not in DataLoader.NON_METRIC_COLUMNS
            and any(header in point.metrics for point in data_points)
        ]
        if not y_metrics:
            raise ValueError(
                "data.csv must contain at least one numeric metric besides the X axis."
            )

        return data_points, resolved_x

    @staticmethod
    def load_dataset(
        dataset_dir: str,
        x_metric: str,
    ) -> list[ProductDataPoint]:
        """Loads data.csv from directory, parses it, validates assets, and sorts chronologically."""
        data_points, _ = DataLoader._load_dataset_impl(dataset_dir, x_metric)
        return data_points

    @staticmethod
    def load_dataset_with_metric(
        dataset_dir: str,
        x_metric: str,
    ) -> tuple[list[ProductDataPoint], str]:
        """Like :meth:`load_dataset` but also returns the resolved X-metric column name.

        Use this when the caller needs to know which CSV column was ultimately
        chosen as the X axis (e.g. ``'date'`` resolved from a requested ``'year'``).
        """
        return DataLoader._load_dataset_impl(dataset_dir, x_metric)


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
        points, resolved_x = DataLoader.load_dataset_with_metric(dataset_dir, x_metric)
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
        return cls(points, resolved_x, selected, y_scale)

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
