"""Read-only Task5 dataset conversion boundaries."""

from .source_types import EpisodeMetadata, EpisodeSource, NormalizedFrame
from .validate_source import SourceValidationError, detect_source, open_source

__all__ = [
    "EpisodeMetadata",
    "EpisodeSource",
    "NormalizedFrame",
    "SourceValidationError",
    "detect_source",
    "open_source",
]
