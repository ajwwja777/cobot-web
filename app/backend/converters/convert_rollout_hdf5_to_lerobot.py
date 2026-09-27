"""CLI for converting immutable Cobot HDF5 sources to LeRobot 0.4.2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .validate_source import detect_source, open_source


def _inputs(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(path)
    results = sorted(path.rglob("*.hdf5"))
    if not results:
        raise ValueError(f"no HDF5 episodes found below {path}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("legacy", "rollout_v1"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--require-labels",
        action="store_true",
        help="require and archive a final Task5 label sidecar for every episode",
    )
    parser.add_argument(
        "--video-codec",
        choices=("h264", "hevc", "libsvtav1"),
        default="h264",
        help="official LeRobot video codec (h264 is reliable on the Cobot IPC)",
    )
    args = parser.parse_args()

    paths = _inputs(args.input.expanduser().resolve())
    for path in paths:
        detected = detect_source(path)
        if detected != args.mode:
            raise ValueError(
                f"explicit mode {args.mode!r} does not match {path}: {detected!r}"
            )
    from .lerobot_writer import convert_sources

    manifest = convert_sources(
        [open_source(path) for path in paths],
        output=args.output,
        repo_id=args.repo_id,
        task=args.task,
        require_labels=args.require_labels,
        video_codec=args.video_codec,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
