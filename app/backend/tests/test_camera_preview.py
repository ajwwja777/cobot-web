"""Behavioral tests for bounded read-only MJPEG camera previews."""

import numpy as np
import pytest

from capture_core.camera_preview import (
    CameraPreviewUnavailable,
    encode_latest_jpeg,
    iter_mjpeg,
)
from capture_core.ros_cache import LatestMessageCache


def _camera_cache(arrival: float = 1.0) -> LatestMessageCache:
    cache = LatestMessageCache()
    image = np.zeros((8, 12, 3), dtype=np.uint8)
    image[:, :, 1] = 180
    cache.put(
        "camera_high", image, source_stamp=arrival, arrival_stamp=arrival
    )
    return cache


def test_encode_latest_jpeg_rejects_stale_frame():
    with pytest.raises(CameraPreviewUnavailable, match="stale"):
        encode_latest_jpeg(_camera_cache(), "camera_high", now=2.0)


def test_mjpeg_chunk_has_boundary_jpeg_payload_and_fifteen_fps_ceiling():
    sleeps = []

    def stop() -> bool:
        return bool(sleeps)

    stream = iter_mjpeg(
        _camera_cache(),
        "camera_high",
        fps=15.0,
        clock=lambda: 1.0,
        sleeper=sleeps.append,
        stop=stop,
    )

    chunk = next(stream)
    with pytest.raises(StopIteration):
        next(stream)

    assert chunk.startswith(
        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    )
    assert b"\xff\xd8" in chunk
    assert chunk.endswith(b"\r\n")
    assert sleeps == [pytest.approx(1.0 / 15.0)]


@pytest.mark.parametrize("fps", [0.0, -1.0, 15.01])
def test_preview_rate_must_be_positive_and_not_exceed_camera_rate(fps: float):
    with pytest.raises(ValueError, match="fps"):
        iter_mjpeg(_camera_cache(), "camera_high", fps=fps)


def test_preview_rejects_non_camera_cache_keys():
    with pytest.raises(ValueError, match="camera key"):
        encode_latest_jpeg(_camera_cache(), "front_left", now=1.0)
