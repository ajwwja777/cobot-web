"""Bounded, two-thread rollout acquisition and HDF5 lifecycle controller."""

from __future__ import annotations

import math
import shutil
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Condition, Event, RLock, Thread, current_thread
from typing import Any

from .config import RecorderConfig
from .hdf5_writer import Hdf5EpisodeWriter
from .ros_cache import LatestMessageCache
from .sampler import FrameSampler
from .schema import EpisodeIdentity, FrameSample
from .validation import is_disk_below_threshold, validate_preflight


class RecorderError(RuntimeError):
    """A recording error propagated from an acquisition or writer thread."""


@dataclass(frozen=True)
class RecorderStartRequest:
    identity: EpisodeIdentity
    max_timesteps: int | None = None
    data_root: Path | None = None


class RolloutRecorder:
    """Sample on one thread and perform every HDF5 operation on another."""

    def __init__(
        self,
        config: RecorderConfig,
        cache: LatestMessageCache,
        *,
        sampler: FrameSampler | None = None,
        writer_factory: Any = Hdf5EpisodeWriter,
        clock: Any = time.monotonic,
        wall_clock: Any = time.time,
        disk_usage: Any = shutil.disk_usage,
        shutdown_timeout_seconds: float = 5.0,
        drain_timeout_seconds: float | None = None,
        capture_gate: Any = None,
    ) -> None:
        if shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        self._config = config
        self._cache = cache
        self._sampler = sampler or FrameSampler()
        self._writer_factory = writer_factory
        self._clock = clock
        self._wall_clock = wall_clock
        self._disk_usage = disk_usage
        self._shutdown_timeout = shutdown_timeout_seconds
        self._drain_timeout = shutdown_timeout_seconds if drain_timeout_seconds is None else float(drain_timeout_seconds)
        if self._drain_timeout <= 0:
            raise ValueError("drain_timeout_seconds must be positive")
        self._capture_gate = capture_gate
        self._lock = RLock()
        self._lifecycle = Condition(self._lock)
        self._state = "idle"
        self._start_cancelled = False
        self._error: str | None = None
        self._last_error: str | None = None
        self._last_start_error: str | None = None
        self._path: Path | None = None
        self._frames_sampled = 0
        self._frames_written = 0
        self._acquisition_active = False
        self._completion_state = "complete"
        self._termination_reason = "operator_stop"
        self._publication_allowed = False
        self._stop_event = Event()
        self._acquisition_done = Event()
        self._queue: Queue[FrameSample] | None = None
        self._writer: Any = None
        self._acquisition_thread: Thread | None = None
        self._writer_thread: Thread | None = None
        self._max_timesteps = 0
        self._active_data_root = self._config.data_root

    def _maximum_configured_timesteps(self) -> int:
        return math.ceil(
            self._config.sample_rate_hz * self._config.max_duration_seconds
        )

    def start(self, request: RecorderStartRequest) -> dict[str, object]:
        if not isinstance(request, RecorderStartRequest):
            raise TypeError("request must be RecorderStartRequest")
        maximum = self._maximum_configured_timesteps()
        max_timesteps = request.max_timesteps
        if max_timesteps is None:
            max_timesteps = maximum
        if (
            isinstance(max_timesteps, bool)
            or not isinstance(max_timesteps, int)
            or max_timesteps <= 0
            or max_timesteps > maximum
        ):
            raise ValueError(f"max_timesteps must be between 1 and {maximum}")

        with self._lock:
            restartable_stopped = (
                self._state == "stopped"
                and not self._acquisition_active
                and (
                    self._acquisition_thread is None
                    or not self._acquisition_thread.is_alive()
                )
                and (self._writer_thread is None or not self._writer_thread.is_alive())
            )
            if self._state != "idle" and not restartable_stopped:
                raise RecorderError("recorder already started")
            # Reserve the lifecycle before external checks so concurrent starts lose.
            self._state = "starting"
            self._start_cancelled = False
            self._error = None
            self._path = None
            self._writer = None
            self._queue = None
            self._acquisition_thread = None
            self._writer_thread = None
            self._publication_allowed = False
            self._stop_event.clear()
            self._acquisition_done.clear()
        try:
            reset_sampler = getattr(self._sampler, "reset", None)
            if callable(reset_sampler):
                reset_sampler()
            snapshot = self._cache.snapshot(self._clock())
            requested_root = (
                self._config.data_root
                if request.data_root is None
                else request.data_root
            )
            data_root = validate_preflight(
                requested_root,
                snapshot,
                min_free_disk_bytes=self._config.min_free_disk_bytes,
                disk_usage=self._disk_usage,
            )
            writer = self._writer_factory(
                data_root,
                request.identity,
                max_timesteps,
                fps=self._config.sample_rate_hz,
                wall_clock=self._wall_clock,
            )
        except Exception as error:
            with self._lifecycle:
                if self._start_cancelled or self._state == "stopping":
                    self._completion_state = "cancelled"
                    self._termination_reason = "start_cancelled"
                    if self._state != "fatal":
                        self._state = "stopped"
                else:
                    reason = f"start_error: {error}"
                    self._last_start_error = reason
                    self._last_error = reason
                    self._error = None
                    self._state = "idle"
                self._lifecycle.notify_all()
            raise

        with self._lifecycle:
            self._writer = writer
            self._path = writer.incomplete_path
            if self._start_cancelled or self._state == "stopping":
                self._publication_allowed = False
                cancel_start = True
                writer_thread = None
                acquisition_thread = None
            else:
                cancel_start = False
                self._queue = Queue(maxsize=self._config.writer_queue_capacity)
                self._active_data_root = data_root
                self._max_timesteps = max_timesteps
                self._frames_sampled = 0
                self._frames_written = 0
                self._error = None
                self._completion_state = "complete"
                self._termination_reason = "max_timesteps"
                self._publication_allowed = True
                self._acquisition_active = True
                self._state = "recording"
                self._writer_thread = Thread(
                    target=self._writer_loop, name="task5-hdf5-writer", daemon=True
                )
                self._acquisition_thread = Thread(
                    target=self._acquisition_loop,
                    args=(request.identity,),
                    name="task5-frame-sampler",
                    daemon=True,
                )
                writer_thread = self._writer_thread
                acquisition_thread = self._acquisition_thread
                # Starting both workers while holding the lifecycle lock closes
                # the last window where stop could observe an unstarted Thread.
                writer_thread.start()
                acquisition_thread.start()
                self._lifecycle.notify_all()
        if cancel_start:
            self._request_cancel(writer, "start_cancelled")
            try:
                path = writer.abort("start_cancelled")
            except Exception as error:
                with self._lifecycle:
                    self._error = f"start_cancel_error: {error}"
                    self._last_error = self._error
                    self._state = "fatal"
                    self._lifecycle.notify_all()
                raise RecorderError(self._error) from error
            with self._lifecycle:
                self._path = path
                self._completion_state = "cancelled"
                self._termination_reason = "start_cancelled"
                if self._state != "fatal":
                    self._state = "stopped"
                self._lifecycle.notify_all()
            raise RecorderError("start_cancelled")
        assert writer_thread is not None
        assert acquisition_thread is not None
        return self.status()

    def _record_error(self, reason: str, *, fatal: bool = False) -> None:
        with self._lifecycle:
            if self._error is None:
                self._error = reason
                self._last_error = reason
            if fatal or self._state != "fatal":
                self._state = "fatal" if fatal else "error"
            self._acquisition_active = False
            self._publication_allowed = False
            self._lifecycle.notify_all()
        self._stop_event.set()

    def _acquisition_loop(self, identity: EpisodeIdentity) -> None:
        period = self._config.sample_period_seconds
        next_tick = self._clock()
        try:
            while not self._stop_event.is_set():
                gate = self._capture_gate
                permission = (
                    gate.sample_permission()
                    if gate is not None
                    else nullcontext(True)
                )
                with permission as allowed:
                    if not allowed:
                        reached_maximum = False
                        overflow = False
                    else:
                        with self._lock:
                            frame_index = self._frames_sampled
                        snapshot = self._cache.snapshot(self._clock())
                        frame = self._sampler.sample(snapshot, identity, frame_index)
                        assert self._queue is not None
                        overflow = False
                        with self._lock:
                            if self._stop_event.is_set():
                                break
                            try:
                                self._queue.put_nowait(frame)
                            except Full:
                                overflow = True
                            else:
                                self._frames_sampled += 1
                                reached_maximum = (
                                    self._frames_sampled >= self._max_timesteps
                                )
                if not allowed:
                    next_tick = self._clock()
                    self._stop_event.wait(min(period, 0.02))
                    continue
                if overflow:
                    self._record_error("writer_queue_overflow")
                    break
                if self._stop_event.is_set():
                    break
                try:
                    disk_low = is_disk_below_threshold(
                        self._active_data_root,
                        self._config.stop_free_disk_bytes,
                        disk_usage=self._disk_usage,
                    )
                except Exception as error:  # noqa: BLE001 - thread boundary
                    self._record_error(f"disk_check_error: {error}")
                    break
                if disk_low:
                    with self._lock:
                        self._completion_state = "aborted"
                        self._termination_reason = "disk_low"
                    self._stop_event.set()
                    break
                if reached_maximum:
                    self._stop_event.set()
                    break
                next_tick += period
                delay = next_tick - self._clock()
                if delay > 0:
                    self._stop_event.wait(delay)
        except Exception as error:  # noqa: BLE001 - propagate worker failure
            self._record_error(f"acquisition_error: {error}")
        finally:
            with self._lifecycle:
                self._acquisition_active = False
                self._lifecycle.notify_all()
            self._acquisition_done.set()

    def _writer_loop(self) -> None:
        assert self._queue is not None
        try:
            while True:
                with self._lock:
                    error = self._error
                if error is not None:
                    self._abort_after_error(error)
                    return
                try:
                    frame = self._queue.get(timeout=0.02)
                except Empty:
                    if not self._acquisition_done.is_set():
                        continue
                    with self._lock:
                        error = self._error
                    if error is not None:
                        self._abort_after_error(error)
                        return
                    break
                try:
                    self._writer.append(frame)
                    with self._lock:
                        self._frames_written += 1
                except Exception as write_error:  # noqa: BLE001 - writer boundary
                    self._record_error(f"writer_error: {write_error}")
                    self._abort_after_error(f"writer_error: {write_error}")
                    return
                finally:
                    self._queue.task_done()
            with self._lock:
                frames_written = self._frames_written
                error = self._error
                publication_allowed = self._publication_allowed
            if error is not None or not publication_allowed:
                self._abort_after_error(error or "publication_revoked")
                return
            if frames_written == 0:
                path = self._writer.abort("empty_episode")
                with self._lifecycle:
                    self._path = path
                    self._completion_state = "aborted"
                    self._termination_reason = "empty_episode"
                    self._state = "stopped"
                    self._publication_allowed = False
                    self._lifecycle.notify_all()
                return
            with self._lock:
                completion_state = self._completion_state
                termination_reason = self._termination_reason
            path = self._writer.finalize(
                {"termination_reason": termination_reason},
                completion_state=completion_state,
                end_timestamp=float(self._wall_clock()),
            )
            with self._lifecycle:
                self._path = path
                self._state = "stopped"
                self._publication_allowed = False
                self._lifecycle.notify_all()
        except Exception as error:  # noqa: BLE001 - writer thread boundary
            self._record_error(f"writer_error: {error}")
            self._abort_after_error(f"writer_error: {error}")

    def _abort_after_error(self, reason: str) -> None:
        try:
            path = self._writer.abort(reason)
            with self._lifecycle:
                self._path = path
                self._lifecycle.notify_all()
        except Exception as abort_error:  # noqa: BLE001 - retain original failure
            with self._lifecycle:
                self._error = f"{reason}; abort_error: {abort_error}"
                self._lifecycle.notify_all()

    @staticmethod
    def _request_cancel(component: object, reason: str) -> None:
        request_cancel = getattr(component, "request_cancel", None)
        if callable(request_cancel):
            request_cancel(reason)

    @staticmethod
    def _publication_status(component: object) -> str | None:
        status = getattr(component, "publication_status", None)
        return status() if callable(status) else status

    def stop(self) -> Path | None:
        wait_for_start = False
        with self._lifecycle:
            if self._state == "idle":
                self._start_cancelled = True
                self._completion_state = "cancelled"
                self._termination_reason = "start_cancelled"
                self._state = "stopped"
                self._stop_event.set()
                self._lifecycle.notify_all()
                return None
            if self._state == "stopped":
                return self._path
            if self._state == "starting":
                self._start_cancelled = True
                self._completion_state = "cancelled"
                self._termination_reason = "start_cancelled"
                self._publication_allowed = False
                self._state = "stopping"
                self._stop_event.set()
                wait_for_start = True
            elif self._state == "stopping" and self._writer_thread is None:
                wait_for_start = True
            elif self._state == "recording":
                self._termination_reason = "operator_stop"
                self._state = "stopping"
                self._stop_event.set()
            acquisition_thread = self._acquisition_thread
            writer_thread = self._writer_thread
        self._request_cancel(self._sampler, "recorder_stop")
        if wait_for_start:
            deadline = time.monotonic() + self._shutdown_timeout
            with self._lifecycle:
                while self._state == "stopping" and self._writer_thread is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        reason = "shutdown_timeout: starting"
                        self._error = reason
                        self._state = "fatal"
                        self._publication_allowed = False
                        self._lifecycle.notify_all()
                        raise RecorderError(reason)
                    self._lifecycle.wait(remaining)
                error = self._error
                path = self._path
            if error is not None:
                raise RecorderError(error)
            return path
        if (
            acquisition_thread is not None
            and acquisition_thread is not current_thread()
        ):
            acquisition_thread.join(self._shutdown_timeout)
        if acquisition_thread is not None and acquisition_thread.is_alive():
            reason = f"shutdown_timeout: acquisition:{acquisition_thread.name}"
            self._record_error(reason)
            self._request_cancel(self._sampler, reason)
            self._request_cancel(self._writer, reason)
            acquisition_thread.join(self._shutdown_timeout)
            if acquisition_thread.is_alive():
                self._record_error(reason, fatal=True)
        if writer_thread is not None and writer_thread is not current_thread():
            writer_thread.join(self._drain_timeout)
        if writer_thread is not None and writer_thread.is_alive():
            reason = f"shutdown_timeout: writer:{writer_thread.name}"
            publication_status = self._publication_status(self._writer)
            if publication_status in {
                "finalizing",
                "commit_in_progress",
                "committed",
            }:
                with self._lifecycle:
                    if self._state != "stopped":
                        self._state = "stopping"
                    self._lifecycle.notify_all()
                return None
            self._request_cancel(self._writer, reason)
            self._record_error(reason)
            writer_thread.join(self._shutdown_timeout)
            if writer_thread.is_alive():
                self._record_error(reason, fatal=True)
        with self._lock:
            error = self._error
            path = self._path
        if error is not None:
            raise RecorderError(error)
        return path

    def forget_discarded_episode(self, expected_path: Path) -> None:
        """Clear the stopped recorder's UI identity after its files are removed."""
        with self._lifecycle:
            workers = (self._acquisition_thread, self._writer_thread)
            if self._state not in {"idle", "stopped"} or self._acquisition_active or any(t is not None and t.is_alive() for t in workers):
                raise RecorderError("discarded recorder is still active")
            if self._path is not None and self._path not in {expected_path, Path(str(expected_path) + ".incomplete")}:
                raise RecorderError("discarded recorder identity changed")
            self._path = self._writer = self._queue = None
            self._frames_sampled = self._frames_written = 0
            self._completion_state = self._termination_reason = None
            self._state = "idle"
            self._lifecycle.notify_all()

    def recover_error(self) -> dict[str, object]:
        """Release a finished failed recorder without publishing/deleting its file."""
        with self._lifecycle:
            if self._state in {"idle", "stopped"}:
                return self.status()
            if self._state not in {"error", "fatal"}:
                raise RecorderError("recorder is still active")
            workers=(self._acquisition_thread,self._writer_thread)
            if self._acquisition_active or any(t is not None and t.is_alive() for t in workers):
                raise RecorderError("failed recorder worker still alive; retain ownership")
            if self._publication_status(self._writer) in {"finalizing","commit_in_progress"}:
                raise RecorderError("publication is still in progress")
            self._publication_allowed=False
            self._state="idle"
            self._error=None
            self._lifecycle.notify_all()
            return self.status()

    def status(self) -> dict[str, object]:
        # Keep the same lock order as acquisition: gate, then recorder state.
        capture_enabled = (
            True
            if self._capture_gate is None
            else bool(self._capture_gate.is_open())
        )
        with self._lock:
            status = {
                "state": self._state,
                "error": self._error,
                "last_error": self._last_error,
                "last_start_error": self._last_start_error,
                "path": None if self._path is None else str(self._path),
                "completion_state": self._completion_state,
                "frames_sampled": self._frames_sampled,
                "frames_written": self._frames_written,
                "acquisition_active": self._acquisition_active,
                "writer_thread_alive": (
                    self._writer_thread is not None and self._writer_thread.is_alive()
                ),
                "publication_allowed": self._publication_allowed,
                "publication_status": self._publication_status(self._writer),
                "capture_enabled": capture_enabled,
            }
        # Return a fresh plain dict for API serialization and caller isolation.
        return status
