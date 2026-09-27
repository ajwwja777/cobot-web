"""Attach immutable Task5 expert-fact overlays to an existing LeRobot v3 dataset.

This does not rewrite parquet or video payloads.  It records that every frame in
an existing behaviour-cloning demonstration is trusted expert data while keeping
that provenance distinct from Task5 rollout HDF5.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from dataset_tools.manifest import write_json_atomic


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _episode_rows(root: Path) -> list[dict[str, int]]:
    files = sorted((root / "meta/episodes").glob("chunk-*/*.parquet"))
    if not files:
        raise ValueError("LeRobot v3 episode metadata is missing")
    rows: list[dict[str, int]] = []
    for path in files:
        table = pq.read_table(
            path,
            columns=[
                "episode_index",
                "length",
                "data/chunk_index",
                "data/file_index",
            ],
        )
        rows.extend(table.to_pylist())
    rows.sort(key=lambda item: int(item["episode_index"]))
    if [int(item["episode_index"]) for item in rows] != list(range(len(rows))):
        raise ValueError("episode indices must be unique and contiguous")
    if any(int(item["length"]) <= 0 for item in rows):
        raise ValueError("episode lengths must be positive")
    return rows


def _write_neutral_facts(path: Path, frames: int) -> None:
    temporary = path.with_suffix(".tmp.npz")
    zeros_u8 = np.zeros(frames, dtype=np.uint8)
    zeros_u64 = np.zeros(frames, dtype=np.uint64)
    valid = np.ones(frames, dtype=np.bool_)
    np.savez_compressed(
        temporary,
        control_source_left=zeros_u8,
        control_source_right=zeros_u8,
        intervention_id_left=zeros_u64,
        intervention_id_right=zeros_u64,
        left_valid=valid,
        right_valid=valid,
        action_valid=valid,
        state_valid=valid,
    )
    os.replace(temporary, path)


def annotate_legacy_lerobot(
    dataset_root: Path | str,
    *,
    repo_id: str,
    source_version: str,
) -> dict[str, Any]:
    """Write Task5 audit metadata beside an already-migrated LeRobot dataset."""
    root = Path(dataset_root).expanduser().resolve()
    manifest_path = root / "task5_manifest.json"
    facts_root = root / "task5_facts"
    if manifest_path.exists() or facts_root.exists():
        raise FileExistsError("Task5 metadata already exists")
    if not repo_id or "/" not in repo_id:
        raise ValueError("repo_id must be explicit and namespaced")
    if not source_version or source_version == "v3.0":
        raise ValueError("source_version must identify the pre-migration format")
    info_path = root / "meta/info.json"
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("LeRobot info.json is missing or invalid") from error
    if info.get("codebase_version") != "v3.0":
        raise ValueError("Task5 legacy annotation requires a LeRobot v3.0 dataset")
    rows = _episode_rows(root)
    total_frames = sum(int(item["length"]) for item in rows)
    if (
        info.get("total_episodes") != len(rows)
        or info.get("total_frames") != total_frames
    ):
        raise ValueError("LeRobot info totals do not match episode metadata")

    facts_root.mkdir()
    sources: list[dict[str, object]] = []
    parquet_hashes: dict[Path, str] = {}
    try:
        for item in rows:
            episode = int(item["episode_index"])
            frames = int(item["length"])
            chunk = int(item["data/chunk_index"])
            file_index = int(item["data/file_index"])
            data_path = root / f"data/chunk-{chunk:03d}/file-{file_index:03d}.parquet"
            if not data_path.is_file():
                raise ValueError(f"episode data parquet is missing: {data_path}")
            if data_path not in parquet_hashes:
                parquet_hashes[data_path] = _sha256(data_path)
            relative = Path("task5_facts") / f"episode_{episode:06d}.npz"
            _write_neutral_facts(root / relative, frames)
            sources.append(
                {
                    "path": str(data_path),
                    "schema": "legacy_lerobot_v21",
                    "sha256": parquet_hashes[data_path],
                    "frame_count": frames,
                    "facts_overlay": str(relative),
                    "episode_uuid": None,
                }
            )
        manifest: dict[str, Any] = {
            "manifest_schema_version": 1,
            "lerobot_version": "v3.0",
            "source_lerobot_version": source_version,
            "repo_id": repo_id,
            "fps": info["fps"],
            "episode_count": len(rows),
            "frame_count": total_frames,
            "sources": sources,
        }
        write_json_atomic(manifest_path, manifest)
    except BaseException:
        for path in facts_root.glob("*.npz"):
            path.unlink()
        facts_root.rmdir()
        raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--source-version", required=True)
    args = parser.parse_args()
    manifest = annotate_legacy_lerobot(
        args.dataset_root,
        repo_id=args.repo_id,
        source_version=args.source_version,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
