"""Static recorder configuration with safety-oriented defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RecorderConfig:
    """Configuration for one read-only recorder process."""

    data_root: Path = Path("data/raw_rollouts")
    sample_rate_hz: float = 30.0
    max_duration_seconds: float = 120.0
    min_free_disk_bytes: int = 20 * 1024**3
    stop_free_disk_bytes: int = 10 * 1024**3
    writer_queue_capacity: int = 256
    api_port: int = 8015

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_root", Path(self.data_root))
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be positive")
        if self.min_free_disk_bytes <= 0 or self.stop_free_disk_bytes <= 0:
            raise ValueError("disk thresholds must be positive")
        if self.stop_free_disk_bytes > self.min_free_disk_bytes:
            raise ValueError("stop_free_disk_bytes cannot exceed min_free_disk_bytes")
        if self.writer_queue_capacity <= 0:
            raise ValueError("writer_queue_capacity must be positive")
        if not 1 <= self.api_port <= 65535:
            raise ValueError("api_port must be between 1 and 65535")

    @property
    def sample_period_seconds(self) -> float:
        return 1.0 / self.sample_rate_hz
