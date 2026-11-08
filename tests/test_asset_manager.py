# tests/test_asset_manager.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Fonts (variable-font weights), units, icons, product images and logos.

from pathlib import Path

import numpy as np
import pytest
from conftest import WIDGET_DIR
from PIL import Image, ImageDraw

from fromversionzero.asset_manager import AssetManager, fit_in_square, text_logo


def ink_width(font_name: str) -> int:
    """Number of inked pixels for a fixed word: heavier weights ink more."""
    font = AssetManager(".").get_font(font_name, 40)
    image = Image.new("L", (400, 60))
    ImageDraw.Draw(image).text((0, 0), "WEIGHT", fill=255, font=font)
    return int((np.asarray(image) > 128).sum())


def test_font_names_select_real_weights_of_the_variable_font() -> None:
    """Regression: all roles used to render Montserrat Thin (the variable default)."""
    thin, regular, bold = (ink_width(f"Montserrat-{w}") for w in ("Thin", "Regular", "Bold"))
    assert thin < regular < bold


def test_font_roles_and_caching() -> None:
    manager = AssetManager(".")
    assert manager.get_font("heading", 12, 2) is manager.get_font("heading", 12, 2)
    assert manager.get_font("heading", 12, 2).size == 24
    fallback = manager.get_font("NoSuchFont-Bold", 10)
    assert fallback is not None


def test_units_merge_category_then_product(widget_dir: Path) -> None:
    (widget_dir / "units.json").write_text('{"power": "kW"}', encoding="utf-8")
    manager = AssetManager(str(widget_dir))
    assert manager.get_metric_unit("power") == "kW"  # product override
    assert manager.get_metric_unit("Units Sold") == "units"  # normalized category key
    assert manager.get_metric_unit("unknown") == ""


def test_invalid_units_file_is_reported(widget_dir: Path) -> None:
    (widget_dir / "units.json").write_text('{"power": 3}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid metric units file"):
        AssetManager(str(widget_dir))


def test_metric_icons_keywords_and_dataset_override(widget_dir: Path) -> None:
    manager = AssetManager(str(widget_dir))
    assert Path(manager._metric_icon_path("top_speed") or "").name == "speed.svg"
    assert Path(manager._metric_icon_path("units_sold") or "").name == "hashtag.svg"
    assert Path(manager._metric_icon_path("prize_money") or "").name == "dollar.svg"
    assert Path(manager._metric_icon_path("0_to_60") or "").name == "acceleration.svg"
    assert manager.get_metric_icon("mystery_metric", 32) is None
    icon = manager.get_metric_icon("price", 32)
    assert icon is not None and icon.size == (32, 32)

    (widget_dir / "icons").mkdir()
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(widget_dir / "icons" / "mystery_metric.png")
    custom = AssetManager(str(widget_dir)).get_metric_icon("mystery_metric", 16)
    assert custom is not None and custom.getpixel((4, 4)) == (255, 0, 0, 255)


@pytest.mark.parametrize(
    ("category", "product", "icon"),
    [
        ("AI", "ChatGPT", "ai.svg"),
        ("Bike", "Aeroad", "bike.svg"),
        ("Vehicle", "Porsche_911", "car.svg"),
        ("Vehicle", "Honda_CBR", "motorcycle.svg"),
        ("Tech", "Apple_Watch", "watch.svg"),
        ("Tech", "iPhone", "smartphone.svg"),
        ("Tech", "MacBook", "laptop.svg"),
        ("Gadgets", "Widget", None),
    ],
)
def test_category_icon_mapping(
    tmp_path: Path, category: str, product: str, icon: str | None
) -> None:
    folder = tmp_path / category / product
    folder.mkdir(parents=True)
    path = AssetManager(str(folder)).category_icon_path()
    assert (Path(path).name if path else None) == icon


def test_product_images_and_logos(widget_dir: Path) -> None:
    manager = AssetManager(str(widget_dir))
    assert manager.get_product_image("") is None
    assert manager.get_product_image("missing.png") is None
    resized = manager.get_resized_product_image("w1.png", 80, 80)
    assert resized is not None and max(resized.size) == 80
    assert manager.get_resized_product_image("w1.png", 80, 80) is resized  # cached
    assert manager.get_product_logo(64) is None
    mark = manager.get_brand_mark(64)
    assert mark is not None and mark.size == (64, 64)
    Image.new("RGB", (40, 20), "white").save(widget_dir / "logo.png")
    assert AssetManager(str(widget_dir)).get_product_logo(64).size == (64, 64)  # type: ignore[union-attr]


def test_square_fitting_helpers() -> None:
    fitted = fit_in_square(Image.new("RGBA", (200, 50), (255, 255, 255, 255)), 100)
    assert fitted.size == (100, 100)
    assert fitted.getpixel((50, 50))[3] == 255 and fitted.getpixel((50, 5))[3] == 0
    logo = text_logo("ACME", AssetManager(".").get_font("heading", 20), 120, (255, 0, 0))
    assert logo.size == (120, 120) and logo.getbbox() is not None


def test_fixture_dataset_is_self_contained() -> None:
    assert (WIDGET_DIR / "images" / "w1.png").is_file()


def test_fonts_use_the_same_layout_engine_on_every_os() -> None:
    """Raqm is only used where libfribidi is installed; the basic engine is everywhere."""
    from PIL import ImageFont

    manager = AssetManager(str(WIDGET_DIR))
    for role in ("heading", "body", "label", "display"):
        assert manager.get_font(role, 12).layout_engine == ImageFont.Layout.BASIC
