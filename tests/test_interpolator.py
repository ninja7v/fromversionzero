# tests/test_interpolator.py
# MIT License - Copyright (c) 2026 Luc Prevost
# Easing curve and damped camera.

import pytest

from fromversionzero.interpolator import CameraViewport, ease_sigmoid


def test_ease_sigmoid_is_clamped_monotonic_and_symmetric() -> None:
    assert ease_sigmoid(-1) == 0.0 and ease_sigmoid(0) == 0.0
    assert ease_sigmoid(1) == 1.0 and ease_sigmoid(2) == 1.0
    assert ease_sigmoid(0.5) == pytest.approx(0.5)
    samples = [ease_sigmoid(i / 20) for i in range(21)]
    assert samples == sorted(samples)
    assert ease_sigmoid(0.2) == pytest.approx(1 - ease_sigmoid(0.8))


def test_camera_converges_toward_target_frame_rate_independently() -> None:
    def run(fps: int) -> tuple[float, float, float, float]:
        camera = CameraViewport(0, 10, 0, 1)
        camera.set_target(10, 20, 1, 2)
        for _ in range(fps):  # one second
            camera.update(1 / fps, speed=3.0)
        return camera.get_bounds()

    at_30, at_60 = run(30), run(60)
    assert at_30 == pytest.approx(at_60)
    assert 0 < at_30[0] < 10  # moved, not snapped


def test_camera_never_overshoots() -> None:
    camera = CameraViewport(0, 1, 0, 1)
    camera.set_target(5, 6, 5, 6)
    for _ in range(500):
        camera.update(1 / 30, speed=10.0)
        assert camera.get_bounds()[0] <= 5
