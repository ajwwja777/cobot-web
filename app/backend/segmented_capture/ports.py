"""Small thread-safe ports used by the segmented capture overlay."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Iterator


class CaptureGate:
    """A close operation waits for an in-flight sample to leave the gate."""

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._lock = Lock()

    def open(self) -> None:
        with self._lock:
            self._enabled = True

    def close(self) -> None:
        with self._lock:
            self._enabled = False

    def is_open(self) -> bool:
        with self._lock:
            return self._enabled

    @contextmanager
    def sample_permission(self) -> Iterator[bool]:
        self._lock.acquire()
        try:
            yield self._enabled
        finally:
            self._lock.release()
