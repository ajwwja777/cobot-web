"""Pure builders for safe and masked Task5 training views."""

from .expert_mask import chunk_anchor, episode_chunk_anchors, frame_expert_mask

__all__ = ["chunk_anchor", "episode_chunk_anchors", "frame_expert_mask"]
