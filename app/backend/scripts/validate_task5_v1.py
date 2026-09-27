#!/usr/bin/env python3
"""Read-only coexistence and rollout validator for Task5 v1 on the Cobot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import h5py

from capture_core.labels import LabelStore

DEFAULT_PROTECTED_PATHS = (
    "/home/agilex/cobot_magic/cobot-station/scripts/start_backend.sh",
    "/home/agilex/cobot_magic/cobot-station/scripts/start_frontend.sh",
    "/home/agilex/cobot_magic/cobot-station/backend/app/services/collector.py",
    "/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/data_collect/convert_cobot_hdf5_to_lerobot.py",
    "/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/task2_teach_handover/task2_teach_button_node.py",
    "/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/launch/start_ms_piper_5arm_button_teach_task2.launch",
    "/home/agilex/cobot_magic/aloha-devel/Piper-AVP-Teleop/multi_arm_launch_tools/can_config_cobot.sh",
    "/home/agilex/cobot_magic/task3/jiaan/deployments/in_the_pot/pi05/interface_task2_teach_rtc_live.sh",
)
DEFAULT_ALLOWED_MASTER_PUBLISHER = "/task2_teach_handover"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_paths(paths: list[Path]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in paths:
        resolved = path.expanduser()
        item: dict[str, object] = {
            "exists": resolved.is_file() and not resolved.is_symlink(),
            "sha256": None,
            "size": None,
        }
        if item["exists"]:
            item["sha256"] = _sha256(resolved)
            item["size"] = resolved.stat().st_size
        result[str(resolved)] = item
    return result


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(f"{path}.tmp")
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    os.replace(temp, path)


def _ros_validation(allowed_master_publisher: str) -> dict[str, object]:
    try:
        import rosgraph  # type: ignore[import-not-found]

        code, message, state = rosgraph.Master("/task5_v1_validator").getSystemState()
        if code != 1:
            raise RuntimeError(str(message))
        publisher_map = {topic: sorted(nodes) for topic, nodes in state[0]}
        master_topics = ("/master/joint_left", "/master/joint_right")
        expected = [allowed_master_publisher]
        master_publishers = {
            topic: publisher_map.get(topic, []) for topic in master_topics
        }
        task5_publishers = sorted(
            {
                node
                for nodes in publisher_map.values()
                for node in nodes
                if "task5" in node.lower()
            }
        )
        passed = all(master_publishers[topic] == expected for topic in master_topics)
        passed = passed and not task5_publishers
        return {
            "passed": passed,
            "master_publishers": master_publishers,
            "task5_publishers": task5_publishers,
            "error_code": None,
        }
    except Exception:  # noqa: BLE001 - ROS is an optional read-only boundary
        return {
            "passed": False,
            "master_publishers": {},
            "task5_publishers": [],
            "error_code": "ros_master_unavailable",
        }


def _episode_validation(path: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "path": str(path),
        "passed": False,
        "sha256": None,
        "episode_uuid": None,
        "intervention_count": None,
        "error_code": None,
    }
    try:
        resolved = path.resolve(strict=True)
        if resolved.is_symlink() or not resolved.is_file():
            raise ValueError("unsafe episode")
        with h5py.File(resolved, "r") as episode:
            episode_uuid = UUID(str(episode.attrs["episode_uuid"]))
        data_root = resolved.parents[3]
        labels = LabelStore(data_root).get_labels(episode_uuid)
        before = _sha256(resolved)
        after = _sha256(resolved)
        if before != after:
            raise ValueError("episode changed during validation")
        result.update(
            {
                "passed": True,
                "sha256": after,
                "episode_uuid": str(episode_uuid),
                "intervention_count": len(labels["interventions"]),
            }
        )
    except Exception:  # noqa: BLE001 - report a stable machine-readable code
        result["error_code"] = "invalid_episode"
    return result


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("baseline must be an object")
    return payload


def command_snapshot(args: argparse.Namespace) -> int:
    protected = snapshot_paths([Path(item) for item in args.protected])
    report: dict[str, object] = {
        "schema_version": 1,
        "kind": "task5_protected_baseline",
        "created_at": _now(),
        "protected_files": protected,
        "passed": all(item["exists"] for item in protected.values()),
    }
    _atomic_json(args.output, report)
    print(args.output)
    return 0 if report["passed"] else 1


def command_validate(args: argparse.Namespace) -> int:
    baseline = _load_json(args.baseline)
    expected = baseline.get("protected_files")
    if not isinstance(expected, dict):
        raise TypeError("baseline lacks protected_files")
    current = snapshot_paths([Path(item) for item in expected])
    changed = sorted(
        path
        for path, item in current.items()
        if not item["exists"]
        or not isinstance(expected.get(path), dict)
        or item["sha256"] != expected[path].get("sha256")
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "kind": "task5_validation",
        "created_at": _now(),
        "protected_files": current,
        "changed_protected_files": changed,
        "ros": None,
        "episode": None,
    }
    if args.check_ros:
        report["ros"] = _ros_validation(args.allowed_master_publisher)
    if args.episode is not None:
        report["episode"] = _episode_validation(args.episode)
    passed = not changed
    if isinstance(report["ros"], dict):
        passed = passed and bool(report["ros"]["passed"])
    if isinstance(report["episode"], dict):
        passed = passed and bool(report["episode"]["passed"])
    report["passed"] = passed
    _atomic_json(args.output, report)
    print(args.output)
    return 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot", help="hash protected legacy files")
    snapshot.add_argument(
        "--protected", action="append", default=None, help="protected path (repeatable)"
    )
    snapshot.add_argument(
        "--output",
        type=Path,
        default=Path("v1/logs/validation/protected-baseline.json"),
    )
    snapshot.set_defaults(handler=command_snapshot)

    validate = subparsers.add_parser(
        "validate", help="compare and inspect read-only state"
    )
    validate.add_argument(
        "--baseline",
        type=Path,
        default=Path("v1/logs/validation/protected-baseline.json"),
    )
    validate.add_argument(
        "--output", type=Path, default=Path("v1/logs/validation/latest.json")
    )
    validate.add_argument("--episode", type=Path)
    validate.add_argument("--check-ros", action="store_true")
    validate.add_argument(
        "--allowed-master-publisher", default=DEFAULT_ALLOWED_MASTER_PUBLISHER
    )
    validate.set_defaults(handler=command_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "snapshot" and args.protected is None:
        args.protected = list(DEFAULT_PROTECTED_PATHS)
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
