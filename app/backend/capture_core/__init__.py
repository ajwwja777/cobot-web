# Compatibility namespace: implementation is owned by cobot-dagger.
import os as _os
from pathlib import Path as _Path
_dagger = _Path(_os.environ.get("COBOT_DAGGER_PROJECT_ROOT",
    str(_Path(__file__).resolve().parents[4] / "cobot-dagger")))
_impl = _dagger / "src" / __name__
if not _impl.is_dir():
    raise ImportError("Deploy cobot-dagger next to cobot-web, or set COBOT_DAGGER_PROJECT_ROOT")
__path__.insert(0, str(_impl))

"""Task5 v1 read-only rollout recorder."""

from .config import RecorderConfig
from .schema import ControlSource, EpisodeIdentity, EpisodeLabels, FrameSample

__all__ = [
    "ControlSource",
    "EpisodeIdentity",
    "EpisodeLabels",
    "FrameSample",
    "RecorderConfig",
]
