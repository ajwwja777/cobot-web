"""FastAPI boundary for the Task5 rollout recorder and label sidecars."""

from __future__ import annotations

from capture_core.exact_labels import ExactEpisodeLabelStore

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from uuid import UUID

from .asset_storage import migrated_path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from typing_extensions import Annotated

from .camera_preview import (
    CameraPreviewUnavailable,
    encode_latest_jpeg,
    iter_mjpeg,
)
from .config import RecorderConfig
from .control_source import parse_handover_mode
from .deletion import DeletionError, permanently_delete_episode
from .labels import (
    LABEL_SCHEMA_VERSION,
    LabelConflictError,
    LabelNotFoundError,
    LabelStore,
    LabelValidationError,
)
from .preview_jobs import PreviewJobManager, PreviewNotReady, PreviewQueueFull
from .episode_preview import EpisodeFrameError, read_episode_frame_jpeg
from .readiness import evaluate_readiness
from .recorder import RecorderError, RecorderStartRequest, RolloutRecorder
from .ros_cache import LatestMessageCache
from .ros_subscriber import RosSubscriberBridge
from .schema import IDENTIFIER_RE, EpisodeIdentity
from .series_inspection import inspect_prepared_series
from .storage import PreparedSeries, SeriesIdentity, StoragePathError, prepare_series
from .topics import CAMERA_KEYS
from .validation import PreflightError

LOGGER = logging.getLogger(__name__)

Identifier = Annotated[str, Field(pattern=IDENTIFIER_RE.pattern)]


class StartEpisodeRequest(BaseModel):
    """Validated JSON request for starting one passive recording."""

    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    model_id: Identifier
    checkpoint_id: Identifier
    dataset_round: Identifier
    data_root: Optional[str] = None
    episode_index: Optional[int] = Field(default=None, ge=0, strict=True)
    max_timesteps: Optional[int] = Field(default=None, ge=1, strict=True)
    storage_layout: Literal["legacy", "flat"] = os.environ.get("COBOT_RECORDING_LAYOUT", "legacy")


class StoragePrepareRequest(BaseModel):
    """Validated request for selecting one task/model/round directory."""

    model_config = ConfigDict(extra="forbid")

    data_root: str
    task_id: Identifier
    model_id: Identifier
    checkpoint_id: Identifier
    dataset_round: Identifier
    storage_layout: Literal["legacy", "flat"] = os.environ.get("COBOT_RECORDING_LAYOUT", "legacy")


class InterventionUpdate(BaseModel):
    """Identity plus optional semantic augmentation for a derived interval."""

    model_config = ConfigDict(extra="forbid")

    side: Literal["left", "right"]
    intervention_id: int = Field(ge=1, strict=True)
    start_frame: Optional[int] = Field(default=None, ge=0, strict=True)
    end_frame: Optional[int] = Field(default=None, ge=0, strict=True)
    start_timestamp: Optional[float] = Field(default=None, strict=True)
    end_timestamp: Optional[float] = Field(default=None, strict=True)
    handover_mode: Optional[str] = None
    reason: Optional[
        Literal[
            "preventive", "corrective", "recovery", "safety", "efficiency", "unknown"
        ]
    ] = None
    outcome: Optional[Literal["recovered", "not_recovered", "aborted", "uncertain"]] = (
        None
    )
    quality: Optional[Literal["good", "bad", "uncertain"]] = None
    note: Optional[str] = None


class OperatorNodeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_index: StrictInt
    node_kind: Literal["pause", "resume", "marker"]


class LabelUpdateRequest(BaseModel):
    """Partial label update; UUID is mandatory to prevent cross-episode writes."""

    model_config = ConfigDict(extra="forbid")

    episode_uuid: UUID
    label_schema_version: Optional[StrictInt] = None
    episode_outcome: Optional[Literal["success", "failure", "aborted", "unknown"]] = (
        None
    )
    episode_quality: Optional[Literal["good", "bad", "uncertain"]] = None
    last_completed_stage: Optional[str] = None
    failure_stage: Optional[str] = None
    failure_type: Optional[str] = None
    termination_reason: Optional[
        Literal["success", "failure", "timeout", "safety_stop", "operator_abort", "operator_save"]
    ] = None
    keep_for_training: Optional[Literal["true", "false", "undecided"]] = None
    operator_note: Optional[str] = None
    interventions: Optional[List[InterventionUpdate]] = None
    operator_nodes: Optional[List[OperatorNodeUpdate]] = None

    @model_validator(mode="after")
    def validate_schema_version(self) -> LabelUpdateRequest:
        if (
            self.label_schema_version is not None
            and self.label_schema_version != LABEL_SCHEMA_VERSION
        ):
            raise ValueError("invalid label_schema_version")
        return self


class OutcomeUpdateRequest(BaseModel):
    """Minimal operator outcome decision for one finalized rollout."""

    model_config = ConfigDict(extra="forbid")

    episode_uuid: UUID
    outcome: Literal["success", "failure"]


class DeleteEpisodeRequest(BaseModel):
    """Repeated identity required for a permanent historical delete."""

    model_config = ConfigDict(extra="forbid")

    episode_uuid: UUID


def _http_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _public_status(raw: Any) -> Dict[str, object]:
    """Expose operational progress without filesystem paths or raw exceptions."""
    if not isinstance(raw, dict):
        raw = dict(raw)
    state = str(raw.get("state", "unknown"))
    public: Dict[str, object] = {
        "state": state,
        "active": state in {"starting", "recording", "stopping"},
        "completion_state": raw.get("completion_state"),
        "frames_sampled": int(raw.get("frames_sampled", 0) or 0),
        "frames_written": int(raw.get("frames_written", 0) or 0),
        "publication_status": raw.get("publication_status"),
    }
    if "capture_enabled" in raw:
        public["capture_enabled"] = bool(raw["capture_enabled"])
    path = raw.get("path")
    public["episode_file"] = None
    if isinstance(path, str) and path:
        public["episode_file"] = path.replace("\\", "/").rsplit("/", 1)[-1]
    error = raw.get("error")
    public["error_code"] = "recorder_error" if error else None
    return public


def create_app(
    *,
    recorder: Optional[Any] = None,
    label_store: Optional[LabelStore] = None,
    cache: Optional[LatestMessageCache] = None,
    ros_bridge_factory: Any = RosSubscriberBridge,
    preview_manager: Optional[Any] = None,
    monotonic: Any = time.monotonic,
    readiness_provider: Optional[Any] = None,
    writer_coordinator: Optional[Any] = None,
    capture_gate: Optional[Any] = None,
    mount_frontend: bool = True,
    require_previous_labels: bool = True,
) -> FastAPI:
    """Build an independently testable API with injectable recorder boundaries."""
    config = RecorderConfig()
    shared_cache = cache or LatestMessageCache()
    active_recorder = recorder or RolloutRecorder(config, shared_cache)
    labels = label_store or LabelStore(config.data_root)
    previews = preview_manager or PreviewJobManager()
    artifact_cache: Dict[Tuple[Path, str], Any] = {}
    artifact_cache_lock = threading.RLock()
    bridge = ros_bridge_factory(shared_cache)
    lifespan_state = {"started": False}
    active_lease: Dict[str, object] = {"value": None}
    episode_mutation_lock = threading.RLock()
    discarded_replies: Dict[str, Dict[str, object]] = {}

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        lifespan_state["started"] = True
        bridge.start()
        try:
            yield
        finally:
            bridge.shutdown()
            previews.shutdown()
            lifespan_state["started"] = False

    application = FastAPI(
        title="Task5 rollout recorder", version="1", lifespan=lifespan
    )

    def selected_labels(data_root: Optional[str]) -> LabelStore:
        if data_root is None:
            return labels
        return LabelStore(Path(migrated_path(data_root)).expanduser())

    def selected_artifacts(data_root: Optional[str], episode_uuid: UUID):
        """Resolve one finalized episode once, then reuse its immutable paths."""
        store = selected_labels(data_root)
        key = (store.data_root, str(episode_uuid))
        with artifact_cache_lock:
            cached = artifact_cache.get(key)
            if (
                cached is not None
                and cached.episode_path.is_file()
                and not cached.episode_path.is_symlink()
            ):
                return cached
            artifact_cache.pop(key, None)
            resolved = store.get_episode_artifacts(episode_uuid)
            artifact_cache[key] = resolved
            return resolved

    def prepare_workflow(
        *,
        data_root: Union[Path, str],  # noqa: UP007 -- FastAPI runs on Python 3.8.
        task_id: str,
        model_id: str,
        dataset_round: str,
        storage_layout: str = "legacy",
    ) -> Tuple[PreparedSeries, LabelStore, Dict[str, object]]:
        identity = SeriesIdentity(
            task_id=task_id,
            model_id=model_id,
            dataset_round=dataset_round,
            storage_layout=storage_layout,
        )
        prepared = prepare_series(data_root, identity)
        store = LabelStore(prepared.data_root)
        if storage_layout == "flat" and str(active_recorder.status().get("state")) in {"idle","stopped"}:
            from .recovery import quarantine_failed_files
            quarantine_failed_files(prepared.episode_directory,identity)
        inspection = inspect_prepared_series(
            store, prepared, identity,
            require_labels=require_previous_labels or storage_layout != "flat",
        )
        return prepared, store, inspection

    application.state.inspect_storage = prepare_workflow

    def current_readiness() -> Dict[str, object]:
        if readiness_provider is not None:
            return dict(readiness_provider())
        if not lifespan_state["started"]:
            return {"status": "ok"}
        return evaluate_readiness(
            bridge.status(), shared_cache.snapshot(monotonic)
        )

    def acquire_writer() -> object:
        lease = None
        if writer_coordinator is not None:
            lease = writer_coordinator.acquire_writer("rlt")
        if capture_gate is not None:
            capture_gate.open()
        active_lease["value"] = lease
        return lease

    def close_capture_gate() -> None:
        if capture_gate is not None:
            capture_gate.close()

    def release_writer() -> None:
        lease = active_lease.get("value")
        if writer_coordinator is not None and lease is not None:
            writer_coordinator.release_writer(lease)
        active_lease["value"] = None
        active_lease.pop("episode_relative_path", None)
        active_lease.pop("data_root", None)
        active_lease.pop("episode_uuid", None)

    def recover_failed_recorder():
        with episode_mutation_lock:
            result=active_recorder.recover_error()
            close_capture_gate()
            release_writer()
            return result

    def release_completed_writer():
        """Release an orphan lease only after the recorder committed its file."""
        with episode_mutation_lock:
            if active_lease.get("value") is None:
                return
            raw = active_recorder.status()
            if (raw.get("state") not in {"idle", "stopped"} or raw.get("active")
                    or raw.get("writer_thread_alive") or raw.get("acquisition_active")
                    or raw.get("publication_status") != "committed"):
                raise RuntimeError("pending_episode_finalization")
            close_capture_gate()
            release_writer()

    application.state.release_completed_writer = release_completed_writer

    application.state.recover_failed_recorder=recover_failed_recorder

    def release_writer_if_finalized(episode_uuid) -> None:
        if active_lease.get("value") is None:
            return
        if str(active_recorder.status().get("state", "unknown")) in {"idle", "stopped"}:
            # Reviewing a historical episode must not release the current episode's lease.
            try:
                store = ExactEpisodeLabelStore(
                    active_lease["data_root"], active_lease["episode_relative_path"]
                )
                store._find_record(episode_uuid)
            except (LabelNotFoundError, LabelValidationError, KeyError):
                return
            close_capture_gate()
            release_writer()

    @application.get("/healthz")
    def healthz() -> Dict[str, object]:
        return current_readiness()

    @application.get("/api/status")
    def status() -> Dict[str, object]:
        try:
            public = _public_status(active_recorder.status())
            snapshot = shared_cache.snapshot(monotonic)
            mode_available = snapshot.has_value("handover_mode")
            raw_mode = snapshot.get("handover_mode") if mode_available else None
            mode = raw_mode if isinstance(raw_mode, str) else "unknown"
            pair = parse_handover_mode(mode, is_fresh=mode_available)
            bridge_status = bridge.status()
            readiness = current_readiness()
            ros_state = bridge_status["state"]
            if lifespan_state["started"]:
                ros_state = "ready" if readiness["status"] == "ok" else "not_ready"
            public.update(
                {
                    "ros_state": ros_state,
                    "ros_error_code": readiness.get("error_code"),
                    "handover_mode": mode,
                    "control_source_left": pair.left.name.lower(),
                    "control_source_right": pair.right.name.lower(),
                }
            )
            return public
        except Exception as error:
            raise _http_error(500, "recorder_status_failed") from error

    @application.get("/api/cameras/{camera_key}.mjpeg")
    def camera_preview(camera_key: str) -> StreamingResponse:
        if camera_key not in CAMERA_KEYS:
            raise _http_error(404, "camera_not_found")
        try:
            encode_latest_jpeg(shared_cache, camera_key)
            stream = iter_mjpeg(shared_cache, camera_key)
        except CameraPreviewUnavailable as error:
            raise _http_error(503, "camera_preview_unavailable") from error
        except (TypeError, ValueError) as error:
            raise _http_error(422, "invalid_camera_preview") from error
        return StreamingResponse(
            stream,
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    @application.post("/api/storage/prepare")
    def prepare_storage(request: StoragePrepareRequest) -> Dict[str, object]:
        try:
            prepared, _store, inspection = prepare_workflow(
                data_root=request.data_root,
                task_id=request.task_id,
                model_id=request.model_id,
                dataset_round=request.dataset_round,
                storage_layout=request.storage_layout,
            )
            return {
                "data_root": str(prepared.data_root),
                "episode_directory": str(prepared.episode_directory),
                "existing_episode_indices": list(prepared.existing_indices),
                "next_episode_index": prepared.next_episode_index,
                **inspection,
            }
        except (StoragePathError, LabelValidationError, TypeError, ValueError) as error:
            raise _http_error(422, "invalid_storage_request") from error
        except Exception as error:
            raise _http_error(500, "storage_prepare_failed") from error

    @application.post("/api/episodes/start")
    def start_episode(request: StartEpisodeRequest) -> Dict[str, object]:
        with episode_mutation_lock:
            return start_owned_episode(request)

    def start_owned_episode(request: StartEpisodeRequest) -> Dict[str, object]:
        try:
            prepared, _store, inspection = prepare_workflow(
                data_root=request.data_root or labels.data_root,
                task_id=request.task_id,
                model_id=request.model_id,
                dataset_round=request.dataset_round,
                storage_layout=request.storage_layout,
            )
            if inspection["label_blocked"]:
                raise _http_error(409, "latest_episode_labels_required")
            episode_index = request.episode_index
            if episode_index is None:
                episode_index = prepared.next_episode_index
            identity = EpisodeIdentity(
                task_id=request.task_id,
                model_id=request.model_id,
                checkpoint_id=request.checkpoint_id,
                dataset_round=request.dataset_round,
                episode_index=episode_index,
                storage_layout=request.storage_layout,
            )
            recorder_request = RecorderStartRequest(
                identity=identity,
                max_timesteps=request.max_timesteps,
                data_root=prepared.data_root,
            )
            # Wait only for a fresh input snapshot, before opening any writer.
            # Do not retry start/finish requests or loosen freshness thresholds.
            ready = current_readiness()
            deadline = time.monotonic() + 1.0
            while ready["status"] != "ok" and ready.get("error_code") in {"camera_stale", "handover_stale"} and time.monotonic() < deadline:
                time.sleep(0.04)
                ready = current_readiness()
            if ready["status"] != "ok":
                detail = "recorder_not_ready: " + str(ready.get("error_code") or "unknown")
                if ready.get("stale_keys"):
                    detail += ": " + ",".join(ready["stale_keys"])
                LOGGER.warning("RLT recorder preflight rejected: %s", detail)
                raise _http_error(503, detail)
            state = str(active_recorder.status().get("state", "unknown"))
            if state not in {"idle", "stopped"}:
                raise _http_error(409, "recorder_already_started")
            acquire_writer()
            active_lease["data_root"] = prepared.data_root
            active_lease["episode_uuid"] = str(identity.episode_uuid)
            active_lease["episode_relative_path"] = str(
                (prepared.episode_directory / f"episode_{episode_index:06d}.hdf5").relative_to(prepared.data_root)
            )
            try:
                return {**_public_status(active_recorder.start(recorder_request)),
                        "episode_uuid": str(identity.episode_uuid)}
            except Exception:
                close_capture_gate()
                release_writer()
                raise
        except HTTPException:
            raise
        except RecorderError as error:
            raise _http_error(500, "recorder_start_failed") from error
        except FileExistsError as error:
            raise _http_error(409, "episode_already_exists") from error
        except PreflightError as error:
            LOGGER.warning("RLT recorder preflight rejected: %s", error)
            raise _http_error(503, "recorder_not_ready: " + str(error)) from error
        except (StoragePathError, TypeError, ValueError) as error:
            LOGGER.exception("RLT recorder invalid start request (%s)", type(error).__name__)
            raise _http_error(422, f"invalid_start_request: {type(error).__name__}") from error
        except Exception as error:
            raise _http_error(500, "recorder_start_failed") from error

    @application.post("/api/episodes/stop")
    def stop_episode() -> Dict[str, object]:
        try:
            state = str(active_recorder.status().get("state", "unknown"))
            if state in {"error", "fatal"}:
                raise _http_error(500, "recorder_failed")
            if state not in {"starting", "recording", "stopping"}:
                raise _http_error(409, "no_active_recording")
            active_recorder.stop()
            close_capture_gate()
            return _public_status(active_recorder.status())
        except HTTPException:
            raise
        except RecorderError as error:
            raise _http_error(500, "recorder_stop_failed") from error
        except Exception as error:
            raise _http_error(500, "recorder_stop_failed") from error

    @application.post("/api/episodes/discard")
    def discard_episode(request: DeleteEpisodeRequest) -> Dict[str, object]:
        # The UUID returned by start binds discard to the owned writer, including
        # empty episodes that never appear in the finalized history list.
        with episode_mutation_lock:
            uuid = str(request.episode_uuid)
            if uuid in discarded_replies:
                return dict(discarded_replies[uuid])
            if uuid != active_lease.get("episode_uuid") or "data_root" not in active_lease:
                raise _http_error(409, "discard_episode_mismatch")
            root = Path(active_lease["data_root"]).resolve()
            episode = root / str(active_lease["episode_relative_path"])
            if root not in episode.resolve().parents:
                raise _http_error(409, "discard_path_mismatch")
            if previews.is_busy(uuid):
                raise _http_error(409, "preview_active")
            try:
                close_capture_gate()
                state = str(active_recorder.status().get("state", "unknown"))
                if state in {"starting", "recording", "stopping"}:
                    active_recorder.stop()
                deadline = time.monotonic() + 8
                while str(active_recorder.status().get("state")) == "stopping" and time.monotonic() < deadline:
                    time.sleep(.05)
                status = active_recorder.status()
                if status.get("state") not in {"idle", "stopped"} or status.get("writer_thread_alive") or status.get("acquisition_active"):
                    raise _http_error(409, "recorder_not_stopped")
                record = permanently_delete_episode(
                    episode, episode.with_suffix(".labels.json"), episode.parent / ".previews" / uuid,
                    expected_uuid=uuid, incomplete_episode_path=Path(str(episode) + ".incomplete"),
                    record_deletion=False,
                )
                previews.forget(uuid)
                with artifact_cache_lock:
                    artifact_cache.pop((root, uuid), None)
                forget = getattr(active_recorder, "forget_discarded_episode", None)
                if forget is not None:
                    forget(episode)
                release_writer()
                result = {"discarded": True, "episode_uuid": uuid, "episode_index": record.episode_index}
                discarded_replies[uuid] = result
                # Transport retries are idempotent without a persistent ledger.
                while len(discarded_replies) > 16:
                    discarded_replies.pop(next(iter(discarded_replies)))
                return result
            except HTTPException:
                raise
            except DeletionError as error:
                LOGGER.exception("discard cleanup failed")
                raise _http_error(500, "episode_discard_failed") from error

    def set_capture_enabled(enabled: bool) -> Dict[str, object]:
        if capture_gate is None:
            raise _http_error(503, "capture_gate_unavailable")
        if str(active_recorder.status().get("state", "unknown")) != "recording":
            raise _http_error(409, "no_active_recording")
        if enabled:
            capture_gate.open()
        else:
            capture_gate.close()
        status = _public_status(active_recorder.status())
        status["capture_enabled"] = capture_gate.is_open()
        return status

    @application.post("/api/episodes/capture/pause")
    def pause_episode_capture() -> Dict[str, object]:
        return set_capture_enabled(False)

    @application.post("/api/episodes/capture/resume")
    def resume_episode_capture() -> Dict[str, object]:
        return set_capture_enabled(True)

    @application.get("/api/episodes")
    def list_episodes(data_root: Optional[str] = None) -> List[Dict[str, object]]:
        try:
            return selected_labels(data_root).list_episodes()
        except Exception as error:
            raise _http_error(500, "episode_list_failed") from error

    @application.get("/api/episodes/{episode_uuid}/labels")
    def get_episode_labels(
        episode_uuid: UUID, data_root: Optional[str] = None
    ) -> Dict[str, object]:
        try:
            store = selected_labels(data_root)
            response = store.get_labels(episode_uuid)
            response["intervention_phases"] = store.get_intervention_phases(
                episode_uuid
            )
            return response
        except LabelNotFoundError as error:
            raise _http_error(404, "episode_not_found") from error
        except LabelConflictError as error:
            raise _http_error(409, str(error)) from error
        except LabelValidationError as error:
            raise _http_error(422, "invalid_label_sidecar") from error
        except Exception as error:
            raise _http_error(500, "label_read_failed") from error

    @application.post("/api/episodes/{episode_uuid}/outcome")
    def set_episode_outcome(
        episode_uuid: UUID,
        request: OutcomeUpdateRequest,
        data_root: Optional[str] = None,
        episode_relative_path: Optional[str] = None,
    ) -> Dict[str, object]:
        if request.episode_uuid != episode_uuid:
            raise _http_error(409, "episode_uuid_mismatch")
        try:
            result = (ExactEpisodeLabelStore(data_root or labels.data_root, episode_relative_path) if episode_relative_path is not None else selected_labels(data_root)).set_outcome(
                episode_uuid, request.outcome
            )
            release_writer_if_finalized(episode_uuid)
            return result
        except LabelNotFoundError as error:
            raise _http_error(404, "episode_not_found") from error
        except LabelConflictError as error:
            raise _http_error(409, str(error)) from error
        except LabelValidationError as error:
            raise _http_error(422, "invalid_outcome") from error
        except Exception as error:
            raise _http_error(500, "outcome_write_failed") from error

    @application.post("/api/episodes/{episode_uuid}/preview")
    def generate_episode_replay(
        episode_uuid: UUID, data_root: Optional[str] = None
    ) -> Dict[str, object]:
        try:
            artifacts = selected_artifacts(data_root, episode_uuid)
            return previews.queue(artifacts)
        except LabelNotFoundError as error:
            raise _http_error(404, "episode_not_found") from error
        except PreviewQueueFull as error:
            raise _http_error(503, "preview_queue_full") from error
        except Exception as error:
            raise _http_error(500, "preview_queue_failed") from error

    @application.get("/api/episodes/{episode_uuid}/preview/status")
    def episode_replay_status(
        episode_uuid: UUID, data_root: Optional[str] = None
    ) -> Dict[str, object]:
        try:
            artifacts = selected_artifacts(data_root, episode_uuid)
            state = previews.status(str(episode_uuid))
            if state["state"] == "missing":
                state = previews.queue(artifacts)
            return state
        except LabelNotFoundError as error:
            raise _http_error(404, "episode_not_found") from error
        except PreviewQueueFull as error:
            raise _http_error(503, "preview_queue_full") from error
        except Exception as error:
            raise _http_error(500, "preview_status_failed") from error

    @application.get("/api/episodes/{episode_uuid}/preview.mp4")
    def stream_episode_replay(
        episode_uuid: UUID, data_root: Optional[str] = None
    ) -> FileResponse:
        try:
            artifacts = selected_artifacts(data_root, episode_uuid)
            path = previews.preview_path(artifacts)
            return FileResponse(
                path,
                media_type="video/mp4",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
        except LabelNotFoundError as error:
            raise _http_error(404, "episode_not_found") from error
        except PreviewNotReady as error:
            raise _http_error(409, "preview_not_ready") from error
        except Exception as error:
            raise _http_error(500, "preview_read_failed") from error

    @application.get(
        "/api/episodes/{episode_uuid}/frames/{frame_index}/{camera_key}.jpg"
    )
    def episode_camera_frame(
        episode_uuid: UUID,
        frame_index: int,
        camera_key: str,
        data_root: Optional[str] = None,
    ) -> Response:
        try:
            artifacts = selected_artifacts(data_root, episode_uuid)
            payload = read_episode_frame_jpeg(
                artifacts.episode_path,
                frame_index,
                camera_key,
                cache_directory=artifacts.preview_directory,
            )
            return Response(
                content=payload,
                media_type="image/jpeg",
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
        except LabelNotFoundError as error:
            raise _http_error(404, "episode_not_found") from error
        except EpisodeFrameError as error:
            raise _http_error(422, str(error)) from error
        except Exception as error:
            raise _http_error(500, "episode_frame_read_failed") from error

    @application.get("/api/episodes/{episode_uuid}/preview/{artifact_name}")
    def stream_episode_preview_artifact(
        episode_uuid: UUID,
        artifact_name: Literal["contact-sheet.jpg", "qpos.png"],
        data_root: Optional[str] = None,
    ) -> FileResponse:
        key = "contact_sheet" if artifact_name == "contact-sheet.jpg" else "qpos"
        media_type = "image/jpeg" if key == "contact_sheet" else "image/png"
        try:
            artifacts = selected_artifacts(data_root, episode_uuid)
            path = previews.artifact_path(artifacts, key)
            return FileResponse(
                path,
                media_type=media_type,
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
        except LabelNotFoundError as error:
            raise _http_error(404, "episode_not_found") from error
        except PreviewNotReady as error:
            raise _http_error(409, "preview_not_ready") from error
        except Exception as error:
            raise _http_error(500, "preview_artifact_read_failed") from error

    @application.delete("/api/episodes/{episode_uuid}")
    def delete_episode(
        episode_uuid: UUID,
        request: DeleteEpisodeRequest,
        data_root: Optional[str] = None,
    ) -> Dict[str, object]:
        if request.episode_uuid != episode_uuid:
            raise _http_error(409, "episode_uuid_mismatch")
        state = str(active_recorder.status().get("state", "unknown"))
        if state not in {"idle", "stopped"}:
            raise _http_error(409, "recorder_active")
        if previews.is_busy(str(episode_uuid)):
            raise _http_error(409, "preview_active")
        try:
            artifacts = selected_artifacts(data_root, episode_uuid)
            record = permanently_delete_episode(
                artifacts.episode_path,
                artifacts.sidecar_path,
                artifacts.preview_directory,
                expected_uuid=str(artifacts.episode_uuid),
            )
            previews.forget(str(episode_uuid))
            with artifact_cache_lock:
                artifact_cache.pop((selected_labels(data_root).data_root, str(episode_uuid)), None)
            return {
                "deleted": True,
                "episode_uuid": record.episode_uuid,
                "episode_index": record.episode_index,
            }
        except LabelNotFoundError as error:
            raise _http_error(404, "episode_not_found") from error
        except DeletionError as error:
            raise _http_error(409, "episode_delete_refused") from error
        except Exception as error:
            raise _http_error(500, "episode_delete_failed") from error

    @application.put("/api/episodes/{episode_uuid}/labels")
    def put_episode_labels(
        episode_uuid: UUID,
        request: LabelUpdateRequest,
        data_root: Optional[str] = None,
        episode_relative_path: Optional[str] = None,
    ) -> Dict[str, object]:
        update = request.model_dump(exclude_unset=True, mode="json")
        try:
            result = (ExactEpisodeLabelStore(data_root or labels.data_root, episode_relative_path) if episode_relative_path is not None else selected_labels(data_root)).update_labels(episode_uuid, update)
            release_writer_if_finalized(episode_uuid)
            return result
        except LabelNotFoundError as error:
            raise _http_error(404, "episode_not_found") from error
        except LabelConflictError as error:
            raise _http_error(409, str(error)) from error
        except LabelValidationError as error:
            raise _http_error(422, "invalid_labels") from error
        except Exception as error:
            raise _http_error(500, "label_write_failed") from error

    frontend = Path(__file__).resolve().parents[1] / "frontend"
    if mount_frontend and frontend.is_dir():
        application.mount(
            "/", StaticFiles(directory=frontend, html=True), name="frontend"
        )
    return application


app = create_app()
