# src/fromversionzero/asset_manager.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Discovery, loading and caching of fonts, metric icons, units, product images and logos.

from __future__ import annotations

import json
import logging
import os
from importlib import resources
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from fromversionzero.media import MediaCache, is_url, render_svg

if TYPE_CHECKING:
    from fromversionzero.config import MediaSettings

logger = logging.getLogger(__name__)

#: Pillow uses Raqm when the system has libfribidi (most Linux distributions)
#: and its basic engine otherwise (macOS, Windows), and glyphs land on slightly
#: different pixels. Always using the basic engine, which ships with Pillow and
#: handles kerning, makes text identical on every OS. The videos only show
#: left-to-right text, which needs no complex shaping.
LAYOUT = ImageFont.Layout.BASIC

RESOURCES_DIR: str = str(resources.files("fromversionzero.resources"))
PACKAGED_ICONS_DIR: str = os.path.join(RESOURCES_DIR, "icons")
BRAND_MARK_PATH: str = os.path.join(RESOURCES_DIR, "branding", "FromVersionZero.png")

#: Metric-name keyword -> packaged icon. First match wins.
METRIC_ICON_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("weight", "weight.svg"),
    ("power", "horse.svg"),
    ("hp", "horse.svg"),
    ("horse", "horse.svg"),
    ("acceleration", "acceleration.svg"),
    ("accel", "acceleration.svg"),
    ("0_to_", "acceleration.svg"),
    ("speed", "speed.svg"),
    ("price", "dollar.svg"),
    ("cost", "dollar.svg"),
    ("usd", "dollar.svg"),
    ("money", "dollar.svg"),
    ("fuel", "fuel.svg"),
    ("drag", "drag.svg"),
    ("sold", "hashtag.svg"),
    ("units", "hashtag.svg"),
    ("count", "hashtag.svg"),
)


#: Local product images are downscaled to this longest side when loaded. The
#: largest on-screen use (the Shorts hero of a 4K render) is about 4000 px.
MAX_IMAGE_SIDE = 4096


def normalize_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


class AssetManager:
    """Loads and caches every asset a renderer needs for one dataset.

    Icons are looked up in a dataset-level ``icons/`` folder first (to let a
    dataset override or add icons), then in the icons shipped with the package.
    """

    def __init__(
        self,
        dataset_dir: str,
        media: MediaSettings | None = None,
        media_cache: MediaCache | None = None,
        offline: bool = False,
    ) -> None:
        from fromversionzero.config import MediaSettings

        self.dataset_dir = dataset_dir
        self.media: MediaSettings = media or MediaSettings()
        self.media_cache: MediaCache = media_cache or MediaCache(offline=offline)
        self.font_roles: dict[str, str] = {
            "display": "Montserrat-Light",
            "heading": "Montserrat-SemiBold",
            "body": "Montserrat-Regular",
            "label": "Montserrat-Medium",
        }
        self.font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self.image_cache: dict[str, Image.Image | None] = {}
        self.resized_cache: dict[tuple[str, int, int], Image.Image] = {}
        self.icon_cache: dict[tuple[str, int], Image.Image] = {}
        self._icon_paths: dict[str, str | None] = {}
        self.icon_dirs: list[str] = [
            d
            for d in (os.path.join(dataset_dir, "icons"), os.path.join(dataset_dir, "Icons"))
            if os.path.isdir(d)
        ] + [PACKAGED_ICONS_DIR]
        self.metric_units = self._load_metric_units()

    # ------------------------------------------------------------------
    # Fonts
    # ------------------------------------------------------------------
    def _font_file(self, name: str) -> tuple[str | None, str | None]:
        """Resolves ``name`` to (file, variable-font instance name).

        ``Family-Weight`` uses ``Family-Weight.ttf`` when present, otherwise the
        named instance ``Weight`` of ``Family-Variable.ttf`` (the bundled
        Montserrat variable font serves Montserrat-Light/-Medium/-Bold...).
        """
        if os.path.isabs(name):
            return (name if os.path.isfile(name) else None), None
        for folder in (os.path.join(self.dataset_dir, "fonts"), RESOURCES_DIR):
            static = os.path.join(folder, f"{name}.ttf")
            if os.path.isfile(static):
                return static, None
            family, _, weight = name.partition("-")
            variable = os.path.join(folder, f"{family}-Variable.ttf")
            if weight and os.path.isfile(variable):
                return variable, weight
        return None, None

    def get_font(self, role_or_name: str, size: float, unit: int = 1) -> ImageFont.FreeTypeFont:
        """Font for a role or font name, ``size`` in design units scaled by ``unit``."""
        name = self.font_roles.get(role_or_name, role_or_name)
        pixel_size = max(1, round(size * unit))
        key = (name, pixel_size)
        if key in self.font_cache:
            return self.font_cache[key]
        path, instance = self._font_file(name)
        font: ImageFont.FreeTypeFont | None = None
        if path is not None:
            font = ImageFont.truetype(path, pixel_size, layout_engine=LAYOUT)
            if instance:
                try:
                    font.set_variation_by_name(instance)
                except (OSError, ValueError) as e:
                    logger.warning(f"Font '{path}' has no '{instance}' instance: {e}")
        if font is None:
            try:
                # System font lookup.
                font = ImageFont.truetype(f"{name}.ttf", pixel_size, layout_engine=LAYOUT)
            except OSError:
                logger.warning(f"Font '{name}' not found; using Pillow's default font.")
                font = ImageFont.load_default(pixel_size)  # type: ignore[assignment]
        assert font is not None
        self.font_cache[key] = font
        return font

    # ------------------------------------------------------------------
    # Units
    # ------------------------------------------------------------------
    def _load_metric_units(self) -> dict[str, str]:
        """Category ``units.json`` first, then per-product overrides."""
        units: dict[str, str] = {}
        for path in (
            os.path.join(os.path.dirname(self.dataset_dir), "units.json"),
            os.path.join(self.dataset_dir, "units.json"),
        ):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as file:
                    loaded = json.load(file)
                if not isinstance(loaded, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in loaded.items()
                ):
                    raise ValueError("expected an object containing string values")
            except (OSError, json.JSONDecodeError, ValueError) as e:
                raise ValueError(f"Invalid metric units file '{path}': {e}") from e
            units.update({normalize_key(k): v for k, v in loaded.items()})
        return units

    def get_metric_unit(self, metric_name: str) -> str:
        return self.metric_units.get(normalize_key(metric_name), "")

    # ------------------------------------------------------------------
    # Icons
    # ------------------------------------------------------------------
    def _find_icon(self, filename: str) -> str | None:
        for directory in self.icon_dirs:
            path = os.path.join(directory, filename)
            if os.path.isfile(path):
                return path
        return None

    def _metric_icon_path(self, metric_name: str) -> str | None:
        """Icon file for a metric (remembered: it is asked for every drawn point)."""
        if metric_name not in self._icon_paths:
            self._icon_paths[metric_name] = self._search_metric_icon(metric_name)
        return self._icon_paths[metric_name]

    def _search_metric_icon(self, metric_name: str) -> str | None:
        key = normalize_key(metric_name)
        # An icon named after the metric (e.g. icons/units_sold.svg) wins.
        for directory in self.icon_dirs:
            for candidate in sorted(os.listdir(directory)):
                stem, ext = os.path.splitext(candidate)
                if ext.lower() in (".svg", ".png") and normalize_key(stem) == key:
                    return os.path.join(directory, candidate)
        for keyword, filename in METRIC_ICON_KEYWORDS:
            if keyword in metric_name.lower():
                return self._find_icon(filename)
        return None

    def load_icon(self, path: str, size: int) -> Image.Image:
        cache_key = (path, size)
        if cache_key in self.icon_cache:
            return self.icon_cache[cache_key]
        if path.lower().endswith(".svg"):
            with open(path, "rb") as f:
                image = render_svg(f.read(), size, size)
        else:
            image = Image.open(path).convert("RGBA")
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        self.icon_cache[cache_key] = image
        return image

    def get_metric_icon(self, metric_name: str, size: int) -> Image.Image | None:
        """Icon (alpha = shape) for a metric, or ``None`` to draw a plain point."""
        path = self._metric_icon_path(metric_name)
        if path is None:
            return None
        try:
            return self.load_icon(path, size)
        except (OSError, ValueError) as e:
            logger.debug(f"Icon '{path}' failed to load: {e}")
            return None

    def category_icon_path(self) -> str | None:
        """Product-type icon for placeholders, chosen from the category/product folder name."""
        category = os.path.basename(os.path.dirname(os.path.abspath(self.dataset_dir))).lower()
        product = os.path.basename(os.path.abspath(self.dataset_dir)).lower()
        filename = ""
        if category == "ai":
            filename = "ai.svg"
        elif category == "bike":
            filename = "bike.svg"
        elif category == "vehicle":
            moto_terms = ("motorcycle", "ninja", "cbr", "zx-10r")
            filename = "motorcycle.svg" if any(t in product for t in moto_terms) else "car.svg"
        elif category == "tech":
            if "watch" in product:
                filename = "watch.svg"
            elif "macbook" in product or "laptop" in product:
                filename = "laptop.svg"
            elif any(
                t in product for t in ("phone", "iphone", "galaxy_s", "galaxy_a", "ipad", "tab")
            ):
                filename = "smartphone.svg"
        return self._find_icon(filename) if filename else None

    def get_category_icon(self, size: int, color: tuple[int, int, int]) -> Image.Image | None:
        path = self.category_icon_path()
        if path is None:
            return None
        try:
            icon = self.load_icon(path, size)
        except (OSError, ValueError) as e:
            logger.debug(f"Category icon failed to load: {e}")
            return None
        tinted = Image.new("RGBA", icon.size, (*color, 255))
        tinted.putalpha(icon.getchannel("A"))
        return tinted

    # ------------------------------------------------------------------
    # Product images and logos
    # ------------------------------------------------------------------
    def get_product_image(self, filename: str) -> Image.Image | None:
        """The product image from ``images/`` or a URL, or ``None`` when absent/unreadable.

        URLs are downloaded once and redrawn as FromVersionZero line art
        (see :mod:`fromversionzero.media`).
        """
        if not filename:
            return None
        if filename in self.image_cache:
            return self.image_cache[filename]
        if is_url(filename):
            remote = self.media_cache.mark(
                filename,
                levels=self.media.image_levels,
                invert=self.media.image_invert,
                stylized=self.media.stylize,
                line=self.media.line,
            )
            self.image_cache[filename] = remote
            return remote
        path = os.path.join(self.dataset_dir, "images", filename)
        image: Image.Image | None = None
        if os.path.isfile(path):
            try:
                with Image.open(path) as source:
                    # Decode large photos at reduced size: every worker keeps its own copy.
                    source.draft("RGB", (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                    image = source.convert("RGBA")
                image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
            except (OSError, ValueError) as e:
                logger.warning(f"Error loading image {filename}: {e}")
        self.image_cache[filename] = image
        return image

    def get_resized_product_image(
        self, filename: str, max_w: int, max_h: int
    ) -> Image.Image | None:
        """Product image fitted inside ``max_w`` x ``max_h`` (cached per size)."""
        key = (filename, max_w, max_h)
        if key in self.resized_cache:
            return self.resized_cache[key]
        image = self.get_product_image(filename)
        if image is None:
            return None
        ratio = min(max_w / image.width, max_h / image.height)
        size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        resized = image.resize(size, Image.Resampling.LANCZOS)
        self.resized_cache[key] = resized
        return resized

    def _load_fitted(self, path: str, box: int, cache_key: str) -> Image.Image | None:
        if cache_key in self.image_cache:
            return self.image_cache[cache_key]
        fitted: Image.Image | None = None
        try:
            if path.lower().endswith(".svg"):
                fitted = self.load_icon(path, box)
            else:
                fitted = fit_in_square(Image.open(path).convert("RGBA"), box)
        except (OSError, ValueError) as e:
            logger.debug(f"Logo '{path}' failed to load: {e}")
        self.image_cache[cache_key] = fitted
        return fitted

    def prefetch(self, image_refs: list[str]) -> list[str]:
        """Downloads and stylizes every remote image and the logo; returns failed URLs.

        Called once before rendering so parallel workers find everything cached.
        """
        urls = [ref for ref in image_refs if is_url(ref)]
        failed = [url for url in urls if self.get_product_image(url) is None]
        if self.media.logo and is_url(self.media.logo) and self.get_product_logo(64) is None:
            failed.append(self.media.logo)
        return failed

    def get_brand_mark(self, box: int) -> Image.Image | None:
        """The FromVersionZero mark fitted in a ``box`` square."""
        return self._load_fitted(BRAND_MARK_PATH, box, f"__fvz_mark_{box}")

    def get_product_logo(self, box: int) -> Image.Image | None:
        """The dataset logo fitted in a ``box`` square, if any.

        ``media.logo`` in ``config.json`` wins: a URL is downloaded and redrawn
        as line art, a relative path is read from the dataset folder. Otherwise
        a ``logo.(png|jpg|jpeg|svg)`` file in the dataset folder is used.
        """
        ref = self.media.logo
        if ref:
            if not is_url(ref):
                return self._load_fitted(
                    os.path.join(self.dataset_dir, ref), box, f"__product_logo_{box}"
                )
            cache_key = f"__product_logo_{box}"
            if cache_key not in self.image_cache:
                mark = self.media_cache.mark(
                    ref,
                    levels=self.media.logo_levels,
                    invert=self.media.logo_invert,
                    stylized=self.media.stylize,
                    line=self.media.line,
                )
                self.image_cache[cache_key] = fit_in_square(mark, box) if mark else None
            return self.image_cache[cache_key]
        for ext in (".png", ".jpg", ".jpeg", ".svg"):
            path = os.path.join(self.dataset_dir, f"logo{ext}")
            if os.path.isfile(path):
                return self._load_fitted(path, box, f"__product_logo_{box}")
        return None


def fit_in_square(image: Image.Image, box: int) -> Image.Image:
    """Fits ``image`` inside a transparent ``box`` x ``box`` square, centered."""
    ratio = min(box / image.width, box / image.height)
    size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    resized = image.resize(size, Image.Resampling.LANCZOS)
    square = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    square.paste(resized, ((box - size[0]) // 2, (box - size[1]) // 2), resized)
    return square


def text_logo(
    text: str, font: ImageFont.FreeTypeFont, box: int, color: tuple[int, int, int]
) -> Image.Image:
    """Outlined wordmark used when a dataset has no logo file."""
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    width = int(probe.textlength(text, font=font))
    pad = max(4, int(font.size * 0.8))
    image = Image.new("RGBA", (width + 2 * pad, int(font.size * 2.6)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [0, 0, image.width - 1, image.height - 1],
        radius=pad // 2,
        outline=(*color, 200),
        width=max(1, int(font.size) // 10),
    )
    draw.text(
        (pad, (image.height - font.size) / 2 - font.size * 0.1), text, fill=(*color, 255), font=font
    )
    return fit_in_square(image, box)
