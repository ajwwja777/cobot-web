"""Versioned operator review of trainable node intervals."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from threading import RLock
from typing import Dict, List, Optional

from .schema import SCHEMA_VERSION, training_intervals

REVIEW_SCHEMA_VERSION = "task5-segment-review-v1"
_SAFE_UUID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class SegmentReviewError(RuntimeError):
    """A review would not preserve the immutable capture evidence."""


class SegmentReviewStore:
    """Append review revisions while keeping raw sidecar/HDF5 untouched."""

    def __init__(self, sidecar_root: Path) -> None:
        self.sidecar_root = Path(sidecar_root)
        self._lock = RLock()

    def _episode_root(self, episode_uuid: str) -> Path:
        if _SAFE_UUID.fullmatch(episode_uuid) is None:
            raise FileNotFoundError(episode_uuid)
        matches = list(self.sidecar_root.glob(f"*/*/*/{episode_uuid}"))
        if not matches and (self.sidecar_root / episode_uuid).is_dir():
            matches = [self.sidecar_root / episode_uuid]
        if len(matches) != 1:
            raise FileNotFoundError(episode_uuid)
        return matches[0]

    @staticmethod
    def _read_json(path: Path) -> Dict[str, object]:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict):
            raise SegmentReviewError("invalid_review_json")
        return value

    def _source(self, episode_uuid: str):
        root = self._episode_root(episode_uuid)
        sidecar_path = root / "sidecar.json"
        if not sidecar_path.is_file():
            raise SegmentReviewError("episode_not_complete")
        raw = sidecar_path.read_bytes()
        sidecar = json.loads(raw)
        if sidecar.get("schema_version") != SCHEMA_VERSION:
            raise SegmentReviewError("unsupported_capture_schema")
        if sidecar.get("episode_uuid") != episode_uuid:
            raise SegmentReviewError("episode_uuid_mismatch")
        if sidecar.get("commit_state") != "complete":
            raise SegmentReviewError("episode_not_complete")
        intervals = training_intervals(list(sidecar.get("nodes", [])))
        return root, hashlib.sha256(raw).hexdigest(), intervals

    def load(self, episode_uuid: str) -> Dict[str, object]:
        with self._lock:
            root, source_hash, intervals = self._source(episode_uuid)
            pointer = root / "segment-review.json"
            if pointer.is_file():
                review = self._read_json(pointer)
                if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
                    raise SegmentReviewError("unsupported_review_schema")
                if review.get("episode_uuid") != episode_uuid:
                    raise SegmentReviewError("episode_uuid_mismatch")
                recorded_hash = review.get("source_sidecar_sha256")
                if recorded_hash is not None and recorded_hash != source_hash:
                    raise SegmentReviewError("source_sidecar_changed")
            else:
                review = {
                    "schema_version": REVIEW_SCHEMA_VERSION,
                    "episode_uuid": episode_uuid,
                    "review_revision": 0,
                    "source_sidecar_sha256": source_hash,
                    "selected_interval_ids": [],
                    "note": None,
                }
            result = dict(review)
            result["intervals"] = intervals
            return result

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_new(path: Path, payload: Dict[str, object]) -> None:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def save(
        self,
        episode_uuid: str,
        *,
        expected_revision: int,
        selected_interval_ids: List[int],
        note: Optional[str],
    ) -> Dict[str, object]:
        with self._lock:
            current = self.load(episode_uuid)
            if int(current["review_revision"]) != expected_revision:
                raise SegmentReviewError("stale_review_revision")
            allowed = {int(item["interval_id"]) for item in current["intervals"]}
            if any(isinstance(value, bool) or not isinstance(value, int) for value in selected_interval_ids):
                raise SegmentReviewError("invalid_interval_id")
            selected = sorted(set(selected_interval_ids))
            if not set(selected).issubset(allowed):
                raise SegmentReviewError("unknown_interval")
            if note is not None:
                note = note.strip()
                if len(note) > 500:
                    raise SegmentReviewError("review_note_too_long")
                if not note:
                    note = None
            revision = expected_revision + 1
            payload = {
                "schema_version": REVIEW_SCHEMA_VERSION,
                "episode_uuid": episode_uuid,
                "review_revision": revision,
                "source_sidecar_sha256": current["source_sidecar_sha256"],
                "selected_interval_ids": selected,
                "note": note,
            }
            root = self._episode_root(episode_uuid)
            revisions = root / "reviews"
            revisions.mkdir(exist_ok=True)
            immutable = revisions / f"review_{revision:06d}.json"
            self._write_new(immutable, payload)
            self._fsync_directory(revisions)
            pointer_tmp = root / f"segment-review.json.writing-{os.getpid()}"
            self._write_new(pointer_tmp, payload)
            os.replace(pointer_tmp, root / "segment-review.json")
            self._fsync_directory(root)
            result = dict(payload)
            result["intervals"] = current["intervals"]
            return result


__all__ = ["REVIEW_SCHEMA_VERSION", "SegmentReviewError", "SegmentReviewStore"]
