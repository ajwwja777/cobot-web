from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def project_tmp() -> Path:
    root = Path(__file__).resolve().parents[1] / "scratch" / "test-runs" / str(uuid4())
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)
