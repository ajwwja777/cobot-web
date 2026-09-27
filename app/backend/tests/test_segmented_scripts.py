from __future__ import annotations

import subprocess
from pathlib import Path


def test_segmented_start_check_and_stop_scripts_are_valid_bash() -> None:
    scripts = Path(__file__).parents[1] / "scripts"
    for name in (
        "start_segmented_capture_v1.sh",
        "check_segmented_capture_v1.sh",
        "stop_segmented_capture_v1.sh",
    ):
        result = subprocess.run(
            ["bash", "-n", str(scripts / name)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
