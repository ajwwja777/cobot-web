"""Memory-bounded cached replay generation for finalized Task5 episodes."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from uuid import UUID

import cv2
import h5py
import numpy as np

PREVIEW_VERSION = 4
_CAMERAS = (
    ("left", "cam_left_wrist"),
    ("high", "cam_high"),
    ("right", "cam_right_wrist"),
)
_CAMERA_DATASETS = {
    "camera_left": "cam_left_wrist",
    "camera_high": "cam_high",
    "camera_right": "cam_right_wrist",
}
_FRAME_CACHE_LOCKS: dict[tuple[str, int, int], threading.Lock] = {}
_FRAME_CACHE_LOCKS_GUARD = threading.Lock()
_METADATA_FIELDS = frozenset(
    {
        "preview_version",
        "episode_uuid",
        "source_frame_count",
        "source_mtime_ns",
        "output_frame_count",
        "source_fps",
        "output_fps",
        "frame_width",
        "frame_height",
        "camera_order",
        "codec",
        "contact_sheet",
        "qpos_plot",
    }
)


class EpisodePreviewError(RuntimeError):
    """A replay could not be generated or validated safely."""


class EpisodeFrameError(RuntimeError):
    """One requested episode frame could not be read safely."""


@dataclass(frozen=True)
class PreviewIdentity:
    episode_uuid: str
    frame_count: int
    source_mtime_ns: int
    source_fps: float


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise ValueError("attribute is not text")


def selected_frame_indices(
    frame_count: int, *, source_fps: float, output_fps: float
) -> Tuple[int, ...]:
    """Select monotonically increasing source frames at the requested rate."""
    if isinstance(frame_count, bool) or frame_count <= 0:
        raise ValueError("frame_count must be positive")
    if (
        not math.isfinite(source_fps)
        or not math.isfinite(output_fps)
        or source_fps <= 0
        or output_fps <= 0
        or output_fps > source_fps
    ):
        raise ValueError("preview frame rates are invalid")
    count = math.ceil(frame_count * output_fps / source_fps)
    return tuple(
        min(frame_count - 1, int(index * source_fps / output_fps))
        for index in range(count)
    )


def _identity(path: Path, episode: h5py.File) -> PreviewIdentity:
    completion = _text(episode.attrs["completion_state"])
    if completion != "complete":
        raise EpisodePreviewError("episode is not finalized")
    episode_uuid = str(UUID(_text(episode.attrs["episode_uuid"])))
    source_fps = float(episode.attrs["fps"])
    datasets = []
    for _label, name in _CAMERAS:
        dataset = episode[f"observations/images/{name}"]
        if (
            dataset.ndim != 4
            or dataset.dtype != np.dtype(np.uint8)
            or dataset.shape[-1] != 3
            or any(size <= 0 for size in dataset.shape)
        ):
            raise EpisodePreviewError(f"invalid camera dataset: {name}")
        datasets.append(dataset)
    counts = {int(dataset.shape[0]) for dataset in datasets}
    if len(counts) != 1:
        raise EpisodePreviewError("camera streams are not aligned")
    return PreviewIdentity(
        episode_uuid=episode_uuid,
        frame_count=counts.pop(),
        source_mtime_ns=path.stat().st_mtime_ns,
        source_fps=source_fps,
    )


def _validate_paths(hdf5_path: Path, output_directory: Path, episode_uuid: str) -> None:
    if hdf5_path.is_symlink() or not hdf5_path.is_file():
        raise EpisodePreviewError("episode path is unsafe")
    expected = hdf5_path.parent / ".previews" / episode_uuid
    if output_directory != expected:
        raise EpisodePreviewError("preview path does not match episode identity")
    previews = hdf5_path.parent / ".previews"
    for candidate in (previews, output_directory):
        if candidate.is_symlink():
            raise EpisodePreviewError("preview path contains a symlink")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _transcode_h264(source: Path, target: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-an",
            "-vcodec",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "26",
            "-tune",
            "fastdecode",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(target),
        ],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and target.is_file() and target.stat().st_size > 0


def _compose_frame(
    episode: h5py.File, index: int, frame_size: Tuple[int, int]
) -> np.ndarray:
    views = []
    for label, name in _CAMERAS:
        rgb = np.asarray(episode[f"observations/images/{name}"][index])
        resized = cv2.resize(rgb, frame_size, interpolation=cv2.INTER_AREA)
        bgr = cv2.cvtColor(resized, cv2.COLOR_RGB2BGR)
        cv2.putText(
            bgr,
            label,
            (8, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        views.append(bgr)
    return np.concatenate(views, axis=1)


def read_episode_frame_jpeg(
    hdf5_path: Path | str,
    frame_index: int,
    camera_key: str,
    *,
    quality: int = 88,
    cache_directory: Path | str | None = None,
) -> bytes:
    """Read one immutable RGB camera frame and return a browser-ready JPEG."""
    source = Path(hdf5_path).expanduser()
    if source.is_symlink() or not source.is_file():
        raise EpisodeFrameError("episode path is unsafe")
    if camera_key not in _CAMERA_DATASETS:
        raise EpisodeFrameError("camera not found")
    if isinstance(frame_index, bool) or frame_index < 0:
        raise EpisodeFrameError("frame index is invalid")
    if isinstance(quality, bool) or not 1 <= quality <= 100:
        raise EpisodeFrameError("jpeg quality is invalid")

    cache_root: Path | None = None
    cache_path: Path | None = None
    if cache_directory is not None:
        cache_root = Path(cache_directory).expanduser()
        expected_parent = source.parent / ".previews"
        if (
            cache_root.parent != expected_parent
            or expected_parent.is_symlink()
            or cache_root.is_symlink()
        ):
            raise EpisodeFrameError("frame cache path is unsafe")
        cache_path = (
            cache_root
            / "frames-v1"
            / f"{int(frame_index):06d}"
            / f"{camera_key}-q{int(quality)}.jpg"
        )
        if cache_path.is_file() and not cache_path.is_symlink():
            return cache_path.read_bytes()

    lock_key = (str(source.resolve()), int(frame_index), int(quality))
    with _FRAME_CACHE_LOCKS_GUARD:
        frame_lock = _FRAME_CACHE_LOCKS.setdefault(lock_key, threading.Lock())
    with frame_lock:
        if cache_path is not None and cache_path.is_file() and not cache_path.is_symlink():
            return cache_path.read_bytes()
        encoded_by_camera: dict[str, bytes] = {}
        try:
            with h5py.File(source, "r") as episode:
                for key, dataset_name in _CAMERA_DATASETS.items():
                    dataset = episode[f"observations/images/{dataset_name}"]
                    if (
                        dataset.ndim != 4
                        or dataset.dtype != np.dtype(np.uint8)
                        or dataset.shape[-1] != 3
                        or frame_index >= int(dataset.shape[0])
                    ):
                        raise EpisodeFrameError("frame is unavailable")
                    rgb = np.asarray(dataset[frame_index])
                    ok, encoded = cv2.imencode(
                        ".jpg",
                        cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                        [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)],
                    )
                    if not ok:
                        raise EpisodeFrameError("could not encode frame")
                    encoded_by_camera[key] = encoded.tobytes()
        except EpisodeFrameError:
            raise
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise EpisodeFrameError("frame is unavailable") from error

        if cache_path is not None and cache_root is not None:
            frame_directory = cache_path.parent
            frame_directory.mkdir(parents=True, exist_ok=True)
            if frame_directory.is_symlink():
                raise EpisodeFrameError("frame cache path is unsafe")
            for key, payload in encoded_by_camera.items():
                target = frame_directory / f"{key}-q{int(quality)}.jpg"
                if target.is_symlink():
                    raise EpisodeFrameError("frame cache path is unsafe")
                temporary = target.with_name(
                    f".{target.name}.{os.getpid()}.{threading.get_ident()}.incomplete"
                )
                temporary.write_bytes(payload)
                os.replace(temporary, target)
            return cache_path.read_bytes()

        return encoded_by_camera[camera_key]

    # Unreachable, kept out of the HDF5 branch above to make the cache the
    # single source of truth for subsequent requests.
    raise EpisodeFrameError("frame is unavailable")


def _write_contact_sheet(episode: h5py.File, path: Path, frame_size: Tuple[int, int]) -> None:
    rows = []
    for label, name in _CAMERAS:
        dataset = episode[f"observations/images/{name}"]
        indices = (0, len(dataset) // 2, len(dataset) - 1)
        views = []
        for index in indices:
            rgb = np.asarray(dataset[index])
            bgr = cv2.cvtColor(cv2.resize(rgb, frame_size, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2BGR)
            cv2.putText(bgr, f"{label}  frame {index}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX,
                        .55, (255, 255, 255), 2, cv2.LINE_AA)
            views.append(bgr)
        rows.append(np.concatenate(views, axis=1))
    if not cv2.imwrite(str(path), np.concatenate(rows, axis=0), [int(cv2.IMWRITE_JPEG_QUALITY), 88]):
        raise EpisodePreviewError("could not write contact sheet")


def _write_qpos_plot(episode: h5py.File, path: Path) -> None:
    width, height = 1200, 440
    canvas = np.full((height, width, 3), 250, dtype=np.uint8)
    cv2.rectangle(canvas, (54, 28), (width - 24, height - 46), (220, 224, 228), 1)
    if "observations/qpos" not in episode:
        cv2.putText(canvas, "qpos unavailable in this episode", (80, height // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, .8, (85, 92, 100), 2, cv2.LINE_AA)
    else:
        values = np.asarray(episode["observations/qpos"])
        if values.ndim == 1:
            values = values[:, None]
        colors = ((31,111,104),(65,90,119),(197,92,85),(122,91,208),(36,126,150),
                  (173,107,19),(38,132,93),(96,96,105),(70,145,137),(91,116,147),
                  (218,124,115),(151,126,220),(75,158,178),(201,145,70))
        left, top, right, bottom = 56, 30, width - 26, height - 48
        for index in range(values.shape[1]):
            series = np.asarray(values[:, index], dtype=np.float64)
            finite = np.isfinite(series)
            if finite.sum() < 2:
                continue
            low, high = float(np.nanmin(series)), float(np.nanmax(series))
            scale = high - low if high > low else 1.0
            xs = np.linspace(left, right, len(series)).astype(np.int32)
            ys = (bottom - (series - low) / scale * (bottom - top)).astype(np.int32)
            points = np.stack((xs, ys), axis=1)[finite].reshape((-1, 1, 2))
            cv2.polylines(canvas, [points], False, colors[index % len(colors)], 1, cv2.LINE_AA)
        cv2.putText(canvas, "Robot state / qpos (each channel normalized)", (56, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, .52, (60, 66, 72), 1, cv2.LINE_AA)
    if not cv2.imwrite(str(path), canvas):
        raise EpisodePreviewError("could not write qpos plot")


def generate_episode_preview(
    hdf5_path: Path | str,
    output_directory: Path | str,
    *,
    output_fps: float = 10.0,
    frame_size: Tuple[int, int] = (256, 192),
    progress: Optional[Callable[[int, int], None]] = None,
    transcode: bool = True,
) -> dict[str, object]:
    """Stream one source timestep at a time into an atomic replay cache."""
    source = Path(hdf5_path).expanduser()
    output = Path(output_directory).expanduser()
    if (
        len(frame_size) != 2
        or any(isinstance(value, bool) or value <= 0 for value in frame_size)
    ):
        raise ValueError("frame_size must contain positive integers")
    with h5py.File(source, "r") as episode:
        identity = _identity(source, episode)
        _validate_paths(source, output, identity.episode_uuid)
        effective_output_fps = min(float(output_fps), identity.source_fps)
        indices = selected_frame_indices(
            identity.frame_count,
            source_fps=identity.source_fps,
            output_fps=effective_output_fps,
        )
        output.mkdir(parents=True, exist_ok=True)
        raw_temp = output / "preview.incomplete.mp4"
        h264_temp = output / "preview.h264.incomplete.mp4"
        final_video = output / "preview.mp4"
        metadata_temp = output / "preview.json.incomplete"
        contact_temp = output / "contact_sheet.incomplete.jpg"
        qpos_temp = output / "qpos.incomplete.png"
        final_metadata = output / "preview.json"
        final_contact = output / "contact_sheet.jpg"
        final_qpos = output / "qpos.png"
        for temporary in (raw_temp, h264_temp, metadata_temp, contact_temp, qpos_temp):
            if temporary.is_symlink():
                raise EpisodePreviewError("preview temporary path is a symlink")
            if temporary.exists():
                temporary.unlink()

        width, height = frame_size
        writer = cv2.VideoWriter(
            str(raw_temp),
            cv2.VideoWriter_fourcc(*"mp4v"),
            effective_output_fps,
            (width * 3, height),
        )
        if not writer.isOpened():
            raise EpisodePreviewError("could not open preview video writer")
        try:
            total = len(indices)
            for completed, frame_index in enumerate(indices, start=1):
                writer.write(_compose_frame(episode, frame_index, frame_size))
                if progress is not None:
                    progress(completed, total)
        except Exception:
            writer.release()
            raw_temp.unlink(missing_ok=True)
            raise
        finally:
            writer.release()
        _write_contact_sheet(episode, contact_temp, frame_size)
        _write_qpos_plot(episode, qpos_temp)

    if not raw_temp.is_file() or raw_temp.stat().st_size <= 0:
        raise EpisodePreviewError("preview writer produced no video")
    codec = "mp4v"
    publish_source = raw_temp
    if transcode and _transcode_h264(raw_temp, h264_temp):
        codec = "h264"
        publish_source = h264_temp
    publish_source.replace(final_video)
    if raw_temp.exists():
        raw_temp.unlink()
    if h264_temp.exists():
        h264_temp.unlink()
    _fsync_file(final_video)
    contact_temp.replace(final_contact)
    qpos_temp.replace(final_qpos)
    _fsync_file(final_contact)
    _fsync_file(final_qpos)

    metadata: dict[str, object] = {
        "preview_version": PREVIEW_VERSION,
        "episode_uuid": identity.episode_uuid,
        "source_frame_count": identity.frame_count,
        "source_mtime_ns": identity.source_mtime_ns,
        "output_frame_count": len(indices),
        "source_fps": identity.source_fps,
        "output_fps": effective_output_fps,
        "frame_width": frame_size[0] * 3,
        "frame_height": frame_size[1],
        "camera_order": [label for label, _name in _CAMERAS],
        "codec": codec,
        "contact_sheet": "contact_sheet.jpg",
        "qpos_plot": "qpos.png",
    }
    metadata_temp.write_text(
        json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    _fsync_file(metadata_temp)
    metadata_temp.replace(final_metadata)
    _fsync_directory(output)
    return metadata


def validate_preview_cache(
    hdf5_path: Path | str, output_directory: Path | str
) -> Optional[dict[str, object]]:
    """Return trusted cache metadata or None when regeneration is required."""
    source = Path(hdf5_path).expanduser()
    output = Path(output_directory).expanduser()
    metadata_path = output / "preview.json"
    video_path = output / "preview.mp4"
    contact_path = output / "contact_sheet.jpg"
    qpos_path = output / "qpos.png"
    if (
        output.is_symlink()
        or metadata_path.is_symlink()
        or video_path.is_symlink()
        or contact_path.is_symlink()
        or qpos_path.is_symlink()
        or not metadata_path.is_file()
        or not video_path.is_file()
        or not contact_path.is_file()
        or not qpos_path.is_file()
        or video_path.stat().st_size <= 0
        or contact_path.stat().st_size <= 0
        or qpos_path.stat().st_size <= 0
    ):
        return None
    try:
        with h5py.File(source, "r") as episode:
            identity = _identity(source, episode)
        _validate_paths(source, output, identity.episode_uuid)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or set(metadata) != _METADATA_FIELDS:
            return None
        if (
            metadata["preview_version"] != PREVIEW_VERSION
            or metadata["episode_uuid"] != identity.episode_uuid
            or metadata["source_frame_count"] != identity.frame_count
            or metadata["source_mtime_ns"] != identity.source_mtime_ns
        ):
            return None
        return metadata
    except (OSError, KeyError, TypeError, ValueError, EpisodePreviewError):
        return None
