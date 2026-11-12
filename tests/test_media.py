# tests/test_media.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Linked media: line-art marks, download cache, fallbacks, config, loader and CLI.

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image, ImageDraw

from fromversionzero import media
from fromversionzero.asset_manager import AssetManager
from fromversionzero.cli import main
from fromversionzero.config import MediaSettings, VideoConfig
from fromversionzero.data_loader import DataLoader
from fromversionzero.marks import stylize
from fromversionzero.media import MediaCache, is_url

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><circle cx="50" cy="50" r="40" fill="#c33"/></svg>'


def png_bytes() -> bytes:
    img = Image.new("RGBA", (120, 120), (255, 255, 255, 255))
    ImageDraw.Draw(img).ellipse((15, 15, 105, 105), fill=(30, 30, 200, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeWeb:
    """Stands in for media._open; counts requests per URL."""

    def __init__(self, pages: dict[str, bytes]) -> None:
        self.pages = pages
        self.calls: dict[str, int] = {}

    def __call__(self, request: Any, timeout: float = 0) -> io.BytesIO:
        url = request.full_url
        self.calls[url] = self.calls.get(url, 0) + 1
        if url not in self.pages:
            raise urllib.error.URLError("not found")
        return io.BytesIO(self.pages[url])


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    fake = FakeWeb(
        {"https://example.org/logo.png": png_bytes(), "https://example.org/logo.svg": SVG}
    )
    monkeypatch.setattr(media, "_open", fake)
    return fake


def _line_pixels(mark: Image.Image) -> np.ndarray:
    pixels = np.asarray(mark)
    opaque = pixels[pixels[..., 3] > 200][:, :3].astype(int)
    assert len(opaque) > 0
    return opaque


def test_stylize_keeps_the_silhouette_and_the_source_colours() -> None:
    source = Image.open(io.BytesIO(png_bytes()))
    mark = stylize(source, size=128, dots=False)
    assert mark.mode == "RGBA" and max(mark.size) <= 128
    # The lines are drawn in the source's blue, not washed out by its background.
    assert (abs(_line_pixels(mark) - (30, 30, 200)) < 40).all()


def test_stylize_with_a_fixed_line_colour() -> None:
    source = Image.open(io.BytesIO(png_bytes()))
    mark = stylize(source, size=128, dots=False, line="#38E8FF")
    assert (abs(_line_pixels(mark) - (0x38, 0xE8, 0xFF)) < 30).all()


def test_line_colour_setting_and_cache_key(tmp_path: Path, web: FakeWeb) -> None:
    assert VideoConfig.from_dict({}).media.line is None
    assert VideoConfig.from_dict({"media": {"line_color": "#38E8FF"}}).media.line == "#38E8FF"
    with pytest.raises(ValueError, match="line_color"):
        VideoConfig.from_dict({"media": {"line_color": "blue"}})
    cache, url = MediaCache(tmp_path), "https://example.org/logo.png"
    own = cache.mark(url, levels=3)
    fixed = cache.mark(url, levels=3, line="#38E8FF")
    assert own is not None and fixed is not None
    assert own.tobytes() != fixed.tobytes()


def test_is_url() -> None:
    assert is_url("https://a.b/c.png") and is_url("http://a.b")
    assert not is_url("images/v1.png") and not is_url("ftp://x")


def test_cache_downloads_once_and_works_offline(tmp_path: Path, web: FakeWeb) -> None:
    cache = MediaCache(tmp_path)
    url = "https://example.org/logo.png"
    first = cache.mark(url, levels=3)
    assert first is not None
    assert MediaCache(tmp_path).mark(url, levels=3) is not None
    assert web.calls[url] == 1
    # A different style reuses the downloaded source.
    assert cache.mark(url, levels=2) is not None
    assert web.calls[url] == 1


def test_svg_sources_are_rasterized(tmp_path: Path, web: FakeWeb) -> None:
    mark = MediaCache(tmp_path).mark("https://example.org/logo.svg", levels=3, size=128)
    assert mark is not None and mark.getchannel("A").getbbox() is not None


def test_svg_internal_entities_are_inlined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svg = (
        b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY ns_svg "http://www.w3.org/2000/svg">]>'
        b'<svg xmlns="&ns_svg;" width="100" height="100"><rect x="10" y="10" width="80"'
        b' height="80" fill="#222"/></svg>'
    )
    monkeypatch.setattr(media, "_open", FakeWeb({"https://e.org/a.svg": svg}))
    assert MediaCache(tmp_path).mark("https://e.org/a.svg", levels=3, size=128) is not None


def test_unstylized_media_is_returned_as_is(tmp_path: Path, web: FakeWeb) -> None:
    image = MediaCache(tmp_path).mark("https://example.org/logo.png", levels=3, stylized=False)
    assert image is not None and image.size == (120, 120)


def test_broken_links_fail_softly(tmp_path: Path, web: FakeWeb) -> None:
    cache = MediaCache(tmp_path)
    url = "https://example.org/missing.png"
    assert cache.mark(url, levels=3) is None
    assert cache.mark(url, levels=3) is None
    assert web.calls[url] == 1 and url in cache.failed


def test_unreadable_downloads_fail_softly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "_open", FakeWeb({"https://example.org/x.png": b"not an image"}))
    cache = MediaCache(tmp_path)
    assert cache.mark("https://example.org/x.png", levels=3) is None
    assert "unreadable" in cache.failed["https://example.org/x.png"]


def test_oversized_downloads_are_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "MAX_BYTES", 10)
    monkeypatch.setattr(media, "_open", FakeWeb({"https://example.org/big.png": png_bytes()}))
    assert MediaCache(tmp_path).fetch("https://example.org/big.png") is None


def test_asset_manager_uses_linked_logo_and_images(
    tmp_path: Path, web: FakeWeb, widget_dir: Path
) -> None:
    settings = MediaSettings(logo="https://example.org/logo.svg")
    assets = AssetManager(str(widget_dir), media=settings, media_cache=MediaCache(tmp_path))
    logo = assets.get_product_logo(96)
    assert logo is not None and logo.size == (96, 96)
    assert assets.get_product_image("https://example.org/logo.png") is not None
    assert assets.get_product_image("https://example.org/missing.png") is None
    assert assets.prefetch(["https://example.org/logo.png", "https://example.org/missing.png"]) == [
        "https://example.org/missing.png"
    ]


def test_local_logo_path_in_media_settings(tmp_path: Path, widget_dir: Path) -> None:
    Image.open(io.BytesIO(png_bytes())).save(widget_dir / "brand.png")
    assets = AssetManager(
        str(widget_dir), media=MediaSettings(logo="brand.png"), media_cache=MediaCache(tmp_path)
    )
    assert assets.get_product_logo(64) is not None


def test_media_settings_are_validated_and_round_trip() -> None:
    config = VideoConfig.from_dict({"media": {"logo": "https://x.y/l.svg", "logo_levels": 4}})
    assert config.media.logo == "https://x.y/l.svg" and config.media.logo_levels == 4
    assert VideoConfig.from_dict(config.to_dict()).media == config.media
    with pytest.raises(ValueError, match="logo_levels"):
        VideoConfig.from_dict({"media": {"logo_levels": 9}})
    with pytest.raises(ValueError, match="media"):
        VideoConfig.from_dict({"media": {"unknown": 1}})


def test_loader_accepts_image_urls(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text(
        "name,year,p,image\nA,2000,1,https://example.org/a.png\nB,2001,2,\n", encoding="utf-8"
    )
    points = DataLoader.load_dataset(str(tmp_path), "year")
    assert points[0].image_filename == "https://example.org/a.png"


def run(args: list[str]) -> int:
    with pytest.raises(SystemExit) as exit_info:
        main(args)
    return int(exit_info.value.code or 0)


def test_validate_check_media_reports_broken_links(
    widget_dir: Path,
    web: FakeWeb,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(media.CACHE_ENV, str(tmp_path / "cache"))
    config = json.loads((widget_dir / "config.json").read_text(encoding="utf-8"))
    config["media"] = {"logo": "https://example.org/missing.svg"}
    (widget_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    assert run(["validate", "--product", str(widget_dir)]) == 0
    assert run(["validate", "--product", str(widget_dir), "--check-media", "--strict"]) == 1
    assert "Linked media failed" in capsys.readouterr().out


def test_validate_rejects_missing_local_logo(widget_dir: Path) -> None:
    config = json.loads((widget_dir / "config.json").read_text(encoding="utf-8"))
    config["media"] = {"logo": "nope.png"}
    (widget_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    assert run(["validate", "--product", str(widget_dir)]) == 1


def test_mark_command(tmp_path: Path, web: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(media.CACHE_ENV, str(tmp_path / "cache"))
    source = tmp_path / "in.png"
    source.write_bytes(png_bytes())
    assert run(["mark", str(source), str(tmp_path / "out.png"), "--size", "96"]) == 0
    assert Image.open(tmp_path / "out.png").size[0] <= 96
    assert run(["mark", "https://example.org/logo.svg", str(tmp_path / "web.png")]) == 0
    assert run(["mark", "https://example.org/missing.png", str(tmp_path / "x.png")]) == 1


@pytest.mark.parametrize("unit", ["mm", "pt", "in"])
def test_svg_with_physical_units_is_rasterized(unit: str) -> None:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="40{unit}" height="20{unit}" '
        f'viewBox="0 0 40 20"><rect width="40" height="20" fill="#222"/></svg>'
    ).encode()
    image = media.render_svg(svg, 200)
    assert image.width == 200
    assert abs(image.height - 100) <= 2  # intrinsic size is rounded to whole pixels
    assert image.getchannel("A").getbbox() is not None


# ----------------------------------------------------------------------
# Links are untrusted input
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/logo.png",
        "http://localhost/logo.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/logo.png",
        "http://10.0.0.5/logo.png",
        "ftp://example.org/logo.png",
        "file:///etc/passwd",
    ],
)
def test_private_and_non_http_links_are_refused(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(media.ALLOW_PRIVATE_ENV, raising=False)
    with pytest.raises(ValueError):
        media.check_public_url(url)
    if is_url(url):
        cache = MediaCache(tmp_path)
        assert cache.fetch(url) is None and url in cache.failed
        assert not list(tmp_path.glob("*.src"))


def test_private_links_can_be_allowed_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(media.ALLOW_PRIVATE_ENV, "1")
    media.check_public_url("http://127.0.0.1:8080/logo.png")


def test_redirects_to_private_addresses_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(media.ALLOW_PRIVATE_ENV, raising=False)
    handler = media._CheckedRedirects()
    request = media.urllib.request.Request("https://example.org/a.png")
    with pytest.raises(urllib.error.URLError, match="redirect refused"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1/secret")


def test_offline_cache_reads_cached_media_but_never_downloads(tmp_path: Path, web: FakeWeb) -> None:
    url, other = "https://example.org/logo.png", "https://example.org/logo.svg"
    assert MediaCache(tmp_path).mark(url, levels=3) is not None
    offline = MediaCache(tmp_path, offline=True)
    assert offline.mark(url, levels=3) is not None
    assert offline.mark(other, levels=3) is None
    assert web.calls == {url: 1}


def test_cache_writes_leave_no_temporary_files(tmp_path: Path, web: FakeWeb) -> None:
    assert MediaCache(tmp_path).mark("https://example.org/logo.png", levels=3) is not None
    assert sorted(p.suffix for p in tmp_path.iterdir()) == [".png", ".src"]


def test_images_with_too_many_pixels_are_refused(
    tmp_path: Path, web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media, "MAX_PIXELS", 100)
    cache = MediaCache(tmp_path)
    assert cache.mark("https://example.org/logo.png", levels=3) is None
    assert "too large" in cache.failed["https://example.org/logo.png"]
