"""Build non-duplicating safe or per-arm-masked DAgger dataset overlays."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from capture_core.schema import ControlSource

from .expert_mask import episode_chunk_anchors, frame_expert_mask
from .manifest import is_derived_path, write_json_atomic

RULE_VERSION = "task5_expert_mask_v1"
ViewKind = Literal["safe", "masked"]


@dataclass
class EpisodeFacts:
    source_kind: str
    episode_key: str
    source_path: Path
    source_sha256: str
    control_source_left: NDArray[np.uint8]
    control_source_right: NDArray[np.uint8]
    intervention_id_left: NDArray[np.uint64]
    intervention_id_right: NDArray[np.uint64]
    left_valid: NDArray[np.bool_]
    right_valid: NDArray[np.bool_]
    action_valid: NDArray[np.bool_]
    state_valid: NDArray[np.bool_]
    dataset_root: Path | None = None
    dataset_episode_index: int | None = None

    def __post_init__(self) -> None:
        arrays = (
            self.control_source_left,
            self.control_source_right,
            self.intervention_id_left,
            self.intervention_id_right,
            self.left_valid,
            self.right_valid,
            self.action_valid,
            self.state_valid,
        )
        lengths = {len(value) for value in arrays if value.ndim == 1}
        if len(lengths) != 1 or any(value.ndim != 1 for value in arrays):
            raise ValueError("episode facts must be aligned one-dimensional arrays")
        if not lengths or lengths == {0}:
            raise ValueError("episode facts must not be empty")
        valid_sources = [int(value) for value in ControlSource]
        if (
            not np.isin(self.control_source_left, valid_sources).all()
            or not np.isin(self.control_source_right, valid_sources).all()
        ):
            raise ValueError("episode facts contain invalid control source codes")
        if (self.dataset_root is None) != (self.dataset_episode_index is None):
            raise ValueError("dataset root and episode index must be provided together")
        if self.dataset_episode_index is not None and self.dataset_episode_index < 0:
            raise ValueError("dataset episode index must be non-negative")

    @property
    def frame_count(self) -> int:
        return len(self.control_source_left)


@dataclass(frozen=True)
class EpisodeOverlay:
    expert_mask: NDArray[np.float32]
    train_anchor: NDArray[np.bool_]


def _approved_interventions(
    labels: Mapping[str, object] | None,
) -> set[tuple[str, int]]:
    if labels is None or labels.get("keep_for_training") != "true":
        return set()
    interventions = labels.get("interventions")
    if not isinstance(interventions, list):
        return set()
    approved: set[tuple[str, int]] = set()
    for item in interventions:
        if not isinstance(item, Mapping):
            continue
        side = item.get("side")
        identifier = item.get("intervention_id")
        if (
            side in {"left", "right"}
            and isinstance(identifier, int)
            and not isinstance(identifier, bool)
            and item.get("quality") == "good"
            and item.get("outcome") == "recovered"
        ):
            approved.add((str(side), identifier))
    return approved


def build_episode_overlay(
    facts: EpisodeFacts,
    *,
    labels: Mapping[str, object] | None,
    view: ViewKind,
    action_horizon: int,
) -> EpisodeOverlay:
    if view not in {"safe", "masked"}:
        raise ValueError("view must be safe or masked")
    if isinstance(action_horizon, bool) or action_horizon <= 0:
        raise ValueError("action_horizon must be positive")
    masks = np.zeros((facts.frame_count, 14), dtype=np.float32)
    if facts.source_kind == "legacy":
        masks[:] = 1.0
    else:
        approved = _approved_interventions(labels)
        for index in range(facts.frame_count):
            raw = frame_expert_mask(
                int(facts.control_source_left[index]),
                int(facts.control_source_right[index]),
                bool(facts.left_valid[index]),
                bool(facts.right_valid[index]),
            )
            if (
                "left",
                int(facts.intervention_id_left[index]),
            ) not in approved:
                raw[:7] = 0
            if (
                "right",
                int(facts.intervention_id_right[index]),
            ) not in approved:
                raw[7:] = 0
            masks[index] = raw
    invalid = ~(facts.action_valid & facts.state_valid)
    masks[invalid] = 0

    if view == "safe":
        bilateral = np.all(masks == 1.0, axis=1)
        masks[~bilateral] = 0
        anchors = np.zeros(facts.frame_count, dtype=np.bool_)
        for start in range(facts.frame_count):
            end = start + action_horizon
            anchors[start] = end <= facts.frame_count and bool(
                np.all(bilateral[start:end])
            )
    else:
        anchors = episode_chunk_anchors(
            masks,
            action_valid=facts.action_valid,
            state_valid=facts.state_valid,
            horizon=action_horizon,
        )
    return EpisodeOverlay(masks, anchors)


def _write_overlay(path: Path, overlay: EpisodeOverlay) -> None:
    temporary = path.with_suffix(".tmp.npz")
    np.savez_compressed(
        temporary,
        expert_mask=overlay.expert_mask,
        train_anchor=overlay.train_anchor,
    )
    os.replace(temporary, path)


def write_view(
    episodes: list[tuple[EpisodeFacts, Mapping[str, object] | None]],
    *,
    output: Path | str,
    view: ViewKind,
    action_horizon: int,
    seed: int,
    command_line: list[str],
    overwrite_derived: bool = False,
) -> dict[str, Any]:
    output_path = Path(output).expanduser().resolve()
    if output_path.exists() and not overwrite_derived:
        raise FileExistsError(output_path)
    if output_path.exists() and overwrite_derived and not is_derived_path(output_path):
        raise ValueError("overwrite is allowed only under data/derived")
    output_path.mkdir(parents=True, exist_ok=True)
    overlay_root = output_path / "episodes"
    overlay_root.mkdir(exist_ok=True)

    episode_entries: list[dict[str, Any]] = []
    left_elements = right_elements = bilateral_frames = 0
    accepted = rejected = frame_count = 0
    for index, (facts, labels) in enumerate(
        sorted(episodes, key=lambda item: item[0].episode_key)
    ):
        overlay = build_episode_overlay(
            facts,
            labels=labels,
            view=view,
            action_horizon=action_horizon,
        )
        relative = Path("episodes") / f"episode_{index:06d}.npz"
        _write_overlay(output_path / relative, overlay)
        left_elements += int(overlay.expert_mask[:, :7].sum())
        right_elements += int(overlay.expert_mask[:, 7:].sum())
        bilateral_frames += int(np.all(overlay.expert_mask == 1.0, axis=1).sum())
        episode_accepted = int(overlay.train_anchor.sum())
        accepted += episode_accepted
        rejected += facts.frame_count - episode_accepted
        frame_count += facts.frame_count
        episode_entries.append(
            {
                "episode_key": facts.episode_key,
                "source_kind": facts.source_kind,
                "source_path": str(facts.source_path),
                "source_sha256": facts.source_sha256,
                "dataset_root": (
                    str(facts.dataset_root.resolve())
                    if facts.dataset_root is not None
                    else None
                ),
                "dataset_episode_index": facts.dataset_episode_index,
                "frame_count": facts.frame_count,
                "accepted_window_count": episode_accepted,
                "overlay": str(relative),
            }
        )
    manifest: dict[str, Any] = {
        "view_schema_version": 1,
        "rule_version": RULE_VERSION,
        "view": view,
        "action_horizon": action_horizon,
        "seed": seed,
        "command_line": command_line,
        "episode_count": len(episode_entries),
        "frame_count": frame_count,
        "accepted_window_count": accepted,
        "rejected_window_count": rejected,
        "left_expert_element_count": left_elements,
        "right_expert_element_count": right_elements,
        "bilateral_expert_frame_count": bilateral_frames,
        "episodes": episode_entries,
    }
    write_json_atomic(output_path / "task5_view_manifest.json", manifest)
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_labels(labels_root: Path | None) -> dict[str, Mapping[str, object]]:
    if labels_root is None:
        return {}
    result: dict[str, Mapping[str, object]] = {}
    for path in sorted(labels_root.rglob("*.labels.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(
            payload.get("episode_uuid"), str
        ):
            raise ValueError(  # noqa: TRY004 - malformed JSON schema is a value error
                f"invalid label sidecar: {path}"
            )
        episode_uuid = payload["episode_uuid"]
        if episode_uuid in result:
            raise ValueError(f"duplicate label UUID: {episode_uuid}")
        result[episode_uuid] = payload
    return result


def _load_dataset_facts(dataset_root: Path) -> list[tuple[EpisodeFacts, str | None]]:
    manifest_path = dataset_root / "task5_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result: list[tuple[EpisodeFacts, str | None]] = []
    for index, source in enumerate(manifest["sources"]):
        overlay = (dataset_root / source["facts_overlay"]).resolve()
        overlay.relative_to(dataset_root.resolve())
        with np.load(overlay) as arrays:
            values = {name: np.array(arrays[name], copy=True) for name in arrays.files}
        schema = source["schema"]
        legacy_schemas = {"legacy_cobot_hdf5", "legacy_lerobot_v21"}
        if schema not in {*legacy_schemas, "task5_rollout_hdf5_v1"}:
            raise ValueError(f"unsupported dataset source schema: {schema}")
        result.append(
            (
                EpisodeFacts(
                    source_kind="legacy" if schema in legacy_schemas else "rollout_v1",
                    episode_key=f"{dataset_root.name}:{index}",
                    source_path=Path(source["path"]),
                    source_sha256=source["sha256"],
                    dataset_root=dataset_root.resolve(),
                    dataset_episode_index=index,
                    **values,
                ),
                source.get("episode_uuid"),
            )
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", choices=("safe", "masked"), required=True)
    parser.add_argument("--action-horizon", type=int, required=True)
    parser.add_argument("--demo-dataset", action="append", type=Path, default=[])
    parser.add_argument("--rollout-dataset", action="append", type=Path, default=[])
    parser.add_argument("--labels-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--overwrite-derived", action="store_true")
    args = parser.parse_args()
    labels = _load_labels(args.labels_root)
    episodes: list[tuple[EpisodeFacts, Mapping[str, object] | None]] = []
    for root in [*args.demo_dataset, *args.rollout_dataset]:
        for facts, episode_uuid in _load_dataset_facts(root.resolve()):
            episodes.append((facts, labels.get(episode_uuid) if episode_uuid else None))
    manifest = write_view(
        episodes,
        output=args.output,
        view=args.view,
        action_horizon=args.action_horizon,
        seed=args.seed,
        command_line=[shlex.join(sys.argv)],
        overwrite_derived=args.overwrite_derived,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
