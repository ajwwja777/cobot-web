"""Durable deterministic manifests for derived Task5 views."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def is_derived_path(path: Path) -> bool:
    parts = path.resolve().parts
    return any(
        parts[index : index + 2] == ("data", "derived")
        for index in range(len(parts) - 1)
    )
