"""Crash-recoverable sidecar persistence for segmented capture."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from .schema import SidecarValidationError, snapshot_payload
from .state import CaptureSnapshot

_SAFE_UUID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class FinalizeResult:
    episode_root: Path
    sidecar: Path


class AtomicEpisodeStore:
    """Keep an atomic, incrementally recoverable JSON sidecar."""

    def __init__(
        self,
        root: Path,
        *,
        episode_uuid: str,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> None:
        if _SAFE_UUID.fullmatch(episode_uuid) is None:
            raise ValueError("episode_uuid must be path-safe")
        self.root = Path(root)
        self.episode_uuid = episode_uuid
        self.episode_root = self.root / episode_uuid
        self.incomplete = self.episode_root / "sidecar.json.incomplete"
        self.sidecar = self.episode_root / "sidecar.json"
        self._frames = 0
        self._snapshot: CaptureSnapshot | None = None
        self._metadata = dict(metadata or {})

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_incomplete(self, commit_state: str) -> None:
        if self._snapshot is None:
            raise RuntimeError("store has not begun")
        payload = snapshot_payload(
            self._snapshot,
            training_frame_count=self._frames,
            commit_state=commit_state,
            metadata=self._metadata,
        )
        staging = self.episode_root / "sidecar.json.writing"
        if staging.exists():
            raise FileExistsError(staging)
        with staging.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, self.incomplete)
        self._fsync_directory(self.episode_root)

    def begin(self, snapshot: CaptureSnapshot) -> None:
        if self.sidecar.exists() or self.incomplete.exists():
            raise FileExistsError(self.sidecar if self.sidecar.exists() else self.incomplete)
        self.episode_root.mkdir(parents=True, exist_ok=True)
        self._snapshot = snapshot
        self._write_incomplete("recording")

    def append_frame(self) -> None:
        if self._snapshot is None:
            raise RuntimeError("store has not begun")
        self._frames += 1
        self._write_incomplete("recording")

    def sync_frame_count(self, count: int) -> None:
        if count < self._frames:
            raise ValueError("training frame count cannot move backwards")
        self._frames = count

    def transition(self, snapshot: CaptureSnapshot) -> None:
        self._snapshot = snapshot
        self._write_incomplete("recording")

    def write_marker(self, snapshot: CaptureSnapshot) -> None:
        self.transition(snapshot)

    def finalize(self, snapshot: CaptureSnapshot) -> FinalizeResult:
        if self.sidecar.exists():
            raise FileExistsError(self.sidecar)
        self._snapshot = snapshot
        self._write_incomplete("complete")
        os.replace(self.incomplete, self.sidecar)
        self._fsync_directory(self.episode_root)
        return FinalizeResult(self.episode_root, self.sidecar)

    @classmethod
    def recover(cls, root: Path, episode_uuid: str) -> Dict[str, object]:
        store = cls(root, episode_uuid=episode_uuid)
        path = store.incomplete if store.incomplete.exists() else store.sidecar
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if payload.get("episode_uuid") != episode_uuid:
            raise SidecarValidationError("episode_uuid mismatch")
        return payload


__all__ = ["AtomicEpisodeStore", "FinalizeResult", "SidecarValidationError"]
