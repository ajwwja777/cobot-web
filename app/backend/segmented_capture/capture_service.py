"""Serialized capture orchestration around the existing Task5 recorder."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Dict, Optional

import cv2
import numpy as np

from capture_core.deletion import permanently_delete_episode
from capture_core.recorder import RecorderStartRequest
from capture_core.ros_cache import LatestMessageCache
from capture_core.schema import EpisodeIdentity
from capture_core.topics import CAMERA_KEYS

from .ports import CaptureGate
from .review import SegmentReviewStore
from .schema import node_to_dict
from .state import (
    CaptureSnapshot,
    CaptureState,
    SegmentedCaptureReducer,
    SyncedSnapshot,
)
from .store import AtomicEpisodeStore, FinalizeResult
from .teach_mode import TeachModeAdapter, mode_to_mask


class SegmentedCaptureError(RuntimeError):
    """A segmented capture operation could not complete safely."""


class SegmentedCaptureService:
    """One active episode; UI and teach edges are serialized by one lock."""

    def __init__(
        self,
        *,
        recorder: Any,
        cache: LatestMessageCache,
        gate: CaptureGate,
        sidecar_root: Optional[Path],
        clock: Any = time.monotonic,
        drain_timeout_seconds: float = 2.0,
        monitor: bool = True,
        monitor_interval_seconds: float = 0.02,
    ) -> None:
        self.recorder = recorder
        self.cache = cache
        self.gate = gate
        self.sidecar_root = None if sidecar_root is None else Path(sidecar_root)
        self.clock = clock
        self.drain_timeout_seconds = drain_timeout_seconds
        self.monitor = monitor
        self.monitor_interval_seconds = monitor_interval_seconds
        self._lock = RLock()
        self._reducer: Optional[SegmentedCaptureReducer] = None
        self._mode: Optional[TeachModeAdapter] = None
        self._store: Optional[AtomicEpisodeStore] = None
        self._identity: Optional[EpisodeIdentity] = None
        self._recording_data_root: Optional[Path] = None
        self._monitor_stop = Event()
        self._monitor_thread: Optional[Thread] = None
        self._monitor_error: Optional[str] = None
        self._finalize_succeeded = False

    def _selected_sidecar_root(self, data_root: Optional[Path] = None) -> Path:
        if self.sidecar_root is not None:
            return self.sidecar_root
        if data_root is None:
            raise SegmentedCaptureError("data_root_required")
        return Path(data_root) / ".segments"

    @property
    def active(self) -> bool:
        return self._reducer is not None and self._reducer.snapshot().capture_state not in {
            CaptureState.COMMITTED,
            CaptureState.RECOVERY_REQUIRED,
        }

    def _frame_count(self) -> int:
        return int(self.recorder.status().get("frames_written", 0) or 0)

    def _drain_writer(self) -> int:
        deadline = time.monotonic() + self.drain_timeout_seconds
        while True:
            status = self.recorder.status()
            sampled = int(status.get("frames_sampled", 0) or 0)
            written = int(status.get("frames_written", 0) or 0)
            if written >= sampled:
                return written
            if time.monotonic() >= deadline:
                raise SegmentedCaptureError("writer_drain_timeout")
            time.sleep(0.005)

    def _current_mode(self) -> str:
        snapshot = self.cache.snapshot(self.clock())
        value = snapshot.get("handover_mode")
        if not snapshot.has_value("handover_mode") or not isinstance(value, str):
            raise SegmentedCaptureError("handover_mode_unavailable")
        mode_to_mask(value)
        return value

    def _capture_node_snapshot(
        self, node_id: int, frame_index: int, *, replace_existing: bool = False
    ) -> SyncedSnapshot:
        if self._store is None:
            raise SegmentedCaptureError("episode_store_unavailable")
        snapshot = self.cache.snapshot(self.clock())
        node_dir = self._store.episode_root / "nodes" / f"node{node_id:04d}"
        if node_dir.exists() and not replace_existing:
            raise FileExistsError(node_dir)
        if replace_existing and not node_dir.is_dir():
            raise FileNotFoundError(node_dir)
        encoded: Dict[str, bytes] = {}
        for key in sorted(CAMERA_KEYS):
            if not snapshot.is_fresh(key):
                raise SegmentedCaptureError(f"node_camera_unavailable:{key}")
            image = snapshot.get(key)
            if (
                not isinstance(image, np.ndarray)
                or image.dtype != np.uint8
                or image.ndim != 3
                or image.shape[-1] != 3
            ):
                raise SegmentedCaptureError(f"node_camera_invalid:{key}")
            success, jpeg = cv2.imencode(".jpg", image)
            if not success:
                raise SegmentedCaptureError(f"node_camera_encode_failed:{key}")
            encoded[key] = bytes(jpeg)
        if not replace_existing:
            node_dir.mkdir(parents=True)
        pending = []
        try:
            for key, payload in encoded.items():
                final = node_dir / f"{key}.jpg"
                path = node_dir / f".{key}.jpg.writing-{os.getpid()}"
                with path.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                pending.append((path, final))
            for path, final in pending:
                os.replace(path, final)
        finally:
            for path, _final in pending:
                path.unlink(missing_ok=True)
        return SyncedSnapshot(
            sample_timestamp=float(snapshot.now),
            frame_index=frame_index,
            keyframe_ref=f"nodes/node{node_id:04d}",
        )

    def _plain(self, snapshot: CaptureSnapshot) -> Dict[str, object]:
        return {
            "episode_uuid": snapshot.episode_uuid,
            "capture_state": snapshot.capture_state.value,
            "generation": snapshot.generation,
            "training_frame_count": self._frame_count(),
            "node_count": len(snapshot.nodes),
            "nodes": [node_to_dict(node) for node in snapshot.nodes],
            "teach_mask": {
                "left": snapshot.teach_mask.left,
                "right": snapshot.teach_mask.right,
            },
        }

    def start(self, identity: EpisodeIdentity, *, data_root: Path) -> Dict[str, object]:
        with self._lock:
            if self.active:
                raise SegmentedCaptureError("episode_already_active")
            mode = self._current_mode()
            mask = mode_to_mask(mode)
            episode_uuid = str(identity.episode_uuid)
            sidecar_root = self._selected_sidecar_root(Path(data_root))
            series_sidecars = (
                sidecar_root
                / identity.task_id
                / identity.model_id
                / identity.dataset_round
            )
            if identity.storage_layout == "flat":
                series_sidecars = sidecar_root
            self._identity = identity
            self._recording_data_root = Path(data_root).resolve()
            self._mode = TeachModeAdapter(mode)
            self._reducer = SegmentedCaptureReducer(episode_uuid=episode_uuid)
            source_hdf5 = (
                Path(identity.task_id)
                / identity.model_id
                / identity.dataset_round
                / f"episode_{identity.episode_index:06d}.hdf5"
            )
            if identity.storage_layout == "flat":
                source_hdf5 = Path(f"episode_{identity.episode_index:06d}.hdf5")
            self._store = AtomicEpisodeStore(
                series_sidecars,
                episode_uuid=episode_uuid,
                metadata={
                    "storage_layout": identity.storage_layout,
                    "task_id": identity.task_id,
                    "model_id": identity.model_id,
                    "checkpoint_id": identity.checkpoint_id,
                    "dataset_round": identity.dataset_round,
                    "episode_index": identity.episode_index,
                    "source_hdf5_relative": source_hdf5.as_posix(),
                },
            )
            self._store.episode_root.mkdir(parents=True, exist_ok=False)
            node = self._capture_node_snapshot(1, 0)
            started = self._reducer.start(mask, node)
            self._store.begin(started)
            if started.capture_state is CaptureState.RECORDING:
                self.gate.open()
            else:
                self.gate.close()
            try:
                self.recorder.start(
                    RecorderStartRequest(identity=identity, data_root=Path(data_root))
                )
            except Exception:
                self.gate.close()
                raise
            self._monitor_error = None
            self._finalize_succeeded = False
            self._monitor_stop.clear()
            if self.monitor:
                self._monitor_thread = Thread(
                    target=self._monitor_loop,
                    name="task5-segmented-teach-mode",
                    daemon=True,
                )
                self._monitor_thread.start()
            return self._plain(started)

    def _monitor_loop(self) -> None:
        while not self._monitor_stop.wait(self.monitor_interval_seconds):
            try:
                mode = self._current_mode()
                self.observe_mode(mode)
            except Exception as error:  # noqa: BLE001 - thread safety boundary
                # stop() seals the reducer while the monitor can already be waiting
                # for the service lock.  That normal shutdown race must not be
                # reported as a capture fault after a successful commit.
                if self._monitor_stop.is_set() or not self.active:
                    return
                self.gate.close()
                self._monitor_error = type(error).__name__
                return

    def _transition_edge(self, *, side: str, entered: bool) -> CaptureSnapshot:
        assert self._reducer is not None
        assert self._store is not None
        current = self._reducer.snapshot()
        if not entered:
            self.gate.close()
        frame = self._drain_writer()
        if not entered and current.capture_state is CaptureState.RECORDING:
            node = self._capture_node_snapshot(len(current.nodes) + 1, frame)
        elif (
            entered
            and current.capture_state is CaptureState.PAUSED
            and len(current.nodes) == 1
            and current.nodes[0].kind == "start"
        ):
            node = self._capture_node_snapshot(1, frame, replace_existing=True)
        elif current.nodes:
            anchor = current.nodes[-1]
            node = SyncedSnapshot(
                anchor.sample_timestamp,
                anchor.frame_index,
                anchor.keyframe_ref,
            )
        else:
            node = SyncedSnapshot(float(self.clock()), frame)
        updated = (
            self._reducer.teach_enter(side, node)
            if entered
            else self._reducer.teach_exit(side, node)
        )
        self._store.sync_frame_count(frame)
        self._store.transition(updated)
        if entered:
            self.gate.open()
        return updated

    def observe_mode(self, mode: str) -> Dict[str, object]:
        with self._lock:
            if not self.active or self._mode is None or self._reducer is None:
                raise SegmentedCaptureError("episode_not_active")
            edges = self._mode.observe(mode)
            updated = self._reducer.snapshot()
            for edge in edges:
                updated = self._transition_edge(side=edge.side, entered=edge.entered)
            return self._plain(updated)

    def pause(self) -> Dict[str, object]:
        with self._lock:
            if not self.active or self._reducer is None or self._store is None:
                raise SegmentedCaptureError("episode_not_active")
            self.gate.close()
            frame = self._drain_writer()
            node = self._capture_node_snapshot(len(self._reducer.snapshot().nodes) + 1, frame)
            updated = self._reducer.ui_pause(node)
            self._store.sync_frame_count(frame)
            self._store.transition(updated)
            return self._plain(updated)

    def resume(self) -> Dict[str, object]:
        with self._lock:
            if not self.active or self._reducer is None or self._store is None:
                raise SegmentedCaptureError("episode_not_active")
            frame = self._drain_writer()
            current = self._reducer.snapshot()
            if len(current.nodes) == 1 and current.nodes[0].kind == "start":
                node = self._capture_node_snapshot(1, frame, replace_existing=True)
            else:
                anchor = current.nodes[-1]
                node = SyncedSnapshot(
                    anchor.sample_timestamp,
                    anchor.frame_index,
                    anchor.keyframe_ref,
                )
            updated = self._reducer.ui_resume(node)
            self._store.sync_frame_count(frame)
            self._store.transition(updated)
            self.gate.open()
            return self._plain(updated)

    def marker(self) -> Dict[str, object]:
        with self._lock:
            if not self.active or self._reducer is None or self._store is None:
                raise SegmentedCaptureError("episode_not_active")
            self.gate.close()
            frame = self._drain_writer()
            node = self._capture_node_snapshot(len(self._reducer.snapshot().nodes) + 1, frame)
            try:
                updated = self._reducer.marker(node)
                self._store.sync_frame_count(frame)
                self._store.write_marker(updated)
            finally:
                if self._reducer.snapshot().capture_state is CaptureState.RECORDING:
                    self.gate.open()
            return self._plain(updated)

    def stop(self, *, discard: bool = False) -> FinalizeResult:
        with self._lock:
            if not self.active or self._reducer is None or self._store is None:
                raise SegmentedCaptureError("episode_not_active")
            self.gate.close()
            self._monitor_stop.set()
            frame = self._drain_writer()
            current = self._reducer.snapshot()
            if discard or (current.capture_state is CaptureState.PAUSED and current.nodes[-1].kind == "transition"):
                anchor = current.nodes[-1]
                node = SyncedSnapshot(
                    anchor.sample_timestamp,
                    anchor.frame_index,
                    anchor.keyframe_ref,
                )
            else:
                node = self._capture_node_snapshot(len(current.nodes) + 1, frame)
            stopped = self._reducer.stop(node)
            self.recorder.stop()
            final_frame = self._frame_count()
            self._store.sync_frame_count(final_frame)
            result = self._store.finalize(stopped)
            self._finalize_succeeded = True
            return result

    def finalized_successfully(self) -> bool:
        """True only after both recorder stop and atomic sidecar finalize returned."""
        with self._lock:
            return bool(
                self._finalize_succeeded
                and self._reducer is not None
                and self._reducer.snapshot().capture_state is CaptureState.COMMITTED
            )

    def label_outcome(self, outcome: str) -> Dict[str, object]:
        from capture_core.labels import LabelStore
        with self._lock:
            if not self.finalized_successfully() or self._identity is None or self._recording_data_root is None:
                raise SegmentedCaptureError("episode_not_finalized")
            store = LabelStore(self._recording_data_root)
            uuid = str(self._identity.episode_uuid)
            if outcome in {"success", "failure"}:
                return store.set_outcome(uuid, outcome)
            if outcome != "unknown":
                raise SegmentedCaptureError("invalid_outcome")
            return store.update_labels(uuid, {"episode_uuid": uuid, "episode_outcome": "unknown",
                "episode_quality": "uncertain", "termination_reason": "operator_save", "keep_for_training": "false"})

    @staticmethod
    def _outcome_summary(payload, data_root):
        if data_root is None or not payload.get("source_hdf5_relative"):
            return {}
        root = Path(data_root).resolve()
        path = (root / payload["source_hdf5_relative"]).with_suffix(".labels.json")
        try:
            path.resolve().relative_to(root)
            labels = json.loads(path.read_text(encoding="utf-8"))
            if labels.get("episode_uuid") != payload.get("episode_uuid"):
                return {}
            return {"episode_outcome": labels.get("episode_outcome"), "has_hil": bool(labels.get("interventions"))}
        except (OSError, ValueError, TypeError):
            return {}

    def discard(self) -> Dict[str, object]:
        """Finalize the owned writer, then permanently remove only this episode."""
        with self._lock:
            if not self.active or self._identity is None or self._recording_data_root is None:
                raise SegmentedCaptureError("episode_not_active")
            uuid = str(self._identity.episode_uuid)
            root = self._recording_data_root
            self.stop(discard=True)
            self.delete_episode(uuid, data_root=root, record_deletion=False)
            self._reducer = self._mode = self._store = self._identity = None
            self._recording_data_root = None
            self._monitor_error = None
            self._finalize_succeeded = False
            return {**self.status(), "discarded_episode_uuid": uuid}

    def status(self) -> Dict[str, object]:
        with self._lock:
            if self._reducer is None:
                return {
                    "capture_state": CaptureState.IDLE.value,
                    "generation": 0,
                    "node_count": 0,
                    "nodes": [],
                    "training_frame_count": 0,
                    "error_code": self._monitor_error,
                }
            public = self._plain(self._reducer.snapshot())
            public["data_root"] = str(self._recording_data_root) if self._recording_data_root else None
            public["error_code"] = self._monitor_error
            public["finalized"] = self.finalized_successfully()
            return public

    def _episode_root(
        self, episode_uuid: str, *, data_root: Optional[Path] = None
    ) -> Path:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", episode_uuid) is None:
            raise FileNotFoundError(episode_uuid)
        root = self._selected_sidecar_root(data_root)
        matches = list(root.glob(f"*/*/*/{episode_uuid}"))
        if (root / episode_uuid).is_dir():
            matches.append(root / episode_uuid)
        if len(matches) != 1:
            raise FileNotFoundError(episode_uuid)
        return matches[0]

    def read_episode(
        self, episode_uuid: str, *, data_root: Optional[Path] = None
    ) -> Dict[str, object]:
        root = self._episode_root(episode_uuid, data_root=data_root)
        sidecar = root / "sidecar.json"
        if not sidecar.exists():
            sidecar = root / "sidecar.json.incomplete"
        with sidecar.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def list_episodes(
        self,
        *,
        data_root: Optional[Path] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[Dict[str, object]]:
        """List episodes newest first without repeatedly rescanning the directory.

        A directory can contain hundreds of episodes.  The old implementation
        called ``read_episode`` for every sidecar; that method performs another
        recursive glob and made the history request quadratic.  Rank the
        sidecars once, then only parse the requested page.
        """
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        candidates: Dict[str, tuple[int, Path]] = {}
        root = self._selected_sidecar_root(data_root)
        for pattern in ("*/sidecar.json", "*/sidecar.json.incomplete", "*/*/*/*/sidecar.json", "*/*/*/*/sidecar.json.incomplete"):
            for path in root.glob(pattern):
                episode_uuid = path.parent.name
                try:
                    stamp = path.stat().st_mtime_ns
                except OSError:
                    continue
                previous = candidates.get(episode_uuid)
                # Prefer the committed sidecar if both committed and incomplete
                # artifacts briefly coexist during an atomic finalize.
                committed = path.name == "sidecar.json"
                previous_committed = bool(previous and previous[1].name == "sidecar.json")
                if previous and (previous_committed or not committed):
                    continue
                candidates[episode_uuid] = (stamp, path)

        ranked = sorted(candidates.items(), key=lambda item: item[1][0], reverse=True)
        selected = ranked[offset:] if limit is None else ranked[offset:offset + limit]
        episodes = []
        for episode_uuid, (_stamp, path) in selected:
            try:
                with path.open("r", encoding="utf-8") as stream:
                    payload = json.load(stream)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            episodes.append(
                {
                    "episode_uuid": episode_uuid,
                    "episode_index": payload.get("episode_index"),
                    "task_id": payload.get("task_id"),
                    "model_id": payload.get("model_id"),
                    "dataset_round": payload.get("dataset_round"),
                    "commit_state": payload.get("commit_state"),
                    "capture_state": payload.get("capture_state"),
                    "training_frame_count": payload.get("training_frame_count", 0),
                    "node_count": len(payload.get("nodes", [])),
                    **self._outcome_summary(payload, data_root),
                    "last_timestamp": (
                        payload.get("nodes", [{}])[-1].get("sample_timestamp", 0)
                        if payload.get("nodes")
                        else 0
                    ),
                }
            )
        return sorted(episodes, key=lambda item: float(item["last_timestamp"]), reverse=True)

    def replay_artifacts(self, episode_uuid: str, *, data_root=None):
        """Resolve one committed episode from its sidecar, without scanning HDF5s."""
        from uuid import UUID
        import h5py
        from capture_core.labels import EpisodeArtifacts
        payload = self.read_episode(episode_uuid, data_root=data_root)
        if payload.get("capture_state") != "committed":
            raise SegmentedCaptureError("episode_not_committed")
        if data_root is None:
            raise ValueError("data_root_required")
        root = Path(data_root).resolve()
        source = root / str(payload["source_hdf5_relative"])
        source.resolve().relative_to(root)
        if source.is_symlink() or not source.is_file():
            raise FileNotFoundError(source)
        with h5py.File(source, "r") as handle:
            identity = handle.attrs["episode_uuid"]
            if isinstance(identity, bytes):
                identity = identity.decode("utf-8")
            if str(identity) != str(episode_uuid):
                raise ValueError("episode_identity_mismatch")
        return EpisodeArtifacts(
            episode_uuid=UUID(str(episode_uuid)),
            episode_index=int(payload["episode_index"]),
            episode_path=source,
            sidecar_path=self._episode_root(episode_uuid, data_root=data_root) / "sidecar.json",
            preview_directory=source.parent / ".previews" / str(episode_uuid),
            frame_count=int(payload["training_frame_count"]),
            size_bytes=source.stat().st_size,
        )

    def keyframe_path(
        self,
        episode_uuid: str,
        node_id: int,
        camera_key: str,
        *,
        data_root: Optional[Path] = None,
    ) -> Path:
        if camera_key not in CAMERA_KEYS:
            raise FileNotFoundError(camera_key)
        payload = self.read_episode(episode_uuid, data_root=data_root)
        node = next(
            (item for item in payload["nodes"] if int(item["node_id"]) == node_id),
            None,
        )
        if node is None or not node.get("keyframe_ref"):
            raise FileNotFoundError(node_id)
        root = self._episode_root(episode_uuid, data_root=data_root).resolve()
        path = (root / str(node["keyframe_ref"]) / f"{camera_key}.jpg").resolve()
        path.relative_to(root)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def load_review(
        self, episode_uuid: str, *, data_root: Optional[Path] = None
    ) -> Dict[str, object]:
        return SegmentReviewStore(self._selected_sidecar_root(data_root)).load(
            episode_uuid
        )

    def save_review(
        self,
        episode_uuid: str,
        *,
        expected_revision: int,
        selected_interval_ids: list[int],
        note: Optional[str],
        data_root: Optional[Path] = None,
    ) -> Dict[str, object]:
        return SegmentReviewStore(self._selected_sidecar_root(data_root)).save(
            episode_uuid,
            expected_revision=expected_revision,
            selected_interval_ids=selected_interval_ids,
            note=note,
        )

    def delete_episode(
        self, episode_uuid: str, *, data_root: Path, record_deletion: bool = True
    ) -> Dict[str, object]:
        with self._lock:
            # A new rollout may be recording while an operator removes an
            # older, complete episode. Never permit deletion of the active
            # writer's own episode.
            if self.active and (
                self._identity is None or str(self._identity.episode_uuid) == episode_uuid
            ):
                raise SegmentedCaptureError("episode_active")
            root = Path(data_root).resolve()
            payload = self.read_episode(episode_uuid, data_root=root)
            if payload.get("commit_state") != "complete":
                raise SegmentedCaptureError("episode_not_complete")
            task_id = str(payload["task_id"])
            model_id = str(payload["model_id"])
            dataset_round = str(payload["dataset_round"])
            episode_index = int(payload["episode_index"])
            series = root / task_id / model_id / dataset_round
            expected_relative = (
                Path(task_id)
                / model_id
                / dataset_round
                / f"episode_{episode_index:06d}.hdf5"
            )
            flat = payload.get("storage_layout", "legacy") == "flat"
            if flat:
                series = root
                expected_relative = Path(f"episode_{episode_index:06d}.hdf5")
            if str(payload.get("source_hdf5_relative")) != expected_relative.as_posix():
                raise SegmentedCaptureError("source_hdf5_mismatch")
            sidecar = self._episode_root(episode_uuid, data_root=root).resolve()
            expected_sidecar = (
                self._selected_sidecar_root(root)
                / task_id
                / model_id
                / dataset_round
                / episode_uuid
            ).resolve()
            if flat:
                expected_sidecar = (self._selected_sidecar_root(root) / episode_uuid).resolve()
            if sidecar != expected_sidecar:
                raise SegmentedCaptureError("segment_sidecar_mismatch")
            episode = root / expected_relative
            incomplete_episode = None
            if int(payload.get("training_frame_count", -1)) == 0:
                incomplete_episode = Path(str(episode) + ".incomplete")
            record = permanently_delete_episode(
                episode,
                episode.with_suffix(".labels.json"),
                series / ".previews" / episode_uuid,
                expected_uuid=episode_uuid,
                incomplete_episode_path=incomplete_episode,
                extra_artifacts=(sidecar,),
                record_deletion=record_deletion,
            )
            if not record_deletion:
                forget = getattr(self.recorder, "forget_discarded_episode", None)
                if forget is not None:
                    forget(episode)
            return {
                "episode_uuid": record.episode_uuid,
                "episode_index": record.episode_index,
                "deleted_at": record.deleted_at,
            }
