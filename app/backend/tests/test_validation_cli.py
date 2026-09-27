"""Tests for the read-only Task5 acceptance validator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_task5_v1.py"
SPEC = importlib.util.spec_from_file_location("task5_validator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def test_snapshot_and_validate_detect_protected_file_changes(tmp_path: Path):
    protected = tmp_path / "legacy.sh"
    protected.write_text("original\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    report = tmp_path / "report.json"

    assert (
        validator.main(
            ["snapshot", "--protected", str(protected), "--output", str(baseline)]
        )
        == 0
    )
    assert (
        validator.main(
            ["validate", "--baseline", str(baseline), "--output", str(report)]
        )
        == 0
    )
    protected.write_text("changed\n", encoding="utf-8")

    assert (
        validator.main(
            ["validate", "--baseline", str(baseline), "--output", str(report)]
        )
        == 1
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["changed_protected_files"] == [str(protected)]


def test_snapshot_fails_closed_for_missing_or_symlinked_files(tmp_path: Path):
    source = tmp_path / "source"
    source.write_text("data", encoding="utf-8")
    symlink = tmp_path / "link"
    symlink.symlink_to(source)
    output = tmp_path / "baseline.json"

    result = validator.main(
        [
            "snapshot",
            "--protected",
            str(tmp_path / "missing"),
            "--protected",
            str(symlink),
            "--output",
            str(output),
        ]
    )

    assert result == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert all(not item["exists"] for item in payload["protected_files"].values())
