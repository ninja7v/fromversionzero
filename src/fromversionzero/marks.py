# src/fromversionzero/marks.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Turns a logo or product photo into FromVersionZero "signal" line art.

"""Stylizes third-party media into FromVersionZero line art.

Luminance is posterized into a few bands, the borders between bands become
thin contour lines with a soft glow, and the darkest band gets a halftone
dot fill. The lines take the colours of the source where they run (or one fixed
colour); texture, typography and fine detail are discarded.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageChops, ImageFilter

# "signal" theme palette from the generator (themes.py).
FILL = "#D6FF3F"
SUPERSAMPLE = 3


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected #RRGGBB, got {hex_color!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _foreground(img: Image.Image) -> np.ndarray:
    """Alpha mask of the subject: real alpha, else everything unlike the corners."""
    rgba = np.asarray(img.convert("RGBA"), dtype=np.float32) / 255.0
    alpha = rgba[..., 3]
    if float(np.min(alpha)) < 0.99:
        return alpha
    rgb = rgba[..., :3]
    corners = np.stack([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
    background = np.median(corners, axis=0)
    distance = np.sqrt(((rgb - background) ** 2).sum(axis=-1))
    return np.clip((distance - 0.06) / 0.10, 0.0, 1.0)


def _bands(img: Image.Image, mask: np.ndarray, levels: int, invert: bool) -> np.ndarray:
    """Posterized luminance bands inside the mask; -1 marks background."""
    lum = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    if invert:
        lum = 1.0 - lum
    inside = mask > 0.5
    labels = np.full(lum.shape, -1, dtype=np.int16)
    if not inside.any():
        return labels
    # Quantile thresholds so every band covers a similar share of the subject.
    edges = np.quantile(lum[inside], np.linspace(0, 1, levels + 1)[1:-1])
    labels[inside] = np.digitize(lum[inside], edges).astype(np.int16)
    return _despeckle(labels, levels)


def _morph(mask: np.ndarray, radius: int) -> np.ndarray:
    """Opening then closing: drops specks and fills pinholes up to ``radius``."""
    img = Image.fromarray(mask.astype(np.uint8) * 255)
    k = 2 * radius + 1
    img = img.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))
    img = img.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
    return np.asarray(img) > 127


def _despeckle(labels: np.ndarray, levels: int) -> np.ndarray:
    """Cleans every band so contours follow shapes, not texture or noise."""
    radius = SUPERSAMPLE * 2
    clean = labels.copy()
    subject = _morph(labels >= 0, radius)
    clean[~subject] = -1
    # Darker bands claim pixels first; brighter bands are drawn over them.
    for band in range(levels):
        clean[_morph(labels == band, radius) & subject] = band
    label_img = Image.fromarray((clean + 1).astype(np.uint8) * 40)
    label_img = label_img.filter(ImageFilter.ModeFilter(2 * SUPERSAMPLE + 1))
    return (np.asarray(label_img) // 40).astype(np.int16) - 1


def _contours(labels: np.ndarray) -> np.ndarray:
    """Boolean map of pixels whose band differs from a 4-neighbour."""
    edge = np.zeros(labels.shape, dtype=bool)
    edge[:-1, :] |= labels[:-1, :] != labels[1:, :]
    edge[1:, :] |= labels[1:, :] != labels[:-1, :]
    edge[:, :-1] |= labels[:, :-1] != labels[:, 1:]
    edge[:, 1:] |= labels[:, 1:] != labels[:, :-1]
    return edge


def _halftone(shape: tuple[int, int], region: np.ndarray, pitch: int) -> np.ndarray:
    """Dot grid clipped to ``region``."""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    cy = (yy % pitch) - pitch / 2
    cx = (xx % pitch) - pitch / 2
    dots = (cx**2 + cy**2) <= (pitch * 0.22) ** 2
    return dots & region


def _spread(rgb: np.ndarray, weight: Image.Image, radius: float) -> tuple[np.ndarray, np.ndarray]:
    """Weighted blur of ``rgb``: the mean colour of nearby weighted pixels, and their weight."""
    blur = ImageFilter.GaussianBlur(radius)
    share = np.asarray(weight, dtype=np.float32)[..., None] / 255.0
    spread = np.asarray(Image.fromarray((rgb * share).astype(np.uint8)).filter(blur), np.float32)
    total = np.asarray(weight.filter(blur), dtype=np.float32)[..., None]
    return spread / np.maximum(total, 1.0) * 255.0, total


def _source_colors(src: Image.Image, radius: float) -> Image.Image:
    """The subject's own colours, spread past its outline so a contour drawn on
    the edge is not washed out by the background colour."""
    rgb = np.asarray(src.convert("RGB"), dtype=np.float32)
    subject = Image.fromarray((_foreground(src) > 0.99).astype(np.uint8) * 255)
    # The antialiased rim is already mixed with the background: prefer the
    # inside of each shape, and fall back to the rim for strokes too thin to have one.
    inside = subject.filter(ImageFilter.MinFilter(2 * SUPERSAMPLE + 1))
    colors = rgb
    for weight in (subject, inside):
        mean, total = _spread(rgb, weight, radius)
        colors = np.where(total > 8.0, mean, colors)
    return Image.fromarray(np.clip(colors, 0, 255).astype(np.uint8)).convert("RGBA")


def stylize(
    source: Image.Image,
    size: int = 512,
    levels: int = 3,
    line: str | None = None,
    fill: str = FILL,
    dots: bool = True,
    invert: bool = False,
) -> Image.Image:
    """Returns an RGBA mark fitted in a ``size`` x ``size`` box (aspect kept).

    Lines are drawn in the source's colours, or in ``line`` (``#RRGGBB``) when given.
    """
    src = source.convert("RGBA")
    src.thumbnail((size, size), Image.Resampling.LANCZOS)
    sharp = src.resize(
        (src.width * SUPERSAMPLE, src.height * SUPERSAMPLE), Image.Resampling.LANCZOS
    )
    work = sharp.filter(ImageFilter.GaussianBlur(SUPERSAMPLE * 1.2))

    mask = _foreground(work)
    labels = _bands(work, mask, levels, invert)
    stroke = max(1, round(SUPERSAMPLE * size / 400))

    edge = Image.fromarray(_contours(labels).astype(np.uint8) * 255)
    edge = edge.filter(ImageFilter.MaxFilter(2 * stroke + 1))
    glow = edge.filter(ImageFilter.GaussianBlur(SUPERSAMPLE * 4)).point(lambda v: v * 0.55)
    line_alpha = ImageChops.lighter(edge, glow)

    canvas = Image.new("RGBA", work.size, (0, 0, 0, 0))
    if dots:
        darkest = labels == 0
        dot_mask = _halftone(labels.shape, darkest, pitch=SUPERSAMPLE * max(5, size // 64))
        dot_alpha = Image.fromarray(dot_mask.astype(np.uint8) * 150)
        canvas = Image.composite(
            Image.new("RGBA", work.size, (*_rgb(fill), 255)), canvas, dot_alpha
        )
    if line is None:
        ink = _source_colors(sharp, radius=stroke + SUPERSAMPLE * 2)
    else:
        ink = Image.new("RGBA", work.size, (*_rgb(line), 255))
    canvas = Image.composite(ink, canvas, line_alpha)

    out = canvas.resize(src.size, Image.Resampling.LANCZOS)
    bbox = out.getbbox()
    return out.crop(bbox) if bbox else out
