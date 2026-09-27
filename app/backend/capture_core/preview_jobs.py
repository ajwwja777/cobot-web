"""Single-worker lifecycle for bounded rollout replay generation."""

from __future__ import annotations

import logging
from pathlib import Path
from queue import Full, Queue
from threading import Event, RLock, Thread
from typing import Any, Optional

from .episode_preview import generate_episode_preview, validate_preview_cache
from .labels import EpisodeArtifacts

LOGGER = logging.getLogger(__name__)


class PreviewQueueFull(RuntimeError):
    """The bounded preview queue has no free capacity."""


class PreviewNotReady(RuntimeError):
    """A requested replay does not have a valid published cache."""


class PreviewJobManager:
    """Run at most one preview encoder and expose path-free public state."""

    def __init__(
        self,
        *,
        generator: Any = generate_episode_preview,
        validator: Any = validate_preview_cache,
        max_queue: int = 8,
    ) -> None:
        if isinstance(max_queue, bool) or max_queue <= 0:
            raise ValueError("max_queue must be positive")
        self._generator = generator
        self._validator = validator
        self._queue: Queue[Optional[EpisodeArtifacts]] = Queue(maxsize=max_queue)
        self._lock = RLock()
        self._states: dict[str, dict[str, object]] = {}
        self._artifacts: dict[str, EpisodeArtifacts] = {}
        self._stopping = Event()
        self._thread = Thread(
            target=self._worker, name="task5-preview-worker", daemon=True
        )
        self._thread.start()

    @staticmethod
    def _initial_state(episode_uuid: str, state: str) -> dict[str, object]:
        return {
            "episode_uuid": episode_uuid,
            "state": state,
            "completed_frames": 0,
            "total_frames": 0,
            "error_code": None,
        }

    def _ready_from_cache(
        self, artifacts: EpisodeArtifacts
    ) -> Optional[dict[str, object]]:
        metadata = self._validator(
            artifacts.episode_path, artifacts.preview_directory
        )
        if metadata is None:
            return None
        state = self._initial_state(str(artifacts.episode_uuid), "ready")
        count = int(metadata.get("output_frame_count", 0) or 0)
        state["completed_frames"] = count
        state["total_frames"] = count
        for key in ("source_frame_count", "source_fps", "output_fps"):
            value = metadata.get(key)
            if value is not None:
                state[key] = value
        return state

    def queue(self, artifacts: EpisodeArtifacts) -> dict[str, object]:
        if not isinstance(artifacts, EpisodeArtifacts):
            raise TypeError("artifacts must be EpisodeArtifacts")
        episode_uuid = str(artifacts.episode_uuid)
        cached = self._ready_from_cache(artifacts)
        with self._lock:
            if cached is not None:
                self._states[episode_uuid] = cached
                self._artifacts[episode_uuid] = artifacts
                return dict(cached)
            current = self._states.get(episode_uuid)
            if current is not None and current["state"] in {"queued", "generating"}:
                return dict(current)
            state = self._initial_state(episode_uuid, "queued")
            self._states[episode_uuid] = state
            self._artifacts[episode_uuid] = artifacts
            try:
                self._queue.put_nowait(artifacts)
            except Full as error:
                self._states.pop(episode_uuid, None)
                self._artifacts.pop(episode_uuid, None)
                raise PreviewQueueFull("preview queue is full") from error
            return dict(state)

    def status(self, episode_uuid: str) -> dict[str, object]:
        with self._lock:
            state = self._states.get(str(episode_uuid))
            if state is None:
                return self._initial_state(str(episode_uuid), "missing")
            return dict(state)

    def is_busy(self, episode_uuid: str) -> bool:
        return self.status(episode_uuid)["state"] in {"queued", "generating"}

    def preview_path(self, artifacts: EpisodeArtifacts) -> Path:
        cached = self._ready_from_cache(artifacts)
        if cached is None:
            raise PreviewNotReady("preview is not ready")
        path = artifacts.preview_directory / "preview.mp4"
        if path.is_symlink() or not path.is_file():
            raise PreviewNotReady("preview is not ready")
        return path

    def artifact_path(self, artifacts: EpisodeArtifacts, name: str) -> Path:
        filenames = {"contact_sheet":"contact_sheet.jpg", "qpos":"qpos.png"}
        if name not in filenames or self._ready_from_cache(artifacts) is None:
            raise PreviewNotReady("preview artifact is not ready")
        path = artifacts.preview_directory / filenames[name]
        if path.is_symlink() or not path.is_file():
            raise PreviewNotReady("preview artifact is not ready")
        return path

    def forget(self, episode_uuid: str) -> None:
        with self._lock:
            self._states.pop(str(episode_uuid), None)
            self._artifacts.pop(str(episode_uuid), None)

    def _progress(self, episode_uuid: str, complete: int, total: int) -> None:
        with self._lock:
            state = self._states.get(episode_uuid)
            if state is None:
                return
            state["completed_frames"] = int(complete)
            state["total_frames"] = int(total)

    def _worker(self) -> None:
        while True:
            artifacts = self._queue.get()
            if artifacts is None:
                self._queue.task_done()
                return
            episode_uuid = str(artifacts.episode_uuid)
            with self._lock:
                state = self._states.get(episode_uuid)
                if state is None:
                    self._queue.task_done()
                    continue
                state["state"] = "generating"
                state["error_code"] = None
            try:
                self._generator(
                    artifacts.episode_path,
                    artifacts.preview_directory,
                    progress=lambda complete, total, uuid=episode_uuid: self._progress(
                        uuid, complete, total
                    ),
                )
                cached = self._ready_from_cache(artifacts)
                if cached is None:
                    raise PreviewNotReady("generated preview cache is invalid")
                with self._lock:
                    previous = self._states[episode_uuid]
                    cached["completed_frames"] = max(
                        int(cached["completed_frames"]),
                        int(previous["completed_frames"]),
                    )
                    cached["total_frames"] = max(
                        int(cached["total_frames"]), int(previous["total_frames"])
                    )
                    self._states[episode_uuid] = cached
            except Exception:
                LOGGER.exception("Task5 preview generation failed for %s", episode_uuid)
                with self._lock:
                    state = self._states.get(episode_uuid)
                    if state is not None:
                        state["state"] = "error"
                        state["error_code"] = "preview_generation_failed"
            finally:
                self._queue.task_done()

    def shutdown(self, timeout: float = 5.0) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        try:
            self._queue.put_nowait(None)
        except Full:
            # A daemon worker cannot block process shutdown; it exits after its job.
            return
        self._thread.join(timeout=timeout)
