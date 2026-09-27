"""Thread-safe mode selection and single-writer ownership."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Optional
from uuid import uuid4

_VALID_MODES = frozenset(("normal", "rlt"))


class ModeConflict(RuntimeError):
    """A requested mode or writer transition is unsafe."""


@dataclass(frozen=True)
class WriterLease:
    token: str
    mode: str


@dataclass(frozen=True)
class ConsoleModeSnapshot:
    selected_mode: str
    active_mode: Optional[str]
    writer_token: Optional[str]
    episode_uuid: Optional[str]
    generation: int
    fault_reason: Optional[str]


class RecorderModeCoordinator:
    def __init__(self, *, initial_mode: str = "normal") -> None:
        if initial_mode not in _VALID_MODES:
            raise ValueError("invalid_mode")
        self._lock = RLock()
        self._selected_mode = initial_mode
        self._active_mode: Optional[str] = None
        self._writer_token: Optional[str] = None
        self._episode_uuid: Optional[str] = None
        self._generation = 0
        self._fault_reason: Optional[str] = None
        self._last_released_token: Optional[str] = None

    def snapshot(self) -> ConsoleModeSnapshot:
        with self._lock:
            return ConsoleModeSnapshot(
                selected_mode=self._selected_mode,
                active_mode=self._active_mode,
                writer_token=self._writer_token,
                episode_uuid=self._episode_uuid,
                generation=self._generation,
                fault_reason=self._fault_reason,
            )

    def select(self, mode: str) -> ConsoleModeSnapshot:
        if mode not in _VALID_MODES:
            raise ValueError("invalid_mode")
        with self._lock:
            if self._writer_token is not None and mode != self._selected_mode:
                raise ModeConflict("writer_busy")
            if mode != self._selected_mode:
                self._selected_mode = mode
                self._generation += 1
            return self.snapshot()

    def acquire_writer(
        self, mode: str, episode_uuid: Optional[str] = None
    ) -> WriterLease:
        if mode not in _VALID_MODES:
            raise ValueError("invalid_mode")
        with self._lock:
            if mode != self._selected_mode:
                raise ModeConflict("mode_not_selected")
            if self._writer_token is not None:
                raise ModeConflict("writer_busy")
            lease = WriterLease(token=str(uuid4()), mode=mode)
            self._active_mode = mode
            self._writer_token = lease.token
            self._episode_uuid = episode_uuid
            self._fault_reason = None
            self._generation += 1
            return lease

    def bind_episode(self, lease: WriterLease, episode_uuid: str) -> ConsoleModeSnapshot:
        with self._lock:
            self._verify(lease)
            self._episode_uuid = str(episode_uuid)
            self._generation += 1
            return self.snapshot()

    def mark_fault(self, lease: WriterLease, reason: str) -> ConsoleModeSnapshot:
        with self._lock:
            self._verify(lease)
            self._fault_reason = str(reason)
            self._generation += 1
            return self.snapshot()

    def release_writer(self, lease: WriterLease) -> ConsoleModeSnapshot:
        with self._lock:
            if self._writer_token is None:
                if lease.token == self._last_released_token:
                    return self.snapshot()
                raise ModeConflict("stale_writer_lease")
            self._verify(lease)
            self._last_released_token = lease.token
            self._active_mode = None
            self._writer_token = None
            self._episode_uuid = None
            self._generation += 1
            return self.snapshot()

    def _verify(self, lease: WriterLease) -> None:
        if (
            not isinstance(lease, WriterLease)
            or lease.token != self._writer_token
            or lease.mode != self._active_mode
        ):
            raise ModeConflict("stale_writer_lease")
