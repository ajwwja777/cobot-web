"""Thread-safe, copy-on-write cache for values received from ROS callbacks.

This module deliberately has no ROS dependency.  Subscription wiring can
convert ROS message objects to plain arrays/scalars and call :meth:`put`.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from math import nan
from threading import Lock
from types import MappingProxyType

import numpy as np

from .topics import REQUIRED_TOPICS, freshness_window_seconds


def _copy_value(value: object) -> object:
    """Deep-copy mutable payloads without sharing callback or snapshot state."""
    if isinstance(value, np.ndarray):
        return np.array(value, copy=True)
    return deepcopy(value)


@dataclass(frozen=True)
class CacheEntry:
    """A copied stream value and the clocks recorded at receipt time."""

    value: object
    source_stamp: float
    arrival_stamp: float
    sequence: int = 0


class CacheSnapshot:
    """An immutable-in-structure point-in-time view of all cached streams."""

    def __init__(self, now: float, entries: Mapping[str, CacheEntry]) -> None:
        self.now = float(now)
        self._entries = MappingProxyType(dict(entries))

    @property
    def entries(self) -> Mapping[str, CacheEntry]:
        return self._entries

    def get(self, key: str) -> object | None:
        """Return a private copy so a consumer cannot mutate this snapshot."""
        entry = self._entries.get(key)
        return None if entry is None else _copy_value(entry.value)

    def has_value(self, key: str) -> bool:
        """Return whether a stream has published at least one cached value."""
        return key in self._entries

    def source_timestamp(self, key: str) -> float:
        entry = self._entries.get(key)
        return nan if entry is None else entry.source_stamp

    def arrival_timestamp(self, key: str) -> float:
        entry = self._entries.get(key)
        return nan if entry is None else entry.arrival_stamp

    def sequence(self, key: str) -> int:
        entry = self._entries.get(key)
        return 0 if entry is None else int(entry.sequence)

    def is_fresh(self, key: str) -> bool:
        entry = self._entries.get(key)
        if entry is None:
            return False
        age = self.now - entry.arrival_stamp
        return 0.0 <= age <= freshness_window_seconds(key)


class LatestMessageCache:
    """Own the latest copied value for each Task2 stream.

    Values are copied on insertion and again for snapshots, which keeps ROS
    callback buffers, cache state, snapshots, and later HDF5 frames isolated.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[str, CacheEntry] = {}
        self._sequences: dict[str, int] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def put(
        self, key: str, value: object, source_stamp: float, arrival_stamp: float
    ) -> None:
        """Store a copied value and source/arrival timestamps for ``key``."""
        if key not in REQUIRED_TOPICS:
            raise KeyError(f"unknown Task2 stream: {key}")
        with self._lock:
            sequence = self._sequences.get(key, 0) + 1
            self._sequences[key] = sequence
            self._entries[key] = CacheEntry(
                value=_copy_value(value),
                source_stamp=float(source_stamp),
                arrival_stamp=float(arrival_stamp),
                sequence=sequence,
            )

    def snapshot_keys(self):
        with self._lock:
            return tuple(self._entries)

    def snapshot(self, now: float, keys=None) -> CacheSnapshot:
        """Copy all current entries under one lock and evaluate freshness at ``now``."""
        with self._lock:
            entries = {
                key: CacheEntry(
                    _copy_value(entry.value), entry.source_stamp, entry.arrival_stamp, entry.sequence
                )
                for key, entry in self._entries.items() if keys is None or key in keys
            }
        return CacheSnapshot(now=now, entries=entries)
