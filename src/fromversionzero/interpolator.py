# src/fromversionzero/interpolator.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Easing and the damped camera used for smooth reveals and pans.

import math


def ease_sigmoid(t: float) -> float:
    r"""Apply a smooth S-curve easing for soft acceleration and deceleration.

    This is used for camera transitions and reveal timing so motion feels
    cinematic rather than abrupt. The curve is implemented as a normalized
    logistic function over a clamped input $t \in [0, 1]$.
    """
    t = max(0.0, min(1.0, t))
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0

    x: float = 12.0 * (t - 0.5)
    raw: float = 1.0 / (1.0 + math.exp(-x))
    low: float = 1.0 / (1.0 + math.exp(6.0))
    high: float = 1.0 / (1.0 + math.exp(-6.0))
    return (raw - low) / (high - low)


class CameraViewport:
    """Manages the current view box (xmin, xmax, ymin, ymax) with smooth lag/interpolation."""

    def __init__(self, xmin: float, xmax: float, ymin: float, ymax: float) -> None:
        self.current_xmin: float = xmin
        self.current_xmax: float = xmax
        self.current_ymin: float = ymin
        self.current_ymax: float = ymax

        self.target_xmin: float = xmin
        self.target_xmax: float = xmax
        self.target_ymin: float = ymin
        self.target_ymax: float = ymax

    def set_target(self, xmin: float, xmax: float, ymin: float, ymax: float) -> None:
        self.target_xmin = xmin
        self.target_xmax = xmax
        self.target_ymin = ymin
        self.target_ymax = ymax

    def update(self, dt: float, speed: float = 3.0) -> None:
        r"""Update the viewport smoothly with frame-rate independent damping.

        The camera follows its target using an exponential-decay step:
        $C_t = C_{t-1} + (T_t - C_{t-1}) \cdot (1 - e^{-\lambda dt})$.
        This keeps the pan/zoom motion fluid while still reacting to the latest
        revealed node bounds.
        """
        decay: float = 1.0 - math.exp(-speed * dt)
        self.current_xmin += (self.target_xmin - self.current_xmin) * decay
        self.current_xmax += (self.target_xmax - self.current_xmax) * decay
        self.current_ymin += (self.target_ymin - self.current_ymin) * decay
        self.current_ymax += (self.target_ymax - self.current_ymax) * decay

    def get_bounds(self) -> tuple[float, float, float, float]:
        return (
            self.current_xmin,
            self.current_xmax,
            self.current_ymin,
            self.current_ymax,
        )
