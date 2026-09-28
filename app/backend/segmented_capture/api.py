"""Standalone no-control HTTP application for segmented expert capture."""

from __future__ import annotations

import os
import time
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal
from uuid import UUID

from capture_core.asset_storage import migrated_path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import Annotated

from capture_core.camera_preview import encode_latest_jpeg, iter_mjpeg
from capture_core.labels import LabelNotFoundError
from capture_core.preview_jobs import PreviewJobManager, PreviewNotReady, PreviewQueueFull
from capture_core.config import RecorderConfig
from capture_core.deletion import DeletionError
from capture_core.readiness import evaluate_readiness
from capture_core.recorder import RolloutRecorder
from capture_core.ros_cache import LatestMessageCache
from capture_core.ros_subscriber import RosSubscriberBridge
from capture_core.schema import IDENTIFIER_RE, EpisodeIdentity
from capture_core.storage import SeriesIdentity, StoragePathError, prepare_series
from capture_core.topics import CAMERA_KEYS

from .capture_service import SegmentedCaptureError, SegmentedCaptureService
from .ports import CaptureGate
from .review import SegmentReviewError
from .state import InvalidCaptureEvent

LOGGER = logging.getLogger(__name__)

Identifier = Annotated[str, Field(pattern=IDENTIFIER_RE.pattern)]
DEFAULT_DATA_ROOT = Path(
    os.environ.get(
        "TASK5_SEGMENTED_DATA_ROOT",
        str(Path(__file__).resolve().parents[3] / "data/raw"),
    )
)
DEFAULT_ALLOWED_DATA_ROOT = Path(
    os.environ.get(
        "TASK5_SEGMENTED_ALLOWED_DATA_ROOT",
        str(Path(__file__).resolve().parents[3] / "data"),
    )
)


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_root: str
    task_id: Identifier = "plug"
    model_id: Identifier = "expert"
    checkpoint_id: Identifier = "manual"
    dataset_round: Identifier = "collection"
    storage_layout: Literal["legacy", "flat"] = "legacy"
    episode_index: Optional[int] = Field(default=None, ge=0, strict=True)
    use_model: bool = False
    collection_model_id: Optional[str] = None


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_uuid: UUID
    generation: int = Field(ge=0, strict=True)


class StopRequest(VersionRequest):
    outcome: Optional[Literal["success", "failure", "unknown"]] = None


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    selected_interval_ids: List[int]
    note: Optional[str] = Field(default=None, max_length=500)


def _buttons(state: str) -> Dict[str, bool]:
    return {
        "pause": state == "recording",
        "resume": state == "paused",
        "marker": state == "recording",
        "stop": state in {"recording", "paused"},
        "discard": state in {"recording", "paused"},
    }


def _public(service: Any) -> Dict[str, object]:
    payload = dict(service.status())
    payload["buttons"] = _buttons(str(payload.get("capture_state", "idle")))
    return payload


def create_app(
    *,
    service: Optional[Any] = None,
    bridge: Optional[Any] = None,
    cache: Optional[LatestMessageCache] = None,
    allowed_data_root: Optional[Path] = None,
    preview_manager: Optional[Any] = None,
    monotonic: Any = time.monotonic,
    writer_coordinator: Optional[Any] = None,
    mount_frontend: bool = True,
) -> FastAPI:
    previews = preview_manager or PreviewJobManager()
    shared_cache = cache
    if service is None:
        shared_cache = shared_cache or LatestMessageCache()
        gate = CaptureGate(enabled=False)
        data_root = DEFAULT_DATA_ROOT.resolve()
        config = RecorderConfig(
            data_root=data_root,
            sample_rate_hz=30.0,
            max_duration_seconds=300.0,
            min_free_disk_bytes=30 * 1024**3,
            stop_free_disk_bytes=20 * 1024**3,
            api_port=int(os.environ.get("TASK5_SEGMENTED_PORT", "8015")),
        )
        recorder = RolloutRecorder(config, shared_cache, capture_gate=gate)
        service = SegmentedCaptureService(
            recorder=recorder,
            cache=shared_cache,
            gate=gate,
            sidecar_root=None,
        )
        allowed_data_root = DEFAULT_ALLOWED_DATA_ROOT
    active_bridge = bridge or RosSubscriberBridge(shared_cache)
    started = {"value": False}
    active_lease: Dict[str, object] = {"value": None}

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        started["value"] = True
        active_bridge.start()
        try:
            yield
        finally:
            previews.shutdown()
            active_bridge.shutdown()
            started["value"] = False

    application = FastAPI(
        title="Task5 segmented teach recorder", version="1", lifespan=lifespan
    )

    def model_control():
        return getattr(application.state, "collection_model", None)

    def public():
        payload = _public(service)
        controller = model_control()
        if controller:
            model = controller.status()
            payload["collection_model"] = model
            if model["enabled"] and service.active:
                payload["buttons"]["pause"] = model["running"] or payload["buttons"]["pause"]
                payload["buttons"]["resume"] = not model["running"] and not any((payload.get("teach_mask") or {}).values())
        return payload

    def data_roots():
        import json
        primary = (allowed_data_root or DEFAULT_ALLOWED_DATA_ROOT).expanduser().resolve()
        additional = json.loads(os.environ.get("COBOT_ADDITIONAL_DATA_ROOTS", "[]"))
        return [primary] + [Path(item).expanduser().resolve() for item in additional if Path(item).is_absolute()]

    def containing_root(path):
        for root in data_roots():
            try:
                path.resolve().relative_to(root)
                return root
            except ValueError:
                pass
        raise HTTPException(status_code=422, detail="data_root_not_allowed")

    def selected_root(value: Optional[str]) -> Optional[Path]:
        if value is None and allowed_data_root is None:
            return None
        candidate = DEFAULT_DATA_ROOT if value is None else Path(migrated_path(value)).expanduser()
        if not candidate.is_absolute():
            raise HTTPException(status_code=422, detail="data_root_must_be_absolute")
        resolved = candidate.resolve()
        if allowed_data_root is not None:
            containing_root(resolved)
        return resolved

    def prepare(request: StartRequest):
        root = selected_root(request.data_root)
        assert root is not None
        prepared = prepare_series(
            root,
            SeriesIdentity(request.task_id, request.model_id, request.dataset_round, request.storage_layout),
            reuse_deleted_indices=False,
        )
        return prepared

    def current_readiness() -> Dict[str, object]:
        state = active_bridge.status()
        if not started["value"] or shared_cache is None:
            return {
                "status": "ok" if state.get("state") == "ready" else "not_ready",
                "error_code": state.get("error_code"),
            }
        return evaluate_readiness(state, shared_cache.snapshot(float(monotonic())))

    @application.get("/healthz")
    def healthz() -> Dict[str, object]:
        return current_readiness()

    @application.get("/api/segmented-teach/status")
    def status() -> Dict[str, object]:
        return public()

    @application.get("/api/segmented-teach/storage/directories")
    def directories(path: str = "") -> Dict[str, object]:
        allowed = (allowed_data_root or DEFAULT_ALLOWED_DATA_ROOT).expanduser().resolve()
        value = path.strip() or str(allowed) + "/"
        candidate = Path(migrated_path(value)).expanduser()
        if not candidate.is_absolute():
            raise HTTPException(status_code=422, detail="data_root_must_be_absolute")
        allowed = containing_root(candidate)
        if value.endswith("/") or candidate == allowed:
            parent, prefix = candidate, ""
        else:
            parent, prefix = candidate.parent, candidate.name
        resolved = parent.resolve()
        try:
            resolved.relative_to(allowed)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="data_root_not_allowed") from error
        if not resolved.exists():
            return {"directory": str(resolved), "exists": False, "directories": [], "truncated": False}
        if not resolved.is_dir():
            raise HTTPException(status_code=422, detail="data_root_not_directory")
        try:
            children = sorted(p for p in resolved.iterdir()
                              if not p.name.startswith(".") and p.name.startswith(prefix)
                              and not p.is_symlink() and p.is_dir())
        except OSError as error:
            raise HTTPException(status_code=422, detail="directory_not_readable") from error
        return {"directory": str(resolved), "exists": True,
                "directories": [str(p) for p in children[:256]], "truncated": len(children) > 256}

    @application.post("/api/segmented-teach/storage/prepare")
    def prepare_storage(request: StartRequest) -> Dict[str, object]:
        try:
            prepared = prepare(request)
            return {
                "data_root": str(prepared.data_root),
                "episode_directory": str(prepared.episode_directory),
                "next_episode_index": prepared.next_episode_index,
                "existing_indices": list(prepared.existing_indices),
                "deleted_indices": list(prepared.deleted_indices),
            }
        except HTTPException:
            raise
        except (StoragePathError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.post("/api/segmented-teach/start")
    def start(request: StartRequest) -> Dict[str, object]:
        try:
            readiness = current_readiness()
            if readiness["status"] != "ok":
                code = readiness.get("error_code") or "unknown"
                raise HTTPException(status_code=503, detail="recorder_not_ready: " + str(code))
            prepared = prepare(request)
            root = prepared.data_root
            index = prepared.next_episode_index if request.episode_index is None else request.episode_index
            identity = EpisodeIdentity(
                request.task_id,
                request.model_id,
                request.checkpoint_id,
                request.dataset_round,
                episode_index=index,
                storage_layout=request.storage_layout,
            )
            lease = None
            if writer_coordinator is not None:
                lease = writer_coordinator.acquire_writer("normal")
                active_lease["value"] = lease
            try:
                controller = model_control()
                if request.use_model and not controller:
                    raise HTTPException(409, "采集模型服务未启动")
                if controller:
                    try:
                        controller.before_start(request.use_model, request.collection_model_id)
                    except RuntimeError as error:
                        raise HTTPException(409, str(error)) from error
                result = service.start(identity, data_root=root)
                if controller:
                    controller.action("start")
            except Exception:
                if model_control():
                    model_control().action("pause")
                if getattr(service, "active", False):
                    service.discard()
                if model_control():
                    model_control().finished()
                if writer_coordinator is not None and lease is not None:
                    writer_coordinator.release_writer(lease)
                    active_lease["value"] = None
                raise
            result = dict(result)
            result["buttons"] = _buttons(str(result["capture_state"]))
            return public()
        except (FileExistsError, InvalidCaptureEvent, SegmentedCaptureError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except HTTPException:
            raise
        except (StoragePathError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail="invalid_start_request") from error
        except Exception as error:
            raise HTTPException(status_code=500, detail="segmented_start_failed") from error

    def guarded(request: VersionRequest, operation: str) -> Dict[str, object]:
        current = service.status()
        if (
            str(request.episode_uuid) != str(current.get("episode_uuid"))
            or request.generation != int(current.get("generation", 0))
        ):
            raise HTTPException(status_code=409, detail="stale_capture_generation")
        if not public()["buttons"].get(operation, False):
            raise HTTPException(status_code=409, detail=f"{operation}_not_allowed")
        try:
            controller = model_control()
            if controller and operation == "pause":
                controller.action("pause")
            redundant = (operation == "pause" and current.get("capture_state") == "paused") or (operation == "resume" and current.get("capture_state") == "recording")
            if not redundant:
                result = getattr(service, operation)()
            else:
                result = service.status()
            if controller and operation == "resume":
                try:
                    controller.action("resume")
                except Exception:
                    service.pause()
                    raise
            result = dict(result)
            result["buttons"] = _buttons(str(result["capture_state"]))
            return public()
        except (InvalidCaptureEvent, SegmentedCaptureError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=500, detail=f"{operation}_failed") from error

    @application.post("/api/segmented-teach/pause")
    def pause(request: VersionRequest) -> Dict[str, object]:
        return guarded(request, "pause")

    @application.post("/api/segmented-teach/resume")
    def resume(request: VersionRequest) -> Dict[str, object]:
        return guarded(request, "resume")

    @application.post("/api/segmented-teach/marker")
    def marker(request: VersionRequest) -> Dict[str, object]:
        return guarded(request, "marker")

    @application.post("/api/segmented-teach/stop")
    def stop(request: StopRequest) -> Dict[str, object]:
        current = service.status()
        same_episode = str(request.episode_uuid) == str(current.get("episode_uuid"))

        def finalized() -> bool:
            check = getattr(service, "finalized_successfully", None)
            return bool(check()) if callable(check) else False

        def release_lease() -> None:
            lease = active_lease.get("value")
            if writer_coordinator is not None and lease is not None:
                writer_coordinator.release_writer(lease)
                active_lease["value"] = None

        def final_response():
            release_lease()
            if model_control():
                model_control().finished()
            if request.outcome is not None:
                try:
                    service.label_outcome(request.outcome)
                except Exception as error:
                    raise HTTPException(503, "数据已保存，结果标注失败：" + str(error)) from error
            return _public(service)

        # Make stop idempotent only for a proven, fully finalized episode.  This
        # covers a lost HTTP response and a failure while releasing the writer
        # lease without concealing recorder or sidecar failures.
        if same_episode and str(current.get("capture_state")) == "committed" and finalized():
            return final_response()
        if not same_episode or request.generation != int(current.get("generation", 0)):
            raise HTTPException(status_code=409, detail="stale_capture_generation")
        if not _buttons(str(current.get("capture_state", "idle")))["stop"]:
            raise HTTPException(status_code=409, detail="stop_not_allowed")
        try:
            if model_control():
                model_control().action("stop")
            service.stop()
        except Exception as error:
            # stop() may have completed atomically before a later lease/reply
            # step failed.  Return the verified committed result in that case.
            if finalized():
                return final_response()
            LOGGER.exception("segmented stop failed before durable finalize")
            raise HTTPException(status_code=500, detail="segmented_stop_failed") from error
        return final_response()

    @application.post("/api/segmented-teach/discard")
    def discard(request: VersionRequest) -> Dict[str, object]:
        current = service.status()
        if str(request.episode_uuid) != str(current.get("episode_uuid")) or request.generation != int(current.get("generation", 0)):
            raise HTTPException(status_code=409, detail="stale_capture_generation")
        if not _buttons(str(current.get("capture_state", "idle")))["discard"]:
            raise HTTPException(status_code=409, detail="discard_not_allowed")
        try:
            if model_control():
                model_control().action("discard")
            service.discard()
            if model_control():
                model_control().finished()
            return _public(service)
        except Exception as error:
            raise HTTPException(status_code=500, detail="segmented_discard_failed") from error
        finally:
            # A delete failure must not leave a finalized writer leased forever.
            if not service.active:
                lease = active_lease.get("value")
                if writer_coordinator is not None and lease is not None:
                    writer_coordinator.release_writer(lease)
                    active_lease["value"] = None

    @application.get("/api/segmented-teach/episodes")
    def episodes(
        data_root: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, object]]:
        if offset < 0 or (limit is not None and (limit < 1 or limit > 200)):
            raise HTTPException(status_code=422, detail="invalid_history_page")
        root = selected_root(data_root)
        if limit is None and offset == 0:
            return service.list_episodes(data_root=root)
        return service.list_episodes(data_root=root, limit=limit, offset=offset)

    @application.get("/api/segmented-teach/episodes/{episode_uuid}")
    def episode(
        episode_uuid: UUID, data_root: Optional[str] = None
    ) -> Dict[str, object]:
        try:
            return service.read_episode(
                str(episode_uuid), data_root=selected_root(data_root)
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="episode_not_found") from error

    @application.get(
        "/api/segmented-teach/episodes/{episode_uuid}/nodes/{node_id}/{camera_key}.jpg"
    )
    def keyframe(
        episode_uuid: UUID,
        node_id: int,
        camera_key: str,
        data_root: Optional[str] = None,
    ) -> FileResponse:
        try:
            return FileResponse(
                service.keyframe_path(
                    str(episode_uuid),
                    node_id,
                    camera_key,
                    data_root=selected_root(data_root),
                )
            )
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail="keyframe_not_found") from error

    @application.get("/api/segmented-teach/episodes/{episode_uuid}/review")
    def review(
        episode_uuid: UUID, data_root: Optional[str] = None
    ) -> Dict[str, object]:
        try:
            return service.load_review(
                str(episode_uuid), data_root=selected_root(data_root)
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="episode_not_found") from error
        except SegmentReviewError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.put("/api/segmented-teach/episodes/{episode_uuid}/review")
    def save_review(
        episode_uuid: UUID,
        request: ReviewRequest,
        data_root: Optional[str] = None,
    ) -> Dict[str, object]:
        try:
            return service.save_review(
                str(episode_uuid),
                expected_revision=request.expected_revision,
                selected_interval_ids=request.selected_interval_ids,
                note=request.note,
                data_root=selected_root(data_root),
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="episode_not_found") from error
        except SegmentReviewError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    def replay_artifacts(episode_uuid, data_root):
        root = selected_root(data_root)
        try:
            payload = service.read_episode(str(episode_uuid), data_root=root)
            if payload.get("capture_state") != "committed":
                raise HTTPException(status_code=409, detail="episode_not_committed")
            return service.replay_artifacts(str(episode_uuid), data_root=root or DEFAULT_DATA_ROOT)
        except (ValueError, SegmentedCaptureError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (FileNotFoundError, LabelNotFoundError) as error:
            raise HTTPException(status_code=404, detail="episode_not_found") from error

    @application.get("/api/segmented-teach/episodes/{episode_uuid}/preview")
    def replay_status(episode_uuid: UUID, data_root: Optional[str] = None):
        artifacts = replay_artifacts(episode_uuid, data_root)
        try:
            state = previews.status(str(episode_uuid)) if hasattr(previews, "status") else {"state":"missing"}
            return previews.queue(artifacts) if state["state"] == "missing" else state
        except PreviewQueueFull as error:
            raise HTTPException(status_code=503, detail="preview_queue_full") from error

    @application.get("/api/segmented-teach/episodes/{episode_uuid}/preview.mp4")
    def replay_video(episode_uuid: UUID, data_root: Optional[str] = None):
        artifacts = replay_artifacts(episode_uuid, data_root)
        try:
            return FileResponse(previews.preview_path(artifacts), media_type="video/mp4",
                                headers={"Cache-Control": "no-store"})
        except PreviewNotReady as error:
            raise HTTPException(status_code=409, detail="preview_not_ready") from error

    @application.get("/api/segmented-teach/episodes/{episode_uuid}/contact-sheet.jpg")
    def replay_contact_sheet(episode_uuid: UUID, data_root: Optional[str] = None):
        artifacts = replay_artifacts(episode_uuid, data_root)
        try:
            return FileResponse(previews.artifact_path(artifacts, "contact_sheet"), media_type="image/jpeg",
                                headers={"Cache-Control": "no-store"})
        except PreviewNotReady as error:
            raise HTTPException(status_code=409, detail="preview_not_ready") from error

    @application.get("/api/segmented-teach/episodes/{episode_uuid}/qpos.png")
    def replay_qpos(episode_uuid: UUID, data_root: Optional[str] = None):
        artifacts = replay_artifacts(episode_uuid, data_root)
        try:
            return FileResponse(previews.artifact_path(artifacts, "qpos"), media_type="image/png",
                                headers={"Cache-Control": "no-store"})
        except PreviewNotReady as error:
            raise HTTPException(status_code=409, detail="preview_not_ready") from error

    @application.delete("/api/segmented-teach/episodes/{episode_uuid}")
    def delete_episode(
        episode_uuid: UUID, data_root: Optional[str] = None
    ) -> Dict[str, object]:
        root = selected_root(data_root)
        if root is None:
            raise HTTPException(status_code=422, detail="data_root_required")
        try:
            if previews.is_busy(str(episode_uuid)):
                raise HTTPException(status_code=409, detail="preview_active")
            result = service.delete_episode(str(episode_uuid), data_root=root)
            previews.forget(str(episode_uuid))
            return result
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="episode_not_found") from error
        except (DeletionError, SegmentedCaptureError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    if shared_cache is not None:
        @application.get("/api/cameras/{camera_key}.mjpeg")
        def camera(camera_key: str) -> StreamingResponse:
            if camera_key not in CAMERA_KEYS:
                raise HTTPException(status_code=404, detail="camera_not_found")
            encode_latest_jpeg(shared_cache, camera_key)
            return StreamingResponse(
                iter_mjpeg(shared_cache, camera_key),
                media_type="multipart/x-mixed-replace; boundary=frame",
            )

    frontend = Path(__file__).resolve().parents[1] / "segmented_frontend"
    if mount_frontend and frontend.is_dir():
        application.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return application


app = create_app()
