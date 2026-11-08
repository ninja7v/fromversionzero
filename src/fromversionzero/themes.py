# src/fromversionzero/themes.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Visual identities. Each theme defines a complete palette (8 distinct curve
# colors so up to 8 metrics never share a color) and a font role mapping.

from typing import Any

# Font roles use ``Family-Weight`` names served from the bundled Montserrat
# variable font. "display" is large, elegant type (titles, years, generation
# marks); the other roles stay heavy enough to survive video compression.
_FONTS_DEFAULT: dict[str, str] = {
    "display": "Montserrat-Light",
    "heading": "Montserrat-SemiBold",
    "body": "Montserrat-Regular",
    "label": "Montserrat-Medium",
}

THEMES: dict[str, dict[str, Any]] = {
    # Signature look: near-black field, electric curves, lime accent.
    "signal": {
        "background": "#07090D",
        "grid": "#252A33",
        "grid_dots": "#3A424F",
        "line": "#38E8FF",
        "line_glow": "#38E8FF",
        "text_primary": "#F4F7FB",
        "text_secondary": "#8D98A8",
        "accent": "#D6FF3F",
        "accent_glow": "#D6FF3F",
        "fonts": dict(_FONTS_DEFAULT),
        "line_colors": [
            "#38E8FF",
            "#FF4F87",
            "#D6FF3F",
            "#FF9F43",
            "#9D7BFF",
            "#4DFFB8",
            "#5C8DFF",
            "#F2F2F2",
        ],
        "ambient": True,
    },
    # Technical drawing: deep navy, cyan ink, amber highlights.
    "blueprint": {
        "background": "#061522",
        "grid": "#173B55",
        "grid_dots": "#2A5E7D",
        "line": "#5CE1FF",
        "line_glow": "#5CE1FF",
        "text_primary": "#EAF8FF",
        "text_secondary": "#7EA9C2",
        "accent": "#FFC857",
        "accent_glow": "#FFC857",
        "fonts": {
            "display": "Montserrat-ExtraLight",
            "heading": "Montserrat-Medium",
            "body": "Montserrat-Regular",
            "label": "Montserrat-Regular",
        },
        "line_colors": [
            "#5CE1FF",
            "#4D8DFF",
            "#FFC857",
            "#FF6B8A",
            "#80FFDB",
            "#B89CFF",
            "#F28F3B",
            "#EAF8FF",
        ],
        "ambient": True,
    },
    # Editorial print: warm paper, ink-dark type, vermilion accent.
    "paper": {
        "background": "#F6F4EF",
        "grid": "#E3DED3",
        "grid_dots": "#CFC8BA",
        "line": "#1F5EFF",
        "line_glow": "#1F5EFF",
        "text_primary": "#15171C",
        "text_secondary": "#6B6F78",
        "accent": "#E4572E",
        "accent_glow": "#E4572E",
        "fonts": dict(_FONTS_DEFAULT),
        "line_colors": [
            "#1F5EFF",
            "#E4572E",
            "#0F9D76",
            "#C98A00",
            "#7B4BD6",
            "#D6336C",
            "#2B8CBE",
            "#3D3D3D",
        ],
        "ambient": False,
    },
}

#: Retired theme names and the theme that replaces them.
DEPRECATED_THEMES: dict[str, str] = {
    "dark_modern": "signal",
    "neon_cyber": "signal",
    "minimal": "signal",
    "light_modern": "paper",
}
