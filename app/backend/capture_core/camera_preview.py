"""Read-only, rate-bounded MJPEG previews sourced from the ROS cache."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import cv2
import numpy as np

from .ros_cache import LatestMessageCache
from .topics import CAMERA_KEYS

MAX_PREVIEW_FPS = 15.0
MJPEG_BOUNDARY = b"frame"


class CameraPreviewUnavailable(RuntimeError):
    """The requested camera has no valid fresh frame for preview."""


def encode_latest_jpeg(
    cache: LatestMessageCache,
    key: str,
    *,
    quality: int = 70,
    now: float | None = None,
) -> bytes:
    """Copy and JPEG-encode the newest fresh BGR camera frame."""
    if key not in CAMERA_KEYS:
        raise ValueError(f"invalid camera key: {key}")
    if isinstance(quality, bool) or not isinstance(quality, int) or not 1 <= quality <= 95:
        raise ValueError("quality must be an integer between 1 and 95")
    snapshot = cache.snapshot(time.monotonic() if now is None else now)
    if not snapshot.is_fresh(key):
        raise CameraPreviewUnavailable(f"camera frame is stale: {key}")
    image = snapshot.get(key)
    if (
        not isinstance(image, np.ndarray)
        or image.dtype != np.uint8
        or image.ndim != 3
        or image.shape[-1] != 3
    ):
        raise CameraPreviewUnavailable(f"camera frame is invalid: {key}")
    success, encoded = cv2.imencode(
        ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    )
    if not success:
        raise CameraPreviewUnavailable(f"camera JPEG encode failed: {key}")
    return bytes(encoded)


def _mjpeg_chunk(jpeg: bytes) -> bytes:
    return (
        b"--"
        + MJPEG_BOUNDARY
        + b"\r\nContent-Type: image/jpeg\r\n\r\n"
        + jpeg
        + b"\r\n"
    )


def iter_mjpeg(
    cache: LatestMessageCache,
    key: str,
    *,
    fps: float = MAX_PREVIEW_FPS,
    quality: int = 70,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], object] = time.sleep,
    stop: Callable[[], bool] | None = None,
) -> Iterator[bytes]:
    """Return a generator capped at camera rate that ends on stale input."""
    if not np.isfinite(fps) or fps <= 0 or fps > MAX_PREVIEW_FPS:
        raise ValueError(f"fps must be in (0, {MAX_PREVIEW_FPS}]")
    if key not in CAMERA_KEYS:
        raise ValueError(f"invalid camera key: {key}")
    period = 1.0 / float(fps)
    should_stop = stop or (lambda: False)

    def generate() -> Iterator[bytes]:
        while not should_stop():
            started = clock()
            try:
                jpeg = encode_latest_jpeg(
                    cache, key, quality=quality, now=started
                )
            except CameraPreviewUnavailable:
                return
            yield _mjpeg_chunk(jpeg)
            delay = period - (clock() - started)
            if delay > 0:
                sleeper(delay)

    return generate()
