"""Single-worker replay job state tests."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Event
from uuid import uuid4

from capture_core.labels import EpisodeArtifacts
from capture_core.preview_jobs import PreviewJobManager


def _artifacts(tmp_path: Path) -> EpisodeArtifacts:
    tmp_path.mkdir(parents=True, exist_ok=True)
    episode_uuid = uuid4()
    episode = tmp_path / "episode_000000.hdf5"
    episode.write_bytes(b"episode")
    return EpisodeArtifacts(
        episode_uuid=episode_uuid,
        episode_index=0,
        episode_path=episode,
        sidecar_path=episode.with_suffix(".labels.json"),
        preview_directory=tmp_path / ".previews" / str(episode_uuid),
        frame_count=4,
        size_bytes=episode.stat().st_size,
    )


def _wait_state(manager: PreviewJobManager, uuid: str, expected: str) -> dict[str, object]:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        state = manager.status(uuid)
        if state["state"] == expected:
            return state
        time.sleep(0.01)
    raise AssertionError(f"preview did not reach {expected}: {manager.status(uuid)}")


def test_preview_job_transitions_and_reuses_ready_cache(tmp_path: Path):
    artifacts = _artifacts(tmp_path)
    calls = []
    cached = {}

    def generate(source, output, progress):
        calls.append((source, output))
        progress(1, 2)
        progress(2, 2)
        cached["metadata"] = {"episode_uuid": str(artifacts.episode_uuid)}

    manager = PreviewJobManager(
        generator=generate,
        validator=lambda _source, _output: cached.get("metadata"),
    )
    try:
        queued = manager.queue(artifacts)
        ready = _wait_state(manager, str(artifacts.episode_uuid), "ready")
        reused = manager.queue(artifacts)

        assert queued["state"] in {"queued", "generating"}
        assert ready["completed_frames"] == 2
        assert ready["total_frames"] == 2
        assert reused["state"] == "ready"
        assert len(calls) == 1
    finally:
        manager.shutdown()


def test_preview_job_reports_stable_error_code_without_exception_text(tmp_path: Path):
    artifacts = _artifacts(tmp_path)

    def fail(_source, _output, _progress):
        raise RuntimeError("private path and internals")

    manager = PreviewJobManager(
        generator=fail, validator=lambda _source, _output: None
    )
    try:
        manager.queue(artifacts)
        failed = _wait_state(manager, str(artifacts.episode_uuid), "error")

        assert failed["error_code"] == "preview_generation_failed"
        assert "private" not in str(failed)
    finally:
        manager.shutdown()


def test_preview_jobs_are_globally_serial(tmp_path: Path):
    first = _artifacts(tmp_path / "one")
    second = _artifacts(tmp_path / "two")
    release = Event()
    started = []

    def generate(source, _output, *, progress):
        del progress
        started.append(source)
        if len(started) == 1:
            release.wait(timeout=1.0)

    manager = PreviewJobManager(
        generator=generate, validator=lambda _source, _output: None
    )
    try:
        manager.queue(first)
        manager.queue(second)
        _wait_state(manager, str(first.episode_uuid), "generating")
        time.sleep(0.03)
        assert started == [first.episode_path]
        release.set()
        _wait_state(manager, str(second.episode_uuid), "error")
        assert started == [first.episode_path, second.episode_path]
    finally:
        release.set()
        manager.shutdown()
