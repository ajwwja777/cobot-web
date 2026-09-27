#!/usr/bin/env python3
"""Compare Task5 legacy normalization with the proven Task3 frame iterator."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Iterable, Mapping
from itertools import zip_longest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from converters.validate_source import open_source

CAMERAS = ("cam_high", "cam_left_wrist", "cam_right_wrist")


def compare_legacy_frames(
    path: Path,
    reference_frames: Iterable[Mapping[str, object]],
) -> dict[str, object]:
    source = open_source(path)
    count = 0
    sentinel = object()
    for current, reference in zip_longest(
        source.iter_frames(), reference_frames, fillvalue=sentinel
    ):
        if current is sentinel or reference is sentinel:
            raise ValueError("reference and Task5 frame counts differ")
        assert not isinstance(current, object) or hasattr(current, "qpos")
        assert isinstance(reference, Mapping)
        np.testing.assert_array_equal(current.qpos, reference["observation.state"])
        np.testing.assert_array_equal(current.action, reference["action"])
        for camera in CAMERAS:
            np.testing.assert_array_equal(
                current.images[camera], reference[f"observation.images.{camera}"]
            )
        count += 1
    return {
        "frame_count": count,
        "camera_count": len(CAMERAS),
        "standard_arrays_equal": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode", type=Path, required=True)
    parser.add_argument("--legacy-python-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.legacy_python_root.expanduser().resolve()
    sys.path.insert(0, str(root))
    module = importlib.import_module("cobot_task3.lerobot_conversion")
    config = SimpleNamespace(cameras=CAMERAS)
    reference = module.iter_episode_frames(args.episode.resolve(), config)
    print(
        json.dumps(compare_legacy_frames(args.episode.resolve(), reference), indent=2)
    )


if __name__ == "__main__":
    main()
