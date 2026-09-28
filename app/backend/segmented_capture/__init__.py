# Compatibility namespace: implementation is owned by cobot-dagger.
import os as _os
from pathlib import Path as _Path
_dagger = _Path(_os.environ.get("COBOT_DAGGER_PROJECT_ROOT",
    str(_Path(__file__).resolve().parents[4] / "cobot-dagger")))
_impl = _dagger / "src" / __name__
if not _impl.is_dir():
    raise ImportError("Deploy cobot-dagger next to cobot-web, or set COBOT_DAGGER_PROJECT_ROOT")
__path__.insert(0, str(_impl))

"""Optional segmented-teach capture overlay for Task5."""

from .state import (
    CaptureNode,
    CaptureSnapshot,
    CaptureState,
    InvalidCaptureEvent,
    SegmentedCaptureReducer,
    SyncedSnapshot,
    TeachMask,
)

__all__ = [
    "CaptureNode",
    "CaptureSnapshot",
    "CaptureState",
    "InvalidCaptureEvent",
    "SegmentedCaptureReducer",
    "SyncedSnapshot",
    "TeachMask",
]
