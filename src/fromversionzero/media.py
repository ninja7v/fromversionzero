# src/fromversionzero/media.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Downloads linked logos and product images and caches their stylized marks.

"""Remote media for datasets.

A dataset may reference its logo (``config.json`` -> ``media.logo``) and its
generation images (``data.csv`` -> ``image``) by URL instead of shipping the
files. At render time the source is downloaded once, turned into
FromVersionZero line art by :func:`fromversionzero.marks.stylize`, and both are
cached on disk, so later renders work offline.

Cache layout (default ``~/.cache/fromversionzero/media``)::

    <sha256(url)>.src          downloaded bytes
    <sha256(url+options)>.png  stylized mark
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import ipaddress
import logging
import os
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

from fromversionzero import __version__
from fromversionzero.marks import stylize

logger = logging.getLogger(__name__)

CACHE_ENV = "FVZ_MEDIA_CACHE"
#: Set to ``1`` to allow links to private, loopback or link-local addresses.
ALLOW_PRIVATE_ENV = "FVZ_ALLOW_PRIVATE_MEDIA"
MAX_BYTES = 20 * 1024 * 1024
#: Decoded size limit: a small file can expand to a huge bitmap.
MAX_PIXELS = 64_000_000
TIMEOUT = 30
USER_AGENT = f"FromVersionZero/{__version__} (+https://github.com/ninja7v/fromversionzero)"


def is_url(ref: str) -> bool:
    return ref.startswith(("http://", "https://"))


def default_cache_dir() -> Path:
    if os.environ.get(CACHE_ENV):
        return Path(os.environ[CACHE_ENV])
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "fromversionzero" / "media"


def check_public_url(url: str) -> None:
    """Raises ``ValueError`` unless ``url`` is http(s) and its host is a public address.

    Links come with datasets written by other people. Without this check a
    dataset could make the renderer request services on the local machine or
    network (``http://localhost:...``, cloud metadata addresses). The host is
    resolved here and again by the HTTP client, so a DNS server that changes
    its answer between the two can still get through; this is a guard against
    plain mistakes and simple abuse, not a sandbox.
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("only http(s) links are allowed")
    if os.environ.get(ALLOW_PRIVATE_ENV) == "1":
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 0, type=socket.SOCK_STREAM)
    except OSError as e:
        raise ValueError(f"cannot resolve host '{parts.hostname}' ({e})") from None
    for info in infos:
        address = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if not address.is_global:
            raise ValueError(
                f"host '{parts.hostname}' is a private or local address "
                f"(set {ALLOW_PRIVATE_ENV}=1 to allow it)"
            )


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Applies :func:`check_public_url` to every redirect target."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        try:
            check_public_url(newurl)
        except ValueError as e:
            raise urllib.error.URLError(f"redirect refused: {e}") from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(request: urllib.request.Request, timeout: float) -> Any:
    """Opens ``request`` after checking its host (tests replace this function)."""
    check_public_url(request.full_url)
    return urllib.request.build_opener(_CheckedRedirects).open(request, timeout=timeout)


def _write_atomic(path: Path, data: bytes) -> None:
    """Writes through a uniquely named temp file, so concurrent renders that
    fill the same cache never see or clobber each other's partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


_ENTITY = re.compile(rb'<!ENTITY\s+([\w.-]+)\s+"([^"]*)"\s*>')
_DOCTYPE = re.compile(rb"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", re.DOTALL)


def _inline_entities(svg: bytes) -> bytes:
    """Expands internal string entities and drops the DOCTYPE.

    Illustrator exports declare namespaces as entities (``&ns_extend;``), which
    strict SVG parsers reject. Only literal internal entities are expanded;
    external entities are never resolved.
    """
    doctype = _DOCTYPE.search(svg)
    if not doctype:
        return svg
    entities = dict(_ENTITY.findall(doctype.group(0)))
    svg = svg[: doctype.start()] + svg[doctype.end() :]
    for name, value in entities.items():
        svg = svg.replace(b"&" + name + b";", value)
    return svg


def render_svg(svg: bytes, width: int, height: int | None = None) -> Image.Image:
    """Rasterizes SVG markup with resvg, which ships in its wheel (no system
    library) and renders identically on Linux, macOS and Windows."""
    import resvg_py

    markup = _inline_entities(svg).decode("utf-8", errors="replace")
    # resvg-py defaults to 0 DPI, which collapses mm/pt/in sizes to nothing.
    png = resvg_py.svg_to_bytes(svg_string=markup, width=width, height=height, dpi=96.0)
    return Image.open(io.BytesIO(bytes(png))).convert("RGBA")


def _decode(data: bytes, url: str, size: int) -> Image.Image:
    """Opens raster images with Pillow and SVG with :func:`render_svg`."""
    head = data[:512].lstrip().lower()
    if url.lower().split("?")[0].endswith(".svg") or head.startswith((b"<svg", b"<?xml")):
        return render_svg(data, size * 2)
    image = Image.open(io.BytesIO(data))
    if image.width * image.height > MAX_PIXELS:
        raise ValueError(f"image is too large ({image.width}x{image.height} pixels)")
    return image.convert("RGBA")


class MediaCache:
    """Fetches URLs once and keeps the raw bytes and stylized marks on disk."""

    def __init__(
        self, cache_dir: str | os.PathLike[str] | None = None, offline: bool = False
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        #: Never touch the network: only files already in the cache are used.
        #: Render workers run offline after the pipeline has prefetched every
        #: link once, so a broken link is not retried by each worker and every
        #: frame sees the same media.
        self.offline = offline
        self.failed: dict[str, str] = {}

    def fetch(self, url: str) -> bytes | None:
        """Raw bytes of ``url`` (from the cache when present), ``None`` on failure."""
        path = self.cache_dir / f"{_digest(url)}.src"
        if path.is_file():
            return path.read_bytes()
        if url in self.failed:
            return None
        if self.offline:
            self.failed[url] = "not in the media cache"
            return None
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with _open(request, TIMEOUT) as response:
                data = response.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.failed[url] = str(e)
            logger.warning(f"Could not download {url}: {e}")
            return None
        if len(data) > MAX_BYTES:
            self.failed[url] = "file larger than 20 MB"
            logger.warning(f"Skipping {url}: file larger than 20 MB")
            return None
        try:
            _write_atomic(path, data)
        except OSError as e:  # read-only or full cache: still usable for this run
            logger.warning(f"Could not cache {url}: {e}")
        return data

    def mark(
        self,
        url: str,
        *,
        levels: int,
        invert: bool = False,
        stylized: bool = True,
        size: int = 512,
        line: str | None = None,
    ) -> Image.Image | None:
        """The FromVersionZero mark of ``url`` (or the plain image when ``stylized`` is False)."""
        key = f"{url}|{levels}|{invert}|{stylized}|{size}|{line or 'source'}"
        path = self.cache_dir / f"{_digest(key)}.png"
        if path.is_file():
            try:
                return Image.open(path).convert("RGBA")
            except OSError:
                path.unlink(missing_ok=True)
        data = self.fetch(url)
        if data is None:
            return None
        try:
            source = _decode(data, url, size)
            image = (
                stylize(source, size=size, levels=levels, invert=invert, line=line)
                if stylized
                else source
            )
        except Exception as e:  # noqa: BLE001 - any decoding/stylizing problem falls back
            self.failed[url] = f"unreadable image ({e})"
            logger.warning(f"Could not read the image at {url}: {e}")
            return None
        try:
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            _write_atomic(path, buffer.getvalue())
        except OSError as e:
            logger.warning(f"Could not cache the mark of {url}: {e}")
        return image
