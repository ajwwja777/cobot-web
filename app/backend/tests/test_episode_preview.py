"""Memory-bounded three-camera rollout replay tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

import cv2
import h5py
import numpy as np

from capture_core.episode_preview import (
    generate_episode_preview,
    read_episode_frame_jpeg,
    selected_frame_indices,
    validate_preview_cache,
)


def _episode(tmp_path: Path, frames: int = 6) -> tuple[Path, str]:
    episode_uuid = str(uuid4())
    series = tmp_path / "in_the_pot" / "pi05" / "round_001"
    series.mkdir(parents=True)
    path = series / "episode_000000.hdf5"
    with h5py.File(path, "w") as episode:
        episode.attrs["episode_uuid"] = episode_uuid
        episode.attrs["completion_state"] = "complete"
        episode.attrs["fps"] = 30.0
        images = episode.create_group("observations/images")
        for offset, name in enumerate(
            ("cam_high", "cam_left_wrist", "cam_right_wrist")
        ):
            values = np.zeros((frames, 16, 20, 3), dtype=np.uint8)
            for index in range(frames):
                values[index, :, :, offset] = index * 20 + 20
            images.create_dataset(name, data=values)
    return path, episode_uuid


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_selected_indices_preserve_duration_when_downsampling():
    assert selected_frame_indices(6, source_fps=30.0, output_fps=15.0) == (
        0,
        2,
        4,
    )
    assert selected_frame_indices(5, source_fps=30.0, output_fps=15.0) == (
        0,
        2,
        4,
    )


def test_generate_preview_is_atomic_playable_and_keeps_hdf5_immutable(
    tmp_path: Path,
):
    episode, episode_uuid = _episode(tmp_path)
    before = _sha256(episode)
    output = episode.parent / ".previews" / episode_uuid
    progress = []

    metadata = generate_episode_preview(
        episode,
        output,
        frame_size=(32, 24),
        progress=lambda complete, total: progress.append((complete, total)),
        transcode=False,
    )

    assert metadata["episode_uuid"] == episode_uuid
    assert metadata["source_frame_count"] == 6
    assert metadata["output_frame_count"] == 2
    assert metadata["output_fps"] == 10.0
    assert metadata["frame_width"] == 96
    assert metadata["frame_height"] == 24
    assert metadata["camera_order"] == ["left", "high", "right"]
    assert progress[-1] == (2, 2)
    assert (output / "preview.mp4").stat().st_size > 0
    assert (output / "preview.json").is_file()
    assert (output / "contact_sheet.jpg").stat().st_size > 0
    assert (output / "qpos.png").stat().st_size > 0
    assert not list(output.glob("*.incomplete"))
    capture = cv2.VideoCapture(str(output / "preview.mp4"))
    try:
        assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 2
    finally:
        capture.release()
    assert _sha256(episode) == before


def test_generate_preview_clamps_output_rate_to_source_rate(tmp_path: Path):
    episode, episode_uuid = _episode(tmp_path, frames=4)
    with h5py.File(episode, "r+") as handle:
        handle.attrs["fps"] = 10.0
    output = episode.parent / ".previews" / episode_uuid

    metadata = generate_episode_preview(
        episode, output, output_fps=15.0, frame_size=(32, 24), transcode=False
    )

    assert metadata["output_fps"] == 10.0
    assert metadata["output_frame_count"] == 4


def test_read_episode_frame_jpeg_returns_selected_camera_frame(tmp_path: Path):
    episode, _episode_uuid = _episode(tmp_path, frames=3)

    payload = read_episode_frame_jpeg(episode, 2, "camera_high")
    decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)

    assert decoded is not None
    assert decoded.shape == (16, 20, 3)
    assert float(decoded[:, :, 2].mean()) > 50


def test_read_episode_frame_jpeg_populates_all_three_persistent_frame_caches(
    tmp_path: Path,
):
    episode, episode_uuid = _episode(tmp_path, frames=3)
    cache = episode.parent / ".previews" / episode_uuid

    high = read_episode_frame_jpeg(
        episode, 1, "camera_high", cache_directory=cache
    )

    frame_cache = cache / "frames-v1" / "000001"
    assert len(high) > 0
    assert sorted(path.name for path in frame_cache.glob("*.jpg")) == [
        "camera_high-q88.jpg",
        "camera_left-q88.jpg",
        "camera_right-q88.jpg",
    ]
    assert read_episode_frame_jpeg(
        episode, 1, "camera_left", cache_directory=cache
    ) == (frame_cache / "camera_left-q88.jpg").read_bytes()


def test_validate_preview_cache_rejects_source_change(tmp_path: Path):
    episode, episode_uuid = _episode(tmp_path)
    output = episode.parent / ".previews" / episode_uuid
    generated = generate_episode_preview(
        episode, output, frame_size=(32, 24), transcode=False
    )

    assert validate_preview_cache(episode, output) == generated

    with episode.open("ab") as stream:
        stream.write(b"changed")

    assert validate_preview_cache(episode, output) is None
