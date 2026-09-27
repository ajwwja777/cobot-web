"""Pinned LeRobot 0.4.2 writer for normalized Task5 episode sources."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from functools import partial
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np

from capture_core.hdf5_writer import ROLLOUT_VALIDITY_KEYS
from capture_core.topics import REQUIRED_TOPICS

from .legacy_hdf5 import CAMERAS
from .source_types import EpisodeSource, NormalizedFrame

LEROBOT_VERSION = "0.4.2"
MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_VIDEO_CODECS = {"h264", "hevc", "libsvtav1"}


def _vector(dtype: str, size: int) -> dict[str, object]:
    return {"dtype": dtype, "shape": (size,), "names": None}


def task5_lerobot_features(
    image_shape: tuple[int, ...],
) -> dict[str, dict[str, object]]:
    if len(image_shape) != 3 or image_shape[-1] != 3:
        raise ValueError("LeRobot images must have shape [height,width,3]")
    features: dict[str, dict[str, object]] = {
        f"observation.images.{camera}": {
            "dtype": "video",
            "shape": image_shape,
            "names": ["height", "width", "channels"],
        }
        for camera in CAMERAS
    }
    features.update(
        {
            "observation.state": _vector("float32", 14),
            "observation.velocity": _vector("float32", 14),
            "observation.effort": _vector("float32", 14),
            "action": _vector("float32", 14),
            "task5.base_action": _vector("float32", 2),
            "task5.policy_command": _vector("float32", 14),
            "task5.coordinator_command": _vector("float32", 14),
            "task5.front_observation": _vector("float32", 14),
            "task5.rear_observation": _vector("float32", 14),
            "task5.sample_timestamp": _vector("float64", 1),
            "task5.control_source_left": _vector("uint8", 1),
            "task5.control_source_right": _vector("uint8", 1),
            "task5.intervention_id_left": _vector("uint64", 1),
            "task5.intervention_id_right": _vector("uint64", 1),
            "task5.is_intervention_left": _vector("bool", 1),
            "task5.is_intervention_right": _vector("bool", 1),
            "task5.teach_active_left": _vector("bool", 1),
            "task5.teach_active_right": _vector("bool", 1),
            "task5.handover_mode": {
                "dtype": "string",
                "shape": (1,),
                "names": None,
            },
            "task5.handover_fault": {
                "dtype": "string",
                "shape": (1,),
                "names": None,
            },
            "task5.source_schema": {
                "dtype": "string",
                "shape": (1,),
                "names": None,
            },
        }
    )
    for key in sorted(ROLLOUT_VALIDITY_KEYS):
        features[f"task5.valid.{key}"] = _vector("bool", 1)
    for key in REQUIRED_TOPICS:
        features[f"task5.source_timestamp.{key}"] = _vector("float64", 1)
        features[f"task5.arrival_timestamp.{key}"] = _vector("float64", 1)
    return features


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _official_backend_factory(**kwargs: object) -> Any:
    try:
        from importlib.metadata import version

        installed = version("lerobot")
        if installed != LEROBOT_VERSION:
            raise RuntimeError(
                f"Task5 requires lerobot=={LEROBOT_VERSION}, found {installed}"
            )
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
    except ImportError as error:
        raise RuntimeError(
            "LeRobot runtime missing; install the pinned lerobot==0.4.2 environment"
        ) from error
    return LeRobotDataset.create(**kwargs)


def _task5_encode_video_worker(
    video_key: str,
    episode_index: int,
    root: Path,
    fps: int,
    *,
    video_codec: str,
) -> Path:
    """Encode one official LeRobot video using the archive's pinned codec."""
    from lerobot.datasets.lerobot_dataset import DEFAULT_IMAGE_PATH
    from lerobot.datasets.video_utils import encode_video_frames

    temporary = Path(tempfile.mkdtemp(dir=root)) / f"{video_key}_{episode_index:03d}.mp4"
    first_frame = DEFAULT_IMAGE_PATH.format(
        image_key=video_key, episode_index=episode_index, frame_index=0
    )
    image_directory = (Path(root) / first_frame).parent
    encode_video_frames(
        image_directory,
        temporary,
        fps,
        vcodec=video_codec,
        overwrite=True,
    )
    shutil.rmtree(image_directory)
    return temporary


def _configure_official_video_codec(video_codec: str) -> None:
    """Pin LeRobot's private worker so parent and process-pool children agree."""
    import lerobot.datasets.lerobot_dataset as lerobot_dataset

    lerobot_dataset._encode_video_worker = partial(
        _task5_encode_video_worker, video_codec=video_codec
    )


def _scalar(value: object, dtype: object) -> np.ndarray:
    return np.asarray([value], dtype=dtype)


def _frame_record(
    frame: NormalizedFrame, *, task: str, source_schema: str
) -> dict[str, object]:
    record: dict[str, object] = {
        **{
            f"observation.images.{camera}": np.asarray(frame.images[camera]).copy()
            for camera in CAMERAS
        },
        "observation.state": frame.qpos.copy(),
        "observation.velocity": frame.qvel.copy(),
        "observation.effort": frame.effort.copy(),
        "action": frame.action.copy(),
        "task5.base_action": frame.base_action.copy(),
        "task5.policy_command": frame.policy_command.copy(),
        "task5.coordinator_command": frame.coordinator_command.copy(),
        "task5.front_observation": frame.front_observation.copy(),
        "task5.rear_observation": frame.rear_observation.copy(),
        "task5.sample_timestamp": _scalar(frame.sample_timestamp, np.float64),
        "task5.control_source_left": _scalar(int(frame.control_source_left), np.uint8),
        "task5.control_source_right": _scalar(
            int(frame.control_source_right), np.uint8
        ),
        "task5.intervention_id_left": _scalar(frame.intervention_id_left, np.uint64),
        "task5.intervention_id_right": _scalar(frame.intervention_id_right, np.uint64),
        "task5.is_intervention_left": _scalar(frame.is_intervention_left, np.bool_),
        "task5.is_intervention_right": _scalar(frame.is_intervention_right, np.bool_),
        "task5.teach_active_left": _scalar(frame.teach_active_left, np.bool_),
        "task5.teach_active_right": _scalar(frame.teach_active_right, np.bool_),
        "task5.handover_mode": frame.handover_mode,
        "task5.handover_fault": frame.handover_fault,
        "task5.source_schema": source_schema,
        "task": task,
    }
    for key in sorted(ROLLOUT_VALIDITY_KEYS):
        record[f"task5.valid.{key}"] = _scalar(frame.valid.get(key, False), np.bool_)
    for key in REQUIRED_TOPICS:
        record[f"task5.source_timestamp.{key}"] = _scalar(
            frame.source_timestamps.get(key, np.nan), np.float64
        )
        record[f"task5.arrival_timestamp.{key}"] = _scalar(
            frame.arrival_timestamps.get(key, np.nan), np.float64
        )
    return record


def _write_manifest(output: Path, manifest: dict[str, object]) -> None:
    temporary = output / ".task5_manifest.json.tmp"
    final = output / "task5_manifest.json"
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, final)


def _write_episode_facts(
    output: Path,
    episode_index: int,
    frames: list[NormalizedFrame],
) -> Path:
    directory = output / "task5_facts"
    directory.mkdir(exist_ok=True)
    relative = Path("task5_facts") / f"episode_{episode_index:06d}.npz"
    final = output / relative
    temporary = final.with_suffix(".tmp.npz")
    is_legacy = all(frame.handover_mode == "legacy_demo" for frame in frames)

    def side_valid(frame: NormalizedFrame, side: str) -> bool:
        if is_legacy:
            return True
        return all(
            frame.valid.get(key, False)
            for key in (
                "action",
                "qpos",
                "handover_mode",
                f"rear_{side}",
                f"coordinator_{side}",
            )
        )

    np.savez_compressed(
        temporary,
        control_source_left=np.asarray(
            [int(frame.control_source_left) for frame in frames], dtype=np.uint8
        ),
        control_source_right=np.asarray(
            [int(frame.control_source_right) for frame in frames], dtype=np.uint8
        ),
        intervention_id_left=np.asarray(
            [frame.intervention_id_left for frame in frames], dtype=np.uint64
        ),
        intervention_id_right=np.asarray(
            [frame.intervention_id_right for frame in frames], dtype=np.uint64
        ),
        left_valid=np.asarray(
            [side_valid(frame, "left") for frame in frames], dtype=np.bool_
        ),
        right_valid=np.asarray(
            [side_valid(frame, "right") for frame in frames], dtype=np.bool_
        ),
        action_valid=np.asarray(
            [frame.valid.get("action", False) for frame in frames], dtype=np.bool_
        ),
        state_valid=np.asarray(
            [frame.valid.get("qpos", False) for frame in frames], dtype=np.bool_
        ),
    )
    os.replace(temporary, final)
    return relative


def _archive_required_label(
    output: Path, episode_index: int, source: EpisodeSource
) -> dict[str, object]:
    metadata = source.metadata
    if metadata.episode_uuid is None:
        raise ValueError("required labels need a source episode UUID")
    sidecar = metadata.path.with_suffix(".labels.json")
    try:
        raw = sidecar.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"missing or invalid label sidecar: {sidecar}") from error
    if not isinstance(payload, dict) or payload.get("episode_uuid") != metadata.episode_uuid:
        raise ValueError(f"label UUID does not match source: {sidecar}")
    if payload.get("label_schema_version") != 1:
        raise ValueError(f"unsupported label schema: {sidecar}")
    if payload.get("episode_outcome") not in {"success", "failure"}:
        raise ValueError(f"label outcome is not final: {sidecar}")
    if payload.get("episode_quality") not in {"good", "bad"}:
        raise ValueError(f"label quality is not final: {sidecar}")
    if payload.get("keep_for_training") not in {"true", "false"}:
        raise ValueError(f"training decision is not final: {sidecar}")
    if not isinstance(payload.get("interventions"), list):
        raise ValueError(f"label interventions must be a list: {sidecar}")

    directory = output / "task5_labels"
    directory.mkdir(exist_ok=True)
    relative = Path("task5_labels") / f"episode_{episode_index:06d}.labels.json"
    final = output / relative
    temporary = final.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, final)
    return {
        "labels_path": str(relative),
        "labels_sha256": hashlib.sha256(raw).hexdigest(),
        "source_metadata": {
            "task_id": metadata.task_id,
            "model_id": metadata.model_id,
            "checkpoint_id": metadata.checkpoint_id,
            "dataset_round": metadata.dataset_round,
            "episode_index": metadata.episode_index,
            "episode_uuid": metadata.episode_uuid,
            "termination_reason": metadata.termination_reason,
        },
    }


def convert_sources(
    sources: Iterable[EpisodeSource],
    *,
    output: Path | str,
    repo_id: str,
    task: str,
    backend_factory: Callable[..., Any] | None = None,
    require_labels: bool = False,
    video_codec: str = "h264",
) -> dict[str, object]:
    episodes = list(sources)
    if not episodes:
        raise ValueError("at least one source episode is required")
    output_path = Path(output).expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    if not repo_id or "/" not in repo_id or not task.strip():
        raise ValueError("repo_id and task instruction must be explicit")
    if video_codec not in SUPPORTED_VIDEO_CODECS:
        raise ValueError(
            f"unsupported video codec {video_codec!r}; "
            f"choose one of {sorted(SUPPORTED_VIDEO_CODECS)}"
        )
    fps = episodes[0].metadata.fps
    if any(not np.isclose(source.metadata.fps, fps) for source in episodes[1:]):
        raise ValueError("all source episodes must have the same fps")
    first_frame = next(episodes[0].iter_frames())
    image_shape = tuple(first_frame.images[CAMERAS[0]].shape)
    features = task5_lerobot_features(image_shape)
    if backend_factory is None:
        _configure_official_video_codec(video_codec)
    create = backend_factory or _official_backend_factory
    dataset = create(
        repo_id=repo_id,
        fps=round(fps),
        features=features,
        root=output_path,
        robot_type="cobot_magic_task5",
        use_videos=True,
    )
    source_records: list[dict[str, object]] = []
    total_frames = 0
    for episode_index, source in enumerate(episodes):
        fact_frames: list[NormalizedFrame] = []
        for frame in source.iter_frames():
            for camera in CAMERAS:
                if tuple(frame.images[camera].shape) != image_shape:
                    raise ValueError("all source camera shapes must match")
            dataset.add_frame(
                _frame_record(
                    frame,
                    task=task.strip(),
                    source_schema=source.metadata.source_schema,
                )
            )
            fact_frames.append(frame)
            total_frames += 1
        dataset.save_episode()
        facts_overlay = _write_episode_facts(output_path, episode_index, fact_frames)
        source_record: dict[str, object] = {
                "path": str(source.metadata.path),
                "schema": source.metadata.source_schema,
                "sha256": _sha256(source.metadata.path),
                "frame_count": source.metadata.frame_count,
                "facts_overlay": str(facts_overlay),
                "episode_uuid": source.metadata.episode_uuid,
        }
        if require_labels:
            source_record.update(
                _archive_required_label(output_path, episode_index, source)
            )
        source_records.append(source_record)
    dataset.finalize()
    manifest: dict[str, object] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "lerobot_version": LEROBOT_VERSION,
        "video_codec": video_codec,
        "converter_git_commit": _git_commit(),
        "repo_id": repo_id,
        "task": task.strip(),
        "fps": fps,
        "episode_count": len(episodes),
        "frame_count": total_frames,
        "features": json.loads(json.dumps(features)),
        "sources": source_records,
        "automatic_lerobot_fields": [
            "index",
            "episode_index",
            "frame_index",
            "timestamp",
            "task_index",
        ],
    }
    _write_manifest(output_path, manifest)
    return manifest
