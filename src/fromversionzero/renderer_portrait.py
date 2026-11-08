# src/fromversionzero/renderer_portrait.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Portrait (Shorts) composition: large product hero above the graph, bigger
# type, no logos/facts/intro/outro. Design canvas is 1080 units wide.

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from fromversionzero.components import draw_product_hero_shorts

if TYPE_CHECKING:
    from fromversionzero.renderer import Renderer
    from fromversionzero.timeline import FrameState

r"""
Portrait layout (design units, 1080 wide):
 ________________________________
|          PRODUCT NAME          |
|                                |
|       image / generation       |
|             mark               |
|    ▬ LEGEND                    |
| ×10 - - - - - - - - - -/ ●     |
|  ×2 - - - - - - ______/  value |
|  ×1 ___________/               |
|     |_______________|________  |
|     1964            2004       |
|        “Brand motto”           |
|_______(platform UI zone)_______|
"""

HERO_TOP, HERO_MAX_H, HERO_SIDE_MARGIN = 240, 500, 40


def render_frame_shorts(r: Renderer, state: FrameState) -> np.ndarray:
    canvas, draw = r.new_canvas()
    with r.faded(r.graph_alpha_for(state)):
        r.draw_ambient(draw, state.t)
        r.draw_grid_and_axes(draw, state.camera_bounds, state.revealed_pcts)
        r.draw_curves(canvas, draw, state.revealed_pcts, state.camera_bounds)
        if r.config.show_legend:
            r.draw_legend(draw, state.revealed_pcts)

        last = r.last_revealed_index(state.revealed_pcts)
        if last >= 0:
            u = r.unit
            draw_product_hero_shorts(
                r,
                canvas,
                draw,
                r.data_points[last],
                r.data_points[last - 1] if last > 0 else None,
                state.revealed_pcts[last],
                state.t,
                cx=r.draw_w / 2,
                top=HERO_TOP * u,
                max_w=r.draw_w - 2 * HERO_SIDE_MARGIN * u,
                max_h=HERO_MAX_H * u,
            )
        r.draw_motto(draw)
    return r.to_frame(canvas)
